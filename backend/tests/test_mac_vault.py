"""Offline crypto regressions; the separate native smoke tests the Keychain."""
import base64
import sys
import unittest
from unittest.mock import patch
from task_backend import vault


@unittest.skipUnless(sys.platform == 'darwin', 'macOS credential format')
class MacVaultTests(unittest.TestCase):
    def setUp(self):
        from cryptography.fernet import Fernet
        self.key = Fernet.generate_key()
        self.keys = patch('task_backend.mac_keychain.master_key', return_value=self.key)
        self.keys.start()
        self.addCleanup(self.keys.stop)

    def test_authenticated_ciphertext_is_native_and_roundtrips(self):
        value = 'synthetic-secret-中文'
        encrypted = vault.encrypt(value)
        raw = base64.b64decode(encrypted)
        self.assertTrue(vault.is_native(encrypted))
        self.assertNotIn(value.encode(), raw)
        self.assertEqual(vault.decrypt(encrypted), value)
        self.assertNotEqual(vault.encrypt(value), encrypted)

    def test_corrupted_or_other_machine_ciphertext_fails_closed(self):
        from cryptography.fernet import Fernet
        encrypted = vault.encrypt('synthetic')
        with patch('task_backend.mac_keychain.master_key', return_value=Fernet.generate_key()):
            with self.assertRaisesRegex(ValueError, '无法解密'):
                vault.decrypt(encrypted)
        raw = bytearray(base64.b64decode(encrypted))
        raw[-8] ^= 1
        with self.assertRaisesRegex(ValueError, '无法解密'):
            vault.decrypt(base64.b64encode(raw).decode())

    def test_locked_keychain_does_not_fallback_to_plaintext(self):
        with patch('task_backend.mac_keychain.master_key', side_effect=ValueError('钥匙串锁定')):
            with self.assertRaisesRegex(ValueError, '钥匙串锁定'):
                vault.encrypt('never-store-unprotected')


if __name__ == '__main__':
    unittest.main()
