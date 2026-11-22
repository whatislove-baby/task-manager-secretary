import type { DesktopAPI } from '../shared/contracts';
export const mutationMethods = new Set<keyof DesktopAPI>([
  'createTodo', 'editTodo', 'updateTodo', 'moveTodo', 'setCompleted', 'trashTodo',
  'syncCanvas', 'restoreBatch', 'saveDrafts', 'setDayOrder', 'setCompletionConfig', 'saveSettings', 'clearKey', 'importBackup',
]);
export function rendererAllowed(url: string, base: string): boolean {
  return url === base || url === base + '?widget=1';
}
export interface Rect { x: number; y: number; width: number; height: number }
export function widgetBounds(current: Rect, workArea: Rect, expanded: boolean): Rect {
  const width = Math.min(380, workArea.width), height = Math.min(expanded ? 560 : 154, workArea.height);
  return { width, height, x: Math.max(workArea.x, Math.min(current.x, workArea.x + workArea.width - width)), y: Math.max(workArea.y, Math.min(current.y, workArea.y + workArea.height - height)) };
}

export function moveWidgetBounds(current: Rect, workArea: Rect, dx: unknown, dy: unknown): Rect {
  if (typeof dx !== 'number' || typeof dy !== 'number' || !Number.isFinite(dx) || !Number.isFinite(dy) || Math.abs(dx) > 10000 || Math.abs(dy) > 10000) throw new Error('无效窗口位移');
  return { ...current,
    x: Math.max(workArea.x, Math.min(Math.round(current.x + dx), workArea.x + Math.max(0, workArea.width - current.width))),
    y: Math.max(workArea.y, Math.min(Math.round(current.y + dy), workArea.y + Math.max(0, workArea.height - current.height))),
  };
}

/** 禁用Windows无边框窗口的隐藏厚边框，避免位置更新累计边框尺寸。 */
export const widgetWindowStyle = {
  transparent: true, backgroundColor: '#00000000', frame: false, thickFrame: false,
  roundedCorners: false, hasShadow: false, movable: true, resizable: false,
  maximizable: false, fullscreenable: false, skipTaskbar: true, alwaysOnTop: true,
} as const;
