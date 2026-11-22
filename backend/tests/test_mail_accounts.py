from pathlib import Path
import tempfile
import unittest
from task_backend.mail_accounts import AccountManager


class Reader:
    def test_connection(self): pass


class AccountTests(unittest.TestCase):
    def test_bindings_have_independent_encrypted_credentials_and_disconnect(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=AccountManager(Path(folder),reader_factory=lambda *args:Reader())
            a=manager.add('imap');b=manager.add('imap')
            value=dict(email='a@example.invalid',username='',host='IMAP.EXAMPLE.INVALID.',port=993,security='tls',password='private-code')
            manager.configure_imap(a['id'],value);manager.connect(a['id'])
            self.assertEqual(manager.get(a['id'])['state']['account']['email'],'a@example.invalid')
            self.assertIsNone(manager.get(b['id'])['state']['account'])
            file=manager.path(a['id'])/'imap-auth.bin'
            self.assertNotIn(b'private-code',file.read_bytes())
            manager.disconnect(a['id'])
            self.assertFalse(manager.get(a['id'])['state']['imap']['hasSecret'])
            self.assertIsNone(manager.get(a['id'])['state']['account'])

    def test_changed_server_requires_new_secret_and_bad_registry_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);manager=AccountManager(root,reader_factory=lambda *args:Reader())
            account=manager.add('imap');value=dict(email='a@example.invalid',username='',host='imap.example.invalid',port=993,security='tls',password='key')
            manager.configure_imap(account['id'],value)
            with self.assertRaises(ValueError): manager.configure_imap(account['id'],{**value,'host':'other.example.invalid','password':''})
            registry=root/'mail-accounts.json';registry.write_text('bad')
            with self.assertRaises(ValueError): AccountManager(root)
            self.assertEqual(registry.read_text(),'bad')

class OAuthCallbackTests(unittest.TestCase):
    def test_pkce_uses_existing_localhost_registration_and_rejects_wrong_state(self):
        import base64,hashlib,threading,time
        from urllib.parse import urlsplit,parse_qs,urlencode
        from urllib.request import build_opener,ProxyHandler
        from urllib.error import HTTPError
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            manager=AccountManager(Path(folder));seen={};errors=[];threads=[]
            def browser(url):
                query=parse_qs(urlsplit(url).query);redirect=query['redirect_uri'][0]
                self.assertEqual(urlsplit(redirect).path,'')
                self.assertIn('https://graph.microsoft.com/User.Read',query['scope'][0].split())
                self.assertIn('https://graph.microsoft.com/Mail.Read',query['scope'][0].split())
                seen.update(query)
                def callback():
                    try:
                        opener=build_opener(ProxyHandler({}))
                        # Keep the registered redirect URI localhost, but avoid
                        # platform DNS/IPv6 fallback latency in this socket test.
                        loopback='http://127.0.0.1:'+str(urlsplit(redirect).port)
                        try:opener.open(loopback+'/?'+urlencode(dict(state='wrong',code='attack')),timeout=3)
                        except HTTPError as exc:self.assertEqual(exc.code,400)
                        with opener.open(loopback+'/?'+urlencode(dict(state=query['state'][0],code='accepted')),timeout=3) as response:self.assertEqual(response.status,200)
                    except Exception as exc:errors.append(exc)
                thread=threading.Thread(target=callback);threads.append(thread);thread.start()
                return True
            def token(client,payload):
                self.assertEqual(payload['code'],'accepted')
                actual=base64.urlsafe_b64encode(hashlib.sha256(payload['code_verifier'].encode()).digest()).decode().rstrip('=')
                self.assertEqual(actual,seen['code_challenge'][0])
                return dict(access_token='synthetic-token')
            manager.token_request=token;end=time.monotonic()+5
            with patch('task_backend.mail_accounts.webbrowser.open',browser), \
                    patch('socket.getfqdn',side_effect=AssertionError('OAuth loopback listener must not need DNS')):
                result=manager.oauth_login('00000000-0000-4000-8000-000000000001',lambda:time.monotonic()<end)
            for thread in threads:thread.join(5)
            self.assertEqual(errors,[]);self.assertEqual(result['access_token'],'synthetic-token')

    def test_token_exchange_and_refresh_request_profile_and_mail_scopes(self):
        import json
        from urllib.parse import parse_qs
        with tempfile.TemporaryDirectory() as folder:
            seen=[]
            def request(url,body,headers,limit,timeout):
                payload=parse_qs(body.decode());seen.append(payload)
                self.assertEqual(set(payload['scope'][0].split()),{
                    'openid','profile','offline_access',
                    'https://graph.microsoft.com/User.Read','https://graph.microsoft.com/Mail.Read'})
                return json.dumps(dict(access_token='synthetic-token')).encode()
            manager=AccountManager(Path(folder),request=request)
            for grant in ('authorization_code','refresh_token'):
                result=manager.token_request('00000000-0000-4000-8000-000000000001',dict(grant_type=grant))
                self.assertEqual(result['access_token'],'synthetic-token')
            self.assertEqual([v['grant_type'][0] for v in seen],['authorization_code','refresh_token'])

class CommitSafetyTests(unittest.TestCase):
    def test_separate_connection_cannot_restore_credentials_after_disconnect(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);first=AccountManager(root,reader_factory=lambda *args:Reader());account=first.add('imap');id=account['id']
            first.configure_imap(id,dict(email='a@example.invalid',username='',host='imap.example.invalid',port=993,security='tls',password='private-code'))
            snapshot=first.read(id);generation=first.generations.get(id,0)
            other=AccountManager(root);other.disconnect(id)
            with self.assertRaises(ValueError): first.commit(id,snapshot,generation,dict(connected=True))
            self.assertFalse(other.get(id)['state']['imap']['hasSecret'])
