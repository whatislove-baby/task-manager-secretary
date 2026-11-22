export interface WidgetAPI {
  getState(): Promise<{ enabled: boolean }>;
  setEnabled(enabled: boolean): Promise<void>;
  setExpanded(expanded: boolean): Promise<void>;
  setPointerPassthrough?(ignore:boolean): Promise<void>;
  moveBy?(dx: number, dy: number): Promise<void>;
  minimizeToTray?(): Promise<void>;
  close(): Promise<void>;
  showMain(): Promise<void>;
  quit(): Promise<void>;
  onChanged(callback: () => void): () => void;
}
