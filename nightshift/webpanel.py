"""Local web settings panel (no tkinter needed - pure stdlib http.server).

Serves a single-page UI on 127.0.0.1 only. The tray icon opens it in your
browser; everything the CLI can do is one click here: quota bars, recent
sessions (pick one to continue it), daily warmup time, bedtime prompt
queue, run history with logs, watch autopilot, start-with-Windows.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Nightshift</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: "Segoe UI", system-ui, sans-serif; background:#14161a;
         color:#e8e8e8; max-width:680px; margin:24px auto; padding:0 16px; }
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
  button.small { padding:3px 8px; font-size:12px; }
  button:hover { filter:brightness(1.15); }
  ul { list-style:none; padding:0; margin:8px 0 0; }
  li { background:#14161a; border:1px solid #2a2e35; border-radius:6px;
       padding:6px 8px; margin:4px 0; font-size:12px; display:flex;
       justify-content:space-between; gap:8px; align-items:center; }
  li .grow { flex:1; min-width:0; overflow:hidden; }
  li .t { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  li.sel { border-color:#2b5ea7; background:#1a2433; }
  li.sess { cursor:pointer; }
  .badge { font-size:11px; padding:1px 6px; border-radius:8px; white-space:nowrap; }
  .badge.cut { background:#5a2520; color:#ff9d8a; }
  .badge.res { background:#1f3a5c; color:#9ecbff; }
  .badge.ok { background:#1f4a28; color:#8ae09a; }
  .badge.fail { background:#5a2520; color:#ff9d8a; }
  .ok { color:#5fd068; }
  label { font-size:13px; }
  pre { background:#0e1013; border:1px solid #2a2e35; border-radius:6px;
        padding:8px; font-size:11px; white-space:pre-wrap; max-height:240px;
        overflow:auto; }
  #target { background:#1a2433; border:1px solid #2b5ea7; border-radius:6px;
            padding:6px 10px; font-size:12px; margin-bottom:6px;
            display:flex; justify-content:space-between; align-items:center; }
</style></head><body>
<h1>🌙 Claude Nightshift <small id="src"></small></h1>

<div class="card"><h2>额度</h2>
  <div>5 小时窗口 <b id="u5">…</b> <span class="muted" id="r5"></span></div>
  <div class="bar"><div id="b5" style="width:0%;background:#2e9e4f"></div></div>
  <div>7 天窗口 <b id="u7">…</b> <span class="muted" id="r7"></span></div>
  <div class="bar"><div id="b7" style="width:0%;background:#2e9e4f"></div></div>
  <div class="muted" id="weekly"></div>
  <div class="row"><button class="gray" onclick="act('/api/refresh').then(refresh)">立即刷新</button>
  <button class="gray" onclick="act('/api/warmup_now')">立即预热窗口</button></div>
</div>

<div class="card"><h2>白天保温 — 醒着的时段，窗口一空闲就自动激活</h2>
  <div class="row"><label><input type="checkbox" id="kwon"> 开启</label>
    从 <input type="text" id="kwstart" size="5" placeholder="07:00">
    到 <input type="text" id="kwend" size="5" placeholder="23:00">
    <button onclick="applyKeepwarm()">应用</button>
    <span class="muted" id="kwmsg"></span></div>
  <div class="muted">例：16:00 限额重置，16:01 自动续上新窗口；时段外（睡觉时）不动作，交给每日预热。</div>
</div>

<div class="card"><h2>最近对话 — 每一项是一个对话（按项目分组），点击选中后在下方队列里"续写"它</h2>
  <div class="row"><input type="text" id="sfilter" size="24"
    placeholder="搜索标题 / 目录…" oninput="renderSessions()">
    <label><input type="checkbox" id="hidetrivial" checked
      onchange="renderSessions()"> 隐藏琐碎对话</label></div>
  <ul id="sessions"><li class="muted">加载中…</li></ul>
</div>

<div class="card"><h2>睡前任务队列</h2>
  <div id="target" style="display:none">
    <span>↻ 续写会话：<b id="tname"></b> <span class="muted" id="tdir"></span></span>
    <button class="gray small" onclick="clearTarget()">改为新任务</button>
  </div>
  <textarea id="prompt" placeholder="把任务描述写在这里。选中上面的会话 = 续写该会话；不选 = 在下面目录里开新任务"></textarea>
  <div class="row">目录 <input type="text" id="cwd" size="34">
    <label><input type="checkbox" id="mkdir"> 不存在则创建（新项目）</label></div>
  <div class="row"><button onclick="addJob()">加入队列</button>
    <span class="muted" id="addmsg"></span></div>
  <ul id="queue"></ul>
</div>

<div class="card"><h2>自动驾驶 — 限额一解除：续跑被打断的会话 + 执行队列</h2>
  <div class="row"><button id="wbtn" onclick="act('/api/watch/toggle').then(refresh)">启动 watch</button>
  <span id="wstate" class="muted"></span></div>
</div>

<div class="card"><h2>执行历史</h2>
  <ul id="history"><li class="muted">暂无</li></ul>
  <pre id="logview" style="display:none"></pre>
</div>

<div class="card"><h2>每日预热 — 睡醒前提前激活 5 小时窗口</h2>
  <div class="row">每天 <input type="text" id="wtime" size="5" placeholder="07:00">
    <button onclick="applyWarmup()">应用</button>
    <button class="red" onclick="act('/api/warmup/remove').then(refresh)">移除</button>
    <button class="gray" onclick="suggest()">学习我的作息</button></div>
  <div class="muted" id="wstatus"></div>
</div>

<div class="card"><h2>Telegram — 手机收通知 + 回消息遥控（/status /queue /resume，发任意文字=排任务）</h2>
  <div class="row">Bot Token <input type="text" id="tgtoken" size="34"
    placeholder="123456:ABC-...（@BotFather 创建）"></div>
  <div class="row">Chat ID <input type="text" id="tgchat" size="14"
    placeholder="如 5021..."> 默认目录 <input type="text" id="tgcwd" size="22"></div>
  <div class="row"><button onclick="applyTelegram()">保存并测试</button>
    <span class="muted" id="tgmsg"></span></div>
</div>

<div class="card"><h2>系统</h2>
  <div class="row"><label><input type="checkbox" id="autostart"
    onchange="post('/api/autostart',{enabled:this.checked})"> 开机自启托盘</label></div>
  <div class="muted">数据目录: <span id="datadir"></span></div>
</div>

<script>
const $ = id => document.getElementById(id);
const esc = s => (s||'').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const color = u => u==null?'#5a6472':u>=90?'#cc3333':u>=70?'#e07020':u>=50?'#d8a200':'#2e9e4f';
let SESSIONS = [], TARGET = null;
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
function setTarget(s) {
  TARGET = s;
  $('target').style.display = 'flex';
  $('tname').textContent = s.title.slice(0, 40);
  $('tdir').textContent = s.cwd_name;
  $('cwd').value = s.cwd;
  renderSessions();
  $('prompt').focus();
}
function clearTarget() { TARGET = null; $('target').style.display='none'; renderSessions(); }
function renderSessions() {
  const q = $('sfilter').value.toLowerCase();
  const hideTrivial = $('hidetrivial').checked;
  const items = SESSIONS.filter(s =>
    (!hideTrivial || !s.trivial) &&
    (!q || s.title.toLowerCase().includes(q) || s.cwd.toLowerCase().includes(q)));
  const groups = {};
  for (const s of items) (groups[s.cwd_name] = groups[s.cwd_name] || []).push(s);
  $('sessions').innerHTML = Object.entries(groups).map(([dir, ss]) => `
    <li style="background:none;border:none;padding:8px 0 0;font-size:12px">
      <b style="color:#9ecbff">📁 ${esc(dir)}</b>
      <span class="muted">${ss.length} 个对话</span></li>` +
    ss.map(s => `
    <li class="sess${TARGET && TARGET.session_id===s.session_id ? ' sel':''}"
        onclick='setTarget(${JSON.stringify(s).replace(/'/g,"&#39;")})'>
      <div class="grow"><div class="t">${esc(s.title)}</div>
        <div class="muted t">${s.last_local}</div></div>
      ${s.interrupted ? '<span class="badge cut">被限额打断</span>' : ''}
    </li>`).join('')).join('') || '<li class="muted">近 7 天无对话</li>';
}
function applyKeepwarm() {
  post('/api/keepwarm', {enabled: $('kwon').checked,
    start: $('kwstart').value.trim() || '07:00',
    end: $('kwend').value.trim() || '23:00'})
  .then(r => { $('kwmsg').textContent = r.message; });
}
function applyTelegram() {
  $('tgmsg').textContent = '保存中…';
  post('/api/telegram', {token: $('tgtoken').value, chat: $('tgchat').value,
    default_cwd: $('tgcwd').value})
  .then(r => { $('tgmsg').textContent = r.message; });
}
async function loadSessions() {
  SESSIONS = await (await fetch('/api/sessions')).json();
  renderSessions();
}
async function refresh() {
  const s = await (await fetch('/api/state')).json();
  if (s.usage) {
    fmtWin(s.usage.five_hour,'u5','b5','r5');
    fmtWin(s.usage.seven_day,'u7','b7','r7');
    $('src').textContent = `更新 ${s.usage.fetched_local} · 来源 ${s.usage.source}`;
  } else { $('src').textContent = '额度获取失败（token 过期？任何 claude 命令可刷新）'; }
  if (s.weekly) {
    const w = s.weekly;
    $('weekly').textContent = !w.reliable
      ? `周预算：本周窗口刚开始，数据积累中（粗估到重置时 ~${w.projected}%）`
      : w.exhaust_local
        ? `⚠ 周预算：按当前烧速，周额度约在 ${w.exhaust_local} 用尽（早于重置）`
        : `周预算：按当前烧速，到重置时约用 ${w.projected}%，安全`;
    $('weekly').style.color = w.reliable && w.exhaust_local ? '#e0a020' : '';
  } else { $('weekly').textContent = ''; }
  if (!$('kwstart').matches(':focus') && !$('kwend').matches(':focus')) {
    $('kwon').checked = s.keepwarm.enabled;
    $('kwstart').value = s.keepwarm.start;
    $('kwend').value = s.keepwarm.end;
  }
  if (!$('tgcwd').matches(':focus')) $('tgcwd').value = s.telegram.default_cwd;
  if (s.telegram.configured && !$('tgtoken').value)
    $('tgtoken').placeholder = '已配置（留空保持不变需重新输入才会覆盖）';
  if (!$('wtime').matches(':focus')) $('wtime').value = s.warmup_time;
  $('wstatus').textContent = s.schedule;
  $('queue').innerHTML = s.queue.map(j =>
    `<li><div class="grow"><div class="t" title="${esc(j.prompt)}">${esc(j.prompt)}</div>
     <div class="muted t">${j.session_short ? '↻ 续写 '+j.session_short : '新会话'} · ${esc(j.cwd_name)}</div></div>
     <button class="red small" onclick="post('/api/queue/remove',{id:'${j.id}'}).then(refresh)">删</button></li>`).join('')
    || '<li class="muted">队列为空</li>';
  $('wbtn').textContent = s.watch.running ? '停止 watch' : '启动 watch';
  $('wstate').textContent = s.watch.running ? `运行中 (PID ${s.watch.pid})` : '未运行';
  $('wstate').className = s.watch.running ? 'ok' : 'muted';
  $('autostart').checked = s.autostart;
  $('datadir').textContent = s.data_dir;
  if (!$('cwd').value) $('cwd').value = s.home;
  loadHistory();
}
async function loadHistory() {
  const h = await (await fetch('/api/history')).json();
  $('history').innerHTML = h.map(it => `
    <li><div class="grow"><div class="t" title="${esc(it.prompt)}">${esc(it.prompt)}</div>
      <div class="muted">${it.when}</div></div>
      <span class="badge ${it.status==='done'?'ok':'fail'}">${it.status==='done'?'完成':'失败'}</span>
      <button class="gray small" onclick="showLog('${it.id}','${it.status}')">日志</button></li>`).join('')
    || '<li class="muted">暂无</li>';
}
async function showLog(id, status) {
  const r = await (await fetch(`/api/joblog?id=${id}&status=${status}`)).json();
  $('logview').style.display = 'block';
  $('logview').textContent = r.text;
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
  const r = await post('/api/queue/add', {
    prompt: p, cwd: $('cwd').value.trim(),
    session_id: TARGET ? TARGET.session_id : '',
    create_dir: $('mkdir').checked,
  });
  $('addmsg').textContent = r.ok ? '已加入' : (r.message || '失败');
  if (r.ok) { $('prompt').value = ''; clearTarget(); refresh(); }
}
refresh(); loadSessions();
setInterval(refresh, 30000); setInterval(loadSessions, 120000);
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
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path == "/api/state":
            self._send_json(self.app.state())
        elif url.path == "/api/sessions":
            self._send_json(self.app.sessions())
        elif url.path == "/api/history":
            self._send_json(self.app.history())
        elif url.path == "/api/joblog":
            q = parse_qs(url.query)
            self._send_json(self.app.job_log(
                q.get("id", [""])[0], q.get("status", ["done"])[0]))
        elif url.path == "/api/warmup/suggest":
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
                body.get("prompt", ""), body.get("cwd", ""),
                session_id=body.get("session_id", ""),
                create_dir=bool(body.get("create_dir"))),
            "/api/queue/remove": lambda: self.app.remove_job(
                body.get("id", "")),
            "/api/watch/toggle": self.app.toggle_watch,
            "/api/keepwarm": lambda: self.app.set_keepwarm(
                bool(body.get("enabled")), body.get("start", "07:00"),
                body.get("end", "23:00")),
            "/api/telegram": lambda: self.app.set_telegram(
                body.get("token", ""), body.get("chat", ""),
                body.get("default_cwd", "")),
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
