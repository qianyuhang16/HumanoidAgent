"use strict";

const paths = {
  layers:'<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  workspace:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M9 9v12"/>',
  database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  tool:'<path d="m14 6 4 4 3-3a6 6 0 0 1-8 8l-6 6-4-4 6-6a6 6 0 0 1 8-8l-3 3Z"/>',
  book:'<path d="M12 6c-3-3-7-3-9-2v15c2-1 6-1 9 2 3-3 7-3 9-2V4c-2-1-6-1-9 2v15"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  'arrow-up-right':'<path d="M6 18 18 6M6 6h12v12"/>',
  sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>',
  moon:'<path d="M20 14A9 9 0 0 1 10 4a9 9 0 1 0 10 10Z"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  panel:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  eye:'<path d="M2 12c5-9 15-9 20 0-5 9-15 9-20 0Z"/><circle cx="12" cy="12" r="3"/>',
  activity:'<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  'check-circle':'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  robot:'<rect x="4" y="7" width="16" height="13" rx="3"/><path d="M12 3v4M1 11v5m22-5v5M8 12v2m8-2v2M9 17h6"/>',
  'arrow-up':'<path d="M12 20V4m-6 6 6-6 6 6"/>',
  'circle-info':'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  scan:'<path d="M3 8V4a1 1 0 0 1 1-1h4m8 0h4a1 1 0 0 1 1 1v4m0 8v4a1 1 0 0 1-1 1h-4m-8 0H4a1 1 0 0 1-1-1v-4M7 12h10M12 7v10"/>',
  compass:'<circle cx="12" cy="12" r="9"/><path d="m16 8-3 5-5 3 3-5 5-3Z"/>',
  'git-branch':'<circle cx="6" cy="4" r="2"/><circle cx="6" cy="20" r="2"/><circle cx="18" cy="6" r="2"/><path d="M6 6v12m0-3c0-5 12 0 12-7"/>',
  sliders:'<path d="M4 7h5m5 0h6M4 17h10m5 0h1"/><circle cx="11.5" cy="7" r="2.5"/><circle cx="16.5" cy="17" r="2.5"/>',
  link:'<path d="m10 14 4-4m-6 6-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m4 0 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(2 0) scale(.85 1)"/>',
  copy:'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  x:'<path d="m6 6 12 12M6 18 18 6"/>',
  pause:'<path d="M8 5v14M16 5v14"/>',
  play:'<path d="m8 4 12 8-12 8V4Z"/>',
  square:'<rect x="5" y="5" width="14" height="14" rx="2"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  chevron:'<path d="m8 10 4 4 4-4"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  file:'<path d="M14 3H5v18h14V8l-5-5Z"/><path d="M14 3v5h5M8 13h8m-8 4h5"/>',
  refresh:'<path d="M20 7A9 9 0 0 0 4 7m0 10a9 9 0 0 0 16 0M20 2v5h-5M4 22v-5h5"/>',
  help:'<circle cx="12" cy="12" r="9"/><path d="M9 8a3 3 0 0 1 6 1c0 2-3 2-3 5m0 3v.1"/>',
  download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.file}</svg>`;
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const json = value => JSON.stringify(value, null, 2);
const short = (value, size=180) => { const text = typeof value === "string" ? value : JSON.stringify(value); return text?.length > size ? text.slice(0, size) + "…" : text || ""; };
const time = timestamp => new Date(timestamp * 1000).toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false});
const date = timestamp => new Date(timestamp * 1000).toLocaleString("zh-CN", {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
const statusLabels = {draft:"草稿", ready:"就绪", running:"运行中", waiting:"等待事件", waiting_user:"等待你的回复", paused:"已暂停", stopping:"等待停止确认", cancelled:"已取消", completed:"已完成", failed:"失败", budget_exhausted:"预算已耗尽", stop_unconfirmed:"停止未确认", success:"通过", failure:"未通过", uncertain:"无法确定", observed:"已观测", recorded:"已记录"};
const domainIcons = {state:"eye", execution:"activity", verification:"check-circle", planning:"compass"};
const actionLabels = {call_tool:"调用工具", discover_tools:"发现可用能力", inspect_tool:"读取工具契约", list_skills:"查找 Skill", read_skill:"阅读 Skill", memory_search:"检索 Memory", memory_get:"读取记录", memory_write:"保存决策信息", memory_update:"更新记忆", get_resource:"读取资源引用", wait:"等待新事件", ask_user:"请求补充信息", finish:"提交任务报告", subscribe:"关注状态变化", tool_status:"查询执行状态", cancel_tool:"请求停止工具"};
const badge = status => `<span class="badge ${escapeHTML(status)}">${escapeHTML(statusLabels[status] || status)}</span>`;
const state = {status:null, sessions:[], tools:[], skills:[], view:"home", sessionId:null, snapshot:null, stream:null, domain:"", query:"", offset:0, records:[], previousRecords:[], busy:false, sessionOffset:0};
let toastTimer, searchTimer, collectionRequest = 0;

function hydrate(root=document) { root.querySelectorAll("[data-icon]").forEach(node => node.innerHTML = icon(node.dataset.icon)); }
function toast(message, error=false) { clearTimeout(toastTimer); $("toast").textContent=message; $("toast").className=`toast${error ? " error" : ""}`; $("toast").hidden=false; toastTimer=setTimeout(()=>$("toast").hidden=true, 4500); }
async function api(path, options={}) {
  const response = await fetch(`/api${path}`, {headers:{"Content-Type":"application/json"}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "输入格式不正确，请检查后重试。");
  return data;
}
async function perform(operation) {
  if (state.busy) return;
  state.busy=true;
  try { await operation(); } catch (error) { toast(error.message, true); }
  finally { state.busy=false; updateComposer(); if(state.snapshot) renderTaskActions(); }
}
function navigate(route) { if (location.hash === `#${route}`) routeChanged(); else location.hash=route; }
function empty(symbol, title, description) { return `<div class="empty-state"><span>${icon(symbol)}</span><h3>${escapeHTML(title)}</h3><p>${escapeHTML(description)}</p></div>`; }
function openDialog(title, content, eyebrow="DETAIL") { $("dialog-title").textContent=title; $("dialog-eyebrow").textContent=eyebrow; $("dialog-body").innerHTML=content; if(!$("detail-dialog").open) $("detail-dialog").showModal(); }
function renderSidebar() {
  $("session-list").innerHTML=state.sessions.length ? state.sessions.map(session=>`<button class="session-item ${session.id===state.sessionId ? "active" : ""}" data-session="${escapeHTML(session.id)}" title="${escapeHTML(session.goal)} · ${escapeHTML(statusLabels[session.status] || session.status)}"><span class="${["running","waiting"].includes(session.status)?"pulse":""}">${icon(session.status==="completed" ? "check-circle" : session.status==="draft" ? "file" : "clock")}</span><span class="session-name">${escapeHTML(session.goal)}</span></button>`).join("") : '<p class="session-empty">还没有任务。<br>从一个想完成的目标开始。</p>';
  $("sessions-more").hidden=state.sessions.length < state.sessionOffset+100;
  document.querySelectorAll("[data-nav]").forEach(link=>link.classList.toggle("active", link.dataset.nav===(state.view==="task"?"home":state.view)));
}
async function refreshSessions() {
  const data=await api(`/sessions?limit=100&offset=${state.sessionOffset}`);
  if(state.sessionOffset===0) state.sessions=data.sessions;
  else { const found=new Map(state.sessions.map(s=>[s.id,s])); data.sessions.forEach(s=>found.set(s.id,s)); state.sessions=[...found.values()]; }
  renderSidebar();
}
function updateComposer() {
  const runnable=state.status?.connected && $("robot-select").value;
  $("submit-label").textContent=runnable?"开始任务":"保存任务";
  $("submit-task").disabled=state.busy || !$("task-input").value.trim() || !state.status;
}
function renderEnvironment() {
  const info=state.status;
  if(!info) return;
  $("server-dot").className=`status-dot${info.connected?"":" offline"}`;
  $("server-label").textContent=info.connected?"Runtime 已连接":"工作台已就绪";
  $("footer-status").textContent=info.connected?"Runtime 已连接":"本地草稿模式";
  $("footer-dot").className=`status-dot${info.connected?"":" offline"}`;
  $("tool-count").textContent=info.tools; $("skill-count").textContent=info.skills;
  for(const domain of ["state","execution","verification"]) $(`${domain}-count`).textContent=state.tools.filter(t=>t.domain===domain).length;
  $("robot-select").innerHTML=info.robots.length ? info.robots.map(r=>`<option value="${escapeHTML(r.id)}">${escapeHTML(r.label)}</option>`).join("") : '<option value="">未选择机器人</option>';
  $("connection-note").innerHTML=icon("circle-info")+`<span>${info.connected ? (info.robots.length ? "任务交给已连接的 Runtime 执行，你可以随时暂停决策或请求停止。" : "Runtime 已连接。配置机器人后可执行；现在可先保存任务。") : "Planning 尚未连接。可以先保存任务，接入后继续执行。"}</span>`;
  renderInspector(); updateComposer();
}
function renderInspector() {
  const info=state.status;
  if(!info) return;
  const snapshot=state.view==="task"?state.snapshot:null, session=snapshot?.session;
  const robot=info.robots.find(r=>r.id===session?.robot_id);
  const verification=[...(snapshot?.records || [])].reverse().find(r=>r.event_type==="verification")?.payload?.result?.verification;
  let html=`<section class="inspector-section"><h3>运行环境</h3><div class="environment-card"><div><span class="status-dot${info.connected?"":" offline"}"></span>${info.connected?"Runtime 已连接":"等待连接 Planning"}</div><small>${info.connected?escapeHTML(info.planner):"任务可以先保存为草稿"}</small></div><div class="inspector-row"><span>机器人</span><span>${escapeHTML(robot?.label || session?.robot_id || "未选择")}</span></div><div class="inspector-row"><span>可用工具</span><span>${info.tools} 个</span></div><div class="inspector-row"><span>技能文档</span><span>${info.skills} 份</span></div></section>`;
  html+=`<section class="inspector-section"><h3>控制资源</h3>${snapshot && Object.keys(snapshot.occupied).length ? Object.entries(snapshot.occupied).map(([resource,call])=>`<div class="inspector-row"><span>${escapeHTML(resource)}</span><span class="pill" title="${escapeHTML(call)}">占用中</span></div>`).join("") : `<div class="inspector-placeholder"><span>${icon("robot")}</span>${!info.connected?"连接 Runtime 后<br>查看实时资源占用":session?"当前没有已占用的控制资源":"选择任务后查看<br>机器人资源与占用状态"}</div>`}</section>`;
  html+=`<section class="inspector-section"><h3>最新验证</h3>${verification ? `<div class="verification-card">${badge(verification.conclusion)}<p>${escapeHTML(verification.reason)}</p><button class="event-detail" data-record="${escapeHTML([...snapshot.records].reverse().find(r=>r.event_type==="verification").id)}">查看证据 ↗</button></div>` : `<div class="inspector-placeholder"><span>${icon("check-circle")}</span>尚无验证结果<br>动作结束后，效果由 Verification 判断</div>`}</section>`;
  const limits=info.limits;
  html+=`<section class="inspector-section"><h3>任务预算</h3>${[["决策次数",session?.decisions||0,limits?.max_decisions],["工具调用",session?.tool_calls||0,limits?.max_tool_calls]].map(([label,value,max])=>`<div class="budget-row"><div><span>${label}</span><b>${value} / ${max || "—"}</b></div><progress value="${value}" max="${max || 1}" aria-label="${label}"></progress></div>`).join("")}<p class="inspector-note">${session?`活动时间 ${Math.round(session.active_seconds)} 秒${limits?` / ${limits.max_active_seconds} 秒`:""}`:"预算将在接入 Runtime 后显示。"}</p></section>`;
  $("inspector-content").innerHTML=html;
}
function resourceCards(refs) {
  return refs?.length ? `<div class="resource-grid">${refs.map(ref=>`<button class="resource-tile" data-resource="${escapeHTML(ref.id)}">${ref.uri.startsWith("blob:")&&["image/png","image/jpeg","image/webp","image/gif"].includes(ref.media_type)?`<img src="/api/resources/${encodeURIComponent(ref.id)}/content" alt="${escapeHTML(ref.description || "观测图像")}" loading="lazy">`:""}<span>${icon("file")}${escapeHTML(ref.description || ref.media_type)}</span></button>`).join("")}</div>` : "";
}
function eventMarkup(record) {
  const payload=record.payload, type=record.event_type;
  let label=record.source, description="", card="", symbol=domainIcons[record.domain], tone=record.domain;
  if(type==="task_created") {label="你";description=payload.goal;symbol="file";}
  else if(type==="user_input") {label="你的回复";description=payload.answer;symbol="file";}
  else if(type==="decision") {label="Planning";description=actionLabels[payload.action] || payload.action;}
  else if(type==="task_finished") {label="任务报告";description=payload.message;symbol="check-circle";}
  else if(type==="session_control") {label="任务控制";description={pause:"已暂停高层决策，正在执行的动作仍由 Provider 管理。",resume:"已恢复任务。",cancel:"已请求停止任务。",reconcile:"已重新请求停止确认。"}[payload.action];}
  else if(type==="verification") {const v=payload.result?.verification;label="Verification";description=v?.reason;card=`<button class="event-card" data-record="${escapeHTML(record.id)}"><div class="event-card-title"><span>${escapeHTML(v?.subject || record.source)}</span>${badge(v?.conclusion || "uncertain")}</div><p>${escapeHTML(v?.related_call_id ? `关联动作 · ${v.related_call_id.slice(0,8)}` : "独立的效果判断")}</p></button>`;}
  else if(type==="tool_started" || type==="tool_finished" || type==="tool_progress") {
    label={tool_started:"工具调用",tool_finished:"执行结果",tool_progress:"执行进度"}[type];
    const detail=payload.error?.message || short(type==="tool_started"?payload.arguments:payload.result?.payload || payload);
    card=`<button class="event-card" data-record="${escapeHTML(record.id)}"><div class="event-card-title"><span>${escapeHTML(record.source)}</span>${badge(record.status)}</div><p>${escapeHTML(detail)}</p>${payload.duration_ms!=null?`<small>${(payload.duration_ms/1000).toFixed(2)} s · ${escapeHTML(record.call_id?.slice(0,8))}</small>`:""}</button>`;
    if(payload.error) tone="error";
  } else if(type==="stop_unconfirmed" || type==="hook_error" || record.status==="failed") {label="需要关注";description=payload.error?.message || payload.error || payload.message || short(payload);tone="error";symbol="circle-info";}
  else {label={observation:"State 观测",stop_requested:"请求停止",note:"Planning 笔记"}[type] || record.source;description=short(payload);}
  return `<article class="timeline-entry"><div class="event-symbol ${tone}">${icon(symbol)}</div><div class="event-content"><div class="event-topline"><strong>${escapeHTML(label)}</strong><time>${time(record.timestamp)}</time></div>${description?`<div class="event-description">${escapeHTML(description)}</div>`:""}${card}${resourceCards(record.resource_refs)}<button class="event-detail" data-record="${escapeHTML(record.id)}">${escapeHTML(type)} · 查看详情</button></div></article>`;
}
function renderTaskActions() {
  const session=state.snapshot?.session;
  if(!session) return;
  const connected=state.status?.connected, buttons=[];
  const add=(action,label,symbol,primary=false,danger=false,disabled=false)=>buttons.push(`<button class="${primary?"primary-button":"secondary-button"}${danger?" danger-button":""}" data-control="${action}" ${state.busy||disabled?"disabled":""}>${icon(symbol)}${label}</button>`);
  if(["draft","paused","ready"].includes(session.status)) {
    if(session.status==="draft"&&state.status?.robots.length) buttons.push(`<label class="robot-select-wrap">${icon("robot")}<select id="task-robot-select" aria-label="为任务选择机器人">${state.status.robots.map(r=>`<option value="${escapeHTML(r.id)}" ${r.id===session.robot_id?"selected":""}>${escapeHTML(r.label)}</option>`).join("")}</select></label>`);
    add("resume",session.status==="paused"?"继续任务":"运行任务","play",true,false,!connected);
  }
  if(["running","waiting","ready"].includes(session.status)) add("pause","暂停决策","pause",false,false,!connected);
  if(!["completed","failed","cancelled","budget_exhausted","draft"].includes(session.status)) add("cancel","停止任务","square",false,true,!connected);
  if(session.status==="stopping") add("reconcile","重新确认停止","refresh",false,false,!connected);
  $("task-actions").innerHTML=buttons.join("");
}
function renderTask() {
  const snapshot=state.snapshot, session=snapshot.session;
  $("task-title").textContent=session.goal; $("page-title").textContent=session.goal;
  $("task-status").className=`badge ${session.status}`; $("task-status").textContent=statusLabels[session.status] || session.status;
  $("task-robot").textContent=state.status?.robots.find(r=>r.id===session.robot_id)?.label || session.robot_id || "尚未选择机器人";
  $("task-date").textContent=date(session.created_at);
  const notes={draft:state.status?.connected?"任务已保存。选择机器人后可以开始运行。":"任务已保存为草稿。接入 Planning 与机器人后，可以在这里继续。",paused:"高层决策已暂停。正在执行的动作仍会继续；如需停止，请点击「停止任务」。",stopping:"停止请求尚未全部确认。控制资源继续保持占用，直到 Provider 确认物理动作停止。",budget_exhausted:"本次任务预算已耗尽。请查看执行记录，了解最后状态。"};
  const notice=snapshot.runner_error || notes[session.status] || (!state.status?.connected?"当前查看的是已保存记录。连接 Runtime 后可同步实时执行状态。":null);
  $("task-notice").hidden=!notice; $("task-notice").textContent=notice || ""; $("task-notice").className=`notice${session.status==="stopping"||snapshot.runner_error?" warning":""}`;
  renderTaskActions();
  const all=new Map([...state.previousRecords,...snapshot.records].map(record=>[record.id,record]));
  const records=[...all.values()].sort((a,b)=>a.timestamp-b.timestamp);
  const ended=new Set(records.filter(r=>["tool_finished","verification"].includes(r.event_type)).map(r=>r.call_id));
  const lastProgress=new Map(records.filter(r=>r.event_type==="tool_progress").map(r=>[r.call_id,r.id]));
  const visible=records.filter(r=>(r.event_type!=="action_result"||r.status==="failed") && (r.event_type!=="tool_progress"||(!ended.has(r.call_id)&&lastProgress.get(r.call_id)===r.id)));
  const nearBottom=$("content").scrollHeight-$("content").scrollTop-$("content").clientHeight<100;
  $("timeline").innerHTML=visible.map(eventMarkup).join("") || empty("clock","等待新的事件","执行记录会随着任务进展自动出现在这里。");
  $("older-events").hidden=!snapshot.has_more;
  $("question-card").hidden=session.status!=="waiting_user";
  $("reply-form").hidden=session.status!=="waiting_user";
  if(session.status==="waiting_user") $("question-card").innerHTML=`<h3>${icon("help")}需要你的补充</h3>${escapeHTML(session.question)}${resourceCards(session.last_result.resources || [])}`;
  renderInspector();
  if(nearBottom) $("content").scrollTop=$("content").scrollHeight;
  const index=state.sessions.findIndex(s=>s.id===session.id);
  if(index>=0) state.sessions[index]=session; else state.sessions.unshift(session);
  renderSidebar();
}
async function selectSession(id) {
  state.sessionId=id;state.previousRecords=[];state.snapshot=null;
  $("timeline").innerHTML=empty("clock","正在读取任务","读取持久化记录与当前执行状态。");
  const snapshot=await api(`/sessions/${encodeURIComponent(id)}`);
  if(state.sessionId!==id) return;
  state.snapshot=snapshot;renderTask();
  state.stream=new EventSource(`/api/sessions/${encodeURIComponent(id)}/stream`);
  state.stream.addEventListener("snapshot", event=>{if(state.sessionId===id){state.snapshot=JSON.parse(event.data);renderTask();}});
  state.stream.onopen=()=>$("stream-state").textContent="实时同步";
  state.stream.onerror=()=>$("stream-state").textContent="连接中断，重连中";
}
async function routeChanged() {
  const [route,id]=(location.hash.slice(1)||"home").split("/");
  state.stream?.close();state.stream=null;
  state.view=["memory","tools","skills"].includes(route)?route:route==="session"&&id?"task":"home";
  state.sessionId=state.view==="task"?id:null;state.domain="";state.query="";state.offset=0;
  $("sidebar").classList.remove("mobile-open");
  $("home-view").hidden=state.view!=="home";$("task-view").hidden=state.view!=="task";$("collection-view").hidden=["home","task"].includes(state.view);
  $("content").scrollTop=0;
  $("page-title").textContent={home:"工作台",task:"任务",memory:"Memory",tools:"工具",skills:"Skills"}[state.view];
  renderSidebar();renderInspector();
  if(state.view==="task") await selectSession(id);
  else if(state.view!=="home") {
    $("collection-search").value="";$("lifetime-filter").value="";
    const content={memory:["WORKSPACE MEMORY","Memory","检索观察、执行事实与验证证据。保留来源，连接上下文。"],tools:["TOOL REGISTRY","能力，从这里扩展。","查看已注册工具的接口、资源要求与使用说明。"],skills:["SKILL LIBRARY","给 Agent 的使用知识。","由 Planning 按需阅读的能力说明、适用条件与恢复建议。"]}[state.view];
    [$("collection-eyebrow").textContent,$("collection-title").textContent,$("collection-description").textContent]=content;
    $("lifetime-filter").hidden=state.view!=="memory";$("domain-filters").hidden=state.view==="skills";
    $("collection-search").placeholder=state.view==="memory"?"搜索记录、来源或内容…":"搜索名称或描述…";
    document.querySelectorAll("[data-domain]").forEach(b=>b.classList.toggle("active",b.dataset.domain===""));
    await renderCollection();
  }
}
async function renderCollection(append=false) {
  const request=++collectionRequest, view=state.view;
  let html="";
  $("load-more").hidden=true;
  if(view==="memory") {
    const params=new URLSearchParams({query:state.query,domain:state.domain,lifetime:$("lifetime-filter").value,offset:state.offset,limit:40});
    const data=await api(`/memory?${params}`);
    if(request!==collectionRequest||state.view!==view) return;
    state.records=append?[...state.records,...data.records]:data.records;
    html=state.records.map(r=>`<button class="record-row" data-record="${escapeHTML(r.id)}"><span>${icon(domainIcons[r.domain])}</span><div><header><strong>${escapeHTML(r.source)}</strong>${badge(r.status)}</header><p>${escapeHTML(short(r.payload,250))}</p><footer><span>${date(r.timestamp)}</span><span>${r.lifetime==="long"?"长期":"短期"}</span><code>${escapeHTML(r.event_type)}</code></footer></div></button>`).join("") || empty("database",state.query?"没有找到匹配记录":"Memory 还很安静",state.query?"尝试其他关键词，或调整工具域与生命周期筛选。":"创建任务后，观察、调用与验证记录将在这里汇集。");
    $("load-more").hidden=data.records.length<40;
  } else if(view==="tools") {
    const tools=state.tools.filter(t=>(!state.domain||t.domain===state.domain)&&`${t.name} ${t.description}`.toLowerCase().includes(state.query.toLowerCase()));
    html=tools.length?`<div class="catalog-grid">${tools.map(t=>`<button class="catalog-card" data-tool="${escapeHTML(t.name)}"><div class="card-top">${icon(domainIcons[t.domain])}<span class="pill">${escapeHTML(t.domain)}</span></div><h3>${escapeHTML(t.name)}</h3><p>${escapeHTML(t.description)}</p><footer><span>${t.execution_mode==="async"?"异步":"同步"}</span><span>·</span><span>${escapeHTML(t.provider)}</span></footer></button>`).join("")}</div>`:empty("tool",state.tools.length?"没有匹配的工具":"能力，等待接入",state.tools.length?"调整筛选条件，查看其他工具。":"Provider 注册后会自动出现在这里。界面不会预设机器人能力。");
  } else if(view==="skills") {
    const skills=state.skills.filter(s=>`${s.id} ${s.description}`.toLowerCase().includes(state.query.toLowerCase()));
    html=skills.length?`<div class="catalog-grid">${skills.map(s=>`<button class="catalog-card" data-skill="${escapeHTML(s.id)}"><div class="card-top">${icon("book")}<span class="pill">v${escapeHTML(s.version)}</span></div><h3>${escapeHTML(s.id)}</h3><p>${escapeHTML(s.description)}</p><footer>阅读技能文档 ${icon("arrow-up-right")}</footer></button>`).join("")}</div>`:empty("book","还没有 Skill 文档","加载能力包的 Markdown 索引后，可以在这里阅读使用规则与版本。");
  }
  $("collection-results").innerHTML=html;
}
async function showRecord(id) {
  const record=await api(`/records/${encodeURIComponent(id)}`);
  openDialog(record.source,`<div class="metadata"><dt>事件</dt><dd>${escapeHTML(record.event_type)}</dd><dt>状态</dt><dd>${badge(record.status)}</dd><dt>记录时间</dt><dd>${date(record.timestamp)}</dd><dt>调用 ID</dt><dd>${escapeHTML(record.call_id || "—")}</dd></div>${resourceCards(record.resource_refs)}<h3>记录内容</h3><pre>${escapeHTML(json(record.payload))}</pre><div class="dialog-actions"><button class="secondary-button" data-copy-record="${escapeHTML(record.id)}">${icon("copy")}复制完整记录</button></div>`,record.domain.toUpperCase());
}
function showTool(name) {
  const tool=state.tools.find(t=>t.name===name);
  openDialog(tool.name,`<p>${escapeHTML(tool.description)}</p><dl class="metadata"><dt>工具域</dt><dd>${escapeHTML(tool.domain)}</dd><dt>执行方式</dt><dd>${tool.execution_mode==="async"?"异步":"同步"} · 超时 ${tool.timeout}s</dd><dt>控制资源</dt><dd>${escapeHTML(tool.required_resources.join(" · ")||"不占用控制资源")}</dd><dt>支持的本体</dt><dd>${escapeHTML(tool.supported_robots.join(" · ")||"不限制")}</dd><dt>Provider</dt><dd>${escapeHTML(tool.provider)}</dd></dl>${tool.skill_ref?`<button class="secondary-button" data-skill="${escapeHTML(tool.skill_ref)}">${icon("book")}阅读关联 Skill</button>`:""}<h3>输入契约</h3><pre>${escapeHTML(json(tool.input_schema))}</pre><h3>输出契约</h3><pre>${escapeHTML(json(tool.output_schema))}</pre>`,"TOOL CONTRACT");
}
async function showSkill(id, offset=0) {
  const skill=await api(`/skills/${encodeURIComponent(id)}?offset=${offset}`);
  openDialog(skill.id,`<p>版本 ${escapeHTML(skill.version)} · ${offset+1}–${Math.min(offset+8000,skill.total_chars)} / ${skill.total_chars} 字符</p><pre>${escapeHTML(skill.content)}</pre><div class="dialog-actions">${offset>0?`<button class="secondary-button" data-skill="${escapeHTML(id)}" data-offset="${Math.max(0,offset-8000)}">上一页</button>`:""}${offset+8000<skill.total_chars?`<button class="secondary-button" data-skill="${escapeHTML(id)}" data-offset="${offset+8000}">下一页</button>`:""}</div>`,"SKILL DOCUMENT");
}
async function showResource(id) {
  const ref=await api(`/resources/${encodeURIComponent(id)}`), local=ref.uri.startsWith("blob:");
  const preview=local&&["image/png","image/jpeg","image/webp","image/gif"].includes(ref.media_type);
  openDialog(ref.description||"资源",`${preview?`<img class="resource-preview" src="/api/resources/${encodeURIComponent(id)}/content" alt="${escapeHTML(ref.description)}">`:""}<dl class="metadata"><dt>格式</dt><dd>${escapeHTML(ref.media_type)}</dd><dt>资源 ID</dt><dd>${escapeHTML(ref.id)}</dd><dt>引用</dt><dd>${escapeHTML(ref.uri)}</dd></dl>${Object.keys(ref.metadata).length?`<h3>元数据</h3><pre>${escapeHTML(json(ref.metadata))}</pre>`:""}${local?`<a class="secondary-button" href="/api/resources/${encodeURIComponent(id)}/content" download="${escapeHTML(id)}">${icon("download")}保存资源</a>`:"<p>这是外部资源引用，由对应 Provider 提供读取能力。</p>"}`,"RESOURCE");
}
function showConnection() {
  const info=state.status;
  if(!info) return toast("工作台尚未连接",true);
  openDialog("运行环境",`<p>${info.connected?"界面已连接到真实 Agent Runtime。所有任务操作均由 Runtime 管理。":"当前可以创建草稿、浏览历史任务和 Memory。接入 Planner 后，可以执行与恢复任务。"}</p><dl class="metadata"><dt>Planning</dt><dd>${escapeHTML(info.planner||"尚未连接")}</dd><dt>工作区</dt><dd>${escapeHTML(info.workspace)}</dd><dt>机器人</dt><dd>${escapeHTML(info.robots.map(r=>r.label).join("、")||"尚未配置")}</dd><dt>注册能力</dt><dd>${info.tools} 个 Tool · ${info.skills} 份 Skill</dd></dl><p>工作台不会自动添加模型或机器人能力。</p>`,"LOCAL RUNTIME");
}
function renderSearch() {
  const query=$("session-search").value.toLowerCase();
  const pages=[["工作台","home","workspace"],["Memory 记忆","memory","database"],["工具","tools","tool"],["Skills 技能","skills","book"]];
  $("search-results").innerHTML=[...pages.filter(p=>p[0].toLowerCase().includes(query)).map(([name,route,symbol])=>`<button data-route="${route}">${icon(symbol)}${name}</button>`),...state.sessions.filter(s=>s.goal.toLowerCase().includes(query)).slice(0,20).map(s=>`<button data-route="session/${escapeHTML(s.id)}">${icon("clock")}${escapeHTML(short(s.goal,65))}</button>`)].join("") || "<p>没有匹配的任务或页面</p>";
}
function openSearch() {$("session-search").value="";renderSearch();$("search-dialog").showModal();$("session-search").focus();}
function newTask() {navigate("home");setTimeout(()=>$("task-input").focus(),0);}

document.addEventListener("click", event=>{
  const button=event.target.closest("button, a");if(!button) return;
  const data=button.dataset;
  if(data.session) navigate(`session/${data.session}`);
  if(data.route){$("search-dialog").close();navigate(data.route);}
  if(data.prompt){$("task-input").value=data.prompt;$("task-input").focus();updateComposer();}
  if(data.record) showRecord(data.record).catch(e=>toast(e.message,true));
  if(data.tool) showTool(data.tool);
  if(data.skill) showSkill(data.skill,Number(data.offset||0)).catch(e=>toast(e.message,true));
  if(data.resource) showResource(data.resource).catch(e=>toast(e.message,true));
  if(data.copyRecord) perform(async()=>{const record=await api(`/records/${encodeURIComponent(data.copyRecord)}`);await navigator.clipboard.writeText(json(record));toast("记录已复制");});
  if(data.domain!==undefined){state.domain=data.domain;state.offset=0;document.querySelectorAll("[data-domain]").forEach(b=>b.classList.toggle("active",b===button));renderCollection().catch(e=>toast(e.message,true));}
  if(data.control) perform(async()=>{const robot=$("task-robot-select")?.value||"";button.disabled=true;state.snapshot=await api(`/sessions/${encodeURIComponent(state.sessionId)}/${data.control}?robot_id=${encodeURIComponent(robot)}`,{method:"POST"});renderTask();});
});
$("task-form").addEventListener("submit",event=>{event.preventDefault();perform(async()=>{const goal=$("task-input").value.trim();if(!goal) return;$("submit-task").disabled=true;const session=await api("/sessions",{method:"POST",body:json({goal,robot_id:$("robot-select").value,start:Boolean(state.status.connected&&$("robot-select").value)})});$("task-input").value="";localStorage.removeItem("primitive-draft");await refreshSessions();navigate(`session/${session.id}`);});});
$("reply-form").addEventListener("submit",event=>{event.preventDefault();perform(async()=>{const answer=$("reply-input").value.trim();if(!answer) return;const payload=$("reply-payload").hidden||!$("reply-payload").value.trim()?{}:JSON.parse($("reply-payload").value);if(!payload||Array.isArray(payload)||typeof payload!=="object") throw new Error("附加数据需要是 JSON 对象。");state.snapshot=await api(`/replies/${encodeURIComponent(state.sessionId)}`,{method:"POST",body:json({answer,payload})});$("reply-input").value="";$("reply-payload").value="";renderTask();});});
$("task-input").addEventListener("input",()=>{localStorage.setItem("primitive-draft",$("task-input").value);updateComposer();});
$("robot-select").addEventListener("change",updateComposer);
$("new-task").addEventListener("click",newTask);
$("connection-button").addEventListener("click",showConnection);$("runtime-button").addEventListener("click",showConnection);
$("search-button").addEventListener("click",openSearch);$("session-search").addEventListener("input",renderSearch);
$("close-search").addEventListener("click",()=>$("search-dialog").close());$("close-dialog").addEventListener("click",()=>$("detail-dialog").close());
for(const dialog of [$("search-dialog"),$("detail-dialog")]) dialog.addEventListener("click",e=>{if(e.target===dialog){const rect=dialog.getBoundingClientRect();if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom) dialog.close();}});
$("menu-button").addEventListener("click",()=>$("sidebar").classList.toggle("mobile-open"));
$("context-button").addEventListener("click",()=>{if(innerWidth<=1000) $("inspector").classList.toggle("mobile-open");else $("inspector").hidden=!$("inspector").hidden;});
$("theme-button").addEventListener("click",()=>{const theme=document.documentElement.dataset.theme==="dark"?"light":"dark";document.documentElement.dataset.theme=theme;localStorage.setItem("primitive-theme",theme);$("theme-button").innerHTML=icon(theme==="dark"?"sun":"moon");$("theme-button").setAttribute("aria-label",`切换${theme==="dark"?"浅":"深"}色主题`);});
$("copy-task-id").addEventListener("click",()=>perform(async()=>{await navigator.clipboard.writeText(state.sessionId);toast("任务 ID 已复制");}));
$("toggle-payload").addEventListener("click",()=>{$("reply-payload").hidden=!$("reply-payload").hidden;});
$("collection-search").addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.query=$("collection-search").value;state.offset=0;renderCollection().catch(e=>toast(e.message,true));},200);});
$("lifetime-filter").addEventListener("change",()=>{state.offset=0;renderCollection().catch(e=>toast(e.message,true));});
$("load-more").addEventListener("click",()=>perform(async()=>{state.offset+=40;await renderCollection(true);}));
$("sessions-more").addEventListener("click",()=>perform(async()=>{state.sessionOffset+=100;await refreshSessions();}));
$("older-events").addEventListener("click",()=>perform(async()=>{const all=new Set([...state.previousRecords,...state.snapshot.records].map(r=>r.id));const data=await api(`/memory?session_id=${encodeURIComponent(state.sessionId)}&limit=60&offset=${all.size}`);state.previousRecords.push(...data.records);state.snapshot.has_more=data.records.length===60;renderTask();}));
$("shortcuts-button").addEventListener("click",()=>openDialog("快捷键",'<dl class="metadata"><dt>Ctrl / ⌘ N</dt><dd>新建任务</dd><dt>Ctrl / ⌘ K</dt><dd>搜索任务与页面</dd><dt>Ctrl / ⌘ Enter</dt><dd>提交当前任务或回复</dd><dt>Esc</dt><dd>关闭详情或搜索</dd></dl>',"KEYBOARD SHORTCUTS"));
document.addEventListener("keydown",event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){event.preventDefault();openSearch();}if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="n"){event.preventDefault();newTask();}if((event.metaKey||event.ctrlKey)&&event.key==="Enter"){event.preventDefault();if(state.view==="home") $("task-form").requestSubmit();else if(!$("reply-form").hidden) $("reply-form").requestSubmit();}});
window.addEventListener("hashchange",()=>routeChanged().catch(e=>toast(e.message,true)));
window.addEventListener("beforeunload",()=>state.stream?.close());
window.addEventListener("primitive-command",event=>{if(event.detail==="new-task") newTask();if(event.detail==="search") openSearch();});
window.primitiveDesktop?.onCommand(command=>window.dispatchEvent(new CustomEvent("primitive-command",{detail:command})));

async function initialize() {
  hydrate();document.documentElement.dataset.theme=localStorage.getItem("primitive-theme")||"dark";
  $("task-input").value=localStorage.getItem("primitive-draft")||"";
  const [info,sessions,tools,skills]=await Promise.all([api("/status"),api("/sessions"),api("/tools"),api("/skills")]);
  state.status=info;state.sessions=sessions.sessions;state.tools=tools.tools;state.skills=skills.skills;
  renderEnvironment();await routeChanged();
}
initialize().catch(error=>{toast(`无法连接工作台：${error.message}`,true);$("server-label").textContent="连接失败";$("server-dot").classList.add("error");$("footer-status").textContent="连接失败";$("connection-note").textContent="无法连接本地 Runtime。请确认应用已正常启动后重试。";});
setInterval(()=>{if(document.visibilityState==="visible"&&state.status) refreshSessions().catch(()=>{});},8000);
