import { TRACK, OBSTACLES, TAU, clamp, createCar, step } from './physics.js';
import { standardTriggers, nativeXboxTriggers } from './input.js';

const $ = id => document.getElementById(id);
const canvas = $('track'), ctx = canvas.getContext('2d');
const defaults = { axis: 0, invert: false, throttle: 0, brake: 1, handbrake: 2 };
let mapping = { ...defaults };
try {
  const saved = JSON.parse(localStorage.getItem('midnight-mapping') || '{}');
  for (const key of ['axis', 'throttle', 'brake', 'handbrake']) if (Number.isInteger(saved[key]) && saved[key] >= 0 && saved[key] < 128) mapping[key] = saved[key];
  mapping.invert = saved.invert === true;
} catch { /* 本地存储受限时使用默认映射。 */ }
let car = createCar(), running = false, native = null, lastReceived = 0;
let everConnected = false, wasConnected = false, pollBusy = false, bridgeOK = false;
let currentControl = { steer: 0, throttle: 0, brake: 0, handbrake: false };
let binding = null, bindingTimer = null, lastButtons = [], axisCount = 0;
let shake = 0, eventCount = 0, toastUntil = 0, lastFeedbackSent = 0;
const eventTimes = {}, tireMarks = [], particles = [], keys = new Set();
let audio = null, engineOsc = null, engineGain = null;
const names = { collision: '碰撞反馈', gravel: '砂石路感', skid: '甩尾打滑', lap: '完成一圈' };
const hints = {
  collision: '橙色缓冲障碍分布在赛道两侧。低速靠近并碰撞，触发短促反馈。',
  gravel: '驶入棕色路肩或下方砂石测试区。速度越快，路感越明显。',
  skid: '先加速，再转向并按住空格 / 已绑定的手刹，触发甩尾。'
};

function saveMapping() {
  try { localStorage.setItem('midnight-mapping', JSON.stringify(mapping)); } catch { /* 可继续使用本次绑定。 */ }
  renderMapping();
}
function renderMapping() {
  $('steeringAxis').value = String(mapping.axis);
  $('invertSteering').checked = mapping.invert;
  for (const name of ['throttle', 'brake', 'handbrake']) $(name + 'Mapping').textContent = '按钮 ' + mapping[name];
}
function setToast(text, active = true, duration = 4200) {
  $('eventToast').textContent = text;
  $('eventToast').classList.toggle('active', active);
  toastUntil = performance.now() + duration;
}
function logEvent(title, detail, error = false) {
  const log = $('eventLog');
  log.querySelector('.empty-log')?.remove();
  const entry = document.createElement('div'); entry.className = 'log-entry' + (error ? ' error' : '');
  const content = document.createElement('span'); content.textContent = title;
  const note = document.createElement('small'); note.textContent = detail; content.append(note);
  const time = document.createElement('time'); time.textContent = new Date().toLocaleTimeString('zh-CN', {hour12: false, minute:'2-digit', second:'2-digit'});
  entry.append(content, time); log.prepend(entry);
  while (log.children.length > 8) log.lastElementChild.remove();
}
async function api(path, body) {
  const response = await fetch(path, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body || {}), signal: AbortSignal.timeout(1500) });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `服务返回 ${response.status}`);
  return payload;
}
function stopMotors() {
  fetch('/api/stop', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}', keepalive:true}).catch(() => {});
}
function pause(reason = '休息一下，再出发。', title = '试驾已暂停') {
  running = false; keys.clear(); currentControl = {steer:0,throttle:0,brake:0,handbrake:false};
  stopMotors(); $('startOverlay').classList.remove('hidden'); $('overlayTitle').textContent = title;
  $('overlayCopy').textContent = reason; $('overlayStart').innerHTML = '继续试驾 <span>→</span>';
  $('startPause').innerHTML = '继续试驾 <span>▶</span>'; $('runStatus').textContent = '已暂停'; $('runStatus').classList.remove('running');
  if (engineGain && audio) engineGain.gain.setTargetAtTime(0, audio.currentTime, .08);
}
function start() {
  if (document.hidden) return;
  running = true; $('startOverlay').classList.add('hidden');
  $('startPause').innerHTML = '暂停试驾 <span>Ⅱ</span>'; $('runStatus').textContent = '试驾进行中'; $('runStatus').classList.add('running');
  if ($('soundEnabled').checked) initializeAudio();
  setToast(everConnected ? '手柄与键盘均可驾驶 · 按 Esc 暂停' : '键盘驾驶已就绪 · 手柄连接后自动检测', false);
}
function reset() {
  car = createCar(); tireMarks.length = 0; particles.length = 0; shake = 0;
  stopMotors(); keys.clear(); setToast('已回到发车线。沿箭头方向驶过全部检查点。', false);
}

function renderDevice() {
  const connected = Boolean(native?.connected), device = native?.device;
  $('connectionBadge').className = 'badge ' + (connected ? 'connected' : bridgeOK ? 'muted' : 'error');
  $('connectionBadge').textContent = connected ? '已连接' : bridgeOK ? '未连接' : '服务离线';
  $('deviceName').textContent = connected ? (device?.name || '游戏控制器') : '键盘驾驶可用';
  $('deviceDetail').textContent = connected ? `USB / 本机原生输入 · ${device?.vid || '--'}:${device?.pid || '--'}` : 'WASD / 方向键 · 空格手刹';
  const supported = Boolean(device?.rumble || native?.vibration?.supported);
  $('rumbleNotice').classList.toggle('supported', connected && supported);
  $('rumbleNotice').textContent = !bridgeOK ? '本机输入服务离线，暂时不能测试实体马达。' : !connected ? '未检测到手柄。插入后可检查实际震动能力。' : supported ? '震动接口可用。请用「马达直测」确认实体震感。' : '当前模式不支持震动。按键可驾驶，画面事件仍正常反馈。';
  const count = native?.axes?.length || 0;
  if (count !== axisCount) {
    axisCount = count; $('steeringAxis').replaceChildren();
    for (let i = 0; i < Math.max(count, mapping.axis + 1, 1); i++) {
      const option = document.createElement('option'); option.value = String(i); option.textContent = '轴 ' + i; $('steeringAxis').append(option);
    }
    renderMapping();
  }
  $('bridgeStatus').textContent = bridgeOK ? '● 本机输入服务在线' : '○ 服务未连接 · 键盘模式';
  $('modeStatus').textContent = native?.xinput_slots?.length ? '已检测到 PC360 / XInput 接口，请继续用马达直测确认实际震感。' : '尚未检测到 XInput。当前模式能操作按键，也可能无法接收游戏震动。';
}

async function poll() {
  if (pollBusy) return;
  pollBusy = true;
  try {
    const response = await fetch('/api/state', {cache:'no-store', signal:AbortSignal.timeout(700)});
    if (!response.ok) throw new Error('输入状态读取失败');
    const next = await response.json();
    native = next; lastReceived = performance.now(); bridgeOK = true;
    const connected = Boolean(next.connected);
    if (wasConnected && !connected) {
      pause('手柄已断开。请重新连接后继续，或使用键盘驾驶。', '手柄连接已断开');
      logEvent('手柄已断开', '已暂停驾驶并发送马达停止指令。', true);
    }
    if (connected && !wasConnected) {
      everConnected = true;
      logEvent('手柄已连接', next.device?.rumble ? '震动接口可用，等待实体测试。' : '当前模式没有可用的标准震动输出。', !next.device?.rumble);
    }
    wasConnected = connected;
    const buttons = next.buttons || [];
    if (binding && connected) {
      const index = buttons.findIndex((v, i) => Number(v) > .5 && Number(lastButtons[i] || 0) <= .5);
      if (index >= 0) { mapping[binding] = index; const label = binding; endBinding(); saveMapping(); $('bindingHint').textContent = `${({throttle:'油门',brake:'刹车',handbrake:'手刹'})[label]}已绑定到按钮 ${index}`; }
    }
    lastButtons = [...buttons]; renderDevice();
  } catch {
    bridgeOK = false;
    if (wasConnected) { wasConnected = false; native = null; pause('本机输入服务暂时失联。恢复后可继续驾驶。', '输入连接中断'); }
    renderDevice();
  } finally { pollBusy = false; }
}

function readControls() {
  const fresh = bridgeOK && native?.connected && performance.now() - lastReceived <= 250;
  let axis = fresh ? Number(native.axes?.[mapping.axis] || 0) : 0;
  axis = Number.isFinite(axis) ? clamp(axis, -1, 1) : 0;
  axis = Math.abs(axis) > .12 ? Math.sign(axis) * (Math.abs(axis) - .12) / .88 : 0;
  if (mapping.invert) axis *= -1;
  const button = name => fresh ? clamp(Number(native.buttons?.[mapping[name]]) || 0, 0, 1) : 0;
  const left = keys.has('ArrowLeft') || keys.has('KeyA'), right = keys.has('ArrowRight') || keys.has('KeyD');
  let triggers = fresh && $('analogTriggers').checked ? native.triggers || nativeXboxTriggers(native) : null;
  if (!triggers && fresh && $('analogTriggers').checked && native.device?.vid === '045E') {
    try { triggers = standardTriggers(navigator.getGamepads?.()); } catch { /* 浏览器未授权时保留原生按钮驾驶。 */ }
  }
  const control = { steer: left || right ? Number(right) - Number(left) : axis,
    throttle: keys.has('ArrowUp') || keys.has('KeyW') ? 1 : Math.max(button('throttle'), triggers?.right || 0),
    brake: keys.has('ArrowDown') || keys.has('KeyS') ? 1 : Math.max(button('brake'), triggers?.left || 0),
    handbrake: keys.has('Space') || button('handbrake') > .5 };
  $('steerValue').textContent = control.steer.toFixed(2);
  $('axisFill').style.left = `${50 + Math.min(control.steer, 0) * 50}%`;
  $('axisFill').style.width = `${Math.abs(control.steer) * 50}%`;
  $('throttleValue').textContent = Math.round(control.throttle * 100) + '%'; $('brakeValue').textContent = Math.round(control.brake * 100) + '%';
  $('throttleFill').style.width = control.throttle * 100 + '%'; $('brakeFill').style.width = control.brake * 100 + '%';
  const pressed = fresh ? (native.buttons || []).flatMap((v, i) => Number(v) > .5 ? [i] : []) : [];
  $('pressedButtons').textContent = pressed.length ? '按下：' + pressed.map(i => 'B' + i).join(' · ') : '按钮：等待输入';
  if (wasConnected && !fresh && running) { pause('输入状态超过 250 ms 未刷新，已停止驾驶。', '输入状态暂时过期'); }
  return control;
}

function endBinding() {
  binding = null; clearTimeout(bindingTimer);
  document.querySelectorAll('[data-bind]').forEach(button => { button.classList.remove('waiting'); button.textContent = '绑定'; });
}
document.querySelectorAll('[data-bind]').forEach(button => button.addEventListener('click', () => {
  if (!native?.connected) { $('bindingHint').textContent = '请先连接手柄，再绑定按键。'; return; }
  if (running) pause('正在设置手柄映射。完成后点击继续试驾。', '驾驶映射设置');
  endBinding(); binding = button.dataset.bind; lastButtons = [...(native.buttons || [])];
  button.classList.add('waiting'); button.textContent = '按下…';
  $('bindingHint').textContent = '请松开已按下的按钮，再按一下要绑定的按钮（10 秒内）。';
  bindingTimer = setTimeout(() => { endBinding(); $('bindingHint').textContent = '绑定已超时，可再次点击绑定。'; }, 10000);
}));
$('steeringAxis').addEventListener('change', event => { mapping.axis = Number(event.target.value); saveMapping(); });
$('invertSteering').addEventListener('change', event => { mapping.invert = event.target.checked; saveMapping(); });
$('mappingReset').addEventListener('click', () => { endBinding(); mapping = {...defaults}; saveMapping(); $('bindingHint').textContent = '默认已恢复，可按实际手柄重新绑定。'; });

function initializeAudio() {
  try {
    if (!audio) {
      audio = new AudioContext(); engineOsc = audio.createOscillator(); engineGain = audio.createGain();
      engineOsc.type = 'sawtooth'; engineGain.gain.value = 0;
      const filter = audio.createBiquadFilter(); filter.type = 'lowpass'; filter.frequency.value = 500;
      engineOsc.connect(filter); filter.connect(engineGain); engineGain.connect(audio.destination); engineOsc.start();
    }
    audio.resume();
  } catch { $('soundEnabled').checked = false; }
}
function effectSound(type, strength) {
  if (!audio || !$('soundEnabled').checked) return;
  const oscillator = audio.createOscillator(), gain = audio.createGain(), now = audio.currentTime;
  oscillator.type = type === 'collision' ? 'square' : 'triangle';
  oscillator.frequency.setValueAtTime(type === 'collision' ? 80 : type === 'gravel' ? 130 : 250, now);
  oscillator.frequency.exponentialRampToValueAtTime(35, now + .12);
  gain.gain.setValueAtTime(.025 * strength, now); gain.gain.exponentialRampToValueAtTime(.0001, now + .15);
  oscillator.connect(gain); gain.connect(audio.destination); oscillator.start(now); oscillator.stop(now + .16);
}
$('soundEnabled').addEventListener('change', event => { if (event.target.checked) initializeAudio(); else if (engineGain && audio) engineGain.gain.setTargetAtTime(0, audio.currentTime, .04); });
$('feedbackEnabled').addEventListener('change', event => { if (!event.target.checked) stopMotors(); });

function processEvent(event) {
  eventCount++; $('eventCount').textContent = eventCount + ' 次事件';
  const now = performance.now();
  if (event.type === 'lap') {
    setToast(`第 ${car.lap} 圈完成 · ${formatTime(car.lastLap)} · 再试一次更顺畅的路线！`);
    logEvent('完成一圈', `用时 ${formatTime(car.lastLap)}，检查点全部通过。`); return;
  }
  if (event.type === 'collision') {
    shake = 7 * event.strength;
    for (let i = 0; i < 16; i++) particles.push({x:car.x,y:car.y,vx:(Math.random()-.5)*180,vy:(Math.random()-.5)*180,life:.5,color:'#ffac64'});
  } else if (event.type === 'gravel') shake = Math.max(shake, event.strength * 1.4);
  effectSound(event.type, event.strength);
  const supported = Boolean(native?.device?.rumble || native?.vibration?.supported);
  if (!eventTimes[event.type] || now - eventTimes[event.type] > 1300) {
    eventTimes[event.type] = now;
    const description = !$('feedbackEnabled').checked ? '游戏震动已关闭。' : supported ? '已触发游戏事件，正在请求实体马达。' : '画面事件已触发；当前手柄模式不支持物理震动。';
    setToast(`${names[event.type]} · ${description}`, true, 1700);
    logEvent(names[event.type], description, !supported);
  }
  if (!supported || !$('feedbackEnabled').checked || now - lastFeedbackSent < 95) return;
  lastFeedbackSent = now;
  const levels = event.type === 'collision' ? [event.strength, event.strength * .75, 210] : event.type === 'gravel' ? [event.strength * .35, event.strength * .75, 100] : [event.strength * .6, event.strength * .35, 150];
  api('/api/rumble', {event:event.type, low:levels[0], high:levels[1], duration_ms:levels[2]}).then(result => {
    if (!result.ok && !result.suppressed && (!eventTimes.outputError || now - eventTimes.outputError > 3000)) {
      eventTimes.outputError = now; logEvent('马达输出失败', result.error || '输出未获接受。', true);
    }
  }).catch(() => {});
}

document.querySelectorAll('[data-motor]').forEach(button => button.addEventListener('click', async () => {
  if (running) pause('正在独立测试马达。测试后可继续驾驶。', '马达直测');
  const motor = button.dataset.motor; button.disabled = true;
  $('motorResult').classList.remove('error'); $('motorResult').textContent = '正在请求马达…';
  try {
    const response = await api('/api/rumble', {event:'test',low:motor === 'right' ? 0 : .26,high:motor === 'left' ? 0 : .26,duration_ms:200});
    const ok = response.ok === true;
    $('motorResult').classList.toggle('error', !ok);
    $('motorResult').textContent = ok ? '命令已获接口接受。请用手确认震感；200 ms 后自动停止。' : `未能输出震动：${response.error || (response.suppressed ? '请求已合并，请稍后重试' : '接口拒绝')}`;
    logEvent(button.textContent + '测试', ok ? '接口已接受；实体是否震动需要人工确认。' : response.error || '请求未获接受。', !ok);
  } catch (error) { $('motorResult').classList.add('error'); $('motorResult').textContent = '本机服务请求失败：' + error.message; }
  finally { button.disabled = false; }
}));
$('stopRumble').addEventListener('click', async () => {
  try { await api('/api/stop'); $('motorResult').classList.remove('error'); $('motorResult').textContent = '已发送停止指令。'; }
  catch { $('motorResult').textContent = '服务暂时离线，请检查本机服务。'; }
});
document.querySelectorAll('[data-scene]').forEach(button => button.addEventListener('click', () => setToast(hints[button.dataset.scene], true, 6500)));
$('overlayStart').addEventListener('click', start);
$('startPause').addEventListener('click', () => running ? pause() : start()); $('reset').addEventListener('click', reset);
window.addEventListener('keydown', event => {
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || '')) return;
  if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Space','KeyW','KeyA','KeyS','KeyD','Escape'].includes(event.code)) event.preventDefault();
  if (event.code === 'Escape') { if (running) pause(); return; }
  if (running) keys.add(event.code);
});
window.addEventListener('keyup', event => keys.delete(event.code));
window.addEventListener('blur', () => { if (running) pause('窗口失去焦点，已暂停驾驶并停止马达。'); else { keys.clear(); stopMotors(); } });
document.addEventListener('visibilitychange', () => { if (document.hidden) pause('页面已转入后台。回到页面后点击继续试驾。'); });
window.addEventListener('pagehide', () => { keys.clear(); stopMotors(); });

function formatTime(value) {
  if (value === null || value === undefined) return '--:--.--';
  const hundredths = Math.floor(Math.max(0, value) * 100);
  return `${String(Math.floor(hundredths / 6000)).padStart(2,'0')}:${String(Math.floor(hundredths / 100) % 60).padStart(2,'0')}.${String(hundredths % 100).padStart(2,'0')}`;
}
const speedBars = [];
for (let i = 0; i < 20; i++) { const bar = document.createElement('i'); $('speedBars').append(bar); speedBars.push(bar); }

function ellipse(x, y, a, b, fill, stroke, width = 1) {
  ctx.beginPath(); ctx.ellipse(x,y,a,b,0,0,TAU); if(fill){ctx.fillStyle=fill;ctx.fill();} if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=width;ctx.stroke();}
}
function roundRect(x,y,w,h,r,fill) { ctx.beginPath(); ctx.roundRect(x,y,w,h,r); ctx.fillStyle=fill; ctx.fill(); }
function drawTrack() {
  const {cx,cy,a,b,half} = TRACK;
  ctx.fillStyle = '#101e28'; ctx.fillRect(0,0,TRACK.width,TRACK.height);
  ctx.save();
  for (let i = 0; i < 17; i++) ellipse(cx,cy,220+i*48,110+i*31,null,'#3c655412',1.3);
  for (let i = 0; i < 95; i++) {
    const x = ((i*691+132)%1500)+50, y = ((i*317+27)%1000)+50;
    const radius = Math.hypot((x-cx)/a,(y-cy)/b);
    if (radius > .65 && radius < 1.4) continue;
    ellipse(x,y,10+(i%4)*5,8+(i%4)*4,'#1e3a35'); ellipse(x-3,y-3,6+(i%3)*3,4+(i%3)*3,'#2b4e402c');
  }
  ellipse(cx,cy,a+half+13,b+half+13,'#314139');
  ellipse(cx,cy,a+half+6,b+half+6,'#d3d8cc');
  ellipse(cx,cy,a+half,b+half,'#36434e');
  ellipse(cx,cy,a-half,b-half,'#c2cbbb');
  ellipse(cx,cy,a-half-6,b-half-6,'#203a34');
  ctx.save(); ctx.setLineDash([22,22]); ellipse(cx,cy,a,b,null,'#bcc7c72d',3); ctx.restore();
  ellipse(cx,cy,a+half-10,b+half-10,null,'#c4d2d336',2);
  ellipse(cx,cy,a-half+10,b-half+10,null,'#c4d2d336',2);
  ctx.save();
  ctx.beginPath(); ctx.ellipse(cx,cy,a+half-4,b+half-4,0,1.17,1.72); ctx.ellipse(cx,cy,a+32,b+32,0,1.72,1.17,true);ctx.closePath();ctx.fillStyle='#796447';ctx.fill();ctx.clip();
  for(let i=0;i<360;i++){ctx.fillStyle=i%2?'#b79b7040':'#221c182f';ctx.fillRect(cx-300+(i*97)%700,cy+240+(i*53)%200,3,3);}ctx.restore();
  // 内场与跑道标识。
  ctx.save();ctx.translate(cx,cy);ctx.textAlign='center';ctx.fillStyle='#6a938449';ctx.font='600 17px Segoe UI';ctx.letterSpacing='5px';ctx.fillText('MIDNIGHT',0,-17);ctx.font='300 58px Segoe UI';ctx.letterSpacing='12px';ctx.fillStyle='#84bba13b';ctx.fillText('CIRCUIT 01',0,43);ctx.letterSpacing='0px';ctx.font='11px Microsoft YaHei';ctx.fillStyle='#81a49365';ctx.fillText('沿箭头方向行驶 · 全部检查点通过后计圈',0,81);ctx.restore();
  // 发车线跨过右侧车道。
  for(let x=cx+a-half+5;x<cx+a+half-5;x+=12)for(let y=cy-9;y<cy+9;y+=9){ctx.fillStyle=((Math.floor(x/12)+Math.floor(y/9))%2)?'#e4e6df':'#263943';ctx.fillRect(x,y,12,9);}
  ctx.fillStyle='#7da899';ctx.font='10px Segoe UI';ctx.fillText('START / FINISH',cx+a-45,cy-25);
  for(let i=0;i<14;i++){
    const angle=(i+.5)/14*TAU, x=cx+a*Math.cos(angle), y=cy+b*Math.sin(angle), heading=Math.atan2(b*Math.cos(angle),-a*Math.sin(angle));
    ctx.save();ctx.translate(x,y);ctx.rotate(heading);ctx.beginPath();ctx.moveTo(-8,-8);ctx.lineTo(2,0);ctx.lineTo(-8,8);ctx.strokeStyle='#b6cbcb2f';ctx.lineWidth=3;ctx.stroke();ctx.restore();
  }
  for(let i=1;i<=3;i++){
    const angle=i*Math.PI/2, x=cx+a*Math.cos(angle),y=cy+b*Math.sin(angle);
    ctx.save();ctx.translate(x,y);ctx.rotate(Math.atan2(b*Math.cos(angle),-a*Math.sin(angle)));ctx.fillStyle=(car.passed>=i?'#64edc4':'#6a8b89')+'48';ctx.fillRect(-3,-half+8,6,(half-8)*2);ctx.restore();
    ctx.fillStyle=car.passed>=i?'#64edc4':'#6c9188';ctx.font='10px Segoe UI';ctx.textAlign='center';ctx.fillText(`CP 0${i}`,cx+(a-half-37)*Math.cos(angle),cy+(b-half-30)*Math.sin(angle)+3);
  }
  ctx.textAlign='left';
  for(const obstacle of OBSTACLES){
    ellipse(obstacle.x+4,obstacle.y+7,22,19,'#060d1366');
    roundRect(obstacle.x-19,obstacle.y-19,38,38,8,'#a85129');roundRect(obstacle.x-17,obstacle.y-18,34,31,7,'#ff9d5d');
    ctx.save();ctx.beginPath();ctx.roundRect(obstacle.x-17,obstacle.y-18,34,31,7);ctx.clip();ctx.strokeStyle='#34352c';ctx.lineWidth=6;for(let n=-2;n<4;n++){ctx.beginPath();ctx.moveTo(obstacle.x-35+n*17,obstacle.y+22);ctx.lineTo(obstacle.x-2+n*17,obstacle.y-25);ctx.stroke();}ctx.restore();
  }
  // 两组建筑/护栏形成场地尺度。
  for(const [x,y] of [[190,105],[1180,910]]){
    roundRect(x+7,y+10,215,57,5,'#060d1355');roundRect(x,y,215,57,5,'#273c47');
    for(let i=0;i<7;i++){ctx.fillStyle=i%3===0?'#deb98666':'#76b3b933';ctx.fillRect(x+12+i*29,y+11,19,6);}
    ctx.fillStyle='#769397';ctx.font='8px Segoe UI';ctx.fillText('MIDNIGHT / PIT LANE',x+20,y+39);
  }
  ctx.restore();
}
function drawCar() {
  ctx.save();ctx.translate(car.x,car.y);ctx.rotate(car.heading);
  ctx.fillStyle='#cfefd50b';ctx.beginPath();ctx.moveTo(18,-10);ctx.lineTo(110,-37);ctx.lineTo(110,37);ctx.lineTo(18,10);ctx.fill();
  roundRect(-22+3,-14+5,45,29,8,'#030c1199');
  for(const x of [-15,10])for(const y of [-15,10])roundRect(x,y,11,6,2,'#0a1016');
  roundRect(-23,-12,46,24,7,'#3aac94');roundRect(-19,-11,40,21,5,'#6ee9c1');
  ctx.fillStyle='#b9ffe054';ctx.fillRect(-15,-9,4,17);
  roundRect(-6,-9,15,18,4,'#173a40');ctx.fillStyle='#b1ebde33';ctx.fillRect(3,-7,4,14);
  roundRect(-22,-10,4,20,1,'#1e4d47');
  ctx.fillStyle= currentControl.brake ? '#ff695d' : '#ff9d5d';ctx.fillRect(-23,-9,2,5);ctx.fillRect(-23,4,2,5);
  ctx.fillStyle='#f7ffe7';ctx.fillRect(20,-9,3,5);ctx.fillRect(20,4,3,5);
  ctx.restore();
}
function render(dt) {
  const rect=canvas.getBoundingClientRect(), dpr=Math.min(window.devicePixelRatio||1,2);
  if(canvas.width!==Math.round(rect.width*dpr)||canvas.height!==Math.round(rect.height*dpr)){canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);}
  ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,rect.width,rect.height);
  const scale=Math.min(rect.width/TRACK.width,rect.height/TRACK.height);
  ctx.translate((rect.width-TRACK.width*scale)/2,(rect.height-TRACK.height*scale)/2);ctx.scale(scale,scale);
  if(shake>0){ctx.translate((Math.random()-.5)*shake,(Math.random()-.5)*shake);shake*=.84;}
  drawTrack();
  for(const mark of tireMarks){ctx.strokeStyle=`rgba(7,15,20,${mark.life*.25})`;ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(mark.x,mark.y);ctx.lineTo(mark.x+mark.dx,mark.y+mark.dy);ctx.stroke();mark.life-=dt*.045;}
  while(tireMarks.length>1000||tireMarks[0]?.life<0)tireMarks.shift();
  for(let i=particles.length-1;i>=0;i--){const p=particles[i];p.x+=p.vx*dt;p.y+=p.vy*dt;p.life-=dt;ctx.globalAlpha=Math.max(0,p.life*2);ctx.fillStyle=p.color;ctx.fillRect(p.x,p.y,3,3);if(p.life<=0)particles.splice(i,1);}ctx.globalAlpha=1;
  drawCar();
  $('speed').textContent=Math.round(Math.abs(car.speed)*.72);$('surface').textContent=car.surface;
  $('lap').innerHTML=`${String(car.lap+1).padStart(2,'0')} <i>/ 自由试驾</i>`;$('lapTime').textContent=formatTime(car.lapTime);$('bestLap').textContent='最佳 '+formatTime(car.bestLap);
  speedBars.forEach((bar,i)=>{bar.classList.toggle('on',i<Math.abs(car.speed)/345*20);bar.classList.toggle('high',i>15);});
  if(performance.now()>toastUntil){$('eventToast').classList.remove('active');$('eventToast').textContent=running?'沿箭头方向完成一圈 · 空格手刹 / Esc 暂停':'准备出发 · 可使用手柄或键盘';}
}
let lastFrame=performance.now(), accumulator=0;
function frame(now) {
  const elapsed=clamp((now-lastFrame)/1000,0,.08);lastFrame=now;
  currentControl=readControls();
  if(running){
    accumulator+=elapsed;
    while(accumulator>=1/100){
      const events=step(car,currentControl,1/100);accumulator-=1/100;events.forEach(processEvent);
      if(car.slipping){for(const side of [-1,1])tireMarks.push({x:car.x-Math.cos(car.heading)*16-Math.sin(car.heading)*side*11,y:car.y-Math.sin(car.heading)*16+Math.cos(car.heading)*side*11,dx:-car.vx*.015,dy:-car.vy*.015,life:1});}
    }
  }else accumulator=0;
  if(engineOsc&&engineGain&&audio){engineOsc.frequency.setTargetAtTime(35+Math.abs(car.speed)*.34,audio.currentTime,.06);engineGain.gain.setTargetAtTime(running && $('soundEnabled').checked ? .012 + currentControl.throttle*.005 : 0,audio.currentTime,.06);}
  render(elapsed);requestAnimationFrame(frame);
}
renderMapping();poll();setInterval(poll,50);requestAnimationFrame(frame);
