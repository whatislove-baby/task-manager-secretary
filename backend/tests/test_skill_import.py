"""Imported code remains byte-exact; existing approval and execution are reused."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import json
import os
import unittest
import zipfile

from task_backend.mail_files import MailFiles
from task_backend.skill_library import SkillLibraryService
from task_backend.skill_import import read_package
from task_backend.skill_execution import get_status

SCOPE=dict(kind='personal',label='Imported conversion',emails=[],contextKey='import-example')
CODE=b'''import os,sys\nfrom pathlib import Path\nsys.path.insert(0, '/work/package')\nfrom app.convert import convert\nroot=Path(os.environ['TM_SKILL_INPUT_DIR'])\nout=Path(os.environ['TM_SKILL_OUTPUT_DIR'])\nvalue=(root/'input.txt').read_text().strip()\ntry: result=convert(value)\nexcept ValueError:\n    print('Missing amount',file=sys.stderr);sys.exit(2)\n(out/'result.txt').write_text(result)\n'''
MODULE=b'''from pathlib import Path\ndef convert(value):\n    if not value.isdigit(): raise ValueError('Missing amount')\n    prefix=(Path(__file__).parent.parent/'assets/prefix.txt').read_text()\n    return prefix+str(int(value)*2)+'\\n'\n'''
MARKDOWN=b'---\nname: imported-conversion\ndescription: A user-provided conversion package\n---\n\nPreserve the original files.\n'


def package(changes=None,mutate=None):
    manifest=dict(schemaVersion=1,title='Imported conversion',kind='script',entrypoint='main.py',
        runtimeFiles=['app/__init__.py','app/convert.py','assets/prefix.txt'],
        inputs=[dict(name='input.txt',description='Amount')],outputs=[dict(name='result.txt',description='Doubled amount',comparison='text')],examples=[
            dict(name='first',kind='success',inputFiles={'input.txt':'examples/a/input.txt'},expectedFiles={'result.txt':'examples/a/expected.txt'}),
            dict(name='second',kind='success',inputFiles={'input.txt':'examples/b/input.txt'},expectedFiles={'result.txt':'examples/b/expected.txt'}),
            dict(name='missing',kind='failure',inputFiles={'input.txt':'examples/bad/input.txt'},expectedError='Missing amount')])
    if mutate:mutate(manifest)
    files={'skill.json':json.dumps(manifest).encode(),'SKILL.md':MARKDOWN,'main.py':CODE,
        'app/__init__.py':b'','app/convert.py':MODULE,'assets/prefix.txt':b'Amount: ',
        'examples/a/input.txt':b'2','examples/a/expected.txt':b'Amount: 4\n',
        'examples/b/input.txt':b'7','examples/b/expected.txt':b'Amount: 14\n',
        'examples/bad/input.txt':b'invalid'}
    files.update(changes or {})
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():archive.writestr(name,data)
    return stream.getvalue(),files


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.service=SimpleNamespace(directory=Path(self.tmp.name));self.service.files=MailFiles(self.service)
        self.addCleanup(self.service.files.close);self.calls=[]
        def runner(source,inputs,*,package_files):
            self.calls.append((source,deepcopy(inputs),deepcopy(package_files)))
            self.assertEqual(source.encode(),CODE)
            self.assertEqual(package_files,{'app/__init__.py':b'','app/convert.py':MODULE,'assets/prefix.txt':b'Amount: '})
            value=inputs['input.txt'].decode()
            if not value.isdigit():return dict(ok=False,exitCode=2,outputs={},stdout='',stderr='Missing amount')
            return dict(ok=True,exitCode=0,outputs={'result.txt':('Amount: '+str(int(value)*2)+'\n').encode()},stdout='',stderr='')
        self.service.skills=SkillLibraryService(self.service,runner);self.addCleanup(self.service.skills.close)

    def upload(self,**kwargs):
        raw,files=package(**kwargs)
        return self.service.files._save('existing-skill.zip',raw,'local'),files

    def test_import_preserves_files_review_and_new_input_does_not_rerun_examples(self):
        source,files=self.upload();operation=str(uuid4())
        skill=self.service.skills.import_package(source['id'],SCOPE,'Keep replies brief.',proposal_id=operation)
        self.assertTrue(skill['canApprove']);self.assertFalse(skill['canRun']);self.assertEqual(len(self.calls),3)
        directory=self.service.directory/skill['packagePath']
        for name,data in files.items():self.assertEqual((directory/'imported'/name).read_bytes(),data)
        self.assertEqual((directory/'main.py').read_bytes(),CODE);self.assertEqual((directory/'SKILL.md').read_bytes(),MARKDOWN)
        metadata=skill['proposal']['importedPackage']
        self.assertEqual(metadata['sourceSha256'],source['sha256'])
        self.assertEqual(metadata['originalFiles']['app/convert.py'],hashlib.sha256(MODULE).hexdigest())
        self.assertIn('Keep replies brief.',skill['proposal']['instruction'])
        again=self.service.skills.import_package(source['id'],SCOPE,'Keep replies brief.',proposal_id=operation)
        self.assertEqual(again['id'],skill['id']);self.assertEqual(len(self.calls),3)
        fresh=self.service.files._save('new.txt',b'11','local')
        with self.assertRaises(ValueError):self.service.skills.run(skill['id'],{'input.txt':fresh['id']},skill['scopeId'])
        self.service.skills.approve(dict(id=skill['id'],expectedHash=skill['versionHash']))
        actual=self.service.skills.run(skill['id'],{'input.txt':fresh['id']},skill['scopeId'])
        self.assertEqual(len(self.calls),4)
        self.assertEqual(self.service.files.bytes(actual['files'][0]['id']),b'Amount: 22\n')
        self.assertNotIn('expected',str(list(self.calls[0][2])))

    def test_model_cannot_rewrite_an_imported_parent(self):
        source,_=self.upload();skill=self.service.skills.import_package(source['id'],SCOPE)
        value={k:skill['proposal'][k] for k in ('title','kind','scope','rationale','instruction','code','inputs','outputs','examples')}
        value.update(parentId=skill['id'],code='print("replacement")')
        with self.assertRaisesRegex(ValueError,'不能由模型改写'):self.service.skills.propose(value)
        self.assertEqual(len(self.calls),3)

    def test_rpc_is_strict_and_repeated_upload_reference_reuses_original_review(self):
        source,_=self.upload();args=dict(fileId=source['id'],scope=SCOPE)
        first=self.service.skills.call('importPackage',args)
        second=self.service.skills.call('importPackage',args)
        self.assertEqual(first['id'],second['id']);self.assertEqual(len(self.calls),3)
        with self.assertRaises(ValueError):self.service.skills.call('importPackage',dict(args,code='rewrite'))
        with self.assertRaises(ValueError):self.service.skills.call('importPackage',dict(args,approve=True))
        self.assertFalse(second['canRun'])

    def test_interrupted_import_finishes_existing_package_without_rerunning_code(self):
        from unittest.mock import patch
        source,_=self.upload()
        original=self.service.skills._finish_package
        def fail_after_journal(*args):raise OSError('simulated interruption')
        with patch.object(self.service.skills,'_finish_package',side_effect=fail_after_journal),self.assertRaises(OSError):
            self.service.skills.import_package(source['id'],SCOPE)
        self.assertEqual(len(self.calls),3)
        restored=self.service.skills.import_package(source['id'],SCOPE)
        self.assertTrue(restored['canApprove']);self.assertEqual(len(self.calls),3)
        self.assertEqual(self.service.skills.db.execute('SELECT count(*) FROM skill_package_journal').fetchone()[0],0)

    def test_insufficient_package_is_draft_and_cannot_be_approved(self):
        source,_=self.upload(mutate=lambda m:m.update(examples=m['examples'][:1]))
        skill=self.service.skills.import_package(source['id'],SCOPE)
        self.assertFalse(skill['canApprove']);self.assertEqual(self.calls,[])
        with self.assertRaises(ValueError):self.service.skills.approve(dict(id=skill['id'],expectedHash=skill['versionHash']))

    def test_expected_answers_cannot_be_runtime_resources(self):
        source,_=self.upload(changes={'assets/leaked.txt':b'Amount: 4\n'},mutate=lambda m:m['runtimeFiles'].append('assets/leaked.txt'))
        with self.assertRaisesRegex(ValueError,'完成样例'):self.service.skills.import_package(source['id'],SCOPE)
        self.assertEqual(self.calls,[])

    def test_invalid_manifest_unsafe_paths_and_generated_packages_rejected(self):
        for changes,mutate in [({},lambda m:m.update(entrypoint='setup.py')),({},lambda m:m.update(runtimeFiles=['../host'])),
                              ({'skill.json':b'{"schemaVersion":1,"schemaVersion":1}'},None),
                              ({'SKILL.md':b'no frontmatter'},None),({},lambda m:m.update(runtimeFiles=['examples/a/expected.txt']))]:
            with self.subTest(changes=list(changes)),self.assertRaises(ValueError):read_package(package(changes,mutate)[0])
        raw,_=package();file=self.service.files._save('model.zip',raw,'generated')
        with self.assertRaisesRegex(ValueError,'模型生成'):self.service.skills.import_package(file['id'],SCOPE)
        self.assertEqual(self.calls,[])

    def test_tampered_runtime_module_invalidates_reuse(self):
        source,_=self.upload();skill=self.service.skills.import_package(source['id'],SCOPE)
        self.service.skills.approve(dict(id=skill['id'],expectedHash=skill['versionHash']))
        (self.service.directory/skill['packagePath']/'imported/app/convert.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'校验'):self.service.skills.get(skill['id'])

    def test_approved_supplement_reaches_official_metadata_and_read_file_without_editing_package(self):
        import asyncio
        from deepagents import create_deep_agent
        from deepagents.middleware.skills import SkillsMiddleware
        from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
        from task_backend.deepagents_backend import make_backend
        from test_agent_runtime import ScriptedModel,tool_call
        class ImportReadModel(ScriptedModel):
            async def _agenerate(self,messages,stop=None,run_manager=None,**kwargs):
                return self._generate(messages,**kwargs)
        source,_=self.upload();supplement='Apply this package only to the monthly customer reconciliation.'
        skill=self.service.skills.import_package(source['id'],SCOPE,supplement)
        self.service.skills.approve(dict(id=skill['id'],expectedHash=skill['versionHash']))
        directory=self.service.directory/skill['packagePath']
        before={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in skill['proposal']['packageManifest']}
        backend=make_backend(self.service)
        middleware=SkillsMiddleware(backend=backend,sources=['/skills/approved/'])
        discovered=asyncio.run(middleware.abefore_agent({},None,{}))
        self.assertEqual(discovered['skills_load_errors'],[])
        self.assertEqual(len(discovered['skills_metadata']),1)
        self.assertIn(supplement,discovered['skills_metadata'][0]['description'])
        self.assertEqual(discovered['skills_metadata'][0]['metadata']['include_tools'],'get_skill run_skill')
        path='/skills/approved/'+skill['id']+'/SKILL.md'
        model=ImportReadModel(script=[tool_call('read_file',dict(file_path=path)),AIMessage(content='Read the approved supplementary rule.')])
        graph=create_deep_agent(model,backend=backend,skills=['/skills/approved/'])
        state=asyncio.run(graph.ainvoke({'messages':[HumanMessage(content='Read the registered skill instructions.')]}))
        reads=[m for m in state['messages'] if isinstance(m,ToolMessage) and m.name=='read_file']
        self.assertEqual(len(reads),1);self.assertIn(supplement,str(reads[0].content))
        self.assertIn('Approved import supplement',str(reads[0].content))
        self.assertEqual((directory/'SKILL.md').read_bytes(),MARKDOWN)
        self.assertEqual((directory/'imported/SKILL.md').read_bytes(),MARKDOWN)
        self.assertEqual({name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in before},before)
        self.assertEqual(self.service.skills.get(skill['id'])['versionHash'],skill['versionHash'])
        self.assertEqual(len(self.calls),3)


@unittest.skipUnless(os.environ.get('TM_RUN_SKILL_DOCKER_TESTS')=='1','Opt-in original package code in Docker')
class DockerImportTests(unittest.TestCase):
    def test_package_modules_assets_examples_and_reuse_in_real_docker(self):
        self.assertTrue(get_status()['available'],get_status())
        with TemporaryDirectory() as directory:
            service=SimpleNamespace(directory=Path(directory));service.files=MailFiles(service);self.addCleanup(service.files.close)
            service.skills=SkillLibraryService(service);self.addCleanup(service.skills.close)
            raw,files=package();source=service.files._save('existing.zip',raw,'local')
            skill=service.skills.import_package(source['id'],SCOPE)
            self.assertTrue(skill['canApprove'],skill['validation'])
            self.assertEqual([c['backend'] for c in skill['validation']['cases']],['docker']*3)
            service.skills.approve(dict(id=skill['id'],expectedHash=skill['versionHash']))
            fresh=service.files._save('new.txt',b'15','local')
            output=service.skills.run(skill['id'],{'input.txt':fresh['id']},skill['scopeId'])
            self.assertEqual(service.files.bytes(output['files'][0]['id']),b'Amount: 30\n')
            self.assertTrue(output['execution']['cleanupConfirmed'])


if __name__=='__main__':unittest.main()
