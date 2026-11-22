import React, { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { CanvasSource as Source } from '../shared/canvas';
import type { Result } from '../shared/contracts';
const unwrap = <T,>(result: Result<T>): T => { if (!result.ok) throw new Error(result.error); return result.data; };
const message = (error: unknown) => (error instanceof Error ? error.message : '操作失败，请重试。').replace(/(?:https?|webcal):\/\/[^\s]+/gi, '［地址］');

function Reader({title, source, onClose, openOriginal}: {title:string;source:Source;onClose():void;openOriginal():Promise<void>}) {
  const overlay = useRef<HTMLDivElement>(null), panel = useRef<HTMLElement>(null);
  const heading = useId();
  const [error, setError] = useState('');
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const siblings = Array.from(document.body.children).filter(element => element !== overlay.current) as HTMLElement[];
    const states = siblings.map(element => element.inert);
    siblings.forEach(element => { element.inert = true; });
    panel.current?.focus();
    const scope = () => Array.from(panel.current?.querySelectorAll<HTMLElement>('*') ?? []).filter(node => node.tagName === 'BUTTON' ? !(node as HTMLButtonElement).disabled : node.tabIndex === 0);
    function key(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
      if (event.key === 'Tab') {
        const nodes = scope(), first = nodes[0], last = nodes[nodes.length - 1];
        if (event.shiftKey && (document.activeElement === first || !nodes.includes(document.activeElement as HTMLElement))) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !nodes.includes(document.activeElement as HTMLElement))) { event.preventDefault(); first?.focus(); }
      }
    }
    function focus(event: FocusEvent) { if (panel.current && !panel.current.contains(event.target as Node)) panel.current.focus(); }
    document.addEventListener('keydown', key, true); document.addEventListener('focusin', focus);
    return () => { document.removeEventListener('keydown', key, true); document.removeEventListener('focusin', focus); siblings.forEach((element, i) => { element.inert = states[i]!; }); if (previous?.isConnected) previous.focus(); };
  }, [onClose]);
  return createPortal(<div className="canvas-reader-overlay" ref={overlay} onPointerDown={event => event.stopPropagation()} onContextMenu={event => event.stopPropagation()}>
    <section className="canvas-reader" ref={panel} role="dialog" aria-modal="true" aria-labelledby={heading} tabIndex={-1}>
      <header><div><span className="canvas-reader-eyebrow">CANVAS · 事项说明</span><h2 id={heading}>{title}</h2></div><button aria-label="关闭事项说明" onClick={onClose}>×</button></header>
      <div className="canvas-reader-body" tabIndex={0} aria-label="完整Canvas说明">{source.description || 'Canvas 未提供说明。'}</div>
      <footer><span>本地保存 · {source.description.length.toLocaleString('zh-CN')} 字符</span><div><button onClick={() => { void (async () => { try { await navigator.clipboard.writeText(source.description); setError(''); } catch { setError('复制失败，请直接选择说明文字复制。'); } })(); }}>复制全文</button>{source.url && <button onClick={() => { void openOriginal().catch(error => setError(message(error))); }}>在 Canvas 中打开 ↗</button>}</div>{error && <p role="alert">{error}</p>}</footer>
    </section>
  </div>, document.body);
}
export function CanvasSource({todoId, title, revision}: {todoId:string;title:string;revision:number}) {
  const [snapshot, setSnapshot] = useState<{id:string;source:Source | null} | null>(null);
  const [error, setError] = useState(''), [reading, setReading] = useState(false);
  useEffect(() => {
    let mounted = true, version = 0;
    setSnapshot(null); setReading(false); setError('');
    if (!window.desktop.getCanvasSource) return;
    async function refresh() {
      const request = ++version;
      try { const source = unwrap(await window.desktop.getCanvasSource(todoId)); if (mounted && request === version) { setSnapshot({id:todoId,source}); setError(''); } }
      catch (error) { if (mounted && request === version) setError(message(error)); }
    }
    void refresh(); const off = window.widget?.onChanged(() => { void refresh(); });
    return () => { mounted = false; off?.(); };
  }, [todoId, revision]);
  const source = snapshot?.id === todoId ? snapshot.source : null;
  async function openOriginal() { unwrap(await window.desktop.openCanvasOriginal(todoId)); }
  const close = React.useCallback(() => setReading(false), []);
  if (!source && !error) return null;
  return <section className="canvas-source-card" aria-label="Canvas来源说明">
    <span className="canvas-source-label">Canvas 说明</span>
    {error && <p role="alert">{error}</p>}
    {source && <><div className="canvas-source-buttons"><button onClick={() => setReading(true)}>查看说明 · {source.description.length.toLocaleString('zh-CN')} 字符</button>{source.url && <button onClick={() => { void openOriginal().catch(error => setError(message(error))); }}>在 Canvas 中打开 ↗</button>}</div>{reading && <Reader title={title} source={source} onClose={close} openOriginal={openOriginal}/>}</>}
  </section>;
}
