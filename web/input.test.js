import test from 'node:test';
import assert from 'node:assert/strict';
import {standardTriggers,nativeXboxTriggers} from './input.js';

test('标准手柄读取独立模拟扳机，拒绝非标准或多设备歧义', () => {
  const pad = {connected:true, mapping:'standard', buttons:Array.from({length:8},()=>({value:0}))};
  pad.buttons[6].value=.3; pad.buttons[7].value=.8;
  assert.deepEqual(standardTriggers([null,pad]),{left:.3,right:.8});
  assert.equal(standardTriggers([{...pad,mapping:''}]),null);
  assert.equal(standardTriggers([pad,pad]),null);
  assert.equal(standardTriggers([{...pad,connected:false}]),null);
});

test('扳机异常值不会导致自动油门', () => {
  const pad={connected:true,mapping:'standard',buttons:Array.from({length:8},()=>({value:0}))};
  pad.buttons[6].value=NaN; pad.buttons[7].value=Infinity;
  assert.deepEqual(standardTriggers([pad]),{left:0,right:0});
});

test('精确的原生Xbox布局把扳机静止负轴归零，不应用到未知手柄', () => {
  const state={device:{vid:'045E',pid:'028E'},axes:[0,0,-1,0,0,0.5]};
  assert.deepEqual(nativeXboxTriggers(state),{left:0,right:.75});
  assert.equal(nativeXboxTriggers({...state,device:{vid:'20BC',pid:'505B'}}),null);
  assert.deepEqual(nativeXboxTriggers({...state,axes:[0,0,NaN,0,0,Infinity]}),{left:0,right:0});
});
