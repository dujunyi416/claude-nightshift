"""Local web settings panel (no tkinter needed - pure stdlib http.server).

Serves a single-page UI on 127.0.0.1 only. The tray icon opens it in your
browser; everything the CLI can do is one click here: quota bars, daily
warmup time, bedtime prompt queue, watch autopilot, start-with-Windows.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Nightshift</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: "Segoe UI", system-ui, sans-serif; background:#14161a;
         color:#e8e8e8; max-width:640px; margin:24px auto; padding:0 16px; }
  h1 { font-size:20px; } h1 small { color:#888; font-weight:normal; font-size:12px; }
  .card { background:#1d2025; border:1px solid #2a2e35; border-radius:10px;
          padding:14px 16px; margin:12px 0; }
  .card h2 { font-size:14px; margin:0 0 10px; color:#9ecbff; }
  .bar { background:#2a2e35; border-radius:6px; height:14px; overflow:hidden; margin:4px 0 10px; }
  .bar div { height:100%; border-radius:6px; transition:width .5s; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:6px 0; }
  .muted { color:#8a919c; font-size:12px; }
  input[type=text], textarea { background:#14161a; color:#e8e8e8; border:1px solid #3a3f48;
    border-radius:6px; padding:6px 8px; font-size:13px; }
  textarea { width:100%; box-sizing:border-box; min-height:70px; }
  button { background:#2b5ea7; color:#fff; border:0; border-radius:6px;
           padding:6px 12px; cursor:pointer; font-size:13px; }
  button.gray { background:#3a3f48; } button.red { background:#a73b2b; }
  button:hover { filter:brightness(1.15); }
  ul { list-style:none; padding:0; margin:8px 0 0; }
  li { background:#14161a; border:1px solid #2a2e35; border-radius:6px;
       padding:6px 8px; margin:4px 0; font-size:12px; display:flex;
       justify-content:space-between; gap:8px; align-items:center; }
  li span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ok { color:#5fd068; } .warn { color:#e0a020; }
  label { font-size:13px; }
</style></head><body>
<h1>🌙 Claude Nightshift <small id="src"></small></h1>

<div class="card"><h2>额度</h2>
  <div>5 小时窗口 <b id="u5">…</b> <span class="muted" id="r5"></span></div>
  <div class="bar"><div id="b5" style="width:0%;background:#2e9e4f"></div></div>
  <div>7 天窗口 <b id="u7">…</b> <span class="muted" id="r7"></span></div>
  <div class="bar"><div id="b7" style="width:0%;background:#2e9e4f"></div></div>
  <div class="row"><button class="gray" onclick="act('/api/refresh')">立即刷新</button>
  <button class="gray" onclick="act('/api/warmup_now')">立即预热窗口</button></div>
</div>

<div class="card"><h2>每日预热 — 睡醒前提前激活 5 小时窗口</h2>
  <div class="row">每天 <input type="text" id="wtime" size="5" placeholder="07:00">
    <button onclick="applyWarmup()">应用</button>
    <button class="red" onclick="act('/api/warmup/remove').then(refresh)">移除</button>
    <button class="gray" onclick="suggest()">学习我的作息</button></div>
  <div class="muted" id="wstatus"></div>
</div>

<div class="card"><h2>睡前任务队列</h2>
  <textarea id="prompt" placeholder="把任务描述写在这里，例如：&#10;按 TODO 重构 data/loader.py，跑测试并修复所有失败"></textarea>
  <div class="row">目录 <input type="text" id="cwd" size="38">
    <button onclick="addJob()">加入队列</button></div>
  <ul id="queue"></ul>
</div>

<div class="card"><h2>自动驾驶 — 限额一解除：续跑被打断的会话 + 执行队列</h2>
  <div class="row"><button id="wbtn" onclick="act('/api/watch/toggle').then(refresh)">启动 watch</button>
  <span id="wstate" class="muted"></span></div>
</div>

<div class="card"><h2>系统</h2>
  <div class="row"><label><input type="checkbox" id="autostart"
    onchange="post('/api/autostart',{enabled:this.checked})"> 开机自启托盘</label></div>
  <div class="muted">数据目录: <span id="datadir"></span></div>
</div>

<script>
const $ = id => document.getElementById(id);
const color = u => u==null?'#5a6472':u>=90?'#cc3333':u>=70?'#e07020':u>=50?'#d8a200':'#2e9e4f';
async function post(url, body) {
  const r = await fetch(url, {method:'POST', body: JSON.stringify(body||{})});
  return r.json();
}
const act = url => post(url);
function fmtWin(w, u, b, r) {
  if (!w || w.utilization==null) { $(u).textContent='无数据'; return; }
  $(u).textContent = Math.round(w.utilization)+'%';
  $(b).style.width = Math.min(w.utilization,100)+'%';
  $(b).style.background = color(w.utilization);
  $(r).textContent = w.active ? `· ${w.resets_local} 重置（还有 ${w.countdown}）`
                              : '· 窗口空闲，下一条消息开始计时';
}
async function refresh() {
  const s = await (await fetch('/api/state')).json();
  if (s.usage) {
    fmtWin(s.usage.five_hour,'u5','b5','r5');
    fmtWin(s.usage.seven_day,'u7','b7','r7');
    $('src').textContent = `更新 ${s.usage.fetched_local} · 来源 ${s.usage.source}`;
  } else { $('src').textContent = '额度获取失败（token 过期？任何 claude 命令可刷新）'; }
  if (!$('wtime').matches(':focus')) $('wtime').value = s.warmup_time;
  $('wstatus').textContent = s.schedule;
  $('queue').innerHTML = s.queue.map(j =>
    `<li><span title="${j.prompt}">${j.prompt}</span><span class="muted">${j.cwd_name}</span>
     <button class="red" onclick="post('/api/queue/remove',{id:'${j.id}'}).then(refresh)">删</button></li>`).join('')
    || '<li class="muted">队列为空</li>';
  $('wbtn').textContent = s.watch.running ? '停止 watch' : '启动 watch';
  $('wstate').textContent = s.watch.running ? `运行中 (PID ${s.watch.pid})` : '未运行';
  $('wstate').className = s.watch.running ? 'ok' : 'muted';
  $('autostart').checked = s.autostart;
  $('datadir').textContent = s.data_dir;
  if (!$('cwd').value) $('cwd').value = s.home;
}
async function applyWarmup() {
  const r = await post('/api/warmup/apply', {time: $('wtime').value.trim()});
  $('wstatus').textContent = r.message; refresh();
}
async function suggest() {
  $('wstatus').textContent = '分析最近30天会话中…';
  const r = await (await fetch('/api/warmup/suggest')).json();
  $('wstatus').textContent = r.message;
  if (r.time) $('wtime').value = r.time;
}
async function addJob() {
  const p = $('prompt').value.trim();
  if (!p) return;
  await post('/api/queue/add', {prompt: p, cwd: $('cwd').value.trim()});
  $('prompt').value = ''; refresh();
}
refresh(); setInterval(refresh, 30000);
</script></body></html>
"""


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class PanelHandler(BaseHTTPRequestHandler):
    app = None  # injected by start_server

    def log_message(self, *args) -> None:  # silence request spam
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, code: int = 200) -> None:
        self._send(code, _json_bytes(obj), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send_json(self.app.state())
        elif self.path == "/api/warmup/suggest":
            self._send_json(self.app.suggest_warmup())
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}
        routes = {
            "/api/refresh": lambda: self.app.refresh_usage(force=True),
            "/api/warmup_now": self.app.warmup_now,
            "/api/warmup/apply": lambda: self.app.apply_warmup(
                body.get("time", "")),
            "/api/warmup/remove": self.app.remove_warmup,
            "/api/queue/add": lambda: self.app.add_job(
                body.get("prompt", ""), body.get("cwd", "")),
            "/api/queue/remove": lambda: self.app.remove_job(
                body.get("id", "")),
            "/api/watch/toggle": self.app.toggle_watch,
            "/api/autostart": lambda: self.app.set_autostart(
                bool(body.get("enabled"))),
        }
        fn = routes.get(self.path)
        if fn is None:
            self._send_json({"error": "not found"}, 404)
            return
        try:
            result = fn()
            self._send_json(result if isinstance(result, dict) else {"ok": True})
        except Exception as e:  # surface errors to the UI instead of a 500 page
            self._send_json({"ok": False, "message": str(e)}, 500)


def start_server(app, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (PanelHandler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
