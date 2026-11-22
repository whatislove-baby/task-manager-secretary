#!/usr/bin/env python3
"""Offline RFC5322 fixtures and real local-store seeding, never SMTP/IMAP.

Only message fields and actual MIME attachment bytes enter the product. Oracles,
fixture descriptions and future messages are deliberately excluded. Use a fresh
isolated BackendService profile; this helper is not a production inbox importer.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, parsedate_to_datetime
import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import sys
from uuid import NAMESPACE_URL, uuid5

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'backend'))
from task_backend.transport import parse_mail
from task_backend.mail_identity import header_metadata
from task_backend.mail_store import local_key
from task_backend.mail_files import MAX_BYTES, TYPES


def load_scenario(value, root=None):
    if isinstance(value, (str, Path)):
        path = Path(value).resolve()
        return json.loads(path.read_text(encoding='utf-8')), path.parent if root is None else Path(root).resolve()
    if not isinstance(value, dict) or root is None:
        raise ValueError('A scenario dictionary requires its explicit fixture root')
    return value, Path(root).resolve()


def selected_messages(scenario, cutoff=None, checkpoint=None):
    if cutoff is not None and checkpoint is not None:
        raise ValueError('Choose cutoff or checkpoint, not both')
    messages = sorted(scenario['messages'], key=lambda m: datetime.fromisoformat(m['sentAt']))
    keys = [m['messageKey'] for m in messages]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate messageKey')
    if checkpoint is not None:
        if isinstance(checkpoint, str):
            matches = [v for v in scenario.get('checkpoints', []) if v.get('checkpointId') == checkpoint]
            if len(matches) != 1:
                raise ValueError('Unknown checkpoint')
            checkpoint = matches[0]
        cutoff = checkpoint['throughMessageKey']
    if cutoff is None:
        return messages
    if cutoff in keys:
        return messages[:keys.index(cutoff) + 1]
    at = datetime.fromisoformat(cutoff)
    if at.tzinfo is None:
        raise ValueError('Date cutoff must include timezone')
    return [m for m in messages if datetime.fromisoformat(m['sentAt']) <= at]


def _address(item):
    if isinstance(item, str):
        item = dict(address=item, name='')
    raw = item['address']
    if not raw.rsplit('@', 1)[-1].endswith('.invalid'):
        raise ValueError('Offline fixture addresses must use the reserved .invalid domain')
    return Address(display_name=item.get('name', ''), addr_spec=raw)


def make_eml(message, root):
    """Produce deterministic MIME; do not include fixture/oracle metadata."""
    root = Path(root).resolve()
    mail = EmailMessage(policy=policy.SMTP)
    mail['From'] = _address(message['from'])
    mail['To'] = tuple(_address(v) for v in message['to'])
    for source, header in [('cc', 'Cc'), ('replyTo', 'Reply-To')]:
        if message.get(source):
            mail[header] = tuple(_address(v) for v in message[source])
    at = datetime.fromisoformat(message['sentAt'])
    if at.tzinfo is None:
        raise ValueError('sentAt must include timezone')
    mail['Date'] = format_datetime(at)
    mail['Subject'] = message['subject']
    mail['Message-ID'] = message['messageId']
    if message.get('inReplyTo'):
        mail['In-Reply-To'] = message['inReplyTo']
    if message.get('references'):
        mail['References'] = ' '.join(message['references'])
    mail.set_content(message['body'], charset='utf-8')
    for spec in message.get('attachments', []):
        name = spec['name']
        if Path(name).name != name or re.search(r'[\x00-\x1f\\]', name):
            raise ValueError('Attachment display name must be a plain filename')
        path = root / spec.get('fixturePath', 'attachments/' + name)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('Attachment is missing or leaves the scenario root: ' + name)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('Fixture attachment exceeds 10 MB: ' + name)
        data = path.read_bytes()
        if len(data) > MAX_BYTES:
            raise ValueError('Fixture attachment grew while reading')
        media = TYPES.get(path.suffix.lower().lstrip('.')) or mimetypes.guess_type(name)[0] or 'application/octet-stream'
        major, minor = media.split('/', 1)
        mail.add_attachment(data, maintype=major, subtype=minor, filename=name)
    if mail.is_multipart():
        # Stable boundary makes byte hashes meaningful across fixture replays.
        key = hashlib.sha256(message['messageId'].encode()).hexdigest()[:40]
        mail.set_boundary('=_scenario_' + key)
    return mail.as_bytes()


def parse_eml(raw, remote_id):
    """Use the product's MIME body/identity parsers, plus decoded MIME leaves."""
    mail, body = parse_mail(raw)
    if mail.defects:
        raise ValueError('Malformed fixture MIME')
    attachments, payloads = [], []
    for part in mail.iter_attachments():
        if part.is_multipart():
            raise ValueError('Nested MIME messages are outside these fixtures')
        data = part.get_payload(decode=True)
        if part.defects or not isinstance(data, bytes) or len(data) > MAX_BYTES:
            raise ValueError('Malformed or oversized MIME attachment')
        descriptor = dict(name=part.get_filename() or 'attachment', type=part.get_content_type(),
                          size=len(data), inline=part.get_content_disposition() == 'inline')
        attachments.append(descriptor)
        payloads.append(data)
    identity = header_metadata(mail)
    if identity['identityWarnings']:
        raise ValueError('Invalid fixture identity: ' + ','.join(identity['identityWarnings']))
    received = parsedate_to_datetime(str(mail['Date'])).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    item = dict(id=remote_id, subject=str(mail.get('Subject', '')), sender=str(mail.get('From', '')),
                receivedAt=received, body=body, preview=body[:300], unread=True, webUrl='',
                bodyStatus='complete', bodyBytes=len(body.encode('utf-8')), receivedBytes=len(raw),
                attachments=attachments, hasHtml=False, **identity)
    return item, payloads


def write_emls(scenario, root=None, *, output=None, cutoff=None, checkpoint=None):
    scenario, root = load_scenario(scenario, root)
    target = Path(output) if output is not None else root / 'eml'
    target.mkdir(parents=True, exist_ok=True)
    result = {}
    for message in selected_messages(scenario, cutoff, checkpoint):
        key = message['messageKey']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key):
            raise ValueError('Unsafe messageKey')
        raw = make_eml(message, root)
        parse_eml(raw, message['messageId'])
        path = target / (key + '.eml')
        if path.is_symlink():
            raise ValueError('Refusing linked fixture output')
        path.write_bytes(raw)
        result[key] = dict(path=str(path.resolve()), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    return result


def seed(service, scenario, root=None, cutoff=None, checkpoint=None, *, import_attachments=True,
         binding_id=None, register_binding=False, message_keys=None):
    """Seed ONLY an isolated profile. Return mailIds/files/messages mappings.

    register_binding adds a real, unconfigured/disconnected IMAP entry to that
    isolated profile, allowing draft identity validation without credentials.
    No manager is replaced. With import_attachments=False, use fixture_transport
    below explicitly for offline download tests; it is NOT a real IMAP test.
    """
    scenario, root = load_scenario(scenario, root)
    sid = scenario['scenarioId']
    if not scenario.get('metadata', {}).get('fictional'):
        raise ValueError('This helper only imports explicitly fictional scenarios')
    chosen = selected_messages(scenario, cutoff, checkpoint)
    if message_keys is not None:
        if not isinstance(message_keys, (list, tuple, set)) or any(not isinstance(v, str) for v in message_keys):
            raise ValueError('message_keys must be a collection of visible message keys')
        allowed = set(message_keys)
        if not allowed <= {m['messageKey'] for m in chosen}:
            raise ValueError('Unknown or out-of-checkpoint message key')
        chosen = [m for m in chosen if m['messageKey'] in allowed]
    prepared = [(m, make_eml(m, root)) for m in chosen]
    parsed = [(m, raw, *parse_eml(raw, m['messageId'])) for m, raw in prepared]
    identity = 'offline-rfc5322:' + sid
    registry_path = Path(service.directory) / 'scenario-fixture-bindings.json'
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    if not binding_id:
        if sid in registry:
            binding_id = registry[sid]
        elif register_binding:
            binding_id = service.mail.manager.add('imap')['id']
            registry[sid] = binding_id
            registry_path.write_text(json.dumps(registry, indent=2), encoding='utf-8')
        else:
            binding_id = 'fixture-' + hashlib.sha256(sid.encode()).hexdigest()[:24]
    # A checkpoint cannot hide future messages already seeded into this profile.
    selected_ids = {m['messageId'] for m in chosen}
    for row in service.mail.store.db.execute('SELECT remote_id FROM local_mail WHERE binding_id=? AND identity=?',
                                            (binding_id, identity)):
        if row[0] not in selected_ids:
            raise ValueError('Future messages are already present; use a fresh checkpoint profile')
    result = dict(scenarioId=sid, bindingId=binding_id, mailboxIdentity=identity,
                  mailIds={}, messages={}, files={}, filesByName={}, attachmentErrors=[],
                  addedMessageKeys=[], visibleMessageKeys=[m['messageKey'] for m in chosen],
                  mimeByRemoteId={}, transport='offline MIME fixtures; no SMTP/IMAP/authentication tested')
    for spec, raw, item, payloads in parsed:
        account = spec['to'][0]
        account = account if isinstance(account, str) else account['address']
        result['accountEmail'] = account
        expected_local = local_key(binding_id, identity, item['id'])
        existing = service.mail.store.db.execute('SELECT payload FROM local_mail WHERE local_id=?', (expected_local,)).fetchone()
        sha = hashlib.sha256(raw).hexdigest()
        if existing and json.loads(existing[0]).get('fixtureSourceSha256') != sha:
            raise ValueError('An existing fixture changed; use a fresh profile')
        item['fixtureSourceSha256'] = sha
        local_id = expected_local if existing else service.mail.store.put(binding_id, identity, account, item)['localId']
        key = spec['messageKey']
        if not existing:
            result['addedMessageKeys'].append(key)
        entry = dict(localId=local_id, remoteId=item['id'], attachmentFiles=[], attachments=item['attachments'])
        result['messages'][key] = entry
        result['mailIds'][key] = local_id
        result['mimeByRemoteId'][item['id']] = raw
        if not import_attachments:
            continue
        for index, (descriptor, data) in enumerate(zip(item['attachments'], payloads)):
            try:
                stable = str(uuid5(NAMESPACE_URL, 'offline-attachment:' + local_id + ':' + str(index)))
                record = service.files._save(descriptor['name'], data, 'mail', source_mail=local_id, artifact_id=stable)
                entry['attachmentFiles'].append(record)
                same_name = result['filesByName'].setdefault(record['name'], [])
                same_name.append(record)
                # files is a convenient mapping only when the name is unique.
                if len(same_name) == 1:
                    result['files'][record['name']] = record
                else:
                    result['files'].pop(record['name'], None)
            except ValueError as exc:
                result['attachmentErrors'].append(dict(messageKey=key, index=index, name=descriptor['name'], error=str(exc)))
    return result


@contextmanager
def fixture_transport(service, seeded):
    """Explicit offline attachment download adapter; never authenticates mail.

    Leaves all non-fixture accounts unchanged; use a network-denying test guard
    if the caller also exercises unrelated accounts. Restores manager methods.
    """
    manager = service.mail.manager
    binding, identity = seeded['bindingId'], seeded['mailboxIdentity']
    old_reader, old_check, old_identity = manager.reader, manager.check_identity, manager.identity

    class Reader:
        def get_attachment(self, remote_id, index, expected, max_bytes=MAX_BYTES):
            item, payloads = parse_eml(seeded['mimeByRemoteId'][remote_id], remote_id)
            if type(index) is not int or not 0 <= index < len(payloads):
                raise ValueError('Fixture attachment does not exist')
            if any(item['attachments'][index].get(k) != expected.get(k) for k in ('name', 'type', 'size', 'inline')):
                raise ValueError('Fixture attachment metadata changed')
            data = payloads[index]
            if len(data) > max_bytes:
                raise ValueError('Fixture attachment exceeds requested limit')
            return data

    def check(value, expected):
        if value != binding:
            return old_check(value, expected)
        if expected != identity:
            raise ValueError('Fixture mailbox identity changed')

    manager.reader = lambda value: Reader() if value == binding else old_reader(value)
    manager.check_identity = check
    manager.identity = lambda value: (dict(identity=identity, email=seeded['accountEmail'])
                                      if value == binding else old_identity(value))
    try:
        yield
    finally:
        manager.reader, manager.check_identity, manager.identity = old_reader, old_check, old_identity


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--checkpoint')
    parser.add_argument('--cutoff')
    args = parser.parse_args()
    files = write_emls(args.scenario, output=args.output, checkpoint=args.checkpoint, cutoff=args.cutoff)
    print(json.dumps(dict(messages=len(files), files=files), ensure_ascii=False, indent=2))
