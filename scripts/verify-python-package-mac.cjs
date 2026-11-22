// Tests the shipped bridge/runtime, including relocation, with temporary synthetic data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { Module } = require('node:module');
const { spawnSync } = require('node:child_process');
const { createHash } = require('node:crypto');
const asar = require('@electron/asar');
const root = path.resolve(__dirname, '..');
const output = path.resolve(root, process.env.TM_MAC_OUTPUT || 'release-mac');
const app = path.join(output, process.arch === 'arm64' ? 'mac-arm64' : 'mac', 'Task Manager.app');
const resources = path.join(app, 'Contents', 'Resources');
const archive = path.join(resources, 'app.asar');
const files = asar.listPackage(archive).map(value => value.split(path.sep).join('/'));
// Verify the renderer and privileged IPC bridge against this exact build, not
// merely a runnable older app left in another output directory.
const shippedAppHashes = {};
function verifyAppFile(name) {
  const source=fs.readFileSync(path.join(root,name));
  const shipped=asar.extractFile(archive,name);
  assert.deepEqual(shipped,source,'Packaged application differs from current build: '+name);
  shippedAppHashes[name]=createHash('sha256').update(shipped).digest('hex');
}
for (const name of ['main','preload','python-channels','python-desktop','python-backend']) verifyAppFile('dist-backend/main/'+name+'.js');
function verifyRenderer(directory) {
  for (const entry of fs.readdirSync(path.join(root,directory),{withFileTypes:true})) {
    const name=directory+'/'+entry.name;
    if(entry.isDirectory()) verifyRenderer(name); else if(entry.isFile()) verifyAppFile(name);
  }
}
verifyRenderer('dist-renderer');
for (const name of ['repository', 'service', 'imap-mailbox', 'outlook-auth', 'mail-operations', 'legacy-desktop']) {
  assert.equal(files.includes('/dist-backend/main/' + name + '.js'), false, 'Legacy backend shipped: ' + name);
}
const moduleObject = new Module(path.join(root, 'dist-backend/main/python-backend.js'), module);
moduleObject.filename = path.join(root, 'dist-backend/main/python-backend.js');
moduleObject._compile(asar.extractFile(archive, 'dist-backend/main/python-backend.js').toString('utf8'), moduleObject.filename);
const temporary = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'tm-mac-package-')));
const relocated = path.join(temporary, 'Relocated Runtime');
fs.mkdirSync(relocated);
for (const name of ['python', 'backend', 'libreoffice']) fs.cpSync(path.join(resources, name), path.join(relocated, name), {recursive: true, verbatimSymlinks: true});
const python = path.join(relocated, 'python', 'bin', 'python3');
const smoke = spawnSync(python, ['-B', '-I', '-c', 'import sys,sqlite3,ssl,icalendar,imapclient,cryptography; print(sys.prefix)'], {
  cwd: temporary, encoding: 'utf8', env: {PATH: '/usr/bin:/bin', HOME: os.homedir()},
});
assert.equal(smoke.status, 0, smoke.stderr);
assert.equal(smoke.stdout.trim(), path.join(relocated, 'python'));
const agentSmoke = spawnSync(python, ['-B', '-I', path.join(root, 'scripts', 'verify-runtime-deps.py'),
  '--requirements', path.join(root, 'backend', 'requirements-mac.txt'), '--smoke'], {
  cwd: temporary, encoding: 'utf8', env: {PATH: '/usr/bin:/bin', HOME: os.homedir()},
});
assert.equal(agentSmoke.status, 0, 'Relocated Agent runtime failed: ' + agentSmoke.stderr);
const agentReport = JSON.parse(agentSmoke.stdout);
assert.deepEqual(agentReport.agent, {toolLoop:true,sqliteReopened:true,openrouterAdapter:true,
  deepagentsFramework:true,filesystem:true,progressiveSkills:true,summaryOffload:true,
  rawHistoryPreserved:true,scratchCheckpoint:true,networkAccess:false});
const productionAgentSmoke = spawnSync(python, ['-B', '-I', path.join(root, 'scripts', 'verify-agent-backend-smoke.py'),
  '--backend-directory', path.join(relocated, 'backend')], {
  cwd: temporary, encoding: 'utf8', env: {PATH: '/usr/bin:/bin', HOME: os.homedir()},
});
assert.equal(productionAgentSmoke.status, 0, 'Relocated production Agent failed: ' + productionAgentSmoke.stderr);
assert.equal(JSON.parse(productionAgentSmoke.stdout).productionAgent, true);
const documentSmoke=spawnSync(python,['-B','-I',path.join(root,'scripts','verify-document-backend-smoke.py'),
  '--backend-directory',path.join(relocated,'backend')],{
  cwd:temporary,encoding:'utf8',timeout:120000,env:{PATH:'/usr/bin:/bin',HOME:os.homedir()},
});
assert.equal(documentSmoke.status,0,'Relocated document pipeline failed: '+documentSmoke.stderr);
assert.equal(JSON.parse(documentSmoke.stdout).nativeWordRenderer,true);
const { PythonBackend } = moduleObject.exports;
async function exercise(runtime, dataDirectory) {
  const executable = path.join(runtime, 'python', 'bin', 'python3');
  const bridge = new PythonBackend(dataDirectory, {python: executable, backendDirectory: path.join(runtime, 'backend')});
  try {
    const health = await bridge.call('health'); assert.equal(health.ok, true, health.error);
    assert.equal(fs.realpathSync(health.data.environment.executable), fs.realpathSync(executable));
    const created = await bridge.call('createTodo', {title: 'Mac packaged handover check'}); assert.equal(created.ok, true, created.error);
    const listed = await bridge.call('listTodos'); assert.equal(listed.ok, true, listed.error);
    assert.equal(listed.data[0].title, 'Mac packaged handover check');
    const compose = await bridge.call('mail-compose:listRecent');
    assert.equal(compose.ok, true, compose.error);
    assert.deepEqual(compose.data, []);
    const automation = await bridge.call('mail-automation:getConfig');
    assert.equal(automation.ok, true, automation.error);
    assert.equal(automation.data.enabled, false);
    assert.deepEqual(automation.data.times, ['09:00','18:00']);
    const automationItems = await bridge.call('mail-automation:listResults', {});
    assert.equal(automationItems.ok, true, automationItems.error);
    assert.equal(automationItems.data.total, 0);
    const conversations = await bridge.call('agent:listConversations');
    assert.equal(conversations.ok, true, conversations.error);
    assert.deepEqual(conversations.data, []);
    const pending = await bridge.call('agent:listPending');
    assert.equal(pending.ok, true, pending.error);
    assert.deepEqual(pending.data, []);
    const secretary = await bridge.call('agent:getSecretary');
    assert.equal(secretary.ok,true,secretary.error);assert.equal(secretary.data.kind,'secretary');
    const reopenedSecretary = await bridge.call('agent:getSecretary');
    assert.equal(reopenedSecretary.ok,true,reopenedSecretary.error);assert.equal(reopenedSecretary.data.id,secretary.data.id);
    const removeSecretary = await bridge.call('agent:deleteConversation',secretary.data.id);
    assert.equal(removeSecretary.ok,false,'Fixed secretary must survive deletion requests');
    const missingBriefing = await bridge.call('agent:getBriefing','00000000-0000-4000-8000-000000000000');
    assert.equal(missingBriefing.ok,false,'Missing briefing must not become an invented empty success');
    const invalidChild = await bridge.call('agent:createChild',{parentConversationId:'00000000-0000-4000-8000-000000000000',title:'Synthetic child check',goal:'Reject unknown parent without model execution'});
    assert.equal(invalidChild.ok,false,'Unknown parent must not queue a child task');
    for(const method of ['skill:list','learning:list','task-changes:list']) {
      const result=await bridge.call(method);
      assert.equal(result.ok,true,result.error);assert.deepEqual(result.data,[]);
    }
    for(const method of ['skill:approve','skill:disable','learning:approve','learning:disable','task-changes:approve','task-changes:reject']) {
      const result=await bridge.call(method,{id:'00000000-0000-4000-8000-000000000000',expectedHash:'a'.repeat(64)});
      assert.equal(result.ok,false,'Missing review proposal must not be accepted: '+method);
      assert.equal(typeof result.error,'string');
    }
    const missingRun = await bridge.call('agent:getRun', '00000000-0000-4000-8000-000000000000');
    assert.equal(missingRun.ok, false, 'Unknown Agent run must be a safe RPC error');
    assert.equal(typeof missingRun.error, 'string');
    const unknownAgent = await bridge.call('agent:executeShell', 'never execute');
    assert.equal(unknownAgent.ok, false, 'Unknown Agent capability must be rejected');
    for(const method of ['skill:propose','skill:run','skill:execute','skill:write']) {
      const result=await bridge.call(method,{});
      assert.equal(result.ok,false,'Skill mutation/execution must not be a renderer RPC: '+method);
    }
    const job = await bridge.call('mail-operations:startImport', {bindingIds: ['synthetic-missing-account'], onlyNew: true});
    assert.equal(job.ok, true, job.error);
    const end = Date.now() + 15000; let latest;
    do {
      latest = await bridge.call('mail-operations:getJob', job.data.id);
      if (latest.ok && ['completed', 'failed'].includes(latest.data.state)) break;
      await new Promise(done => setTimeout(done, 100));
    } while (Date.now() < end);
    assert.equal(latest.ok, true, latest.error); assert.equal(latest.data.state, 'completed');
    assert.equal(latest.data.report.failed, 1);
  } finally {await bridge.dispose();}
}
(async () => {
  try {
    await exercise(relocated, path.join(temporary, 'relocated-data'));
    // Also execute the original signed app's backend/worker. Writing pyc files
    // into its Resources would invalidate the signature, so verify after use.
    await exercise(resources, path.join(temporary, 'signed-app-data'));
    const signature = spawnSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', app], {encoding: 'utf8'});
    assert.equal(signature.status, 0, 'App signature changed after running its Python backend: ' + signature.stderr);
    const hashes = {};
    for (const name of ['secretary.py','secretary_tools.py','agent_runtime.py', 'agent_delivery.py', 'agent_fresh_context.py', 'agent_business_policy.py', 'file_archive.py', 'agent_tools.py', 'agent_openrouter.py', 'agent_worker.py', 'deepagents_backend.py','agent_evidence.py','draft_lifecycle.py','business_documents.py','service.py', '__main__.py','document_agent.py','document_assistant.py','document_completion.py','document_review.py','task_draft_state.py','skill_import.py','mail_files.py','document_formats.py','document_render.py','csv_documents.py','learning.py','learning_tools.py','learning_schema.py','skill_library.py','skill_tools.py','skill_schema.py','skill_execution.py','task_changes.py','bundle_files.py','spreadsheet_files.py','mail_identity.py','mail_readers.py','mail_store.py','mail_compose.py','assets/deepagents-skills/AGENTS.md','assets/deepagents-skills/documents/SKILL.md','assets/deepagents-skills/learning/SKILL.md','assets/deepagents-skills/mail-workflow/SKILL.md','assets/skills/documents.md','assets/skills/learning.md','assets/skills/skill-authoring.md','assets/skill_runtime/Dockerfile','assets/skill_runtime/requirements.txt','assets/skill_runtime/runner.py']) {
      const source = fs.readFileSync(path.join(root, 'backend', 'task_backend', name));
      const shipped = fs.readFileSync(path.join(resources, 'backend', 'task_backend', name));
      assert.deepEqual(shipped, source, 'Packaged backend differs from current source: ' + name);
      hashes[name] = createHash('sha256').update(shipped).digest('hex');
    }
    const evidence = path.resolve(root,process.env.TM_PACKAGE_EVIDENCE_OUTPUT||'handover/agent-runtime');
    fs.mkdirSync(evidence, {recursive:true});
    fs.writeFileSync(path.join(evidence, 'package-verification.json'), JSON.stringify({
      checkedAt: new Date().toISOString(), app, platform:process.platform, architecture:process.arch,
      dependencies:agentReport.dependencies, agent:agentReport.agent,
      productionAgent:JSON.parse(productionAgentSmoke.stdout), shippedSourceSha256:hashes,shippedAppSha256:shippedAppHashes,
      documents:JSON.parse(documentSmoke.stdout),
      bridgeRPC:true,reviewRPC:true,skillReviewRPC:true,skillExecutionRPCRejected:true,unknownReviewRejected:true, unknownAgentRPCRejected:true, signaturePreserved:true,
      relocated:true, realAccountsContacted:false, guiAcceptanceIncluded:false,
    }, null, 2) + '\n');
    console.log('MAC_PACKAGE_PASS: shipped bridge, relocated Python, Agent/tool loop, persisted checkpoints, SQLite, worker failure reporting, and preserved app signature. No external accounts contacted.');
  } finally {fs.rmSync(temporary, {recursive: true, force: true});}
})().catch(error => {console.error(error.message); process.exitCode = 1;});
