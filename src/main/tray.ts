import { Menu, Tray, nativeImage } from 'electron';

export interface AppTrayOptions {
  iconPath: string;
  showMain(): void;
  toggleWidget(): void;
  isWidgetEnabled(): boolean;
  quit(): void;
}
/** Tray callbacks delegate lifecycle to main; quitting never bypasses beforeunload. */
export function createAppTray(options: AppTrayOptions) {
  const source = nativeImage.createFromPath(options.iconPath);
  if (source.isEmpty()) throw new Error('找不到应用托盘图标。');
  const tray = new Tray(source.resize({ width: 20, height: 20 }));
  function refresh() {
    if (tray.isDestroyed()) return;
    tray.setToolTip('Task Manager');
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: '打开任务界面', click: options.showMain },
      { label: options.isWidgetEnabled() ? '关闭悬浮窗' : '开启悬浮窗', click: () => { options.toggleWidget(); refresh(); } },
      { type: 'separator' },
      { label: '退出应用', click: options.quit },
    ]));
  }
  tray.on('click', options.showMain);
  tray.on('double-click', options.showMain);
  tray.on('right-click', refresh);
  refresh();
  return {
    refresh,
    destroy() { if (!tray.isDestroyed()) { tray.removeAllListeners(); tray.destroy(); } },
  };
}
