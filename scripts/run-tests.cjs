// 统一测试入口：给 Node 测试进程足够堆空间，并让内存占用最高的悬浮窗组件测试单独运行。
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const tsx = require.resolve('tsx/cli');
const root = path.resolve(__dirname, '..');
const testsDir = path.join(root, 'tests');
const files = fs.readdirSync(testsDir).filter(name => /\.test\.(ts|tsx)$/.test(name)).map(name => 'tests/' + name).sort();
const heavy = files.filter(name => name.endsWith('widget-ui.test.tsx'));
const rest = files.filter(name => !heavy.includes(name));
const env = { ...process.env, NODE_OPTIONS: ((process.env.NODE_OPTIONS ?? '') + ' --max-old-space-size=8192').trim() };
function run(list) {
  if (!list.length) return 0;
  const result = spawnSync(process.execPath, [
    '--max-old-space-size=8192', tsx, '--test', '--test-concurrency=1', ...list,
  ], { cwd: root, stdio: 'inherit', windowsHide: true, env });
  if (result.error) throw result.error;
  return result.status ?? 1;
}
const first = run(rest);
if (first !== 0) process.exit(first);
process.exit(run(heavy));
