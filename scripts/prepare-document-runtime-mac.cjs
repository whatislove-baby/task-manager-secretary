// Copy an explicitly installed open-source LibreOffice runtime, including its
// licenses, into a relocatable resource directory. No machine-specific paths ship.
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
if(process.platform!=='darwin')throw Error('This helper prepares a Mac runtime.');
const root=path.resolve(__dirname,'..');
const source=path.resolve(process.env.TM_LIBREOFFICE_DIR||'/Applications/LibreOffice.app');
const executable=path.join(source,'Contents/MacOS/soffice');
if(!fs.existsSync(executable))throw Error('Install LibreOffice, or set TM_LIBREOFFICE_DIR to its .app directory.');
const result=spawnSync(executable,['--headless','--version'],{encoding:'utf8',timeout:30000});
if(result.status!==0)throw Error('LibreOffice runtime did not start.');
const target=path.join(root,'.libreoffice-runtime-mac');
fs.mkdirSync(target,{recursive:true});
const app=path.join(target,'LibreOffice.app');
const sourceReal=fs.realpathSync(source);
const appReal=fs.existsSync(app)?fs.realpathSync(app):path.join(fs.realpathSync(target),'LibreOffice.app');
if(sourceReal===appReal||sourceReal.startsWith(appReal+path.sep)||appReal.startsWith(sourceReal+path.sep))throw Error('Source and prepared runtime must not overlap. Nothing was removed.');
fs.rmSync(app,{recursive:true,force:true});
fs.cpSync(source,app,{recursive:true,verbatimSymlinks:true});
fs.writeFileSync(path.join(target,'runtime-manifest.json'),JSON.stringify({
  product:'LibreOffice',version:result.stdout.trim(),architecture:process.arch,
  upstream:'https://www.libreoffice.org/',licenseNotice:'Original license and notice files retained in LibreOffice.app/Contents/Resources.',
},null,2)+'\n');
console.log('DOCUMENT_RENDERER_READY',result.stdout.trim());
