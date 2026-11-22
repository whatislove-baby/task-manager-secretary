import type { Due } from './todo';
export interface CanvasSource { description: string; url: string }
export interface CanvasItem extends CanvasSource { key: string; title: string; due: Due }
export interface CanvasConfig { configured: boolean; host: string }
export interface CanvasSyncReport {
  expired?: number;
  added: number; updated: number; unchanged: number; skipped: number;
  issues?: Array<{title: string; reason: string}>;
  changes: Array<{id: string; title: string; kind: 'added' | 'updated'; previousTitle?: string; previousDue?: Due; due: Due; descriptionChanged?: boolean; urlChanged?: boolean}>;
}
