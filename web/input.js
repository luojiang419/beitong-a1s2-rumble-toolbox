// 浏览器已识别为标准布局时，使用规范定义的 LT/RT，避免猜原始轴。
export function standardTriggers(pads) {
  const standard = Array.from(pads || []).filter(pad => pad?.connected && pad.mapping === 'standard');
  if (standard.length !== 1) return null;
  const value = index => {
    const number = Number(standard[0].buttons?.[index]?.value);
    return Number.isFinite(number) ? Math.max(0, Math.min(1, number)) : 0;
  };
  return {left: value(6), right: value(7)};
}

export function nativeXboxTriggers(state) {
  // 本机 Steam 的 SDL Mapping 已确认 045e/028e 的 LT=a2、RT=a5。
  if (state?.device?.vid !== '045E' || state.device.pid !== '028E' || state.axes?.length !== 6) return null;
  const pressure = index => {
    const value = state.axes[index];
    return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(1, (value + 1) / 2)) : 0;
  };
  return {left:pressure(2), right:pressure(5)};
}
