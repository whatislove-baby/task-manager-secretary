import type { BrowserWindow } from 'electron';
import { logWindowEvent } from './window-log';

type PageWindow = Pick<BrowserWindow, 'on' | 'once' | 'isDestroyed' | 'show' | 'destroy' | 'loadFile' | 'webContents'>;
type CloseRequest = { readonly defaultPrevented: boolean };

/**
 * 首次绘制之外的显示兜底：导航迟迟不提交时也必须让窗口出现，
 * 否则用户只能看到“进程活着但没有任何窗口”。
 */
export const PAGE_SHOW_FALLBACK_MS = 3000;

/**
 * 加载页面并在合适时机显示窗口。
 * - 正常路径：ready-to-show（无闪烁）；页面加载完成也可显示，不额外等待绘制。
 * - 兜底：PAGE_SHOW_FALLBACK_MS 后仍未显示则强制显示。
 * - 关闭语义分三层：已请求关闭且未被拦截 → 加载取消不是故障，不报错；
 *   关闭被拦截（隐藏到托盘）→ 真实加载故障仍报告，但页面就绪后不得把窗口弹回来；
 *   已销毁 → 任何回调都不再操作原生窗口。
 */
export function loadWindowPage(window: PageWindow, file: string, widget: boolean, reportFailure: () => void): Promise<void> {
  let closed = false;
  let closeRequest: CloseRequest | undefined;
  let shown = false;
  let reported = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const name = widget ? 'widget' : 'main';
  const clearTimer = () => { if (timer !== undefined) { clearTimeout(timer); timer = undefined; } };
  // 关闭请求已发出且没有任何监听器阻止：加载会被取消，这不是页面故障。
  const abandoned = () => closed || window.isDestroyed() || !!closeRequest && !closeRequest.defaultPrevented;
  // 关闭被拦截并隐藏（关闭到托盘）：页面就绪后不应再自行显示。
  const hiddenByUser = () => !!closeRequest && closeRequest.defaultPrevented;
  const available = () => !abandoned() && !hiddenByUser();
  const show = (reason: string) => {
    if (shown || !available()) return;
    shown = true;
    clearTimer();
    logWindowEvent('page:show', { name, reason });
    window.show();
  };
  const fail = (reason: string) => {
    if (reported || abandoned()) return;
    reported = true;
    clearTimer();
    logWindowEvent('page:failed', { name, reason });
    reportFailure();
    // 对话框期间也可能关闭应用，不再次销毁或干扰新建的悬浮窗。
    if (!abandoned()) window.destroy();
  };

  window.on('close', event => { closeRequest = event; logWindowEvent('page:close-request', { name }); });
  window.once('closed', () => { closed = true; clearTimer(); logWindowEvent('page:closed', { name }); });
  window.once('ready-to-show', () => { logWindowEvent('page:ready-to-show', { name }); show('ready-to-show'); });
  window.webContents.on('dom-ready', () => logWindowEvent('page:dom-ready', { name }));
  window.webContents.on('did-finish-load', () => { logWindowEvent('page:did-finish-load', { name }); show('did-finish-load'); });
  window.webContents.on('did-fail-load', (_event, code, description, url, isMainFrame) =>
    logWindowEvent('page:did-fail-load', { name, code, description, mainFrame: isMainFrame, file: file.slice(-48), url: String(url).slice(0, 80) }));
  window.webContents.on('render-process-gone', (_event, details) => logWindowEvent('page:render-process-gone', { name, ...details }));
  window.webContents.on('preload-error', (_event, preloadPath, error) =>
    logWindowEvent('page:preload-error', { name, preload: String(preloadPath).slice(-40), message: String(error instanceof Error ? error.message : error).slice(0, 200) }));

  timer = setTimeout(() => show('fallback-timeout'), PAGE_SHOW_FALLBACK_MS);
  // 不让诊断定时器阻止进程退出。
  if (typeof (timer as { unref?: () => void }).unref === 'function') (timer as { unref: () => void }).unref();

  return window.loadFile(file, widget ? { query: { widget: '1' } } : {}).then(
    () => { logWindowEvent('page:load-resolved', { name }); },
    () => {
      if (abandoned()) { logWindowEvent('page:load-cancelled', { name }); return; }
      fail('load-rejected');
    },
  );
}
