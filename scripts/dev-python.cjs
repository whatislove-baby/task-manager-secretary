const {spawn} = require('node:child_process');
const child = spawn(require('electron'), ['.'], {stdio:'inherit', windowsHide:true, env:{...process.env,TM_BACKEND:process.argv.includes('--legacy') ? 'typescript' : 'python'}});
child.on('error', () => {console.error('无法启动桌面应用。'); process.exitCode=1;});
child.on('exit', code => {process.exitCode=code ?? 1;});
