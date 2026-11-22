const {spawnSync}=require('node:child_process');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const python=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const result=spawnSync(python,[path.join(root,'scripts/prepare-python-runtime.py')],{cwd:root,stdio:'inherit',windowsHide:true,env:{...process.env,PYTHONUTF8:'1'}});
if(result.error)console.error('项目Python环境不可用，请先建立.venv。');
process.exit(result.status??1);
