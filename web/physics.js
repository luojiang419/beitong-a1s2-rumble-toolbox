export const TAU = Math.PI * 2;
export const TRACK = { cx: 800, cy: 550, a: 555, b: 335, half: 79, width: 1600, height: 1100 };
export const OBSTACLES = [.52, 2.27, 3.9, 5.3].map((angle, i) => ({
  x: TRACK.cx + (TRACK.a + (i % 2 ? -30 : 30)) * Math.cos(angle),
  y: TRACK.cy + (TRACK.b + (i % 2 ? -30 : 30)) * Math.sin(angle), radius: 20
}));
export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
export function trackPosition(x, y) {
  const nx = (x - TRACK.cx) / TRACK.a, ny = (y - TRACK.cy) / TRACK.b;
  return { radius: Math.hypot(nx, ny), angle: (Math.atan2(ny, nx) + TAU) % TAU };
}
export function createCar() {
  return { x: TRACK.cx + TRACK.a, y: TRACK.cy, heading: Math.PI / 2,
    speed: 0, vx: 0, vy: 0, steer: 0, time: 0, lapTime: 0, lap: 0,
    bestLap: null, lastLap: null, nextCheckpoint: 1, passed: 0, progress: 0,
    lastAngle: 0, collisionReady: 0, gravelReady: 0, skidReady: 0,
    surface: '沥青', slipping: false, distance: 0, collisions: 0 };
}
function resetProgress(car) {
  car.nextCheckpoint = 1; car.passed = 0; car.progress = 0;
}
export function updateProgress(car, onTrack) {
  const angle = trackPosition(car.x, car.y).angle;
  let delta = angle - car.lastAngle;
  if (delta > Math.PI) delta -= TAU;
  if (delta < -Math.PI) delta += TAU;
  const previous = car.lastAngle;
  car.lastAngle = angle;
  if (!onTrack || Math.abs(delta) > .25 || delta < -.01) {
    resetProgress(car); return false;
  }
  if (delta <= 0 || Math.abs(car.speed) < 5) return false;
  car.progress += delta;
  const oldSector = Math.floor(previous / (Math.PI / 2));
  const sector = Math.floor(angle / (Math.PI / 2));
  if (oldSector === sector) return false;
  if (sector !== car.nextCheckpoint) { resetProgress(car); return false; }
  if (sector === 0) {
    if (car.passed === 3 && car.progress > TAU - .15) {
      car.lap++; car.lastLap = car.lapTime;
      car.bestLap = car.bestLap === null ? car.lapTime : Math.min(car.bestLap, car.lapTime);
      car.lapTime = 0; resetProgress(car); return true;
    }
    resetProgress(car); return false;
  }
  car.passed++; car.nextCheckpoint = (sector + 1) % 4;
  return false;
}
export function step(car, control, dt) {
  dt = clamp(Number.isFinite(dt) ? dt : 0, 0, .04);
  if (!dt) return [];
  const events = [];
  car.time += dt; car.lapTime += dt;
  car.collisionReady = Math.max(0, car.collisionReady - dt);
  car.gravelReady = Math.max(0, car.gravelReady - dt);
  car.skidReady = Math.max(0, car.skidReady - dt);
  const before = trackPosition(car.x, car.y);
  const road = Math.abs(before.radius - 1) < .205;
  const gravel = !road || (before.angle > 1.17 && before.angle < 1.72 && before.radius > 1.07);
  car.surface = gravel ? '砂石路肩' : '沥青';
  const throttle = clamp(Number(control.throttle) || 0, 0, 1);
  const brake = clamp(Number(control.brake) || 0, 0, 1);
  const steering = clamp(Number(control.steer) || 0, -1, 1);
  car.steer += (steering - car.steer) * Math.min(1, dt * 10);
  const handbrake = Boolean(control.handbrake);
  const resistance = (gravel ? 1.4 : .22) + (handbrake ? 1.9 : 0);
  if (brake > 0 && car.speed > 3) car.speed = Math.max(0, car.speed - brake * 230 * dt);
  else if (brake > 0 && throttle === 0) car.speed -= brake * 80 * dt;
  car.speed += throttle * 170 * dt;
  car.speed *= Math.max(0, 1 - resistance * dt);
  car.speed = clamp(car.speed, -70, gravel ? 165 : 345);
  if (Math.abs(car.speed) < .3 && !throttle && !brake) car.speed = 0;
  const speedFactor = Math.min(Math.abs(car.speed) / 55, 1);
  car.heading += car.steer * (handbrake ? 2.7 : 1.85) * speedFactor * Math.sign(car.speed) * dt;
  car.slipping = Math.abs(car.speed) > 95 && (handbrake || (Math.abs(car.steer) > .72 && Math.abs(car.speed) > 225));
  const grip = car.slipping ? 2.2 : gravel ? 5 : 10;
  const desiredX = Math.cos(car.heading) * car.speed, desiredY = Math.sin(car.heading) * car.speed;
  car.vx += (desiredX - car.vx) * Math.min(1, dt * grip);
  car.vy += (desiredY - car.vy) * Math.min(1, dt * grip);
  car.x += car.vx * dt; car.y += car.vy * dt;
  car.distance += Math.hypot(car.vx, car.vy) * dt;
  const hit = intensity => {
    if (car.collisionReady) return;
    car.collisionReady = .75; car.collisions++;
    events.push({ type: 'collision', strength: clamp(intensity, .3, 1) });
  };
  for (const obstacle of OBSTACLES) {
    const dx = car.x - obstacle.x, dy = car.y - obstacle.y;
    const distance = Math.hypot(dx, dy), limit = obstacle.radius + 17;
    if (distance < limit) {
      const nx = distance > .001 ? dx / distance : Math.cos(car.heading + Math.PI);
      const ny = distance > .001 ? dy / distance : Math.sin(car.heading + Math.PI);
      hit(Math.abs(car.speed) / 260);
      car.x = obstacle.x + nx * limit; car.y = obstacle.y + ny * limit;
      car.speed *= -.2; car.vx *= -.25; car.vy *= -.25;
    }
  }
  const px = car.x, py = car.y;
  car.x = clamp(car.x, 40, TRACK.width - 40); car.y = clamp(car.y, 40, TRACK.height - 40);
  if (px !== car.x || py !== car.y) {
    hit(Math.abs(car.speed) / 250); car.speed *= -.22; car.vx *= -.2; car.vy *= -.2;
  }
  if (gravel && Math.abs(car.speed) > 25 && !car.gravelReady) {
    car.gravelReady = .18; events.push({ type: 'gravel', strength: Math.min(.6, Math.abs(car.speed) / 350) });
  }
  if (car.slipping && !car.skidReady) {
    car.skidReady = .2; events.push({ type: 'skid', strength: Math.min(.65, Math.abs(car.speed) / 400) });
  }
  if (updateProgress(car, Math.abs(trackPosition(car.x, car.y).radius - 1) < .23)) events.push({ type: 'lap', strength: 0 });
  return events;
}
