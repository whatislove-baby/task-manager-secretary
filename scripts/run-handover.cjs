const {spawnSync} = require('node:child_process');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const result = spawnSync(python, [path.join(root, 'scripts', 'run-handover.py')], {
  cwd: root, stdio: 'inherit', env: {...process.env, PYTHONUTF8: '1'},
});
if (result.error) console.error('请先按 README 安装项目 Python 环境。');
process.exit(result.status ?? 1);
