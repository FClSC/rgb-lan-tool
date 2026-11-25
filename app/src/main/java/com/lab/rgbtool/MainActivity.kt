package com.lab.rgbtool

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.SharedPreferences
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket

/**
 * 局域网 RGB 灯控制器（实验一 / 第二课 远程控制RGB灯）
 *
 *  手机 --TCP--> WiFi模块 --串口9600--> Arduino
 *  协议： $RGB-Rrrr-Gggg-Bbbb^    三个分量都必须三位数字，以 ^ 结尾
 *
 * 模块是透传桥，不理解协议，只搬字节；所以本程序只负责把字符串写进 TCP 连接。
 * 其它实验课用底部的"自定义按钮"：名称和发送内容都能自己编辑，存在本地。
 */
class MainActivity : Activity() {

    private var sock: Socket? = null
    private var out: OutputStream? = null
    private var busy = false

    private val ui = Handler(Looper.getMainLooper())
    private val logLines = ArrayList<String>()
    private val sendDebounce = Runnable { sendCurrentColor() }

    private lateinit var hostBox: EditText
    private lateinit var portBox: EditText
    private lateinit var cmdBox: EditText
    private lateinit var statusView: TextView
    private lateinit var logView: TextView
    private lateinit var preview: View
    private lateinit var valueView: TextView
    private lateinit var sR: SeekBar
    private lateinit var sG: SeekBar
    private lateinit var sB: SeekBar
    private lateinit var quickContainer: LinearLayout

    private lateinit var prefs: SharedPreferences
    private val quickButtons = ArrayList<QuickButton>()

    /**
     * 不同批次固件的 IP / 端口不一样，自动查找把这些都试一遍。
     * 不放 80 端口：有些手机上 80 会被别的服务占用，造成"假连接成功"。
     */
    private val candidates = arrayOf(
        "192.168.4.1" to 2001, "192.168.1.1" to 2001,
        "192.168.4.1" to 8080, "192.168.1.1" to 8080,
        "192.168.4.1" to 9000, "192.168.1.1" to 9000,
        "192.168.4.1" to 5000, "192.168.1.1" to 5000,
        "192.168.4.1" to 23, "192.168.1.1" to 23
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("rgbtool", Context.MODE_PRIVATE)
        loadQuickButtons()
        setContentView(buildUi())
        appendLog("① 手机连上模块热点（MakeRobot 开头）")
        appendLog("② 点【自动查找】，连上后拖滑块")
    }

    override fun onDestroy() {
        closeQuietly()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ UI

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun text(t: String, size: Float = 14f): TextView {
        val v = TextView(this)
        v.text = t
        v.textSize = size
        v.setPadding(0, dp(6), 0, dp(4))
        return v
    }

    private fun row(): LinearLayout {
        val r = LinearLayout(this)
        r.orientation = LinearLayout.HORIZONTAL
        return r
    }

    private fun button(label: String, onClick: () -> Unit): Button {
        val b = Button(this)
        b.text = label
        b.isAllCaps = false
        b.setOnClickListener { onClick() }
        return b
    }

    private fun buildUi(): View {
        val root = LinearLayout(this)
        root.orientation = LinearLayout.VERTICAL
        root.setPadding(dp(16), dp(16), dp(16), dp(16))

        statusView = text("未连接", 15f)
        root.addView(statusView)

        val addrRow = row()
        hostBox = EditText(this)
        hostBox.setText("192.168.4.1")
        hostBox.inputType = InputType.TYPE_CLASS_TEXT
        portBox = EditText(this)
        portBox.setText("2001")
        portBox.inputType = InputType.TYPE_CLASS_NUMBER
        addrRow.addView(hostBox, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 2f))
        addrRow.addView(portBox, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        root.addView(addrRow)

        val btnRow = row()
        btnRow.addView(button("自动查找") { autoFind() },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        btnRow.addView(button("连接") {
            val p = portBox.text.toString().toIntOrNull() ?: 2001
            connect(hostBox.text.toString().trim(), p)
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        btnRow.addView(button("断开") { disconnect() },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        root.addView(btnRow)

        root.addView(text("颜色", 15f))
        preview = View(this)
        preview.setBackgroundColor(Color.BLACK)
        root.addView(preview, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))

        valueView = text("", 13f)
        root.addView(valueView)

        sR = makeSlider(root, "R")
        sG = makeSlider(root, "G")
        sB = makeSlider(root, "B")

        val presetRow = row()
        addPreset(presetRow, "红", 255, 0, 0)
        addPreset(presetRow, "绿", 0, 255, 0)
        addPreset(presetRow, "蓝", 0, 0, 255)
        addPreset(presetRow, "白", 255, 255, 255)
        addPreset(presetRow, "灭", 0, 0, 0)
        root.addView(presetRow)

        // ---- 自定义按钮 ----
        root.addView(text("自定义按钮（长按可编辑或删除）", 15f))
        quickContainer = LinearLayout(this)
        quickContainer.orientation = LinearLayout.VERTICAL
        root.addView(quickContainer)
        renderQuickButtons()

        // ---- 任意命令 ----
        root.addView(text("任意命令（一次性发送）", 15f))
        cmdBox = EditText(this)
        cmdBox.setText(DEFAULT_COMMAND)
        root.addView(cmdBox)
        root.addView(button("发送") { send(cmdBox.text.toString()) })

        root.addView(text("日志", 15f))
        logView = text("", 12f)
        root.addView(logView)

        valueView.text = rgbCommand(0, 0, 0)

        val scroll = ScrollView(this)
        scroll.addView(root)
        return scroll
    }

    private fun addPreset(parent: LinearLayout, label: String, r: Int, g: Int, b: Int) {
        parent.addView(button(label) { setColor(r, g, b) },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
    }

    private fun makeSlider(parent: LinearLayout, name: String): SeekBar {
        val r = row()
        val t = TextView(this)
        t.text = name
        t.textSize = 15f
        t.width = dp(24)
        r.addView(t)
        val s = SeekBar(this)
        s.max = 255
        s.progress = 0
        s.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                onColorChanged()
            }

            override fun onStartTrackingTouch(seekBar: SeekBar?) {}

            override fun onStopTrackingTouch(seekBar: SeekBar?) {}
        })
        r.addView(s, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        parent.addView(r)
        return s
    }

    // -------------------------------------------------- 自定义按钮（可编辑，本地保存）

    private data class QuickButton(val label: String, val cmd: String)

    private fun renderQuickButtons() {
        quickContainer.removeAllViews()
        for ((index, item) in quickButtons.withIndex()) {
            val b = button(item.label) { send(item.cmd) }
            b.setOnLongClickListener {
                AlertDialog.Builder(this)
                    .setTitle(item.label)
                    .setItems(arrayOf("编辑名称/内容", "删除这个按钮")) { _, which ->
                        if (which == 0) showButtonDialog(index) else confirmDelete(index)
                    }
                    .show()
                true
            }
            quickContainer.addView(b, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        quickContainer.addView(button("＋ 新增按钮") { showButtonDialog(null) },
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
    }

    /** index 为 null 表示新增 */
    private fun showButtonDialog(index: Int?) {
        val editing = index != null && index in quickButtons.indices
        val current = if (editing) quickButtons[index!!] else QuickButton("", cmdBox.text.toString())

        val box = LinearLayout(this)
        box.orientation = LinearLayout.VERTICAL
        box.setPadding(dp(20), dp(8), dp(20), dp(0))

        box.addView(text("按钮名称", 13f))
        val nameEt = EditText(this)
        nameEt.hint = "例如 鸣笛 / 温湿度"
        nameEt.setText(current.label)
        box.addView(nameEt)

        box.addView(text("发送内容（设备协议字符串）", 13f))
        val cmdEt = EditText(this)
        cmdEt.hint = "\$BEEP-1^"
        cmdEt.setText(current.cmd)
        box.addView(cmdEt)

        val builder = AlertDialog.Builder(this)
            .setTitle(if (editing) "编辑按钮" else "新增按钮")
            .setView(box)
            .setPositiveButton("保存") { _, _ ->
                val cmd = cmdEt.text.toString().trim()
                if (cmd.isEmpty()) {
                    appendLog("内容为空，未保存")
                    return@setPositiveButton
                }
                var label = nameEt.text.toString().trim()
                if (label.isEmpty()) label = cmd
                if (editing) {
                    quickButtons[index!!] = QuickButton(label, cmd)
                } else {
                    quickButtons.add(QuickButton(label, cmd))
                }
                saveQuickButtons()
                renderQuickButtons()
                appendLog("已保存按钮：" + label)
            }
            .setNegativeButton("取消", null)

        if (editing) {
            builder.setNeutralButton("删除") { _, _ -> confirmDelete(index!!) }
        }
        builder.show()
    }

    private fun confirmDelete(index: Int) {
        if (index !in quickButtons.indices) return
        AlertDialog.Builder(this)
            .setTitle("删除按钮")
            .setMessage("确定删除「" + quickButtons[index].label + "」？")
            .setPositiveButton("删除") { _, _ ->
                quickButtons.removeAt(index)
                saveQuickButtons()
                renderQuickButtons()
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun loadQuickButtons() {
        quickButtons.clear()
        val raw = prefs.getString(KEY_BUTTONS, null)
        if (raw.isNullOrEmpty()) {
            quickButtons.addAll(defaultQuickButtons())
            saveQuickButtons()
            return
        }
        try {
            val arr = JSONArray(raw)
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                val cmd = o.optString("c")
                if (cmd.isNotEmpty()) quickButtons.add(QuickButton(o.optString("l", cmd), cmd))
            }
        } catch (e: Exception) {
            appendLogSafe("自定义按钮读取失败，用默认值")
            quickButtons.clear()
            quickButtons.addAll(defaultQuickButtons())
        }
    }

    private fun saveQuickButtons() {
        val arr = JSONArray()
        for (b in quickButtons) {
            arr.put(JSONObject().put("l", b.label).put("c", b.cmd))
        }
        prefs.edit().putString(KEY_BUTTONS, arr.toString()).apply()
    }

    private fun defaultQuickButtons(): List<QuickButton> = listOf(
        QuickButton("实验1 LED", "\$LED-S11-S21-S30-L1100-L2050-L3000^"),
        QuickButton("实验2 鸣笛", "\$BEEP-1^"),
        QuickButton("实验3 温湿度", "\$TH-1^"),
        QuickButton("实验4 闹钟", "\$CLK-0-2017-11-10-15-23-20-5^"),
        QuickButton("实验5 超声波", "\$UTL-XXX-123-X^"),
        QuickButton("继电器 开", "\$RELAY-1^"),
        QuickButton("继电器 关", "\$RELAY-0^"),
        QuickButton("舵机 90°", "\$SERVO-90^")
    )

    // -------------------------------------------------------------- 颜色

    private fun onColorChanged() {
        if (!this::sR.isInitialized || !this::sG.isInitialized || !this::sB.isInitialized) return
        val r = sR.progress
        val g = sG.progress
        val b = sB.progress
        preview.setBackgroundColor(Color.rgb(r, g, b))
        valueView.text = rgbCommand(r, g, b)
        ui.removeCallbacks(sendDebounce)
        ui.postDelayed(sendDebounce, 120)
    }

    private fun setColor(r: Int, g: Int, b: Int) {
        sR.progress = r
        sG.progress = g
        sB.progress = b
        onColorChanged()
    }

    private fun rgbCommand(r: Int, g: Int, b: Int): String =
        String.format("${'$'}RGB-R%03d-G%03d-B%03d^", r, g, b)

    private fun sendCurrentColor() {
        send(rgbCommand(sR.progress, sG.progress, sB.progress))
    }

    // -------------------------------------------------------------- 发送

    private fun send(cmd: String) {
        val o = out
        if (o == null) {
            appendLog("未连接，发不出去：" + cmd)
            return
        }
        Thread {
            try {
                o.write(cmd.toByteArray())
                o.flush()
                ui.post { appendLog("→ " + cmd) }
            } catch (e: Exception) {
                ui.post {
                    appendLog("发送失败：" + (e.message ?: ""))
                    setStatus("连接已断开")
                }
            }
        }.start()
    }

    // ------------------------------------------------------------ 连接管理

    private fun setStatus(t: String) {
        statusView.text = t
    }

    /** 一直读模块回传的字节，字节数 / 十六进制 / 文本都打进日志 */
    private fun startReader(s: Socket, ins: InputStream) {
        val t = Thread {
            val buf = ByteArray(256)
            while (true) {
                val n = try {
                    ins.read(buf)
                } catch (e: Exception) {
                    -1
                }
                if (n <= 0) break
                if (sock !== s) break
                val hex = StringBuilder()
                for (i in 0 until n) {
                    if (i > 0) hex.append(' ')
                    hex.append(String.format("%02X", buf[i].toInt() and 0xFF))
                }
                val text = String(buf, 0, n, Charsets.UTF_8).trim()
                ui.post { appendLog("← " + hex + "   [" + text + "]") }
            }
            if (sock === s) {
                ui.post {
                    setStatus("模块断开了连接")
                    appendLog("模块断开了连接")
                }
            }
        }
        t.isDaemon = true
        t.start()
    }

    private fun appendLog(s: String) {
        logLines.add(s)
        while (logLines.size > 30) logLines.removeAt(0)
        if (this::logView.isInitialized) logView.text = logLines.joinToString("\n")
    }

    /** onCreate 早期日志（界面还没建好时） */
    private fun appendLogSafe(s: String) {
        logLines.add(s)
    }

    private fun connect(host: String, port: Int) {
        if (busy) return
        busy = true
        setStatus("连接中 " + host + ":" + port + " …")
        Thread {
            try {
                val s = Socket()
                s.connect(InetSocketAddress(host, port), 2500)
                s.tcpNoDelay = true
                closeQuietly()
                sock = s
                out = s.getOutputStream()
                val ins = s.getInputStream()
                startReader(s, ins)
                ui.post {
                    setStatus("已连接 " + host + ":" + port)
                    appendLog("✔ 已连接 " + host + ":" + port)
                }
            } catch (e: Exception) {
                ui.post {
                    setStatus("连接失败：" + (e.message ?: e.javaClass.simpleName))
                    appendLog("× " + host + ":" + port + "  " + (e.message ?: ""))
                }
            } finally {
                busy = false
            }
        }.start()
    }

    private fun autoFind() {
        if (busy) return
        busy = true
        setStatus("正在自动查找模块…")
        Thread {
            for ((h, p) in candidates) {
                try {
                    val s = Socket()
                    s.connect(InetSocketAddress(h, p), 700)
                    s.tcpNoDelay = true
                    closeQuietly()
                    sock = s
                    out = s.getOutputStream()
                    startReader(s, s.getInputStream())
                    ui.post {
                        hostBox.setText(h)
                        portBox.setText(p.toString())
                        setStatus("找到并连接 " + h + ":" + p)
                        appendLog("✔ " + h + ":" + p)
                    }
                    busy = false
                    return@Thread
                } catch (e: Exception) {
                    ui.post { appendLog("· " + h + ":" + p + " 不通") }
                }
            }
            ui.post {
                setStatus("没找到模块：确认已连模块热点、且开关在局域模式")
                appendLog("所有候选地址都不通")
            }
            busy = false
        }.start()
    }

    private fun disconnect() {
        closeQuietly()
        setStatus("未连接")
    }

    private fun closeQuietly() {
        try {
            out?.close()
        } catch (e: Exception) {
        }
        try {
            sock?.close()
        } catch (e: Exception) {
        }
        out = null
        sock = null
    }

    companion object {
        private const val DEFAULT_COMMAND = "\$RGB-R255-G125-B000^"
        private const val KEY_BUTTONS = "buttons"
    }
}