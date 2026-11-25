#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网 RGB 灯控制工具（替代厂商 APP 的"局域网模式"）

用途：
  1) 扫描模块在局域网里监听的端口（先确认 IP 和端口，别再猜）
  2) 连上模块后发 $RGB-R255-G125-B000^ ，模块会把字节原样转给单片机
  3) 图形界面调色，可当课堂演示用

用法：
  python rgb_tool.py                 # 打开图形界面
  python rgb_tool.py --scan          # 扫描常见端口（先用这个）
  python rgb_tool.py --scan 192.168.1.1
  python rgb_tool.py --send 192.168.4.1 2001 "$RGB-R255-G000-B000^"

只依赖 Python 标准库。
"""

import select
import socket
import sys
import threading
import time

# 常见候选端口：厂商固件不同批次不一样，所以先扫一遍
CANDIDATE_PORTS = [2001, 8080, 9000, 5000, 23, 80, 8081, 8888, 6000, 4001, 1001]
DEFAULT_HOSTS = ["192.168.4.1", "192.168.1.1"]


def probe(host, port, timeout=0.8):
    """能建立 TCP 连接就返回 True。

    用非阻塞 connect + select：直接 settimeout 在 Windows 上遇到不可达地址
    会卡到系统默认超时（约 20 秒），扫描会慢到没法用。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setblocking(False)
    try:
        s.connect_ex((host, port))
        _, writable, errored = select.select([], [s], [s], timeout)
        if not writable and not errored:
            return False
        return s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0
    except Exception:
        return False
    finally:
        s.close()


def scan(hosts=None, ports=None, timeout=0.8):
    hosts = hosts or DEFAULT_HOSTS
    ports = ports or CANDIDATE_PORTS
    found = []
    for h in hosts:
        for p in ports:
            ok = probe(h, p, timeout)
            print("  %-15s:%-5d %s" % (h, p, "OPEN" if ok else "-"))
            if ok:
                found.append((h, p))
    print()
    if found:
        print("可用端点：")
        for h, p in found:
            print("  %s:%d" % (h, p))
        print("\n接下来试：python rgb_tool.py --send %s %d \"$RGB-R255-G000-B000^\"" % found[0])
    else:
        print("没有扫到开放端口。检查：")
        print("  1) 手机/电脑是否连的是模块自己的热点（MakeRobot 开头那种）")
        print("  2) 模块开关是否在【局域模式】，并断电重启过")
        print("  3) 用 ipconfig 看一下本机网关地址，把它作为 host 再扫一次")
    return found


class Endpoint:
    """一个到模块的 TCP 连接。"""

    def __init__(self):
        self.sock = None
        self.host = None
        self.port = None

    @property
    def connected(self):
        return self.sock is not None

    def connect(self, host, port, timeout=2.5):
        self.close()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.settimeout(None)
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        self.sock, self.host, self.port = s, host, port
        return True

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        self.host = self.port = None

    def send(self, text):
        if not self.sock:
            raise RuntimeError("未连接")
        self.sock.sendall(text.encode("utf-8"))


def rgb_cmd(r, g, b):
    """协议要求三个分量都是三位数字，结尾必须是 ^"""
    return "$RGB-R%03d-G%03d-B%03d^" % (r, g, b)


def send_once(host, port, cmd):
    ep = Endpoint()
    ep.connect(host, port)
    ep.send(cmd)
    print("已发送 %s 到 %s:%d" % (cmd, host, port))
    print("（模块不会回话，去看单片机的灯）")
    time.sleep(0.3)
    ep.close()


def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    ep = Endpoint()
    root = tk.Tk()
    root.title("局域网 RGB 灯控制")
    root.geometry("420x560")

    top = ttk.LabelFrame(root, text="连接模块")
    top.pack(fill="x", padx=8, pady=6)

    host_var = tk.StringVar(value="192.168.4.1")
    port_var = tk.StringVar(value="2001")
    ttk.Label(top, text="IP").grid(row=0, column=0, sticky="w", padx=4, pady=4)
    ttk.Entry(top, textvariable=host_var, width=16).grid(row=0, column=1, padx=4)
    ttk.Label(top, text="端口").grid(row=0, column=2, sticky="w", padx=4)
    ttk.Entry(top, textvariable=port_var, width=7).grid(row=0, column=3, padx=4)

    status_var = tk.StringVar(value="未连接")

    def set_status(t):
        status_var.set(t)

    def do_connect():
        try:
            ep.connect(host_var.get().strip(), int(port_var.get()))
            set_status("已连接 %s:%d" % (ep.host, ep.port))
        except Exception as e:
            set_status("连接失败：%s" % e)
            messagebox.showerror("连接失败", str(e))

    def do_disconnect():
        ep.close()
        set_status("未连接")

    def do_scan():
        set_status("扫描中…")
        hosts = [host_var.get().strip()] + [h for h in DEFAULT_HOSTS if h != host_var.get().strip()]

        def work():
            for h in hosts:
                for p in CANDIDATE_PORTS:
                    if probe(h, p):
                        root.after(0, lambda h=h, p=p: (host_var.set(h), port_var.set(str(p)),
                                                        set_status("发现 %s:%d，点连接" % (h, p))))
                        return
            root.after(0, lambda: set_status("没找到开放端口"))

        threading.Thread(target=work, daemon=True).start()

    ttk.Button(top, text="连接", command=do_connect).grid(row=0, column=4, padx=4)
    ttk.Button(top, text="断开", command=do_disconnect).grid(row=0, column=5, padx=4)
    ttk.Button(top, text="自动扫描", command=do_scan).grid(row=1, column=0, columnspan=6, sticky="ew", padx=4, pady=4)
    ttk.Label(top, textvariable=status_var).grid(row=2, column=0, columnspan=6, sticky="w", padx=4, pady=(0, 4))

    mid = ttk.LabelFrame(root, text="颜色 (0-255)")
    mid.pack(fill="both", expand=True, padx=8, pady=6)

    preview = tk.Label(mid, text="", height=3, background="#000000")
    preview.pack(fill="x", padx=6, pady=6)

    scales = {}
    for name in ("R", "G", "B"):
        row = ttk.Frame(mid)
        row.pack(fill="x", padx=6, pady=2)
        ttk.Label(row, text=name, width=2).pack(side="left")
        s = tk.Scale(row, from_=0, to=255, orient="horizontal")
        s.pack(side="left", fill="x", expand=True)
        scales[name] = s

    val_var = tk.StringVar(value=rgb_cmd(0, 0, 0))
    ttk.Label(mid, textvariable=val_var, anchor="center").pack(fill="x", pady=4)

    pending = {"id": None}

    def flush():
        r, g, b = scales["R"].get(), scales["G"].get(), scales["B"].get()
        preview.configure(background="#%02x%02x%02x" % (r, g, b))
        cmd = rgb_cmd(r, g, b)
        val_var.set(cmd)
        if ep.connected:
            try:
                ep.send(cmd)
            except Exception as e:
                set_status("发送失败：%s" % e)

    def on_change(_=None):
        if pending["id"]:
            root.after_cancel(pending["id"])
        pending["id"] = root.after(120, flush)

    for s in scales.values():
        s.configure(command=on_change)

    def preset(r, g, b):
        scales["R"].set(r)
        scales["G"].set(g)
        scales["B"].set(b)
        flush()

    pre = ttk.Frame(mid)
    pre.pack(fill="x", padx=6, pady=6)
    for text, (r, g, b) in (("红", (255, 0, 0)), ("绿", (0, 255, 0)), ("蓝", (0, 0, 255)),
                            ("白", (255, 255, 255)), ("灭", (0, 0, 0))):
        ttk.Button(pre, text=text, width=5, command=lambda r=r, g=g, b=b: preset(r, g, b)).pack(side="left", padx=2)

    raw = ttk.LabelFrame(root, text="发送任意命令（协议样例：$RGB-R255-G125-B000^ ）")
    raw.pack(fill="x", padx=8, pady=6)
    cmd_var = tk.StringVar(value="$RGB-R255-G125-B000^")
    ttk.Entry(raw, textvariable=cmd_var).pack(fill="x", padx=6, pady=4)

    def send_raw():
        if not ep.connected:
            messagebox.showwarning("提示", "先连接模块")
            return
        try:
            ep.send(cmd_var.get())
            set_status("已发送：%s" % cmd_var.get())
        except Exception as e:
            set_status("发送失败：%s" % e)

    ttk.Button(raw, text="发送", command=send_raw).pack(padx=6, pady=(0, 6), fill="x")

    def on_close():
        ep.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def main():
    args = sys.argv[1:]
    if not args:
        run_gui()
    elif args[0] == "--scan":
        hosts = [args[1]] if len(args) > 1 else None
        scan(hosts)
    elif args[0] == "--send" and len(args) >= 4:
        send_once(args[1], int(args[2]), args[3])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()