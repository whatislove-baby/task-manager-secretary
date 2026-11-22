const {spawnSync} = require('node:child_process');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const result = spawnSync(python, ['-m','unittest','discover','-s','tests','-v'], {cwd:path.join(root,'backend'),stdio:'inherit',windowsHide:true,env:{...process.env,PYTHONUTF8:'1'}});
if (result.error) console.error('项目 Python 环境不可用，请先创建 .venv 并安装 backend/requirements.txt。');
process.exit(result.status ?? 1);
