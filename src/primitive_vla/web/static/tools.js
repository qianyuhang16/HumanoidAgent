let active = null, selected = null, busy = false;
const $ = id => document.getElementById(id);
async function api(path, options={}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}
function details(title, data) {
  const entry = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
  summary.textContent=title; pre.textContent=JSON.stringify(data,null,2); entry.append(summary,pre); return entry;
}
async function refresh() {
  const tasks = await api('/api/tasks'); active=tasks.active_id; busy=!!active;
  $('run').disabled=busy||tasks.control_locked; $('cancel').disabled=!busy;
  if(tasks.control_locked) $('status').textContent='控制停止尚未确认，任务已锁定。';
  $('history').replaceChildren(...tasks.tasks.map(task=>{
    const button=document.createElement('button');button.textContent=`${task.status} · ${task.instruction}`;
    button.onclick=()=>{selected=task.id;refresh().catch(showError);};return button;
  }));
  const catalog=await api('/api/tools');
  $('tools').replaceChildren(...catalog.tools.map(t=>details(t.name+' · '+t.description.split('\n')[0],t)));
  if(!selected) selected=active;
  if(!selected) return;
  const task=await api('/api/tasks/'+selected);
  $('status').textContent=task.status+' · '+task.instruction;
  $('report').textContent=JSON.stringify(task.report,null,2);
  const events=await api('/api/tasks/'+selected+'/events');
  $('events').replaceChildren(...events.events.filter(e=>['decision','tool_started','tool_finished','tool_stopped','task_finished','error'].includes(e.event)).map(e=>details(e.event+(e.call?' · '+e.call.name:''),e)));
  const refs=Object.values(task.state?.images||{}).slice(-4);
  $('images').replaceChildren(...refs.map(ref=>{const img=document.createElement('img');img.alt='工具返回的观测';img.src=ref.startsWith('http://')||ref.startsWith('https://')?ref:'/api/media/'+encodeURIComponent(ref);return img;}));
  const videoEvent=events.events.filter(e=>e.event==='tool_finished'&&typeof e.result?.data?.video==='string').at(-1);
  const videoRef=task.id!==active&&videoEvent?videoEvent.result.data.video:null;
  const player=$('video');
  if(player.dataset.ref!==(videoRef||'')){
    player.dataset.ref=videoRef||'';player.hidden=!videoRef;
    if(videoRef) player.src='/api/media/'+encodeURIComponent(videoRef);
    else {player.pause();player.removeAttribute('src');player.load();}
  }
}
function showError(error){$('status').textContent=error.message;}
$('task-form').onsubmit=async event=>{event.preventDefault();try{const task=await api('/api/tasks',{method:'POST',body:JSON.stringify({instruction:$('instruction').value})});selected=task.id;await refresh();}catch(error){showError(error);}};
$('cancel').onclick=async()=>{try{await api('/api/tasks/'+active+'/cancel',{method:'POST'});await refresh();}catch(error){showError(error);}};
$('save').onclick=async()=>{try{await api('/api/config',{method:'PUT',body:JSON.stringify(JSON.parse($('config').value))});$('settings-status').textContent='已保存，下一次任务生效。';}catch(error){$('settings-status').textContent=error.message;}};
$('test').onclick=async()=>{try{await api('/api/vlm/test',{method:'POST'});$('settings-status').textContent='模型连接正常。';}catch(error){$('settings-status').textContent=error.message;}};
$('save-key').onclick=async()=>{try{await api('/api/vlm/key',{method:'POST',body:JSON.stringify({api_key:$('key').value})});$('key').value='';$('settings-status').textContent='密钥已设置，仅保存在当前服务进程。';}catch(error){$('settings-status').textContent=error.message;}};
api('/api/config').then(data=>{$('config').value=JSON.stringify(data.config,null,2);return refresh();}).catch(showError);
setInterval(()=>refresh().catch(showError),1500);
