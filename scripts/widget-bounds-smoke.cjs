// 隐藏原生窗口回归：不运行业务页面、不读取用户数据、不截图或移动鼠标。
const { app, BrowserWindow, screen } = require('electron');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { widgetWindowStyle, widgetBounds, moveWidgetBounds } = require('../dist-backend/main/widget-policy');
const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(root, '.runtime-smoke-bounds-'));
console.log('SMOKE_TEMP_DIRECTORY=' + temporary);
app.setPath('userData', temporary); app.setPath('sessionData', temporary);
function nativeSize(window) {
  const buffer = window.getNativeWindowHandle();
  const handle = buffer.length === 8 ? buffer.readBigUInt64LE().toString() : buffer.readUInt32LE().toString();
  const ps = `Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class RectProbe { [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; } [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out Rect r); }'; $r=New-Object RectProbe+Rect; if(-not [RectProbe]::GetWindowRect([IntPtr]::new([long]${handle}),[ref]$r)){throw "GetWindowRect failed"}; @{width=($r.Right-$r.Left);height=($r.Bottom-$r.Top)}|ConvertTo-Json -Compress`;
  const result = spawnSync('powershell.exe', ['-NoProfile','-NonInteractive','-Command',ps], { encoding:'utf8', windowsHide:true });
  if(result.error || result.status !== 0) throw new Error(result.stderr || String(result.error));
  return JSON.parse(result.stdout.trim());
}
let window;
app.whenReady().then(async () => {
  const work = screen.getPrimaryDisplay().workArea;
  const bounds = widgetBounds({x:work.x+80,y:work.y+80,width:380,height:154}, work, false);
  window = new BrowserWindow({...widgetWindowStyle,...bounds,show:false,minWidth:bounds.width,maxWidth:bounds.width,minHeight:bounds.height,maxHeight:bounds.height,webPreferences:{sandbox:true,nodeIntegration:false,contextIsolation:true}});
  await window.loadURL('data:text/html,<html><body></body></html>');
  for(const expanded of [false,true,false]) {
    const target = widgetBounds(window.getBounds(), work, expanded);
    window.setMinimumSize(0,0); window.setMaximumSize(0,0); window.setBounds(target,false);
    window.setMinimumSize(target.width,target.height); window.setMaximumSize(target.width,target.height);
    const before = nativeSize(window);
    const logicalBefore = window.getBounds();
    assert.ok(Math.abs(logicalBefore.width-target.width)<=1 && Math.abs(logicalBefore.height-target.height)<=1, "初始尺寸偏离目标");
    for(let i=0;i<300;i++) {
      const canonical = widgetBounds(window.getBounds(),work,expanded);
      window.setBounds(moveWidgetBounds(canonical,work,i%2 ? -40 : 40,0),false);
      const observed = window.getBounds();
      assert.equal(observed.width,logicalBefore.width); assert.equal(observed.height,logicalBefore.height);
    }
    const after = nativeSize(window);
    assert.deepEqual(after,before,'Win32 HWND尺寸在连续拖动后改变');
    console.log(JSON.stringify({expanded,moves:300,logical:{width:target.width,height:target.height},nativeBefore:before,nativeAfter:after}));
  }
  console.log('WIDGET_BOUNDS_NATIVE_SMOKE=PASS'); app.exit(0);
}).catch(error => { console.error(error); app.exit(1); });
app.on('will-quit',()=>{ if(window && !window.isDestroyed())window.destroy(); });
