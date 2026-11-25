#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网 RGB 灯 —— 网页版桥接程序

浏览器不能开原始 TCP，所以需要它：
    手机浏览器 --HTTP--> 本程序 --TCP--> WiFi模块 --串口9600--> Arduino

启动：
    python bridge.py
    python bridge.py --port 8770

启动后会打印手机可访问的地址，例如 http://192.168.4.2:8770
把这个地址发给学生，手机连上模块热点后直接打开即可（不用装任何 App）。

只需要 Python 3.8+，无第三方库。
"""

import json
import os
import select
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(HERE, "index.html")

# 不同批次固件的 IP / 端口不一样，自动查找全试一遍。
# 特意不放 80 端口：有些电脑上 80 会被别的服务/代理占用，造成"假连接成功"。
CANDIDATES = [
    (h, p)
    for h in ("192.168.4.1", "192.168.1.1")
    for p in (2001, 8080, 9000, 5000, 23)
]

MAX_LINES = 60


def tcp_probe(host, port, timeout=0.8):
    """非阻塞探测端口。

    直接 socket.connect + settimeout 在 Windows 上对不可达地址会卡到系统
    默认超时（约 20 秒），自动查找会慢到没法用，所以用非阻塞 + select。
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


class Link:
    """到模块的一条 TCP 连接 + 收发缓冲。"""

    def __init__(self):
        self.sock = None
        self.host = None
        self.port = None
        self.lock = threading.Lock()
        self.rx = []      # 模块发回来的内容（十六进制+可读）
        self.log = []     # 本程序的日志
        self.reader = None

    # ---------------- 状态

    @property
    def connected(self):
        return self.sock is not None

    def note(self, text):
        with self.lock:
            self.log.append(time.strftime("%H:%M:%S ") + text)
            del self.log[:-MAX_LINES]

    def _push_rx(self, text):
        with self.lock:
            self.rx.append(text)
            del self.rx[:-MAX_LINES]

    # ---------------- 连接

    def connect(self, host, port, timeout=2.5):
        self.close(quiet=True)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.settimeout(None)
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        self.sock, self.host, self.port = s, host, port
        self.note("已连接 %s:%d" % (host, port))
        self.reader = threading.Thread(target=self._read_loop, args=(s,), daemon=True)
        self.reader.start()
        return True

    def auto_connect(self):
        self.note("本机 IP: " + local_ip())
        for h, p in CANDIDATES:
            if not tcp_probe(h, p, 0.8):
                self.note("  %s:%d 不通" % (h, p))
                continue
            try:
                self.connect(h, p, timeout=2.5)
                return True
            except Exception as e:
                self.note("  %s:%d 连接失败 (%s)" % (h, p, e.__class__.__name__))
        self.note("自动查找失败：确认设备已连模块热点")
        return False

    def close(self, quiet=False):
        s = self.sock
        self.sock = None
        self.host = self.port = None
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
            if not quiet:
                self.note("连接已断开")

    # ---------------- 收发

    def _read_loop(self, s):
        while True:
            try:
                chunk = s.recv(256)
            except Exception:
                chunk = b""
            if not chunk:
                break
            if s is not self.sock:
                break
            hexs = " ".join("%02X" % b for b in chunk)
            try:
                text = chunk.decode("utf-8")
            except Exception:
                text = chunk.decode("latin-1")
            self._push_rx("← %s   [%s]" % (hexs, text.strip()))
        if s is self.sock:
            self.sock = None
            self.note("模块断开了连接")

    def send(self, cmd):
        with self.lock:
            s = self.sock
            if s is None:
                raise RuntimeError("未连接模块")
            s.sendall(cmd.encode("utf-8"))
        self.note("→ " + cmd)


LINK = Link()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 不刷屏

    # ---------------- 工具

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return ""
        return self.rfile.read(n).decode("utf-8", "replace")

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self._json({"ok": False, "error": "找不到 " + path}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------------- 路由

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._file(INDEX_FILE)
        elif path == "/api/status":
            with LINK.lock:
                log = list(LINK.log)
                rx = list(LINK.rx)
            self._json({
                "ok": True,
                "connected": LINK.connected,
                "host": LINK.host,
                "port": LINK.port,
                "myip": local_ip(),
                "log": log,
                "rx": rx,
            })
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        raw = self._body()
        try:
            if path == "/api/send":
                cmd = raw
                if raw.startswith("{"):
                    cmd = json.loads(raw).get("cmd", "")
                if not cmd:
                    self._json({"ok": False, "error": "空的命令"}, 400)
                    return
                LINK.send(cmd)
                self._json({"ok": True})

            elif path == "/api/connect":
                opt = json.loads(raw) if raw.startswith("{") else {}
                if opt.get("auto"):
                    ok = LINK.auto_connect()
                else:
                    ok = LINK.connect(opt.get("host", "192.168.4.1"),
                                      int(opt.get("port", 2001)))
                self._json({"ok": ok, "host": LINK.host, "port": LINK.port})

            elif path == "/api/disconnect":
                LINK.close()
                self._json({"ok": True})

            else:
                self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "error": "%s: %s" % (e.__class__.__name__, e)}, 500)


def local_ips():
    """列出本机所有可能的局域网地址。

    只靠 8.8.8.8 那招在"只连着模块热点、没有外网"时可能取不到，
    所以把主机名解析出来的地址也一起列出，让用户照着试。
    """
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.append(ip)
        finally:
            s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


def local_ip():
    """猜一个本机在局域网里的地址（不会真的发包）。"""
    return local_ips()[0]


def serve(host="0.0.0.0", port=8770):
    """启动服务，返回 server 对象（测试时可直接调用）。"""
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main():
    port = 8770
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

    srv = serve("0.0.0.0", port)
    ips = local_ips()
    print("=" * 60)
    print(" 局域网 RGB 灯 · 网页版桥接已启动")
    print("=" * 60)
    print(" 手机/电脑打开：  http://%s:%d" % (ips[0], port))
    if len(ips) > 1:
        print(" 打不开就试这些： " + "  ".join("http://%s:%d" % (i, port) for i in ips[1:]))
    print(" 本机自测：      http://127.0.0.1:%d" % port)
    print()
    print(" 它做的事：给浏览器当中间人，把网页上的命令转成 TCP 发给模块。")
    print(" 前提：这台电脑要连上模块热点；手机也要连同一个热点才能打开上面的地址。")
    print(" 停止：Ctrl+C")
    print("=" * 60)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        LINK.close(quiet=True)
        srv.server_close()


if __name__ == "__main__":
    main()