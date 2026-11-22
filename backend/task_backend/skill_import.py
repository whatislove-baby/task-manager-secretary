"""Explicit Task Manager skill ZIP v1, never arbitrary skill installation.

Only user-provided immutable package bytes are accepted. Manifest parsing does
not execute code; existing SkillLibrary validation/approval owns execution.
"""
from io import BytesIO
from pathlib import PurePosixPath
import json
import zipfile
from uuid import NAMESPACE_URL,uuid5

from pydantic import Field
from typing import Annotated, Literal
from .learning_schema import LearningInput, Name
from .skill_schema import SkillInput, SkillOutput
from .bundle_files import archive_index, _member_path
from .skill_execution import _name, MAX_TOTAL


class PackageExample(LearningInput):
    name: Name
    kind: Literal['success','failure']
    inputFiles: dict[str,str]=Field(min_length=1,max_length=8)
    expectedFiles: dict[str,str]=Field(default_factory=dict,max_length=5)
    expectedError: Annotated[str,Field(min_length=1,max_length=300)] | None=None


class PackageManifest(LearningInput):
    schemaVersion: Literal[1]
    title: Name
    kind: Literal['script','hybrid']='script'
    entrypoint: Literal['main.py']='main.py'
    runtimeFiles: list[str]=Field(default_factory=list,max_length=128)
    inputs: list[SkillInput]=Field(min_length=1,max_length=8)
    outputs: list[SkillOutput]=Field(min_length=1,max_length=5)
    examples: list[PackageExample]=Field(min_length=1,max_length=10)


def read_package(data):
    index=archive_index(data)
    members={}
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            for member in index['members']:
                if member['directory']:continue
                with archive.open(member['originalPath']) as source:content=source.read(member['byteSize']+1)
                if len(content)!=member['byteSize']:raise ValueError('技能包成员长度与 ZIP 目录不一致')
                members[member['path']]=content
    except ValueError:raise
    except Exception:raise ValueError('技能包成员损坏或校验失败') from None
    required={'skill.json','SKILL.md','main.py'}
    if not required<=members.keys():raise ValueError('需要 Task Manager 技能包：根目录 skill.json、SKILL.md、main.py；不支持任意 SKILL ZIP 直接安装')
    file_paths={p.casefold() for p in members}
    for name in file_paths:
        if any(str(parent) in file_paths for parent in PurePosixPath(name).parents if str(parent)!='.'):
            raise ValueError('技能包包含文件与目录路径冲突')
    try:
        if len(members['skill.json'])>100000:raise ValueError('skill.json 过大')
        # Duplicate keys would otherwise silently override user package rules.
        def unique_pairs(pairs):
            result={}
            for k,v in pairs:
                if k in result:raise ValueError('skill.json 含重复字段')
                result[k]=v
            return result
        manifest=PackageManifest.model_validate(json.loads(members['skill.json'].decode('utf-8'),object_pairs_hook=unique_pairs)).model_dump(exclude_none=True)
        markdown=members['SKILL.md'].decode('utf-8');code=members['main.py'].decode('utf-8')
    except (UnicodeError,TypeError):raise ValueError('技能元数据、SKILL.md 和 main.py 必须为 UTF-8') from None
    if not markdown.startswith('---\n') or markdown.find('\n---',4)<0:raise ValueError('SKILL.md 需要标准 YAML frontmatter 与 Markdown 正文')
    if len(markdown)>20000 or not code.strip() or len(code)>64000 or '\0' in code:raise ValueError('技能说明或 main.py 超过支持范围')
    resources={};folded=[]
    for name in manifest['runtimeFiles']:
        _name(name)
        if name in required or name.startswith(('examples/','tests/')):raise ValueError('运行资源不能包含入口、技能元数据或测试样例目录')
        if name not in members:raise ValueError('声明的运行资源不在 ZIP 中：'+name)
        key=name.casefold()
        if any(key==n or key.startswith(n+'/') or n.startswith(key+'/') for n in folded):raise ValueError('运行资源路径冲突')
        folded.append(key);resources[name]=members[name]
    if sum(map(len,resources.values()))>MAX_TOTAL:raise ValueError('技能运行资源超过 24 MiB')
    references=set()
    for case in manifest['examples']:
        for path in list(case['inputFiles'].values())+list(case.get('expectedFiles',{}).values()):
            if _member_path(path)!=path or path not in members:raise ValueError('样例引用路径不在技能包中：'+path)
            if not path.startswith('examples/'):raise ValueError('测试输入和完成样例须位于 examples/，与运行资源隔离')
            references.add(path)
    return dict(manifest=manifest,members=members,resources=resources,markdown=markdown,code=code,references=references)


def import_package(library,file_id,scope,instruction='',parent_id=None,proposal_id=None,*,teaching=None):
    from .skill_library import sha
    from .mail_files import extract_document,TYPES
    from .bundle_files import extract_archive
    from .learning import normalize_scope,string,digest
    library._verified_reference(file_id)
    original=library.service.files.get(file_id)
    if original['extension']!='zip':raise ValueError('请选择用户提供的技能 ZIP')
    _,normalized_scope=normalize_scope(scope)
    library._file_scope(file_id,normalized_scope)
    instruction=string(instruction,4000,False)
    if proposal_id is None:
        proposal_id=str(uuid5(NAMESPACE_URL,'task-manager-skill-import:'+digest(dict(
            fileId=file_id,sourceSha256=original['sha256'],scope=normalized_scope,
            instruction=instruction,parentId=parent_id))))
        if library.db.execute('SELECT 1 FROM learning_versions WHERE id=?',(proposal_id,)).fetchone():
            # Same immutable file and import options return the original review;
            # do not rerun examples or claim new teaching replaced its history.
            return library.get(proposal_id)
    package=read_package(library.service.files.bytes(file_id));manifest=package['manifest']
    expected={p for case in manifest['examples'] for p in case.get('expectedFiles',{}).values()}
    expected_hashes={sha(package['members'][p]) for p in expected}
    if expected_hashes & {sha(v) for v in package['resources'].values()}:raise ValueError('运行资源不能包含独立完成样例的相同字节')
    # Validate all example bytes before storing any members or executing code.
    for path in package['references']:
        extension=PurePosixPath(path).suffix.lower().lstrip('.')
        if extension not in TYPES:raise ValueError('样例格式不在当前受管文件范围内：'+path)
        extract_document(package['members'][path],extension)
    ids={};paths=sorted(package['references'])
    for offset in range(0,len(paths),20):
        for item in extract_archive(library.service.files,file_id,paths[offset:offset+20]):ids[item['archiveOrigin']['memberPath']]=item['id']
    examples=[]
    for case in manifest['examples']:
        converted=dict(case,inputFiles={name:ids[path] for name,path in case['inputFiles'].items()},
                       expectedFiles={name:ids[path] for name,path in case.get('expectedFiles',{}).items()})
        examples.append(converted)
    body=package['markdown'][package['markdown'].find('\n---',4)+4:].strip()
    if instruction:body+='\n\n## User import supplement\n'+instruction
    value=dict(title=manifest['title'],kind=manifest['kind'],scope=normalized_scope,
               rationale='User supplied an existing immutable Skill ZIP; import and validate its original code without model rewriting.',
               instruction=body,code=package['code'],inputs=manifest['inputs'],outputs=manifest['outputs'],examples=examples,sources=[])
    if parent_id:value['parentId']=parent_id
    metadata=dict(schemaVersion=1,sourceFileId=file_id,sourceSha256=original['sha256'],
        originalFiles={name:sha(data) for name,data in package['members'].items()},
        runtimeFiles=list(package['resources']),supplement=instruction)
    return library._propose(value,proposal_id,teaching=teaching,imported=dict(metadata=metadata,**package))
