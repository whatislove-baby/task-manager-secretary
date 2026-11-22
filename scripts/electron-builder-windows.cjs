// Pass this file's path to electron-builder: an object override would concatenate
// package.json's resource arrays and copy the same native DLLs concurrently.
const path = require('node:path');
const base = require('../package.json').build;
module.exports = {
  extends: null,
  appId: base.appId,
  productName: base.productName,
  files: structuredClone(base.files),
  directories: {output: path.resolve(__dirname, '..', process.env.TM_WINDOWS_OUTPUT || 'release-windows')},
  artifactName: 'Task-Manager-Windows-x64.${ext}',
  win: {
    target: 'zip',
    signAndEditExecutable: false,
    extraResources: [
      {from: '.python-runtime', to: 'python'},
      {from: '.libreoffice-runtime-windows', to: 'libreoffice'},
      {from: 'backend/task_backend', to: 'backend/task_backend', filter: ['**/*.py', 'assets/**/*']},
    ],
  },
  extraFiles: [
    {from: 'docs/WINDOWS.md', to: 'WINDOWS-START.md'},
    {from: 'docs/product-kit/user-manual.pdf', to: 'User-Manual.pdf'},
  ],
};
