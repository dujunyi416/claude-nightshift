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
<title>Sleep Well</title>
<style>
  :root {
    color-scheme: dark;
    --bg:#0a0a0c; --card:rgba(28,28,30,.66); --stroke:rgba(255,255,255,.09);
    --text:#f5f5f7; --text2:#a1a1a6; --text3:#6e6e73;
    --accent:#0a84ff; --accent2:#409cff; --green:#30d158; --red:#ff453a;
    --field:rgba(118,118,128,.16); --radius:18px;
    --font:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text",
           "Segoe UI",system-ui,"PingFang SC","Microsoft YaHei",sans-serif;
  }
  * { box-sizing:border-box; }
  body { font-family:var(--font); color:var(--text); margin:0;
    padding:40px 20px 64px; -webkit-font-smoothing:antialiased;
    letter-spacing:.01em; line-height:1.5;
    background:
      radial-gradient(1100px 600px at 20% -10%, rgba(10,132,255,.16), transparent 60%),
      radial-gradient(900px 600px at 95% 0%, rgba(120,80,255,.12), transparent 55%),
      var(--bg);
    background-attachment:fixed; }
  .wrap { max-width:1040px; margin:0 auto; }
  h1 { font-size:30px; font-weight:600; letter-spacing:-.02em; margin:0 0 22px;
       display:flex; align-items:baseline; gap:12px; }
  h1 small { color:var(--text3); font-weight:400; font-size:12px;
             letter-spacing:0; }
  .tagline { color:var(--text2); font-size:14px; margin:-14px 0 22px;
             letter-spacing:.01em; }
  .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
          gap:16px; align-items:start; grid-auto-flow:row dense; }
  .span2 { grid-column:1 / -1; }
  @media (max-width:760px){ .grid{ grid-template-columns:1fr; }
    body{ padding:24px 14px 48px; } h1{ font-size:24px; } }
  .card { background:var(--card); border:1px solid var(--stroke);
    border-radius:var(--radius); padding:20px 22px;
    backdrop-filter:blur(24px) saturate(160%);
    -webkit-backdrop-filter:blur(24px) saturate(160%);
    box-shadow:0 1px 0 rgba(255,255,255,.05) inset, 0 12px 34px rgba(0,0,0,.34);
    transition:border-color .25s, transform .25s; }
  .card:hover { border-color:rgba(255,255,255,.14); }
  .card h2 { font-size:13px; font-weight:600; margin:0 0 14px;
    color:var(--text2); letter-spacing:.02em; text-transform:none; }
  .bar { background:rgba(255,255,255,.08); border-radius:99px; height:10px;
         overflow:hidden; margin:6px 0 14px; }
  .bar div { height:100%; border-radius:99px;
             transition:width .6s cubic-bezier(.4,0,.2,1); }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin:8px 0; }
  .muted { color:var(--text2); font-size:12px; }
  input[type=text], textarea { background:var(--field); color:var(--text);
    border:1px solid transparent; border-radius:10px; padding:8px 11px;
    font-size:13px; font-family:var(--font); transition:border-color .2s,
    background .2s, box-shadow .2s; outline:none; }
  input[type=text]:focus, textarea:focus { border-color:var(--accent);
    background:rgba(118,118,128,.22);
    box-shadow:0 0 0 4px rgba(10,132,255,.18); }
  textarea { width:100%; min-height:84px; resize:vertical; line-height:1.5; }
  button { background:linear-gradient(180deg,var(--accent2),var(--accent));
    color:#fff; border:0; border-radius:10px; padding:8px 15px; cursor:pointer;
    font-size:13px; font-weight:500; font-family:var(--font);
    transition:filter .15s, transform .08s, box-shadow .2s;
    box-shadow:0 1px 2px rgba(0,0,0,.25); }
  button.gray { background:var(--field); color:var(--text);
    box-shadow:none; border:1px solid var(--stroke); }
  button.red { background:linear-gradient(180deg,#ff6a60,var(--red)); }
  button.small { padding:4px 9px; font-size:12px; border-radius:8px; }
  button:hover { filter:brightness(1.08); }
  button:active { transform:scale(.97); }
  ul { list-style:none; padding:0; margin:10px 0 0; }
  li { background:rgba(255,255,255,.035); border:1px solid var(--stroke);
       border-radius:12px; padding:9px 12px; margin:7px 0; font-size:12px;
       display:flex; justify-content:space-between; gap:9px; align-items:center;
       transition:border-color .2s, background .2s; }
  li .grow { flex:1; min-width:0; overflow:hidden; }
  li .t { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  li.sel { border-color:var(--accent); background:rgba(10,132,255,.12); }
  li.sess { cursor:pointer; }
  li.sess:hover { border-color:rgba(255,255,255,.18);
                  background:rgba(255,255,255,.06); }
  .badge { font-size:11px; padding:2px 8px; border-radius:99px; white-space:nowrap;
           font-weight:500; }
  .badge.cut { background:rgba(255,69,58,.16); color:#ff8a80; }
  .badge.res { background:rgba(10,132,255,.16); color:var(--accent2); }
  .badge.ok { background:rgba(48,209,88,.16); color:#5be07a; }
  .badge.fail { background:rgba(255,69,58,.16); color:#ff8a80; }
  .ok { color:var(--green); }
  label { font-size:13px; display:inline-flex; align-items:center; gap:6px; }
  input[type=checkbox] { accent-color:var(--accent); width:15px; height:15px; }
  pre { background:rgba(0,0,0,.35); border:1px solid var(--stroke);
        border-radius:12px; padding:12px; font-size:11px; white-space:pre-wrap;
        max-height:260px; overflow:auto; line-height:1.45;
        font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace; }
  #target { background:rgba(10,132,255,.1); border:1px solid var(--accent);
            border-radius:12px; padding:8px 12px; font-size:12px;
            margin-bottom:8px; display:flex; justify-content:space-between;
            align-items:center; }
  li.qitem.paused { opacity:.5; }
  li.qitem.dragover { border-color:var(--accent); background:rgba(10,132,255,.12); }
  .drag { cursor:grab; color:var(--text3); padding:0 4px; user-select:none;
          font-size:15px; }
  #edithint { display:none; color:#ffd60a; }
  details.hgroup { margin:6px 0; }
  details.hgroup summary { list-style:none; cursor:pointer; padding:5px 8px;
    border-radius:9px; font-size:12px; color:var(--text2); user-select:none;
    display:flex; align-items:center; gap:6px; transition:background .15s; }
  details.hgroup summary::-webkit-details-marker { display:none; }
  details.hgroup summary:hover { background:rgba(255,255,255,.06); }
  details.hgroup summary .arrow { font-size:10px; transition:transform .2s;
    display:inline-block; color:var(--text3); }
  details.hgroup[open] summary .arrow { transform:rotate(90deg); }
  details.hgroup summary .dlabel { font-weight:600; color:var(--text); }
  details.hgroup summary .dcnt { color:var(--text3); }
  details.hgroup ul { margin:2px 0 4px 14px; padding:0; }
  ::-webkit-scrollbar { width:10px; height:10px; }
  ::-webkit-scrollbar-thumb { background:rgba(255,255,255,.14);
    border-radius:99px; border:2px solid transparent; background-clip:padding-box; }
  ::-webkit-scrollbar-thumb:hover { background:rgba(255,255,255,.24);
    background-clip:padding-box; }
</style></head><body>
<div class="wrap">
<h1>🌙 Sleep Well <small id="src"></small></h1>
<div class="tagline">把没做完的事交出去，安心睡 — 睡前布置任务，就像吞下一粒褪黑素。</div>
<div class="grid">

<div class="card span2"><h2>额度</h2>
  <div>5 小时窗口 <b id="u5">…</b> <span class="muted" id="r5"></span></div>
  <div class="bar"><div id="b5" style="width:0%;background:#2e9e4f"></div></div>
  <div>7 天窗口 <b id="u7">…</b> <span class="muted" id="r7"></span></div>
  <div class="bar"><div id="b7" style="width:0%;background:#2e9e4f"></div></div>
  <div class="muted" id="weekly"></div>
  <div class="row"><button class="gray" onclick="act('/api/refresh').then(refresh)">立即刷新</button>
  <button class="gray" onclick="act('/api/warmup_now')">立即预热窗口</button></div>
</div>

<div class="card span2"><h2>后台运行监控 — 队列在后台跑，这里看现在跑到哪</h2>
  <div id="runbox" class="muted">加载中…</div>
  <pre id="runtail" style="display:none"></pre>
  <div class="muted">运行器进度 + Claude 实时步骤（--output-format stream-json）。
    任务开始和完成也会推到 Telegram。</div>
</div>

<div class="card"><h2>白天保温 — 醒着的时段，窗口一空闲就自动激活</h2>
  <div class="row"><label><input type="checkbox" id="kwon"> 开启</label>
    从 <input type="text" id="kwstart" size="5" placeholder="07:00">
    到 <input type="text" id="kwend" size="5" placeholder="23:00">
    <button onclick="applyKeepwarm()">应用</button>
    <span class="muted" id="kwmsg"></span></div>
  <div class="muted">例：16:00 限额重置，16:01 自动续上新窗口；时段外（睡觉时）不动作，交给每日预热。</div>
</div>

<div class="card span2"><h2>最近对话 — 每一项是一个对话（按项目分组），点击选中后在下方队列里"续写"它</h2>
  <div class="row"><input type="text" id="sfilter" size="24"
    placeholder="搜索标题 / 目录…" oninput="renderSessions()">
    <label><input type="checkbox" id="hidetrivial" checked
      onchange="renderSessions()"> 隐藏琐碎对话</label></div>
  <ul id="sessions"><li class="muted">加载中…</li></ul>
</div>

<div class="card span2"><h2>睡前任务队列</h2>
  <div id="target" style="display:none">
    <span>↻ 续写会话：<b id="tname"></b> <span class="muted" id="tdir"></span></span>
    <button class="gray small" onclick="clearTarget()">改为新任务</button>
  </div>
  <textarea id="prompt" placeholder="把任务描述写在这里。选中上面的会话 = 续写该会话；不选 = 在下面目录里开新任务"></textarea>
  <div class="row">目录 <input type="text" id="cwd" size="34">
    <label><input type="checkbox" id="mkdir"> 不存在则创建（新项目）</label></div>
  <div class="row">附加目录 <input type="text" id="adddirs" size="52"
    placeholder="可选 — 逗号分隔，允许 Claude 跨目录读写（--add-dir）">
  </div>
  <div class="row"><button id="addbtn" onclick="addJob()">加入队列</button>
    <button class="gray small" id="canceledit" style="display:none"
      onclick="cancelEdit()">取消编辑</button>
    <span class="muted" id="edithint">编辑中…保存后覆盖原任务</span>
    <span class="muted" id="addmsg"></span></div>
  <div class="muted">拖动 ⠿ 调整顺序；↑ 置顶、⏸ 暂停（跳过但不删）、改 = 载入编辑。</div>
  <ul id="queue"></ul>
</div>

<div class="card"><h2>自动驾驶 — 限额一解除：续跑被打断的会话 + 执行队列</h2>
  <div class="row"><button id="wbtn" onclick="act('/api/watch/toggle').then(refresh)">启动 watch</button>
  <span id="wstate" class="muted"></span></div>
</div>

<div class="card span2"><h2>执行历史</h2>
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

<div class="card"><h2>Telegram 手机遥控</h2>
  <!-- not configured: minimal setup (auto-detects chat id) -->
  <div id="tgsetup">
    <div class="muted">① 在手机上给机器人发一条任意消息 ② 把 Bot Token 粘进来点连接，
      chat 会自动识别（无需手填）。</div>
    <div class="row">Bot Token <input type="text" id="tgtoken" size="38"
      placeholder="8917...:AAH...（@BotFather 创建）"></div>
    <div class="row"><button onclick="applyTelegram()">连接</button>
      <span class="muted" id="tgmsg"></span></div>
  </div>
  <!-- configured: bot functions, no chat-log clutter -->
  <div id="tgfuncs" style="display:none">
    <div class="row"><span class="ok" id="tgstatus">已连接</span>
      <button class="gray small" onclick="reconfigTg()">重新配置</button></div>
    <div class="row"><button class="gray" onclick="pushTg('status')">把额度推到手机</button>
      <button class="gray" onclick="pushTg('queue')">把队列推到手机</button>
      <span class="muted" id="tgmsg2"></span></div>
    <div class="row">睡前默认目录 <input type="text" id="tgcwd" size="34"
      placeholder="如 D:\\btc_quant；留空=主目录">
      <button class="small" onclick="saveTgCwd()">保存</button>
      <span class="muted" id="tgcwdmsg"></span></div>
    <div class="muted">手机上：直接发一句话 = 在上面这个目录排一个任务；
      也可发 <b>/status</b> 查额度、<b>/queue</b> 看队列、
      <b>/resume</b> 续跑被打断的会话、<b>/warmup</b> 立即预热。</div>
  </div>
</div>

<div class="card"><h2>系统</h2>
  <div class="row"><label><input type="checkbox" id="autostart"
    onchange="post('/api/autostart',{enabled:this.checked})"> 开机自启托盘</label></div>
  <div class="row"><button onclick="makeShortcut()">在桌面创建启动图标</button>
    <span class="muted" id="shortcutmsg"></span></div>
  <div class="muted">进程被关掉后，双击桌面图标（或在开始菜单搜索 “Sleep Well”）
    即可重新启动；命令行也可运行 <b>nightshift tray</b>。</div>
  <div class="muted">数据目录: <span id="datadir"></span></div>
</div>
</div><!-- .grid -->
</div><!-- .wrap -->

<script>
const $ = id => document.getElementById(id);
const esc = s => (s||'').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const color = u => u==null?'#6e6e73':u>=90?'#ff453a':u>=70?'#ff9f0a':u>=50?'#ffd60a':'#30d158';
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
    ss.map(s => {
      const j = JSON.stringify(s).replace(/'/g,"&#39;");
      const badge = s.interrupted
        ? (s.reason==='limit'
            ? '<span class="badge cut">限额打断</span>'
            : '<span class="badge cut">中断(动作未完成)</span>')
        : '';
      const resumeBtn = s.interrupted
        ? `<button class="gray small" onclick='event.stopPropagation();resumeNow(${j})'>续跑</button>`
        : '';
      return `
    <li class="sess${TARGET && TARGET.session_id===s.session_id ? ' sel':''}"
        onclick='setTarget(${j})'>
      <div class="grow"><div class="t">${esc(s.title)}</div>
        <div class="muted t">${s.last_local}</div></div>
      ${badge}${resumeBtn}
    </li>`;}).join('')).join('') || '<li class="muted">近 7 天无对话</li>';
}
async function resumeNow(s) {
  const extra = prompt(`续跑「${s.title.slice(0,30)}」\n\n可选：补充一句指示（留空=用默认"继续完成剩余工作"）：`, '');
  if (extra === null) return;  // cancelled
  const r = await post('/api/resume_now', {session_id: s.session_id, prompt: extra});
  alert(r.message);
}
function applyKeepwarm() {
  post('/api/keepwarm', {enabled: $('kwon').checked,
    start: $('kwstart').value.trim() || '07:00',
    end: $('kwend').value.trim() || '23:00'})
  .then(r => { $('kwmsg').textContent = r.message; });
}
let TG_RECONFIG = false;
function applyTelegram() {
  $('tgmsg').textContent = '连接中…';
  post('/api/telegram', {token: $('tgtoken').value, chat: '', default_cwd: ''})
  .then(r => { $('tgmsg').textContent = r.message;
    if (r.ok) { TG_RECONFIG = false; $('tgtoken').value=''; refresh(); } });
}
function reconfigTg() { TG_RECONFIG = true; refresh(); }
function pushTg(what) {
  $('tgmsg2').textContent = '推送中…';
  post('/api/telegram/push', {what}).then(r => $('tgmsg2').textContent = r.message);
}
function saveTgCwd() {
  post('/api/telegram', {token:'', chat:'', default_cwd: $('tgcwd').value})
  .then(r => $('tgcwdmsg').textContent = '已保存');
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
  const tg = s.telegram;
  $('tgsetup').style.display = (tg.configured && !TG_RECONFIG) ? 'none' : 'block';
  $('tgfuncs').style.display = (tg.configured && !TG_RECONFIG) ? 'block' : 'none';
  if (tg.configured) {
    $('tgstatus').textContent = `已连接 @${tg.bot_username || '?'}（chat ${tg.chat_id}）`;
    if (!$('tgcwd').matches(':focus')) $('tgcwd').value = tg.default_cwd;
  }
  if (!$('wtime').matches(':focus')) $('wtime').value = s.warmup_time;
  $('wstatus').textContent = s.schedule;
  $('queue').innerHTML = s.queue.map((j, i) =>
    `<li class="qitem${j.paused?' paused':''}" data-id="${j.id}" draggable="true"
      ondragstart="qDragStart(event)" ondragover="qDragOver(event)"
      ondragleave="qDragLeave(event)" ondrop="qDrop(event)" ondragend="qDragEnd(event)">
     <span class="drag" title="拖动排序">⠿</span>
     <div class="grow"><div class="t" title="${esc(j.prompt)}">${esc(j.prompt)}</div>
     <div class="muted t">${i===0&&!j.paused?'▶ 下一个 · ':''}${j.session_short ? '↻ 续写 '+j.session_short : '新会话'} · ${esc(j.cwd_name)}${j.add_dirs&&j.add_dirs.length?' +'+j.add_dirs.map(d=>d.split(/[\\/]/).pop()).join(','):''}${j.paused?' · ⏸ 已暂停':''}</div></div>
     <button class="gray small" title="置顶" onclick="post('/api/queue/pin',{id:'${j.id}'}).then(refresh)">↑</button>
     <button class="gray small" title="${j.paused?'启用':'暂停'}" onclick="post('/api/queue/pause',{id:'${j.id}',paused:${!j.paused}}).then(refresh)">${j.paused?'▶':'⏸'}</button>
     <button class="gray small" onclick="editJob('${j.id}')">改</button>
     <button class="red small" onclick="post('/api/queue/remove',{id:'${j.id}'}).then(refresh)">删</button></li>`).join('')
    || '<li class="muted">队列为空</li>';
  const rn = s.running;
  if (rn) {
    $('runbox').innerHTML = `🟢 <b>跑步中</b> · 已 ${rn.elapsed_min} 分钟 · `
      + `模型 ${esc(rn.model)} · ${esc(rn.cwd_name)}<br>`
      + `<span class="muted">${esc(rn.prompt)}</span>`;
  } else {
    $('runbox').textContent = '空闲 — 当前没有任务在跑';
  }
  if (s.runner_tail) {
    $('runtail').style.display = 'block'; $('runtail').textContent = s.runner_tail;
  } else { $('runtail').style.display = 'none'; }
  $('wbtn').textContent = s.watch.running ? '停止 watch' : '启动 watch';
  $('wstate').textContent = s.watch.running ? `运行中 (PID ${s.watch.pid})` : '未运行';
  $('wstate').className = s.watch.running ? 'ok' : 'muted';
  $('autostart').checked = s.autostart;
  $('datadir').textContent = s.data_dir;
  if (!$('cwd').value) $('cwd').value = s.home;
  loadHistory();
}
function _histDateLabel(d) {
  const now = new Date();
  const pad = n => String(n).padStart(2,'0');
  const fmt = x => `${pad(x.getMonth()+1)}-${pad(x.getDate())}`;
  const today = fmt(now);
  const yd = new Date(now); yd.setDate(yd.getDate()-1);
  const yesterday = fmt(yd);
  if (d === today) return '今天';
  if (d === yesterday) return '昨天';
  return d;
}
async function loadHistory() {
  const h = await (await fetch('/api/history')).json();
  if (!h.length) { $('history').innerHTML = '<li class="muted">暂无</li>'; return; }
  const groups = {};
  const order = [];
  for (const it of h) {
    const d = it.date || it.when.slice(0,5);
    if (!groups[d]) { groups[d] = []; order.push(d); }
    groups[d].push(it);
  }
  const now = new Date();
  const pad = n => String(n).padStart(2,'0');
  const todayStr = `${pad(now.getMonth()+1)}-${pad(now.getDate())}`;
  $('history').innerHTML = order.map(d => {
    const isToday = d === todayStr;
    const label = _histDateLabel(d);
    const rows = groups[d].map(it => `
      <li><div class="grow"><div class="t" title="${esc(it.prompt)}">${esc(it.prompt)}</div>
        <div class="muted">${it.when.slice(6)}</div></div>
        <span class="badge ${it.status==='done'?'ok':'fail'}">${it.status==='done'?'完成':'失败'}</span>
        <button class="gray small" onclick="showLog('${it.id}','${it.status}','${d}')">日志</button></li>`
    ).join('');
    return `<details class="hgroup" ${isToday?'open':''} id="hg-${d}">
      <summary><span class="arrow">▶</span>
        <span class="dlabel">${label}</span>
        <span class="dcnt">${groups[d].length} 条</span></summary>
      <ul>${rows}</ul></details>`;
  }).join('');
}
async function showLog(id, status, date) {
  const r = await (await fetch(`/api/joblog?id=${id}&status=${status}`)).json();
  if (date) { const g = document.getElementById('hg-'+date); if (g) g.open = true; }
  $('logview').style.display = 'block';
  $('logview').textContent = r.text;
  $('logview').scrollIntoView({behavior:'smooth', block:'nearest'});
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
async function makeShortcut() {
  $('shortcutmsg').textContent = '创建中…';
  const r = await post('/api/shortcut');
  $('shortcutmsg').textContent = r.message || (r.ok ? '已创建' : '失败');
}
let EDITING = null;
async function editJob(id) {
  const r = await post('/api/job/get', {id});
  if (!r.ok) { alert(r.message || '找不到该任务'); return; }
  EDITING = id;
  clearTarget();
  $('prompt').value = r.prompt;
  $('cwd').value = r.cwd;
  $('adddirs').value = (r.add_dirs || []).join(', ');
  $('addbtn').textContent = '保存修改';
  $('edithint').style.display = 'inline';
  $('canceledit').style.display = 'inline';
  $('prompt').focus();
}
function cancelEdit() {
  EDITING = null;
  $('prompt').value = '';
  $('adddirs').value = '';
  $('addbtn').textContent = '加入队列';
  $('edithint').style.display = 'none';
  $('canceledit').style.display = 'none';
  $('addmsg').textContent = '';
}
function _parseDirs() {
  return $('adddirs').value.trim().split(',').map(s => s.trim()).filter(Boolean);
}
async function addJob() {
  const p = $('prompt').value.trim();
  if (!p) return;
  const add_dirs = _parseDirs();
  if (EDITING) {
    const r = await post('/api/job/update',
      {id: EDITING, prompt: p, cwd: $('cwd').value.trim(), add_dirs});
    $('addmsg').textContent = r.message || (r.ok ? '已保存' : '失败');
    if (r.ok) { cancelEdit(); refresh(); }
    return;
  }
  const r = await post('/api/queue/add', {
    prompt: p, cwd: $('cwd').value.trim(),
    session_id: TARGET ? TARGET.session_id : '',
    create_dir: $('mkdir').checked,
    add_dirs,
  });
  $('addmsg').textContent = r.ok ? '已加入' : (r.message || '失败');
  if (r.ok) { $('prompt').value = ''; $('adddirs').value = ''; clearTarget(); refresh(); }
}
let DRAG_ID = null;
function qDragStart(e) {
  DRAG_ID = e.currentTarget.dataset.id;
  e.dataTransfer.effectAllowed = 'move';
}
function qDragOver(e) {
  e.preventDefault();
  const li = e.currentTarget;
  if (li.dataset.id !== DRAG_ID) li.classList.add('dragover');
}
function qDragLeave(e) { e.currentTarget.classList.remove('dragover'); }
function qDrop(e) {
  e.preventDefault();
  const li = e.currentTarget;
  li.classList.remove('dragover');
  const ul = li.parentNode;
  const dragging = ul.querySelector(`[data-id="${DRAG_ID}"]`);
  if (!dragging || dragging === li) return;
  const rect = li.getBoundingClientRect();
  const after = (e.clientY - rect.top) > rect.height / 2;
  ul.insertBefore(dragging, after ? li.nextSibling : li);
}
function qDragEnd() {
  const ids = [...$('queue').querySelectorAll('[data-id]')].map(x => x.dataset.id);
  if (ids.length) post('/api/queue/reorder', {ids}).then(refresh);
}
async function monitorRefresh() {
  const s = await (await fetch('/api/running')).json();
  const rn = s.running;
  if (rn) {
    $('runbox').innerHTML = `🟢 <b>跑步中</b> · 已 ${rn.elapsed_min} 分钟 · `
      + `模型 ${esc(rn.model)} · ${esc(rn.cwd_name)}<br>`
      + `<span class="muted">${esc(rn.prompt)}</span>`;
  } else {
    $('runbox').textContent = s.watch_on
      ? '🟡 watch 运行中 · 等待额度或任务'
      : '空闲 — 当前没有任务在跑';
  }
  if (s.runner_tail) {
    $('runtail').style.display = 'block'; $('runtail').textContent = s.runner_tail;
  } else { $('runtail').style.display = 'none'; }
}
refresh(); loadSessions();
setInterval(refresh, 15000); setInterval(loadSessions, 120000);
setInterval(monitorRefresh, 3000);
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
        elif url.path == "/api/running":
            self._send_json(self.app.running_state())
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
                create_dir=bool(body.get("create_dir")),
                add_dirs=body.get("add_dirs", [])),
            "/api/queue/remove": lambda: self.app.remove_job(
                body.get("id", "")),
            "/api/job/get": lambda: self.app.get_job(body.get("id", "")),
            "/api/job/update": lambda: self.app.update_job(
                body.get("id", ""), body.get("prompt", ""),
                body.get("cwd", ""), body.get("add_dirs", [])),
            "/api/queue/reorder": lambda: self.app.reorder_jobs(
                body.get("ids", [])),
            "/api/queue/pin": lambda: self.app.pin_job(body.get("id", "")),
            "/api/queue/pause": lambda: self.app.pause_job(
                body.get("id", ""), bool(body.get("paused"))),
            "/api/watch/toggle": self.app.toggle_watch,
            "/api/resume_now": lambda: self.app.resume_now(
                body.get("session_id", ""), body.get("prompt", "")),
            "/api/keepwarm": lambda: self.app.set_keepwarm(
                bool(body.get("enabled")), body.get("start", "07:00"),
                body.get("end", "23:00")),
            "/api/telegram": lambda: self.app.set_telegram(
                body.get("token", ""), body.get("chat", ""),
                body.get("default_cwd", "")),
            "/api/telegram/push": lambda: self.app.tg_push(
                body.get("what", "status")),
            "/api/autostart": lambda: self.app.set_autostart(
                bool(body.get("enabled"))),
            "/api/shortcut": self.app.create_shortcut,
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
