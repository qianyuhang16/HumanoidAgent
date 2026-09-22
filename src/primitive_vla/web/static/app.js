'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let config, selectedId = new URLSearchParams(location.search).get('task'), activeId = null, events = [], lastSeq = 0, taskRecord = null, jsonDirty = false, toastTimer, configURL;
const statusNames = {running:'执行中',completed:'已完成',stopped:'已停止',failed:'失败',cancelled:'已取消',cancelling:'停止中',interrupted:'运行已中断'};
const goalNames = {pour:'配料倒入碗',stir:'完成搅拌',cup_home:'杯子归位',spoon_home:'勺子归位',hands_free:'双手空闲',pack:'水果装盒',close:'关闭盒盖',tool_home:'工具归位',place_red:'红色配料入碗',place_green:'绿色配料入碗',place_yellow:'黄色配料入碗'};
const labels = {dashboard:['操作工作台','从任务目标到技能执行，实时查看每一次决策与验证。'],history:['任务记录','回看每次任务的执行过程、配置快照与最终验证。'],skills:['技能契约','让技能的入口、能力边界与结束效果清晰可见。'],settings:['配置管理','配置模型、任务预算和技能能力，下一次任务即刻使用。']};
async function api(path, options = {}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(e => `${e.loc?.join('.')}: ${e.msg}`).join('；') : data.detail;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data;
}
function toast(message, error = false) { clearTimeout(toastTimer); $('toast').textContent=message; $('toast').className=error?'error':''; $('toast').hidden=false; toastTimer=setTimeout(()=>$('toast').hidden=true, error?9000:3500); }
function guarded(fn) { return async (...args) => { try { await fn(...args); } catch(error) { toast(error.message,true); } }; }
function switchView(view) {
  document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==`view-${view}`);
  document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  $('page-label').textContent=labels[view][0]; $('page-title').innerHTML=esc(labels[view][0])+'<span class="heading-dot">.</span>'; $('page-subtitle').textContent=labels[view][1];
  $('settings-shortcut').hidden=view==='settings';
}
function getPath(object,path) {return path.split('.').reduce((v,k)=>v[k],object);}
function setPath(object,path,value) {const keys=path.split('.'); const key=keys.pop(); keys.reduce((v,k)=>v[k],object)[key]=value;}
function updateConfigUI() {
  $('workspace-name').textContent=config.name;
  $('vla-settings').hidden=!config.vla;
  if(config.vla)document.querySelectorAll('[data-vla]').forEach(el=>el.value=String(config.vla[el.dataset.vla]));
  $('skill-count').textContent=config.skills.length;
  $('planner-mode').textContent=config.planner.type==='vlm'?(config.vlm.provider==='codex'?'Codex 规划器':'VLM 规划器'):'规则规划器';
  $('model-badge').textContent=config.vlm.model+(config.vlm.provider==='codex'?' · '+config.vlm.codex.reasoning_effort:'');
  $('verification-mode').textContent={rules:'规则验证',audit:'VLM 审阅',enforce:'VLM 强制验证'}[config.verification.mode];
  const backend=config.backends[config.observer_backend];
  const simulated=backend.type==='sapien';
  $('backend-tag').textContent={mock:'SYMBOLIC MOCK',sapien:'SAPIEN PHYSICS'}[backend.type]||backend.type.toUpperCase();
  $('simulation-settings').hidden=!simulated;
  if(simulated){
    document.querySelectorAll('[data-sim]').forEach(el=>el.value=String(backend.simulation[el.dataset.sim]));
    const examples=document.querySelectorAll('[data-instruction]');
    examples[0].dataset.instruction='把所有配料放入碗里，然后搅拌并放回工具';examples[0].textContent='多物体入碗与搅拌';
    examples[1].dataset.instruction='多物体入碗并搅拌';examples[1].textContent='使用当前场景配料';
    if(config.vla){examples[0].dataset.instruction='先接近红色配料，再抓取并抬起它，保持持有';examples[0].textContent='π0.5 单物体抓取试验';}
    if(['把所有配料放入碗里，然后搅拌并放回工具','把蓝杯里的配料倒入碗，搅拌，再把杯勺放回','把红色水果装盒并关盖'].includes($('instruction').value))$('instruction').value=examples[0].dataset.instruction;
  } else {
    const examples=document.querySelectorAll('[data-instruction]');
    examples[0].dataset.instruction='把蓝杯里的配料倒入碗，搅拌，再把杯勺放回';examples[0].textContent='倒料与搅拌';
    examples[1].dataset.instruction='把红色水果装盒并关盖';examples[1].textContent='水果装盒';
  }
  $('task-hint').textContent=config.parser.type==='template'?(simulated?'单臂依次夹取配料入碗，持棒搅拌并放回工具。每个任务从配置场景重新开始。':'模板模式支持示例任务；任意自然语言任务可切换 VLM 解析。'):'VLM 将解析自然语言目标，再通过技能契约执行。';
  document.querySelectorAll('[data-config]').forEach(el=>el.value=String(getPath(config,el.dataset.config)));
  if (!jsonDirty) $('config-json').value=JSON.stringify(config,null,2);
  if (configURL) URL.revokeObjectURL(configURL);
  configURL=URL.createObjectURL(new Blob([JSON.stringify(config,null,2)],{type:'application/json'}));
  $('download-config').href=configURL; $('download-config').download='primitive-config.json';
  renderSkills(); renderImages(config.scene.images);
  if (!taskRecord) renderState(initialSnapshot(),config,[]);
}
function initialSnapshot() {
  const mock=config.backends[config.observer_backend].type==='mock';
  $('scene-source').textContent=mock?'INITIAL CONFIG':'AWAITING OBSERVATION';
  return {facts:Object.fromEntries(Object.entries(config.scene.facts).map(([k,v])=>[k,{value:mock?v:'unknown',source:mock?'scene:initial_config':'awaiting:robot_observation',confidence:1,timestamp:0,ttl_s:null}])),observation:{images:config.scene.images,timestamp:0}};
}
function valuesFor(snapshot,cfg,live=false) {
  const now=live?Date.now()/1000:(snapshot.observation?.timestamp||Date.now()/1000);
  return Object.fromEntries(Object.entries(snapshot.facts||{}).map(([k,f])=>[k,f.confidence<cfg.state.confidence_threshold || f.ttl_s!==null && now-f.timestamp>f.ttl_s ? 'unknown':f.value]));
}
function renderScene(values,cfg) {
  const containers=cfg.scene.objects.filter(o=>['bowl','box','support','rack'].includes(o.kind));
  const predefined={bowl:[33,24],box:[70,23],cup_home:[73,54],spoon_home:[22,55],table:[50,57]};
  const positions={};
  let html='';
  containers.forEach((o,i)=>{
    const [x,y]=predefined[o.id] || [18+(i%3)*28,22+Math.floor(i/3)*24]; positions[o.id]=[x,y];
    const caption=o.kind==='box'?`${o.description} · ${values['open:'+o.id]==='true'?'已开':values['open:'+o.id]==='false'?'关闭':'待确认'}`:o.description;
    if(o.id!=='table') html+=`<div class="zone ${esc(o.kind)} ${values['ingredients:'+o.id]==='true'?'filled':''}" style="left:calc(${x}% - 47px);top:${y}%">${esc(caption)}</div>`;
  });
  cfg.scene.objects.filter(o=>!['bowl','box','support','rack'].includes(o.kind)).forEach((o,i)=>{
    let x=47+i*8,y=48,known=false;
    if(values['holding:right:'+o.id]==='true'){x=72;y=68;known=true;}
    else if(values['holding:left:'+o.id]==='true'){x=27;y=68;known=true;}
    else for(const [loc,pos] of Object.entries(positions))if(values[`at:${o.id}:${loc}`]==='true'){[x,y]=pos;y+=4;known=true;break;}
    const symbol=['cup','fruit','spoon'].includes(o.kind)?o.kind:'unknown';
    html+=`<div class="scene-item" style="left:${x}%;top:${y}%"><span class="object-symbol ${known?symbol:'unknown'}">${known&&symbol!=='unknown'?'':'?'}</span><span>${esc(o.description)}</span></div>`;
  });
  $('scene-objects').innerHTML=html;
  for(const hand of ['left','right']){
    const held=cfg.scene.objects.find(o=>values[`holding:${hand}:${o.id}`]==='true');
    $(`${hand}-hand`).querySelector('b').textContent=held?held.description:values[`free:${hand}`]==='true'?'空闲':'待确认';
  }
}
function renderState(snapshot,cfg,goals,live=false) {
  const values=valuesFor(snapshot,cfg,live);
  renderScene(values,cfg);
  renderSimulation(snapshot,cfg,live);
  const completed=goals.filter(g=>Object.entries(g.conditions).every(([k,v])=>values[k]===v)).length;
  $('metric-goals').innerHTML=`${completed} <span>/ ${goals.length}</span>`;
  $('metric-unknown').textContent=String(Object.values(values).filter(v=>v==='unknown').length).padStart(2,'0');
  $('goal-list').innerHTML=goals.length?goals.map(g=>{const ok=Object.entries(g.conditions).every(([k,v])=>values[k]===v);return `<div class="goal ${ok?'':'pending'}"><span class="goal-icon">${ok?'✓':'○'}</span><div><div class="goal-name">${esc(g.id==='hands_free'&&cfg.backends[cfg.observer_backend].type==='sapien'?'夹爪空闲':goalNames[g.id]||g.id)}</div><small>${esc(Object.keys(g.conditions).join(' · '))}</small>${g.after.length?`<small>依赖：${esc(g.after.map(id=>goalNames[id]||id).join(' → '))}</small>`:''}</div></div>`;}).join(''):'<p class="muted">任务启动后显示目标与验证依据。</p>';
  $('facts-table').innerHTML=Object.entries(snapshot.facts||{}).map(([key,f])=>`<tr><td>${esc(key)}</td><td style="color:${values[key]==='unknown'?'var(--amber)':'var(--teal)'}">${esc(values[key])}</td><td>${esc(f.source)}</td><td>${Number(f.confidence).toFixed(2)}</td><td>${f.timestamp?new Date(f.timestamp*1000).toLocaleTimeString('zh-CN'):'初始配置'}</td></tr>`).join('');
}
function renderSimulation(snapshot,cfg,live) {
  const backendType=cfg.backends[cfg.observer_backend].type;
  const simulated=backendType==='sapien';
  $('simulation-view').hidden=!simulated;$('scene').hidden=simulated;
  document.querySelector('.scene-disclaimer').textContent=simulated?'SAPIEN 物理仿真 · 固定碗与刚体配料':'符号状态示意 · 非相机画面';
  $('simulation-empty').textContent='开始任务后显示 SAPIEN 仿真画面';
  $('simulation-frame').alt='物理仿真相机画面';
  if(!simulated)return;
  const observation=snapshot.observation||{}, ref=observation.images?.simulation;
  $('simulation-frame').hidden=!ref;$('simulation-empty').hidden=Boolean(ref);
  if(ref){
    const url='/api/media/'+encodeURIComponent(ref);
    if($('simulation-frame').getAttribute('src')!==url)$('simulation-frame').src=url;
    $('scene-source').textContent='SAPIEN'+(live?' · LIVE RGB':' · TASK SNAPSHOT');
  }
  const state=observation.proprioception||{};
  $('simulation-stats').textContent=`Panda · ${Number(state.sim_time_s||0).toFixed(1)} s · 已观测搅拌 ${(Number(state.stir_angle_rad||0)/(2*Math.PI)).toFixed(2)} 圈`;
  $('simulation-video').hidden=live||!state.video;
  if(state.video)$('simulation-video').href='/api/media/'+encodeURIComponent(state.video);
}
function renderImages(images) {
  const entries=Object.entries(images||{});
  $('images').innerHTML=entries.length?entries.map(([source,ref])=>{
    const url=/^https?:\/\//.test(ref)?ref:'/api/media/'+encodeURIComponent(ref);
    const remove=source==='simulation'?'':`<button data-remove-image="${esc(source)}" aria-label="移除 ${esc(source)}">×</button>`;
    return `<div class="image-card"><a href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}" alt="${esc(source)} 视觉输入"></a><span>${esc(source)}</span>${remove}</div>`;
  }).join(''):'<div class="image-empty">尚未接入图像 · 可添加视觉参考</div>';
}
function renderTimeline() {
  const names={task_started:'开始任务',decision:'下一步决策',result:'效果验证',reobserved:'重新观察',stopped:'控制停止',model_requested:'请求模型',controller_selected:'控制器切换',vla_loading:'加载 VLA 权重',vla_ready:'VLA 已就绪',vla_requested:'VLA 推理请求',vla_chunk_executed:'VLA 动作块执行',model_failed:'模型失败',model_cancelled:'模型已取消',model_response:'模型响应',task_verdict:'任务级验证',cancel_requested:'请求取消',error:'执行异常',interrupted:'运行中断'};
  const visible=events.filter(e=>names[e.event]); $('event-count').textContent=`${events.length} EVENTS`;
  if(!visible.length)return;
  const target=$('timeline'); const atBottom=target.scrollHeight-target.scrollTop-target.clientHeight<45;
  target.innerHTML=visible.map(e=>{
    let title=names[e.event],detail=e.reason||'',pill='';
    if(e.event==='decision'){title=e.candidate?e.candidate.split(':')[0]:'规划器 · '+e.kind;detail=e.reason;}
    if(e.event==='result'){title=e.call.skill;detail=`${e.result.execution} / ${e.result.effect} · ${e.result.reason}`;pill=e.result.effect;}
    if(e.event==='model_requested'||e.event==='model_response'){detail=`${e.purpose} · ${e.model}`+(e.latency_ms?` · ${e.latency_ms} ms`:'');}
    if(e.event==='controller_selected')detail=`${e.controller} · ${e.skill} · ${e.prompt||''}`;
    if(e.event==='vla_requested')detail=e.prompt;
    if(e.event==='vla_chunk_executed')detail=`动作块 ${e.chunk} · ${e.status} · ${e.applied?.length||0} 步`;
    if(e.event==='task_started')detail=e.task.instruction;
    if(e.event==='task_verdict')detail=`${e.verification.mode} / ${e.verification.status}`;
    return `<div class="event ${esc(e.event)} ${e.result?.effect==='failure'?'failed':''}"><div class="event-title"><span>${esc(title)}</span>${pill?`<span class="event-label">${esc(pill)}</span>`:''}<span class="time">${new Date(e.time*1000).toLocaleTimeString('zh-CN')}</span></div><p>${esc(detail)}</p><details><summary>查看记录</summary><pre>${esc(JSON.stringify(e.result||e.verification||{...e,state:undefined},null,2))}</pre></details></div>`;
  }).join('');
  if(atBottom)target.scrollTop=target.scrollHeight;
}
function renderTask(record) {
  taskRecord=record;
  $('task-snapshot').hidden=false;
  $('task-config').textContent=JSON.stringify(record.config,null,2);
  const live=['running','cancelling'].includes(record.status);
  $('metric-status').textContent=record.report.control_locked?'控制已锁定':statusNames[record.status]||record.status;
  $('metric-detail').textContent=record.report.reason||record.instruction;
  $('metric-detail').title=record.report.reason||record.instruction;
  const steps=events.filter(e=>e.event==='result').length;
  $('metric-steps').textContent=String(steps).padStart(2,'0');
  const start=events.find(e=>e.event==='task_started');
  $('metric-budget').textContent=`剩余步骤预算 ${Math.max(0,(start?.task.max_steps||record.config.task_defaults.max_steps)-steps)}`;
  $('scene-source').textContent=live?'LIVE STATE':'TASK SNAPSHOT';
  renderState(record.state,record.config,record.goals||[],live);
  renderReasoning(record,live);
  renderImages(record.state.observation?.images||record.config.scene.images);
  const verification=record.report.verification;
  $('verdict').textContent=verification?`最终验证 · ${verification.mode} / ${verification.status}${verification.reason?' · '+verification.reason:''}`:record.report.reason||'';
}
function renderReasoning(record,live) {
  const cfg=record.config.vlm;
  renderVLA(record);
  $('model-badge').textContent=cfg.model+(cfg.provider==='codex'?' · '+cfg.codex.reasoning_effort:'');
  const requests=events.filter(e=>e.event==='model_requested'&&e.request_id);
  const replies=new Map(events.filter(e=>['model_response','model_failed','model_cancelled'].includes(e.event)).map(e=>[e.request_id,e]));
  const pending=requests.findLast(e=>!replies.has(e.request_id));
  const phases={parse:'解析任务目标',plan:'选择下一技能',verify:'复核目标证据',connection_test:'测试连接'};
  const last=events.at(-1);
  $('model-phase').textContent=pending&&live?phases[pending.purpose]||'等待模型':live?(last?.event==='model_response'?'决策已返回':'执行与观测中'):statusNames[record.status]||'等待任务';
  $('model-pulse').classList.toggle('thinking',Boolean(pending&&live));
  $('model-elapsed').textContent=pending&&live?`${Math.max(0,Date.now()/1000-pending.time).toFixed(0)} s`:`${requests.length} 次调用`;
  if(!requests.length)return;
  const open=new Set([...$('model-decisions').querySelectorAll('details[open]')].map(e=>e.dataset.request));
  const target=$('model-decisions'), bottom=target.scrollHeight-target.scrollTop-target.clientHeight<50;
  target.innerHTML=requests.map((request,i)=>{
    const reply=replies.get(request.request_id), result=reply?.result, input=request.input||{};
    const selected=input.candidates?.find(c=>c.index===result?.index);
    let body='';
    if(result?.goals)body=result.goals.map(g=>`<p><b>${esc(goalNames[g.id]||g.id)}</b><br><small>${esc(Object.entries(g.conditions).map(([k,v])=>`${k} = ${v}`).join(' · '))}${g.after.length?' · 依赖 '+esc(g.after.join(', ')):''}</small></p>`).join('');
    else if(result?.criteria)body=result.criteria.map(c=>`<p><b>${esc(goalNames[c.id]||c.id)} · ${esc(c.status)}</b><br>${esc(c.reason)}<br><small>${esc(c.evidence_refs.join(' · '))}</small></p>`).join('');
    else if(result)body=`<div class="decision-choice">${esc(selected?selected.skill+' · '+Object.entries(selected.bindings).map(([k,v])=>k+'='+v).join(' / '):result.action||'响应')}</div><p>${esc(result.reason)}</p>`;
    else body=`<p class="muted">${esc(reply?.reason||(live?'正在等待模型返回决策摘要…':'调用未返回，任务已结束'))}</p>`;
    const candidates=input.candidates||[];
    const details=candidates.length?`<h4>本次可执行的候选技能</h4>${candidates.map(c=>`<div class="candidate ${c.index===result?.index?'chosen':''}"><b>${c.index===result?.index?'✓':'#'+c.index} ${esc(c.skill)}</b> ${esc(Object.values(c.bindings).join(' · '))}</div>`).join('')}`:'';
    const usage=reply?.usage||{};
    return `<section class="decision-card"><header><b>${String(i+1).padStart(2,'0')} / ${esc(phases[request.purpose]||request.purpose)}</b><time>${new Date(request.time*1000).toLocaleTimeString('zh-CN')}</time></header>${body}<details data-request="${esc(request.request_id)}" ${open.has(request.request_id)?'open':''}><summary>查看输入事实${candidates.length?'与 '+candidates.length+' 个候选':''}</summary>${details}<pre>${esc(JSON.stringify(input.facts||{},null,2))}</pre></details><footer>${reply?.latency_ms?(reply.latency_ms/1000).toFixed(1)+' s · ':''}${usage.input_tokens??usage.prompt_tokens??'—'} 输入 / ${usage.output_tokens??usage.completion_tokens??'—'} 输出 tokens · ${request.image_sources.length} 张图像</footer></section>`;
  }).join('');
  if(bottom)target.scrollTop=target.scrollHeight;
}
function renderVLA(record) {
  $('vla-panel').hidden=!record.config.vla;
  if(!record.config.vla)return;
  $('vla-panel').querySelector('h2').textContent='低层控制 · VLA';
  $('vla-panel').querySelector('.hint').textContent='策略由配置注入；动作执行与物理效果验证分别记录。';
  const selected=events.findLast(e=>e.event==='controller_selected');
  const last=events.findLast(e=>e.event.startsWith('vla_'));
  const chunks=events.filter(e=>e.event==='vla_chunk_executed');
  const replies=events.filter(e=>e.event==='vla_response');
  $('vla-controller').textContent=selected?`${selected.controller.toUpperCase()} · ${selected.skill}`:'π0.5 BASE';
  $('vla-command').textContent=selected?.prompt||'';
  $('vla-summary').textContent=last?.event==='vla_loading'?'正在加载本机 π0.5 权重…':`已记录 ${replies.length} 次 VLA 推理、${chunks.length} 个执行动作块。`;
  const outcome=record.report?.results?.findLast(r=>r.call_id===selected?.call_id);
  if(outcome)$('vla-summary').textContent+=` ${outcome.skill}：${outcome.effect} · ${outcome.reason}`;
  $('vla-chunks').innerHTML=chunks.slice(-8).map(e=>{
    const response=replies.find(r=>r.call_id===e.call_id&&r.chunk===e.chunk);
    const limited=(e.applied||[]).filter(a=>a.arm_limited).length;
    return `<details><summary>动作块 ${e.chunk} · ${esc(e.status)} · ${response?.latency_ms||'—'} ms · ${limited}/${e.applied?.length||0} 步触发关节限幅</summary><pre>${esc(JSON.stringify({predicted:response?.actions?.slice(0,e.applied?.length),executed:e.applied},null,2))}</pre></details>`;
  }).join('');
}
function renderSkills() {
  $('skill-cards').innerHTML=config.skills.map(s=>`<article class="skill-card"><span class="version">v${esc(s.version)}</span><h3>${esc(s.name)}</h3><p>${esc(s.description)}</p><div class="skill-tags"><span>${esc(s.boundary)}</span><span>${s.timeout_s}s 超时</span><span>${s.max_attempts} 次尝试</span>${s.irreversible?'<span>不可逆效果</span>':''}</div><details><summary>入口 / 持续条件 / 效果</summary><pre>${esc(JSON.stringify({preconditions:s.preconditions,invariants:s.invariants,effects:s.effects,resources:s.resources,capability:s.capability,execution:s.execution},null,2))}</pre></details><details><summary>SKILL.md 使用说明</summary><pre>${esc(s.instructions)}</pre></details></article>`).join('');
}
async function selectTask(id) { selectedId=id;history.replaceState(null,'','?task='+encodeURIComponent(id));events=[];lastSeq=0;taskRecord=null;switchView('dashboard');$('timeline').innerHTML='<div class="empty-state"><p>正在读取任务记录…</p></div>';await loadTask(); }
async function loadTask() {
  if(!selectedId)return;
  const id=selectedId;
  const [record,page]=await Promise.all([api(`/api/tasks/${id}`),api(`/api/tasks/${id}/events?after=${lastSeq}`)]);
  if(id!==selectedId)return;
  if(page.events.length){events.push(...page.events);lastSeq=page.events.at(-1).seq;renderTimeline();}
  renderTask(record);
}
async function refresh() {
  try {
    const data=await api('/api/tasks');activeId=data.active_id;
    $('connection-label').textContent='本地服务已连接';
    $('run-task').disabled=Boolean(activeId)||data.control_locked;
    $('cancel-task').disabled=!activeId;
    if(data.control_locked){$('metric-status').textContent='控制已锁定';$('task-hint').textContent='停止尚未确认，请先在机器人侧核对状态。';}
    $('history-count').textContent=data.tasks.length;
    $('history-empty').hidden=Boolean(data.tasks.length);
    $('history-table').innerHTML=data.tasks.map(t=>`<tr><td>${esc(t.instruction)}</td><td><span class="pill ${esc(t.status)}">${esc(statusNames[t.status]||t.status)}</span></td><td>${new Date(t.created*1000).toLocaleString('zh-CN')}</td><td class="mono">${esc(t.id.slice(0,8))}</td><td><button class="text-link" data-task="${esc(t.id)}">查看 ↗</button></td></tr>`).join('');
    if(!selectedId&&(activeId||data.tasks.length)){selectedId=activeId||data.tasks[0].id;events=[];lastSeq=0;}
    await loadTask();
  } catch(error) {$('connection-label').textContent='连接中断，正在重试';}
  finally {setTimeout(refresh,config?.web.refresh_ms||700);}
}
async function saveSettings(fromJSON=false) {
  let draft;
  if(fromJSON)draft=JSON.parse($('config-json').value);
  else {
    draft=structuredClone(config);
    document.querySelectorAll('[data-config]').forEach(el=>setPath(draft,el.dataset.config,el.dataset.type==='boolean'?el.value==='true':el.type==='number'?Number(el.value):el.value));
    if(draft.vla)document.querySelectorAll('[data-vla]').forEach(el=>draft.vla[el.dataset.vla]=el.type==='number'?Number(el.value):el.value);
    const backend=draft.backends[draft.observer_backend];
    if(backend.type==='sapien')document.querySelectorAll('[data-sim]').forEach(el=>backend.simulation[el.dataset.sim]=el.tagName==='SELECT'?el.value==='true':el.type==='number'?Number(el.value):el.value);
  }
  await api('/api/config',{method:'PUT',body:JSON.stringify(draft)});
  if($('api-key').value){await api('/api/vlm/key',{method:'POST',body:JSON.stringify({api_key:$('api-key').value})});$('api-key').value='';}
  const saved=await api('/api/config');config=saved.config;
  if(fromJSON)jsonDirty=false;
  updateConfigUI();$('save-status').textContent='已保存 · 后续任务使用此配置';
  $('vlm-status').textContent=saved.key_configured?'已设置密钥；尚未测试':'未设置密钥（本地服务可不需要）';
  toast('配置已保存，下次任务生效');
}
document.querySelectorAll('[data-view]').forEach(el=>el.onclick=()=>switchView(el.dataset.view));
$('settings-shortcut').onclick=()=>switchView('settings');
$('edit-skills').onclick=guarded(async()=>{const result=await api('/api/skills/reload',{method:'POST'});config=result.config;updateConfigUI();toast('已从 SKILL.md 重新加载，后续任务使用新版本');});
document.querySelectorAll('[data-instruction]').forEach(el=>el.onclick=()=>{$('instruction').value=el.dataset.instruction;$('task-json').value='';});
$('run-task').onclick=guarded(async()=>{
  $('run-task').disabled=true;
  try {const raw=$('task-json').value.trim();const data=await api('/api/tasks',{method:'POST',body:JSON.stringify({instruction:$('instruction').value,task:raw?JSON.parse(raw):null})});activeId=data.id;await selectTask(data.id);$('cancel-task').disabled=false;}
  finally {if(!activeId)$('run-task').disabled=false;}
});
$('cancel-task').onclick=guarded(async()=>{if(activeId){$('cancel-task').disabled=true;await api(`/api/tasks/${activeId}/cancel`,{method:'POST'});toast('已请求停止，正在确认控制状态');}});
$('history-table').onclick=guarded(async e=>{const button=e.target.closest('[data-task]');if(button)await selectTask(button.dataset.task);});
$('save-settings').onclick=guarded(()=>saveSettings());
$('save-json').onclick=guarded(()=>saveSettings(true));
$('config-json').oninput=()=>{jsonDirty=true;};
$('export-task').onclick=guarded(async()=>{
  if(!taskRecord)return;
  const record=taskRecord, allEvents=[];let cursor=0;
  while(true){const page=await api(`/api/tasks/${record.id}/events?after=${cursor}`);if(!page.events.length)break;allEvents.push(...page.events);cursor=page.events.at(-1).seq;}
  const url=URL.createObjectURL(new Blob([JSON.stringify({...record,events:allEvents},null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download=`task-${record.id}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('test-vlm').onclick=guarded(async()=>{
  await saveSettings();$('test-vlm').disabled=true;$('vlm-status').textContent='正在连接…';
  try {const result=await api('/api/vlm/test',{method:'POST'});$('vlm-status').textContent=`连接成功 · ${result.model} · ${result.images} 张图像`;toast('模型连接与 JSON 返回验证通过');}
  catch(error){$('vlm-status').textContent=error.message;throw error;}
  finally{$('test-vlm').disabled=false;}
});
$('image-upload').onchange=guarded(async e=>{
  const file=e.target.files[0];if(!file)return;
  if(file.size>config.vlm.max_image_bytes)throw new Error('图像超过配置的大小限制');
  const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});
  const image=await api('/api/images',{method:'POST',body:JSON.stringify({data_url:data})});
  const source=Object.keys(config.scene.images).length?'view_'+Date.now():'front';
  config.scene.images[source]=image.reference;
  await api('/api/config',{method:'PUT',body:JSON.stringify(config)});updateConfigUI();e.target.value='';toast('图像已添加，后续任务将使用此视觉参考');
});
$('images').onclick=guarded(async e=>{const button=e.target.closest('[data-remove-image]');if(!button)return;delete config.scene.images[button.dataset.removeImage];await api('/api/config',{method:'PUT',body:JSON.stringify(config)});updateConfigUI();toast('已从后续任务的视觉输入中移除');});
setInterval(()=>$('clock').textContent=new Date().toLocaleTimeString('zh-CN',{hour12:false}),1000);
(async()=>{try{const data=await api('/api/config');config=data.config;updateConfigUI();$('vlm-status').textContent=data.key_configured?'密钥已配置':'未设置密钥';await refresh();}catch(error){toast(error.message,true);$('connection-label').textContent='服务不可用';}})();
