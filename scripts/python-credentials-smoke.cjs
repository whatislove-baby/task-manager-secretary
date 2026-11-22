const {app,safeStorage}=require('electron');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),dir=fs.mkdtempSync(path.join(os.tmpdir(),'tm-python-vault-'));
app.setPath('userData',dir);
app.whenReady().then(async()=>{
  try {
    const cipher=await safeStorage.encryptStringAsync('synthetic-credential-only');
    console.log('SYNTHETIC_CIPHER_FORMAT='+cipher.subarray(0,8).toString('hex')+'; bytes='+cipher.length);
    fs.writeFileSync(path.join(dir,'settings.json'),JSON.stringify({baseUrl:'https://example.invalid/v1',model:'fixture',encryptedKey:cipher.toString('base64')}));
    const {PythonBackend}=require('../dist-backend/main/python-backend');
    const {migratePythonCredentials}=require('../dist-backend/main/python-desktop');
    const backend=new PythonBackend(dir);
    try {
      const result=await migratePythonCredentials(backend,async value=>(await safeStorage.decryptStringAsync(value)).result);
      assert.equal(result.ok,true,result.error);
      assert.deepEqual((await backend.call('_legacySecrets')).data,[]);
    } finally {await backend.dispose();}
    const code='from task_backend.settings import Settings; import sys; print(Settings(sys.argv[1]).for_request()["apiKey"])';
    const result=spawnSync(path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python'),['-c',code,dir],{cwd:path.join(root,'backend'),encoding:'utf8',windowsHide:true});
    assert.equal(result.status,0,result.stderr);
    assert.equal(result.stdout.trim(),'synthetic-credential-only');
    console.log('PYTHON_LEGACY_VAULT_PASS: synthetic key migrated, encrypted at rest and decrypted in a separate Python process');
    app.exit(0);
  } catch(error) {console.error('PYTHON_LEGACY_VAULT_FAIL:',error.message);app.exit(1);}
});
