'use strict';
const $ = id => document.getElementById(id);
const names = {'camera.observe':'读取相机','perception.segment':'识别与分割','perception.depth':'深度预测','robot.plan_hover':'生成运动计划','robot.execute':'执行运动','robot.reset_arm':'单臂复位','robot.arm_state':'读取操作状态','vla.run':'运行 VLA','session.read':'回看记录','session.search':'检索记录','outcome.assess':'目标验证','session.assess':'重新验证目标'};
const verdicts = {satisfied:'目标已达成',unsatisfied:'目标未达成',unknown:'尚无法确认'};
const labels = {running:'处理中',cancelling:'正在取消',completed:'已完成',blocked:'未通过',failed:'失败',stopped:'已暂停',cancelled:'已取消',interrupted:'已中断',ok:'完成',error:'错误',rejected:'拒绝',timed_out:'超时'};
let activeId = null, selectedId = null, task = null, view = 'front', connected = false, submitting = false, edited = false, revision = 0, controlLocked = false, freshChat = false;

async function api(path, options = {}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '请求失败，请检查输入或服务日志');
  return value;
}
function notice(text, error = false) { $('notice').textContent = text; $('notice').classList.toggle('error', error); }
function controls() {
  $('start').disabled = !connected || submitting || controlLocked || Boolean(activeId) || !$('instruction').value.trim();
  $('cancel').disabled = !connected || !activeId || submitting;
  $('instruction').disabled = submitting || Boolean(activeId);
  $('newChat').disabled = submitting || Boolean(activeId);
  $('history').disabled = submitting || Boolean(activeId);
  $('recoveryPanel').hidden = !controlLocked;
  $('recoverControl').disabled = !connected || submitting || Boolean(activeId);
}
function clearResult() {
  task = null;
  $('commandPanel').hidden = true; $('command').textContent = ''; $('copy').textContent = '复制命令';
  $('conversation').replaceChildren();
  $('steps').replaceChildren(); $('status').textContent = '等待 Agent'; $('status').className = 'pill';
  $('frameNote').textContent = '等待工具返回图像'; renderImage();
}
function renderImage() {
  const images = task?.images || {};
  const kinds = Object.keys(images);
  if (!kinds.includes(view)) view = kinds[0] || 'front';
  const imageNames = {front:'相机画面',left:'左腕相机',right:'右腕相机',segmentation:'目标分割',depth_prediction:'深度预测'};
  const tabs = kinds.map(kind => {
    const button = document.createElement('button'); button.className = 'tab' + (kind === view ? ' active' : '');
    button.dataset.view = kind; button.setAttribute('role','tab'); button.setAttribute('aria-selected',String(kind === view));
    const [phase, source] = kind.split(':');
    button.textContent = source ? `${phase === 'before' ? '执行前' : '执行后'} · ${imageNames[source] || source}` : imageNames[kind] || kind;
    button.onclick = () => { view = kind; renderImage(); }; return button;
  });
  $('imageTabs').replaceChildren(...tabs);
  const reference = task?.images?.[view];
  $('image').hidden = !reference; $('empty').hidden = Boolean(reference);
  if (reference) {
    const source = '/api/media/' + encodeURIComponent(reference);
    if ($('image').getAttribute('src') !== source) $('image').src = source;
  } else $('image').removeAttribute('src');
  $('caption').textContent = view === 'depth_prediction' ? 'Depth Anything 单目预测，并非实测深度；不参与 IK 坐标计算。' : view === 'segmentation' ? '绿色为目标掩码，红十字为用于规划的像素中心。请核对是否选中了正确物体。' : '当前任务工具返回的图像；图像标签按实际结果生成。';
}
function stepDetail(step) {
  const d = step.data || {};
  if (step.status !== 'ok') return step.text || (step.status === 'running' ? '等待工具返回…' : '未完成');
  if (d.verdict) return `${d.source === 'main_agent' ? '主 Agent 判断' : '独立验证'} · ${d.goal || ''}\n${d.reason || step.text || ''}`;
  if (step.name === 'perception.segment') return d.found ? `检测分数 ${Number(d.score || 0).toFixed(3)} · 像素 ${d.pixel_uv?.map(x => Number(x).toFixed(1)).join(', ')}` : d.reason || '没有找到明确目标';
  if (step.name === 'perception.depth') return '已生成深度预测；未作为实测坐标使用。';
  if (step.name === 'robot.plan_hover') return d.execution_ready ? `规划通过 · ${d.duration_s} 秒 · 未执行运动` : (d.blockers || ['未通过规划']).join('\n');
  if (step.name === 'robot.execute') return d.arrival_verified ? `运动完成，反馈已确认到位 · 轨迹 ${d.nominal_duration_s} 秒` : step.text || '查看执行反馈';
  if (step.name === 'robot.reset_arm') return d.arrival_verified ? (d.target_gripper_m != null ? `关节与夹爪复位完成，反馈已确认 · 夹爪目标 ${(d.target_gripper_m * 1000).toFixed(1)} mm` : '复位完成，夹爪保持当前开度。') : step.text || '查看复位反馈';
  if (step.name === 'vla.run') return `本轮已发送 ${d.actions_sent || 0} 个动作 · ${d.stop_reason || '已返回'}。Agent 将结合执行前后证据判断目标是否达成。`;
  if (step.name === 'camera.observe') return '已读取固定前视相机图像。';
  return step.text || '完成';
}
function renderTask(value) {
  task = value;
  $('status').textContent = labels[value.status] || value.status;
  $('status').className = 'pill' + (value.status === 'completed' ? ' good' : ['blocked','failed'].includes(value.status) ? ' bad' : '');
  $('frameNote').textContent = `当前任务 · ${new Date(value.created*1000).toLocaleString()}`;
  const expanded = new Set([...document.querySelectorAll('#steps li')].filter(li => li.querySelector('details')?.open).map(li => li.dataset.stepId));
  const rows = value.steps.map(step => {
    const li = document.createElement('li'); li.className = 'step'; li.dataset.stepId = step.id;
    const row = document.createElement('div'); row.className = 'step-title';
    const name = document.createElement('span'); name.textContent = names[step.name] || step.name;
    const state = document.createElement('span');
    const failed = step.status === 'ok' && (step.data?.found === false || step.data?.execution_ready === false);
    state.textContent = step.status === 'ok' && verdicts[step.data?.verdict] ? verdicts[step.data.verdict] : failed ? '未通过' : labels[step.status] || step.status;
    if (failed || ['error','rejected','timed_out'].includes(step.status)) state.className = 'error';
    row.append(name, state);
    const detail = document.createElement('p'); detail.textContent = stepDetail(step); li.append(row, detail);
    const details = document.createElement('details'); details.open = expanded.has(step.id); const summary = document.createElement('summary'); summary.textContent = '参数与返回结果';
    const raw = document.createElement('pre'); raw.textContent = JSON.stringify(step.arguments || {}, null, 2) + '\n' + (step.detail_json || JSON.stringify(step.data || {}, null, 2));
    details.append(summary, raw); li.append(details); return li;
  });
  $('steps').replaceChildren(...rows);
  if (!rows.length) { const li = document.createElement('li'); li.className = 'placeholder'; li.textContent = ['running','cancelling'].includes(value.status) ? 'Agent 正在处理任务…' : '本次回答未调用工具。'; $('steps').append(li); }
  const scroll = $('conversation').scrollTop;
  const atBottom = scroll + $('conversation').clientHeight >= $('conversation').scrollHeight - 32;
  const messages = (value.turns || [value]).flatMap(turn => {
    const user = document.createElement('article'); user.className = 'message user';
    const who = document.createElement('strong'); who.textContent = '你';
    const body = document.createElement('p'); body.textContent = turn.instruction; user.append(who, body);
    const assistant = document.createElement('article'); assistant.className = 'message assistant';
    const name = document.createElement('strong'); name.textContent = 'Agent';
    const answer = document.createElement('p');
    const stopReasons = {'Turn budget exhausted':'本轮决策预算已用完，任务暂停。', 'Task time budget exhausted':'本轮时间预算已用完，任务暂停。'};
    answer.textContent = stopReasons[turn.reason] || turn.reason || labels[turn.status] || '正在处理…';
    if (turn.reason?.startsWith('Model response recovery exhausted')) answer.textContent = '模型连续返回无效决策，修复重试未成功。任务已暂停。' + turn.reason.slice(turn.reason.indexOf(':') + 1);
    assistant.append(name, answer);
    if (turn.status === 'stopped') {
      if (turn.model_errors) {
        const errors = document.createElement('p'); errors.textContent = `本轮 ${turn.model_errors} 次模型回复格式错误；这些回复没有执行工具。`; assistant.append(errors);
      }
      if (turn.progress) {
        const progress = document.createElement('p'); progress.textContent = 'Agent 最近记录（并非完成确认）：\n' + turn.progress; assistant.append(progress);
      }
      const next = document.createElement('p'); next.textContent = '可在当前会话说明已经完成的部分，并要求继续剩余步骤。'; assistant.append(next);
    }
    return [user, assistant];
  });
  $('conversation').replaceChildren(...messages);
  $('conversation').scrollTop = atBottom ? $('conversation').scrollHeight : scroll;
  const command = value.status === 'completed' && !edited ? value.command : null;
  $('commandPanel').hidden = !command; $('command').textContent = command || '';
  renderImage();
}
async function refresh() {
  const expectedRevision = revision;
  try {
    const state = await api('/api/status'); if (expectedRevision !== revision) return; connected = true; activeId = state.active_id; controlLocked = Boolean(state.control_locked);
    $('model').textContent = state.model; $('capabilities').textContent = state.tools?.length ? state.tools.map(t => t.name).join(' · ') : '任务开始后按实际配置显示'; $('workspace').textContent = state.workspace;
    if (!selectedId && !freshChat) selectedId = activeId || state.tasks[0]?.id || null;
    const seen = new Set();
    const conversations = state.tasks.filter(row => { const id = row.conversation_id || row.id; if (seen.has(id)) return false; seen.add(id); return true; });
    const selected = state.tasks.find(row => row.id === selectedId);
    const latest = selected && conversations.find(row => (row.conversation_id || row.id) === (selected.conversation_id || selected.id));
    if (latest) selectedId = latest.id;
    const options = conversations.map(row => { const option = document.createElement('option'); option.value = row.id; option.textContent = `${labels[row.status] || row.status} · ${row.instruction.slice(0,35)}`; return option; });
    if (!options.length) { const option = document.createElement('option'); option.value = ''; option.textContent = '暂无会话'; options.push(option); }
    $('history').replaceChildren(...options); $('history').value = selectedId || '';
    const id = selectedId;
    if (id) { const value = await api('/api/tasks/' + encodeURIComponent(id)); if (selectedId === id && expectedRevision === revision) renderTask(value); }
    notice(controlLocked ? '控制已锁定。确认机器人停稳后，点击下方“机器人已停止，恢复控制”。' : activeId ? 'Agent 正在处理。可查看中间结果，或取消当前任务。' : selectedId ? '可以继续追问或补充指令，历史上下文会保留。' : '输入第一条消息，开始新的会话。', controlLocked);
  } catch (error) {
    connected = false; $('commandPanel').hidden = true; notice(error.message, true);
  } finally { controls(); }
}
$('taskForm').onsubmit = async event => {
  event.preventDefault(); if ($('start').disabled) return;
  const parentId = selectedId;
  revision++; submitting = true; edited = false; $('commandPanel').hidden = true; controls(); notice('正在发送消息…');
  try {
    const value = await api('/api/tasks', {method:'POST', body:JSON.stringify({instruction:$('instruction').value.trim(), parent_id:parentId})});
    selectedId = value.id; activeId = value.id; freshChat = false; $('instruction').value = ''; await refresh();
  } catch (error) { notice(error.message, true); }
  finally { submitting = false; controls(); }
};
$('cancel').onclick = async () => {
  if (!activeId) return;
  revision++; submitting = true; $('commandPanel').hidden = true; controls();
  try { await api('/api/tasks/' + encodeURIComponent(activeId) + '/cancel', {method:'POST'}); await refresh(); }
  catch (error) { notice(error.message, true); }
  finally { submitting = false; controls(); }
};
$('history').onchange = () => { revision++; selectedId = $('history').value || null; freshChat = false; edited = false; clearResult(); refresh(); };
$('recoverControl').onclick = async () => {
  if ($('recoverControl').disabled) return;
  if (!window.confirm('请确认：机器人已停止，其他 VLA / 遥操作控制程序已退出。\n恢复过程只读取关节反馈，不移动或复位机器人，也不会重跑旧任务。')) return;
  revision++; submitting = true; controls(); $('recoveryNotice').textContent = '正在检查关节反馈，请稍候…';
  try {
    const result = await api('/api/control/recover', {method:'POST', body:JSON.stringify({confirmed_stopped:true})});
    controlLocked = Boolean(result.control_locked); $('recoveryNotice').textContent = ''; await refresh(); notice(result.reason);
  } catch (error) { $('recoveryNotice').textContent = error.message; notice(error.message, true); }
  finally { submitting = false; controls(); }
};
$('newChat').onclick = () => { revision++; freshChat = true; selectedId = null; edited = false; $('instruction').value = ''; $('history').value = ''; clearResult(); notice('新会话。输入第一条消息开始。'); controls(); $('instruction').focus(); };
for (const id of ['instruction']) $(id).addEventListener('input', () => { edited = true; $('commandPanel').hidden = true; controls(); });
$('image').onerror = () => { $('image').hidden = true; $('empty').hidden = false; $('caption').textContent = '图像文件暂不可用，请检查任务记录。'; };
$('copy').onclick = async () => {
  try { await navigator.clipboard.writeText($('command').textContent); $('copy').textContent = '已复制'; }
  catch { const selection = window.getSelection(); const range = document.createRange(); range.selectNodeContents($('command')); selection.removeAllRanges(); selection.addRange(range); notice('请按 Ctrl+C 复制已选中的命令。'); }
};
const ikUrl = new URL(location.href); ikUrl.port = '18085'; ikUrl.pathname = '/'; ikUrl.search = ''; ikUrl.hash = ''; $('ikLink').href = ikUrl.href;
async function poll() { if (!submitting) await refresh(); setTimeout(poll, 1500); }
poll();
