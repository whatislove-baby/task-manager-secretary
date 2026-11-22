"""Bounded images and ZIP bundles; member names never become filesystem paths."""
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid5
import base64
import hashlib
import json
import posixpath
import re
import stat
import unicodedata
import warnings
import zipfile

IMAGE_EXTENSIONS = frozenset(('png', 'jpg', 'jpeg', 'webp'))
IMAGE_FORMATS = {'png': 'PNG', 'jpg': 'JPEG', 'jpeg': 'JPEG', 'webp': 'WEBP'}
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_SIDE = 16_000
MAX_BYTES = 10 * 1024 * 1024
MAX_MEMBERS = 200
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_RATIO = 200
MAX_ARCHIVE_DEPTH = 2
MAX_EXTRACT_BATCH = 20


def _picture(data, extension):
    from PIL import Image, ImageOps
    if extension not in IMAGE_FORMATS or not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise ValueError('图片格式或大小无效；支持 PNG、JPEG、WebP，单文件不超过 10 MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.format != IMAGE_FORMATS[extension]:
                    raise ValueError('图片内容与文件扩展名不一致')
                if getattr(source, 'n_frames', 1) != 1:
                    raise ValueError('暂不支持多帧图片，请提供静态图片')
                width, height = source.size
                if min(width, height) < 1 or max(width, height) > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError('图片尺寸过大，最多 2400 万像素、单边 16000 像素')
                source.load()
                picture = ImageOps.exif_transpose(source).convert('RGBA')
                # Composite transparent drawings on white, with metadata removed.
                background = Image.new('RGBA', picture.size, 'white')
                background.alpha_composite(picture)
                return background.convert('RGB')
    except ValueError:
        raise
    except Exception:
        raise ValueError('图片损坏或无法完整解码') from None


def inspect_image(data, extension):
    picture = _picture(data, extension)
    try:
        return dict(width=picture.width, height=picture.height, pageCount=1,
                    format=IMAGE_FORMATS[extension], textExtracted=False)
    finally:
        picture.close()


def preview_image(data, extension, page=1):
    if type(page) is not int or page != 1:
        raise ValueError('静态图片仅有第 1 页')
    from PIL import Image
    picture = _picture(data, extension)
    try:
        picture.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
        out = BytesIO(); picture.save(out, format='PNG')
        payload = out.getvalue()
        if len(payload) > 8 * 1024 * 1024:
            raise ValueError('图片预览过大，请缩小图片后重试')
        return dict(page=1, pageCount=1, width=picture.width, height=picture.height,
                    dataUrl='data:image/png;base64,' + base64.b64encode(payload).decode(),
                    mediaType='image/png', renderer='Pillow', rendered=True)
    finally:
        picture.close()


def _member_path(value, *, directory=False):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('ZIP 成员路径为空或过长')
    if re.search(r'[\x00-\x1f\x7f\\:]', value) or value.startswith('/'):
        raise ValueError('ZIP 包含不安全的成员路径')
    path = value[:-1] if directory and value.endswith('/') else value
    parts = path.split('/')
    if any(part in ('', '.', '..') or len(part) > 200 for part in parts):
        raise ValueError('ZIP 成员路径不能越界或包含空目录')
    return unicodedata.normalize('NFC', path)


def archive_index(data, supported=None):
    """Inspect central-directory metadata without decompressing member content."""
    from .mail_files import TYPES
    supported = set(TYPES) if supported is None else set(supported)
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise ValueError('ZIP 不能为空，单文件不能超过 10 MB')
    try:
        with zipfile.ZipFile(BytesIO(data)) as bundle:
            infos = bundle.infolist()
            if not infos or len(infos) > MAX_MEMBERS:
                raise ValueError('ZIP 为空或成员数超过 200')
            members=[]; total=0; seen=set()
            for info in infos:
                directory=info.is_dir()
                path=_member_path(info.orig_filename, directory=directory)
                key=path.casefold()
                if key in seen:
                    raise ValueError('ZIP 包含重复或大小写冲突的成员路径')
                seen.add(key)
                mode=(info.external_attr >> 16) & 0xffff
                kind=stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind==stat.S_IFDIR and not directory):
                    raise ValueError('ZIP 不接受符号链接或特殊文件')
                if info.flag_bits & 1:
                    raise ValueError('暂不支持加密 ZIP，请提供未加密副本')
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
                    raise ValueError('ZIP 使用当前运行时不支持的压缩方式')
                if info.file_size > MAX_BYTES or info.file_size < 0 or info.compress_size < 0:
                    raise ValueError('ZIP 单个成员展开后不能超过 10 MB')
                if info.file_size > MAX_RATIO * max(1, info.compress_size):
                    raise ValueError('ZIP 成员压缩比例超过 200，拒绝异常展开')
                total += info.file_size
                if total > MAX_EXPANDED_BYTES:
                    raise ValueError('ZIP 展开总字节数超过 50 MB')
                extension=PurePosixPath(path).suffix.lower().lstrip('.')
                members.append(dict(path=path, originalPath=info.filename, directory=directory,
                                    byteSize=info.file_size, compressedBytes=info.compress_size,
                                    extension=extension, supported=not directory and extension in supported))
            return dict(memberCount=len(members), expandedBytes=total, members=members,
                        limits=dict(maxMembers=MAX_MEMBERS, maxMemberBytes=MAX_BYTES,
                                    maxExpandedBytes=MAX_EXPANDED_BYTES, maxDepth=MAX_ARCHIVE_DEPTH),
                        warning='这里只读取压缩包目录，未读取成员正文；需按路径展开所需文件。')
    except ValueError:
        raise
    except Exception:
        raise ValueError('ZIP 损坏或目录无法读取') from None


def inspect_archive(files, file_id, offset=0, limit=50):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('压缩包分页参数无效')
    record=files.get(file_id)
    if record['extension'] != 'zip':
        raise ValueError('请选择 ZIP 压缩包')
    index=archive_index(files.bytes(file_id))
    members=index.pop('members')
    return dict(fileId=file_id, sha256=record['sha256'], archiveDepth=record.get('archiveOrigin',{}).get('depth',0),
                **index, members=[{k:v for k,v in member.items() if k!='originalPath'} for member in members[offset:offset+limit]],
                offset=offset, nextOffset=offset+limit if offset+limit<len(members) else None)


def extract_archive(files, file_id, selected):
    """Prevalidate each requested member, then save immutable, retryable copies."""
    from .mail_files import extract_document
    if not isinstance(selected,list) or not 1 <= len(selected) <= MAX_EXTRACT_BATCH or any(not isinstance(p,str) for p in selected):
        raise ValueError('每次选择 1 至 20 个 ZIP 成员相对路径')
    if len(set(selected)) != len(selected):
        raise ValueError('不能重复选择 ZIP 成员')
    record=files.get(file_id)
    if record['extension']!='zip':raise ValueError('请选择 ZIP 压缩包')
    origin=record.get('archiveOrigin',{})
    depth=origin.get('depth',0)
    if type(depth) is not int or depth>=MAX_ARCHIVE_DEPTH:
        raise ValueError('ZIP 嵌套展开超过 2 层上限')
    root_id=origin.get('rootArchiveId',file_id)
    data=files.bytes(file_id);index=archive_index(data)
    lookup={m['path']:m for m in index['members']}
    staged=[]
    try:
        with zipfile.ZipFile(BytesIO(data)) as bundle:
            for path in selected:
                clean=_member_path(path)
                if clean!=path or path not in lookup:
                    raise ValueError('ZIP 成员不存在，请使用目录返回的精确路径')
                info=lookup[path]
                if not info['supported']:
                    raise ValueError('选中的 ZIP 成员不是可读取的文件类型：'+path)
                with bundle.open(info['originalPath']) as stream:
                    content=stream.read(MAX_BYTES+1)
                if len(content)>MAX_BYTES or len(content)!=info['byteSize']:
                    raise ValueError('ZIP 成员实际展开大小与目录不符或超过上限')
                extract_document(content,info['extension'])
                stable=str(uuid5(NAMESPACE_URL,'task-manager-zip:'+file_id+':'+record['sha256']+':'+path))
                lineage=dict(archiveId=file_id,rootArchiveId=root_id,memberPath=path,depth=depth+1,
                             archiveSha256=record['sha256'],memberSha256=hashlib.sha256(content).hexdigest())
                staged.append((stable,path,content,lineage))
    except ValueError:
        raise
    except Exception:
        raise ValueError('ZIP 成员解压或完整性校验失败，未创建本批文件') from None
    # Apply a root-bundle budget across nested archives and repeated calls.
    stored=[json.loads(row[0]) for row in files.db.execute('SELECT record FROM files')]
    existing={item['id']:item for item in stored}
    expanded=sum(item['byteSize'] for item in stored if item.get('archiveOrigin',{}).get('rootArchiveId')==root_id)
    added=sum(len(content) for stable,path,content,lineage in staged if stable not in existing)
    if expanded+added>MAX_EXPANDED_BYTES:
        raise ValueError('本压缩包及嵌套包累计展开超过 50 MB')
    if len(stored)+sum(stable not in existing for stable,*_ in staged)>500:
        raise ValueError('本地资料库文件数不足以保存本批内容')
    if sum(item['byteSize'] for item in stored)+added>512*1024*1024:
        raise ValueError('本地资料库空间不足以保存本批内容')
    for stable,path,content,lineage in staged:
        if stable in existing:
            files.bytes(stable)
            if existing[stable].get('archiveOrigin')!=lineage or existing[stable]['sha256']!=lineage['memberSha256']:
                raise ValueError('已保存的压缩包成员来源不一致')
    result=[]
    for stable,path,content,lineage in staged:
        saved=files._save(PurePosixPath(path).name,content,'archive',parents=[file_id],
                          source_mail=record.get('sourceMailId'),artifact_id=stable,
                          request_hash=hashlib.sha256(json.dumps(lineage,sort_keys=True).encode()).hexdigest(),
                          archive_origin=lineage)
        if saved.get('archiveOrigin')!=lineage:
            raise ValueError('已保存的压缩包成员来源不一致')
        result.append(saved)
    return result


def related_files(files, file_id):
    """Resolve relative Markdown references only within the recorded ZIP bundle."""
    from markdown_it import MarkdownIt
    record=files.get(file_id);files.bytes(file_id)
    origin=record.get('archiveOrigin',{})
    archive_id=origin.get('archiveId')
    candidates=[]; lookup={}; directory={}
    if archive_id:
        bundle=files.get(archive_id)
        if bundle['sha256']!=origin.get('archiveSha256'):
            raise ValueError('压缩包来源版本不一致')
        directory={m['path']:m for m in archive_index(files.bytes(archive_id))['members']}
        for row in files.db.execute('SELECT record FROM files'):
            item=json.loads(row[0]);item_origin=item.get('archiveOrigin',{})
            if item_origin.get('archiveId')==archive_id:
                files.bytes(item['id'])
                candidate={k:item[k] for k in ('id','name','extension','sha256')}
                candidate['memberPath']=item_origin['memberPath']
                candidates.append(candidate);lookup[item_origin['memberPath']]=candidate
    references=[]
    if record['extension']=='md':
        def walk(tokens):
            for token in tokens:
                if token.type in ('image','link_open'):
                    href=token.attrGet('src' if token.type=='image' else 'href') or ''
                    ref=dict(kind='image' if token.type=='image' else 'link',href=href)
                    try:parsed=urlsplit(href)
                    except ValueError:
                        ref['status']='invalid_reference';references.append(ref);continue
                    path=unquote(parsed.path)
                    if parsed.scheme or parsed.netloc:
                        ref['status']='external_not_fetched'
                    elif not path:
                        ref['status']='same_document'
                    elif not archive_id:
                        ref['status']='no_bundle_context'
                    elif '\\' in path or path.startswith('/') or re.search(r'[\x00-\x1f\x7f:]',path):
                        ref['status']='blocked_path'
                    else:
                        resolved=posixpath.normpath(posixpath.join(posixpath.dirname(origin['memberPath']),path))
                        if resolved=='..' or resolved.startswith('../'):
                            ref['status']='blocked_path'
                        else:
                            ref['memberPath']=unicodedata.normalize('NFC',resolved)
                            if ref['memberPath'] in lookup:
                                ref.update(status='imported',file=lookup[ref['memberPath']])
                            elif ref['memberPath'] in directory and directory[ref['memberPath']]['supported']:
                                ref.update(status='needs_extraction',archiveId=archive_id)
                            else:
                                ref['status']='missing_or_unsupported'
                    references.append(ref)
                if token.children:walk(token.children)
        walk(MarkdownIt('commonmark',{'html':False}).parse(files.read(file_id)))
    return dict(fileId=file_id,archiveId=archive_id,candidates=candidates[:100],candidateCount=len(candidates),
                references=references[:100],referenceCount=len(references),referencesTruncated=len(references)>100,
                scope='仅关联同一压缩包的已导入文件与目录；未下载外链，也未自动展开成员。')
