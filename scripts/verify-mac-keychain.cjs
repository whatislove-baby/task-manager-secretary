// Native Keychain smoke: only synthetic settings in a disposable directory.
// Fail closed without UI in this test. Normal app access prompts remain enabled.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
if (process.platform !== 'darwin') throw new Error('This smoke test requires macOS.');
const root = path.resolve(__dirname, '..');
const resources = path.join(root, 'release-mac', process.arch === 'arm64' ? 'mac-arm64' : 'mac',
  'Task Manager.app', 'Contents', 'Resources');
const python = path.join(resources, 'python', 'bin', 'python3');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'tm-native-keychain-'));
const common = 'import sys,ctypes,re; sys.path.insert(0,sys.argv[1]); from task_backend.settings import Settings; from task_backend.mac_keychain import framework; settings=Settings(sys.argv[2]); library=framework(); library.SecKeychainSetUserInteractionAllowed.argtypes=[ctypes.c_ubyte]; library.SecKeychainSetUserInteractionAllowed.restype=ctypes.c_int32; assert library.SecKeychainSetUserInteractionAllowed(False)==0;\n';
function native(code) {
  const checked = common + 'try:\n ' + code + '\nexcept Exception as error:\n status=re.search(r"状态 (-?\\d+)",str(error)); print("KEYCHAIN_STATUS="+status[1] if status else "NATIVE_FAILURE="+type(error).__name__,file=sys.stderr); sys.exit(1)\n';
  const result = spawnSync(python, ['-B', '-I', '-c', checked, path.join(resources, 'backend'), directory], {
    cwd: directory, encoding: 'utf8', timeout: 15000,
  });
  // Print only an allowlisted status, never Python output or credential data.
  if (result.error?.code === 'ETIMEDOUT') throw new Error('Native Keychain operation timed out.');
  const status = result.stderr.match(/KEYCHAIN_STATUS=(-?\d+)|NATIVE_FAILURE=(\w+)/);
  assert.equal(result.status, 0, 'Native Keychain operation failed (' + (status?.[0] || 'no diagnostic') + '). Unlock/authorize through the app and rerun.');
  assert.equal(result.stdout.trim(), 'PASS');
}
try {
  native('settings.save(dict(baseUrl="https://example.invalid/v1",model="synthetic",apiKey="handover-synthetic-only")); print("PASS")');
  assert.equal(fs.readFileSync(path.join(directory, 'settings.json'), 'utf8').includes('handover-synthetic-only'), false);
  native('assert settings.for_request()["apiKey"]=="handover-synthetic-only"; print("PASS")');
  const signature = spawnSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', path.resolve(resources, '../..')], {encoding: 'utf8'});
  assert.equal(signature.status, 0, 'App signature changed after native encryption smoke.');
  console.log('MAC_NATIVE_KEYCHAIN_PASS: packaged Python encrypted synthetic settings and a new process decrypted them; app signature preserved.');
} finally {fs.rmSync(directory, {recursive: true, force: true});}
