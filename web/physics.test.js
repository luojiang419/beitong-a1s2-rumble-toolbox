import test from 'node:test';
import assert from 'node:assert/strict';
import { createCar, step, updateProgress, TRACK, OBSTACLES, TAU } from './physics.js';

function place(car, angle) {
  car.x = TRACK.cx + TRACK.a * Math.cos(angle);
  car.y = TRACK.cy + TRACK.b * Math.sin(angle);
  car.speed = 120;
  return updateProgress(car, true);
}
test('完整顺时针通过赛道才能计圈，原地和来回过线不计圈', () => {
  const car = createCar();
  for (let i = 0; i < 100; i++) place(car, 0);
  assert.equal(car.lap, 0);
  for (let i = 0; i < 100; i++) { place(car, .03); place(car, TAU - .03); }
  assert.equal(car.lap, 0);
  const racing = createCar(); racing.lapTime = 35;
  for (let a = .04; a < TAU + .08; a += .04) place(racing, a % TAU);
  assert.equal(racing.lap, 1); assert.equal(racing.lastLap, 35);
});
test('逆向一圈及跳过检查点不会计圈', () => {
  const car = createCar();
  for (let a = TAU - .04; a > -.08; a -= .04) place(car, (a + TAU) % TAU);
  assert.equal(car.lap, 0);
  for (const a of [2, 4, 6, .02]) place(car, a);
  assert.equal(car.lap, 0);
});
test('异常时间步长被限制且位置保持在场地范围', () => {
  const car = createCar();
  step(car, {throttle: 1}, 1000);
  assert.equal(car.time, .04);
  const before = car.time; step(car, {}, Number.NaN); step(car, {}, -1);
  assert.equal(car.time, before);
  for (let i = 0; i < 3000; i++) step(car, {throttle: 1, steer: 0}, .04);
  assert.ok(car.x >= 40 && car.x <= TRACK.width - 40);
  assert.ok(car.y >= 40 && car.y <= TRACK.height - 40);
});
test('持续重叠不连发碰撞事件，冷却结束后可再次触发', () => {
  const car = createCar(), obstacle = OBSTACLES[0];
  const collide = () => {
    car.x = obstacle.x; car.y = obstacle.y; car.speed = 0; car.vx = 0; car.vy = 0;
    return step(car, {}, .02).filter(event => event.type === 'collision').length;
  };
  assert.equal(collide(), 1);
  assert.equal(collide(), 0);
  for (let i = 0; i < 40; i++) step(car, {}, .02);
  assert.equal(collide(), 1);
});
