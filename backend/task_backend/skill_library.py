"""User-reviewed Markdown/code skill packages with independent example gates.

Reuses the existing immutable review lifecycle. Code executes only through the
isolated runner; model-written tests never decide whether a skill is publishable.
"""
from __future__ import annotations
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from uuid import uuid4, uuid5, NAMESPACE_URL

from .learning import (LearningService, compact, digest, identifier, normalize_scope,
                       shape, string, teaching_from_conversation)
from .mail_identity import address
from .mail_files import extract_document, TYPES, MAX_FILES
from .skill_schema import SkillProposal, SkillPackageImport
from .spreadsheet_files import read_table, workbook_signature, workbook_difference
from .tree import instant

MAX_PACKAGE_BYTES=64*1024*1024
MAX_RUN_OUTPUT_BYTES=24*1024*1024
MAX_PENDING_RUN_BYTES=128*1024*1024


def sha(data):return hashlib.sha256(data).hexdigest()


def compare_output(actual, expected, name, mode):
    if mode=='workbook':
        if Path(name).suffix.lower()!='.xlsx':raise ValueError('workbook 比对要求 XLSX 输出')
        return workbook_signature(actual)==workbook_signature(expected)
    if mode=='bytes':return actual==expected
    if mode=='text':
        return actual.decode('utf-8-sig').replace('\r\n','\n')==expected.decode('utf-8-sig').replace('\r\n','\n')
    ext=Path(name).suffix.lower().lstrip('.')
    if ext not in ('csv','xlsx'):raise ValueError('table 比对只支持 CSV/XLSX；其他文件须选择 text 或 bytes')
    left=read_table(actual,ext);right=read_table(expected,ext)
    return (left['columns'],left['rows'])==(right['columns'],right['rows'])


def output_difference(actual,expected,name,mode):
    """Bounded application-computed diagnostics, never model self-grading."""
    prefix=name+'：'
    if mode=='workbook':return prefix+workbook_difference(actual,expected)
    if mode=='bytes':return prefix+'文件字节不同（实际 '+str(len(actual))+'，样例 '+str(len(expected))+' bytes）'
    if mode=='text':
        left=actual.decode('utf-8-sig').replace('\r\n','\n').split('\n');right=expected.decode('utf-8-sig').replace('\r\n','\n').split('\n')
        for index in range(max(len(left),len(right))):
            a=left[index] if index<len(left) else '<missing>';b=right[index] if index<len(right) else '<missing>'
            if a!=b:return prefix+f'第 {index+1} 行实际 {a[:160]!r}；样例 {b[:160]!r}'
    else:
        ext=Path(name).suffix.lower().lstrip('.');left=read_table(actual,ext);right=read_table(expected,ext)
        if left['columns']!=right['columns']:return prefix+'列名实际 '+repr(left['columns'])[:200]+'；样例 '+repr(right['columns'])[:200]
        if len(left['rows'])!=len(right['rows']):return prefix+f"行数实际 {len(left['rows'])}；样例 {len(right['rows'])}"
        for row,(a,b) in enumerate(zip(left['rows'],right['rows'])):
            for col,(x,y) in enumerate(zip(a,b)):
                if x!=y:return prefix+f'第 {row+1} 行 / {left["columns"][col]} 实际 {str(x)[:160]!r}；样例 {str(y)[:160]!r}'
    return prefix+'内容不同'


class SkillLibraryService(LearningService):
    def call(self,method,*args):
        if method=='importPackage':
            if len(args)!=1:raise ValueError('技能包导入需要一个参数对象')
            value=SkillPackageImport.model_validate(args[0])
            return self.import_package(value.fileId,value.scope.model_dump(),value.instruction,value.parentId)
        return super().call(method,*args)

    def __init__(self,service,runner=None):
        self.root=Path(service.directory)/'skill-library'
        if self.root.is_symlink():raise ValueError('技能库目录不能是符号链接')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.packages=self.root/'packages'
        if self.packages.is_symlink():raise ValueError('技能包目录不能是符号链接')
        self.packages.mkdir(exist_ok=True,mode=0o700)
        super().__init__(SimpleNamespace(directory=self.root))
        self.db.execute('CREATE TABLE IF NOT EXISTS skill_package_journal(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,record TEXT NOT NULL)')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS skill_run_journal(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS skill_run_outputs(run_id TEXT NOT NULL,name TEXT NOT NULL,sha256 TEXT NOT NULL,data BLOB NOT NULL,PRIMARY KEY(run_id,name));
        ''')
        self.service=service
        if runner is None:
            from .skill_execution import run_python
            runner=run_python
        self.runner=runner

    def _file_mails(self,file_id,seen=None):
        record=self.service.files.get(file_id)
        skill=record.get('skill')
        if not skill:return super()._file_mails(file_id,seen)
        seen=set() if seen is None else seen
        if file_id in seen:return set()
        if len(seen)>=60:raise ValueError('文件来源链过长')
        seen.add(file_id);self.service.files.bytes(file_id)
        ids=skill.get('inputIds',[])
        if not ids or not set(ids)<=set(record.get('parentIds',[])):raise ValueError('技能输出的输入来源链不完整')
        result=set()
        for item in ids:result.update(self._file_mails(item,seen))
        return result

    def _verified_reference(self,file_id,*,lineage=None):
        verified=set();active=set()
        def visit(id,depth=0):
            if id in active:raise ValueError('独立完成样例的来源链存在循环')
            if id in verified:return
            if depth>=60 or len(verified)+len(active)>=60:raise ValueError('独立完成样例的来源链过深或过长')
            record=self.service.files.get(id);self.service.files.bytes(id)
            if lineage is not None:lineage[id]=record['sha256']
            if record.get('source')=='generated':
                raise ValueError('完成样例及其祖先须由用户上传或来自邮件；不能用模型生成文件作为自己的正确答案')
            if record.get('source') not in ('local','mail','archive'):raise ValueError('独立完成样例缺少可信的导入来源')
            parents=record.get('parentIds',[]);origin=record.get('archiveOrigin',{})
            if not isinstance(parents,list) or not isinstance(origin,dict):raise ValueError('独立完成样例的来源链无效')
            ancestors=set(parents)
            for key in ('archiveId','rootArchiveId'):
                if origin.get(key):ancestors.add(origin[key])
            if record.get('source')=='archive' and not ancestors:raise ValueError('压缩包完成样例缺少原包来源')
            active.add(id)
            for parent in ancestors:visit(parent,depth+1)
            active.remove(id);verified.add(id)
        visit(file_id)
        return self.service.files.get(file_id)

    def _input_bytes(self,files,contract,scope,source_mail_id=''):
        if not isinstance(files,dict) or set(files)!={v['name'] for v in contract}:raise ValueError('输入文件名须与技能 inputs 契约完全一致')
        result={};snapshots=[]
        for name,file_id in files.items():
            record=self.service.files.get(file_id)
            mails=self._file_scope(file_id,scope,source_mail_id)
            data=self.service.files.bytes(file_id)
            result[name]=data
            snapshots.append(dict(name=name,id=file_id,sha256=sha(data),sourceMailIds=mails))
        if sum(len(v) for v in result.values())>MAX_PACKAGE_BYTES:raise ValueError('技能输入超过 64 MiB')
        return result,snapshots

    def _check_package(self,item):
        directory=self.packages/item['id']
        self._check_directory(directory,item['proposal']['packageManifest'])
        return directory

    def _check_directory(self,directory,manifest):
        if directory.is_symlink() or not directory.is_dir():raise ValueError('技能包缺失或目录被替换')
        actual=set()
        for path in directory.rglob('*'):
            if path.is_symlink():raise ValueError('技能包不能包含符号链接')
            if path.is_file():actual.add(path.relative_to(directory).as_posix())
        if actual!=set(manifest):raise ValueError('技能包文件清单已变化，请提出新的版本')
        for name,wanted in manifest.items():
            data=(directory/name).read_bytes()
            if sha(data)!=wanted:raise ValueError('技能包内容校验失败，请提出新的版本')

    def _finish_package(self,vid,signature):
        """Recover rename-before-commit using an already durable draft journal.

        Do not rerun model code or replace a package after a process death. A
        reviewBase captured before the crash stays unchanged, so later approval
        cannot silently supersede a newer review in another window.
        """
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT request_hash,record FROM skill_package_journal WHERE id=?',(vid,)).fetchone()
            if not row or row['request_hash']!=signature:raise ValueError('技能包恢复日志与调用不匹配')
            journal=json.loads(row['record']);proposal=journal['proposal'];validation=journal['validation']
            target=self.packages/vid;staging=self.packages/journal['staging']
            if staging.parent!=self.packages or not staging.name.startswith('.staging-'):raise ValueError('技能包恢复路径无效')
            if not target.exists():
                self._check_directory(staging,proposal['packageManifest'])
                os.rename(staging,target)
            self._check_directory(target,proposal['packageManifest'])
            number=self.db.execute('SELECT COALESCE(MAX(number),0)+1 FROM learning_versions WHERE scope_id=? AND title=?',(journal['scopeId'],journal['title'])).fetchone()[0]
            self.db.execute('INSERT INTO learning_versions VALUES(?,?,?,?,?,?,?,?,?,?)',(vid,journal['scopeId'],journal['title'],number,journal['parentId'],journal['versionHash'],compact(proposal),compact(validation),journal['createdAt'],signature))
            self.db.execute('INSERT INTO learning_reviews VALUES(?,?,NULL)',(vid,'draft'))
            self.db.execute('DELETE FROM skill_package_journal WHERE id=?',(vid,))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            # Preserve the journal and verified files for an explicit replay.
            raise
        if staging.exists():shutil.rmtree(staging)
        return self.get(vid)

    def get(self,id):
        item=super().get(id)
        self._check_package(item)
        item['kind']='skill' if item['status']=='approved' else 'skill_draft'
        item['validation']=item['preview']
        item['canApprove']=item['status']=='draft' and bool(item['validation']['passed'])
        item['canRun']=item['status']=='approved' and item['proposal']['kind']!='memory'
        item['packagePath']='skill-library/packages/'+item['id']
        return item

    def propose(self,value,proposal_id=None,*,teaching=None):
        return self._propose(value,proposal_id,teaching=teaching)

    def import_package(self,file_id,scope,instruction='',parent_id=None,proposal_id=None,*,teaching=None):
        from .skill_import import import_package
        return import_package(self,file_id,scope,instruction,parent_id,proposal_id,teaching=teaching)

    def _propose(self,value,proposal_id=None,*,teaching=None,imported=None):
        value=SkillProposal.model_validate(value).model_dump(exclude_none=True)
        if len(compact(value))>150000:raise ValueError('技能提议过大')
        if teaching is None:teaching=teaching_from_conversation(self.service)
        elif teaching.get('source')=='conversation':
            turns=teaching.get('turns',[])
            if not turns:raise ValueError('教学原文缺失')
            wanted=teaching_from_conversation(self.service,teaching.get('conversationId'),teaching.get('runId'),through_turn_id=turns[-1]['id'])
            if teaching!=wanted:raise ValueError('教学来源与真实用户原文不一致')
        elif teaching!=teaching_from_conversation(self.service):raise ValueError('教学来源不能伪造')
        request=dict(value=value,teaching=teaching)
        if imported is not None:request['importedPackage']=imported['metadata']
        signature=digest(request)
        vid=identifier(proposal_id) if proposal_id else str(uuid4())
        existing=self.db.execute('SELECT request_hash FROM learning_versions WHERE id=?',(vid,)).fetchone()
        if existing:
            if existing[0]!=signature:raise ValueError('技能调用标识已用于其他内容')
            return self.get(vid)
        pending=self.db.execute('SELECT request_hash FROM skill_package_journal WHERE id=?',(vid,)).fetchone()
        if pending:
            if pending[0]!=signature:raise ValueError('技能恢复调用标识已用于其他内容')
            return self._finish_package(vid,signature)
        scope_id,scope=normalize_scope(value['scope']);title=string(value['title'],200)
        parent_id=value.get('parentId')
        if parent_id:
            parent=self.get(parent_id)
            if parent['scopeId']!=scope_id or parent['title']!=title:raise ValueError('技能修订不能改变客户范围或标题')
            if parent['proposal'].get('importedPackage') and imported is None:
                raise ValueError('这是用户导入的原代码技能；请提供新的 Skill ZIP 修订，不能由模型改写源码')
        elif self.db.execute('SELECT 1 FROM learning_versions WHERE scope_id=? AND title=?',(scope_id,title)).fetchone():
            raise ValueError('此范围已有同名技能；请先 get_skill 读取明确的父版本，再用 {"value":{"parentId":"该版本 UUID",...}} 修订。未自动选择父版本或覆盖旧稿。')
        outputs={v['name']:v for v in value['outputs']}
        for output in outputs.values():
            extension=Path(output['name']).suffix.lower().lstrip('.')
            if extension not in TYPES:raise ValueError('输出格式须为现有受管文件支持的 PDF、DOCX、CSV/XLSX、TXT/MD、图片或 ZIP')
            if output['comparison']=='table' and extension not in ('csv','xlsx'):raise ValueError('table 比对要求 CSV/XLSX 输出')
            if output['comparison']=='workbook' and extension!='xlsx':raise ValueError('workbook 比对要求 XLSX 输出')
        expected_ids=set();input_ids=set();sample_files={};prepared=[];oracle_lineage={}
        for index,case in enumerate(value['examples']):
            data,snapshots=self._input_bytes(case['inputFiles'],value['inputs'],scope)
            input_ids.update(case['inputFiles'].values())
            expected={}
            if case['kind']=='success':
                if set(case['expectedFiles'])!=set(outputs):raise ValueError('成功样例须为每个声明的输出提供独立完成样例')
                if case.get('expectedError'):raise ValueError('成功样例不能有 expectedError')
                for name,fid in case['expectedFiles'].items():
                    rec=self._verified_reference(fid,lineage=oracle_lineage)
                    if Path(rec['name']).suffix.lower()!=Path(name).suffix.lower():raise ValueError('完成样例扩展名须与输出契约一致')
                    expected[name]=self.service.files.bytes(fid);expected_ids.add(fid)
            else:
                if case['expectedFiles'] or not case.get('expectedError'):raise ValueError('失败样例须给具体 expectedError，不能同时给输出文件')
            for name,blob in data.items():sample_files[f'examples/{index}/input/{name}']=blob
            for name,blob in expected.items():sample_files[f'examples/{index}/expected/{name}']=blob
            prepared.append((case,data,expected,snapshots))
        # The isolated process must never receive an oracle as an input, even
        # under another filename/file ID or in a different example. Pure copies
        # need the existing file tools, not a falsely self-certified new script.
        if input_ids & expected_ids:raise ValueError('独立完成样例不能同时作为任何测试输入；请提供能区分转换行为的输入与答案')
        input_hashes={sha(blob) for _,inputs,_,_ in prepared for blob in inputs.values()}
        expected_hashes={sha(blob) for _,_,expected,_ in prepared for blob in expected.values()}
        if input_hashes & expected_hashes:raise ValueError('测试输入包含独立完成样例的相同字节；换文件名或重新导入不能作为独立验证')
        # A ZIP containing the expected member would disclose the answer too.
        # Extracted input/expected siblings remain valid: neither receives the
        # parent ZIP or the other's bytes inside the isolated execution.
        if input_ids & set(oracle_lineage) or input_hashes & set(oracle_lineage.values()):
            raise ValueError('测试输入包含完成样例的来源容器或相同副本；请分别展开原始输入与答案，不把含答案的压缩包交给脚本')
        reference_mails=set()
        for fid in expected_ids-input_ids:reference_mails.update(self._file_mails(fid))
        source_values=list(value['sources'])
        source_values += [dict(kind='file',id=fid) for fid in sorted(input_ids|expected_ids)]
        sources=[];seen=set()
        for source in source_values:
            key=(source['kind'],source['id'])
            if key in seen:continue
            seen.add(key)
            if source['kind']=='file':
                rec=self.service.files.get(source['id']);data=self.service.files.bytes(rec['id'])
                reference=rec['id'] in expected_ids-input_ids
                if not reference:self._file_scope(rec['id'],scope)
                snapshot=dict(kind='file',id=rec['id'],name=rec['name'],sha256=sha(data))
                if reference:snapshot.update(role='reference-output',mailSources=[self._mail(mid,None) for mid in sorted(self._file_mails(rec['id']))])
            else:
                reference=source['id'] in reference_mails
                snapshot=self._mail(source['id'],None if reference else scope)
                if reference:snapshot['role']='reference-output'
            sources.append(snapshot)
        trial_files={}
        validation=self._validate(value,prepared,trial_files,package_files=imported['resources'] if imported is not None else None)
        slug=vid
        markdown='---\nname: '+slug+'\ndescription: '+json.dumps(title,ensure_ascii=False)+'\n---\n\n'+value['instruction'].strip()+'\n'
        if value['kind']!='memory':
            markdown+='\n## Execution contract\n\nRun `python main.py` in the isolated skill runner. Read inputs from `TM_SKILL_INPUT_DIR`; write declared outputs to `TM_SKILL_OUTPUT_DIR`. Invalid business input must exit 2 before producing files.\n'
        if imported is not None:markdown=imported['markdown']
        blobs={'SKILL.md':markdown.encode(),'teaching.json':compact(teaching).encode(),
               'tests/cases.json':compact(value['examples']).encode(),'tests/results.json':compact(validation).encode(),**sample_files,**trial_files}
        if value.get('code'):blobs['main.py']=value['code'].encode()
        if imported is not None:
            blobs.update({'imported/'+name:data for name,data in imported['members'].items()})
            blobs['SKILL.md']=imported['members']['SKILL.md']
            blobs['main.py']=imported['members']['main.py']
        blobs['manifest.json']=compact(dict(schemaVersion=1,id=vid,title=title,kind=value['kind'],scope=scope,
            inputContract=value['inputs'],outputContract=value['outputs'],entrypoint='main.py' if value.get('code') else None,
            files={name:sha(data) for name,data in blobs.items()})).encode()
        if sum(len(v) for v in blobs.values())>MAX_PACKAGE_BYTES:raise ValueError('技能包及样例超过 64 MiB')
        proposal=dict(value,scope=scope,sources=sources,teaching=teaching,markdown=markdown,
            contract=dict(inputs=value['inputs'],outputs=value['outputs'],invocation='TM_SKILL_INPUT_DIR / TM_SKILL_OUTPUT_DIR'),
            packageManifest={name:sha(data) for name,data in blobs.items()})
        if imported is not None:proposal['importedPackage']=imported['metadata']
        target=self.packages/vid
        if target.exists():raise ValueError('技能包目录已存在；未覆盖任何文件')
        temp=Path(tempfile.mkdtemp(prefix='.staging-',dir=self.packages))
        journaled=False
        try:
            for name,data in blobs.items():
                path=temp/name;path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('wb') as stream:
                    stream.write(data);stream.flush();os.fsync(stream.fileno())
            self.db.execute('BEGIN IMMEDIATE')
            try:
                proposal['reviewBase']=self._revision_base(scope_id,title,parent_id)
                version_hash=digest(dict(proposal=proposal,preview=validation))
                journal=dict(proposal=proposal,validation=validation,scopeId=scope_id,title=title,parentId=parent_id,versionHash=version_hash,createdAt=instant(),staging=temp.name)
                self.db.execute('INSERT INTO skill_package_journal VALUES(?,?,?)',(vid,signature,compact(journal)))
                self.db.execute('COMMIT')
                journaled=True
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
            return self._finish_package(vid,signature)
        finally:
            if not journaled and temp.exists():shutil.rmtree(temp)

    def _validate(self,value,prepared,trial_files,package_files=None):
        if value['kind']=='memory':return dict(passed=True,status='manual_review_required',summary='Markdown 已保存；文字经验须由用户核对，未声称业务语义自动验证。',cases=[],normalExamples=0,failureExamples=0)
        successes=[p for p in prepared if p[0]['kind']=='success'];failures=[p for p in prepared if p[0]['kind']=='failure']
        signatures=[digest({k:sha(v) for k,v in p[1].items()}) for p in prepared]
        sufficient=len(successes)>=2 and len(failures)>=1 and len(signatures)==len(set(signatures))
        if not sufficient:return dict(passed=False,status='needs_examples',summary='正式启用脚本至少需要 2 份不同正常输入及独立完成样例，加 1 份不同的失败输入。',cases=[],normalExamples=len(successes),failureExamples=len(failures))
        cases=[]
        for index,(case,inputs,expected,snapshots) in enumerate(prepared):
            try:
                result=(self.runner(value['code'],inputs,package_files=package_files) if package_files is not None else self.runner(value['code'],inputs))
                produced=result.get('outputs',{})
                for name,data in produced.items():
                    if name in {v['name'] for v in value['outputs']}:
                        trial_files[f'tests/{index}/actual/{name}']=data
                if case['kind']=='failure':
                    passed=(result.get('exitCode')==2 and not produced and case['expectedError'] in result.get('stderr','') and not result.get('error'))
                    detail='按约定拒绝错误输入，未产生输出。' if passed else '失败输入未按约定拒绝；普通崩溃、超时或残留文件不能算通过。'
                else:
                    passed=bool(result.get('ok')) and set(produced)==set(expected)
                    mismatched=[]
                    if passed:
                        for spec in value['outputs']:
                            name=spec['name']
                            extract_document(produced[name],Path(name).suffix.lower().lstrip('.'))
                            if not compare_output(produced[name],expected[name],name,spec['comparison']):mismatched.append(output_difference(produced[name],expected[name],name,spec['comparison']))
                        passed=not mismatched
                    detail='完整输出与用户提供的独立样例一致。' if passed else ('输出与样例不一致：'+', '.join(mismatched) if mismatched else '脚本运行未成功或输出文件集合与契约不同。')
                cases.append(dict(name=case['name'],kind=case['kind'],passed=passed,detail=detail,exitCode=result.get('exitCode'),backend=result.get('backend'),stdout=result.get('stdout','')[:3000],stderr=result.get('stderr','')[:3000],runnerError=result.get('error'),inputs=snapshots,outputHashes={k:sha(v) for k,v in produced.items()},expectedHashes={k:sha(v) for k,v in expected.items()}))
                if result.get('error') and result.get('exitCode') is None:
                    # A missing runtime or timed-out container is not repaired
                    # by repeating the same infrastructure call ten times.
                    cases.extend(dict(name=rest[0]['name'],kind=rest[0]['kind'],passed=False,detail='前一项隔离运行环境未完成，后续样例未执行。',skipped=True) for rest in prepared[index+1:])
                    break
            except (ValueError,OSError,UnicodeError) as exc:
                cases.append(dict(name=case['name'],kind=case['kind'],passed=False,detail=str(exc)[:1000]))
        passed=all(v['passed'] for v in cases)
        return dict(passed=passed,status='passed' if passed else 'failed',summary='样例检查通过，待用户审核业务规则。' if passed else '样例检查未通过，当前版本不能批准。',cases=cases,normalExamples=len(successes),failureExamples=len(failures))

    def approve(self,value):
        current=self.get(value['id'])
        if current['status']!='approved' and not current['canApprove']:raise ValueError('技能尚未通过独立样例验证，不能批准；请修订或补充样例')
        return super().approve(value)

    def search(self,query='',sender='',scope_id='',status='approved'):
        return super().search(query,sender,scope_id,status)

    def _cached_run(self,key,signature):
        old=self.db.execute('SELECT request_hash,result FROM learning_runs WHERE id=?',(key,)).fetchone()
        if old:
            if old[0]!=signature:raise ValueError('技能运行标识已用于不同输入')
            result=json.loads(old[1])
            for f in result['files']:
                if sha(self.service.files.bytes(f['id']))!=f['sha256']:raise ValueError('历史技能产物已变化')
            return dict(result,replayed=True)

    def _finish_run(self,key,signature,method,request,*,recovered):
        """Import the exact frozen execution, even after another DB committed.

        Managed files use their own database. A failure after file one, its
        lineage update, or the final run insert leaves this durable journal;
        retry imports the same bytes rather than rerunning nondeterministic code.
        """
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if not self.get(method['id'])['canRun']:raise ValueError('技能在执行期间被停用或替换，未保存结果')
            old=self._cached_run(key,signature)
            if old:
                self.db.execute('COMMIT')
                return old
            row=self.db.execute('SELECT request_hash,record FROM skill_run_journal WHERE id=?',(key,)).fetchone()
            if not row or row['request_hash']!=signature:raise ValueError('技能输出恢复日志与本次输入不一致')
            journal=json.loads(row['record'])
            if journal['request']!=request:raise ValueError('技能输出恢复记录校验失败')
            outputs=self.db.execute('SELECT name,sha256,data FROM skill_run_outputs WHERE run_id=?',(key,)).fetchall()
            produced={row['name']:row['data'] for row in outputs}
            names={v['name'] for v in method['proposal']['outputs']}
            if set(produced)!=names or set(journal['outputHashes'])!=names:raise ValueError('冻结的技能输出文件清单不完整')
            if sum(len(data) for data in produced.values())>MAX_RUN_OUTPUT_BYTES:raise ValueError('冻结的技能输出超过限制')
            if any(sha(row['data'])!=row['sha256'] or row['sha256']!=journal['outputHashes'][row['name']] for row in outputs):raise ValueError('冻结的技能输出校验失败，未重新执行或覆盖')
            audit=journal['audit']
            records=[]
            new_names=[name for name in produced if not self.service.files.db.execute('SELECT 1 FROM files WHERE id=?',(str(uuid5(NAMESPACE_URL,'task-manager-skill-output:'+key+':'+name)),)).fetchone()]
            if self.service.files.db.execute('SELECT count(*) FROM files').fetchone()[0]+len(new_names)>MAX_FILES:raise ValueError('本地资料库没有足够空间保存完整技能结果')
            used=self.service.files.db.execute("SELECT COALESCE(SUM(json_extract(record,'$.byteSize')),0) FROM files").fetchone()[0]
            if used+sum(len(produced[name]) for name in new_names)>512*1024*1024:raise ValueError('本地资料库没有足够容量保存完整技能结果')
            for name,data in produced.items():
                fid=str(uuid5(NAMESPACE_URL,'task-manager-skill-output:'+key+':'+name))
                file_signature=digest(dict(request,name=name))
                prior=self.service.files.db.execute('SELECT request_hash FROM files WHERE id=?',(fid,)).fetchone()
                if prior and prior[0]!=file_signature:raise ValueError('此技能输出标识已用于不同的输入，未覆盖')
                saved=self.service.files._save(name,data,'generated',parents=audit['inputIds'],artifact_id=fid,request_hash=file_signature)
                saved=dict(saved,skill=dict(audit,outputSha256=saved['sha256']))
                self.service.files.db.execute('UPDATE files SET record=? WHERE id=?',(compact(saved),saved['id']))
                records.append(saved)
            reply=dict(kind='skill_result',files=records,audit=audit,replayed=False,recovered=recovered,execution=journal['execution'])
            self.db.execute('INSERT INTO learning_runs VALUES(?,?,?,?)',(key,signature,compact(reply),instant()))
            self.db.execute('DELETE FROM skill_run_outputs WHERE run_id=?',(key,))
            self.db.execute('DELETE FROM skill_run_journal WHERE id=?',(key,))
            self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
        return reply

    def run(self,version_id,inputs,scope_id,source_mail_id='',run_id=None):
        # Approval, package integrity, scope and current input bytes are checked
        # before either a completed replay or a partially imported recovery.
        method=self.get(version_id)
        if not method['canRun']:raise ValueError('只有当前已批准脚本技能可执行；文字经验只可检索参考')
        if not scope_id or method['scopeId']!=scope_id:raise ValueError('技能适用范围不匹配')
        blobs,snapshots=self._input_bytes(inputs,method['proposal']['inputs'],method['scope'],source_mail_id)
        request=dict(versionId=method['id'],versionHash=method['versionHash'],scopeId=scope_id,inputs=snapshots)
        signature=digest(request);key=identifier(run_id) if run_id else str(uuid5(NAMESPACE_URL,'task-manager-skill-run:'+signature))
        old=self._cached_run(key,signature)
        if old:return old
        pending=self.db.execute('SELECT request_hash FROM skill_run_journal WHERE id=?',(key,)).fetchone()
        if pending and pending[0]!=signature:raise ValueError('技能恢复调用标识已用于不同输入')
        if not pending:
            imported=method['proposal'].get('importedPackage')
            if imported:
                directory=self._check_package(method)
                resources={name:(directory/'imported'/name).read_bytes() for name in imported['runtimeFiles']}
                result=self.runner(method['proposal']['code'],blobs,package_files=resources)
            else:result=self.runner(method['proposal']['code'],blobs)
            if not result.get('ok'):raise ValueError('技能执行未完成：'+(result.get('stderr') or result.get('error') or '运行失败')[:1500])
            produced=result.get('outputs',{});names={v['name'] for v in method['proposal']['outputs']}
            if set(produced)!=names:raise ValueError('实际产物与技能输出契约不一致，未保存')
            if any(not isinstance(data,bytes) for data in produced.values()) or sum(len(data) for data in produced.values())>MAX_RUN_OUTPUT_BYTES:raise ValueError('技能总输出须为不超过 24 MiB 的文件字节')
            for spec in method['proposal']['outputs']:
                data=produced[spec['name']]
                extract_document(data,Path(spec['name']).suffix.lower().lstrip('.'))
                if spec['comparison']=='table':read_table(data,Path(spec['name']).suffix.lower().lstrip('.'))
                elif spec['comparison']=='workbook':workbook_signature(data)
                elif spec['comparison']=='text':data.decode('utf-8-sig')
            audit=dict(request,approvedVersion=method['version'],inputIds=list(inputs.values()),inputHashes={s['name']:s['sha256'] for s in snapshots},
                inputProvenance=[dict(name=s['name'],id=s['id'],originalMailIds=sorted(self._file_mails(s['id'])),scopeEvidenceMailIds=s['sourceMailIds']) for s in snapshots],
                sourceMailIdsNotice='inputs[].sourceMailIds 是适用范围核对依据，可能含本次关联邮件；实际附件来源请看 inputProvenance[].originalMailIds。')
            execution={k:result[k] for k in ('backend','exitCode','imageId','runtimeHash','cleanupConfirmed') if k in result}
            execution['stdout']=result.get('stdout','')[:3000]
            journal=dict(request=request,audit=audit,execution=execution,outputHashes={name:sha(data) for name,data in produced.items()})
            self.db.execute('BEGIN IMMEDIATE')
            try:
                if not self.get(method['id'])['canRun']:raise ValueError('技能在执行期间被停用或替换，未保存结果')
                # A concurrent identical call may have frozen its results first.
                # Its exact bytes win; never replace them with a second execution.
                stored=self.db.execute('SELECT 1 FROM skill_run_journal WHERE id=?',(key,)).fetchone()
                completed=self.db.execute('SELECT 1 FROM learning_runs WHERE id=?',(key,)).fetchone()
                if not stored and not completed:
                    used=self.db.execute('SELECT COALESCE(SUM(length(data)),0) FROM skill_run_outputs').fetchone()[0]
                    if used+sum(len(data) for data in produced.values())>MAX_PENDING_RUN_BYTES:raise ValueError('未完成技能输出的恢复日志已达 128 MiB 上限，请先恢复已有任务')
                    self.db.execute('INSERT INTO skill_run_journal VALUES(?,?,?)',(key,signature,compact(journal)))
                    self.db.executemany('INSERT INTO skill_run_outputs VALUES(?,?,?,?)',[(key,name,sha(data),data) for name,data in produced.items()])
                self.db.execute('COMMIT')
            except BaseException:self.db.execute('ROLLBACK');raise
        return self._finish_run(key,signature,method,request,recovered=bool(pending))
