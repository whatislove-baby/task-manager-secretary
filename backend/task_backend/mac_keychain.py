"""Keep the local encryption master key in the macOS login Keychain.

Secrets never travel through command-line arguments or shell commands. The
Security framework controls access and presents its normal unlock dialog.
"""
import base64
import ctypes
import os
import sys
from functools import lru_cache

# A developer's Homebrew Python and the signed bundled Python have different
# Keychain identities. Their data directories are separate as well; do not make
# the release build request access to the developer interpreter's master key.
PACKAGED = '.app/Contents/' in os.path.realpath(sys.executable)
SERVICE = (b'com.gao.taskmanager.python-vault.packaged-v1' if PACKAGED
           else b'com.gao.taskmanager.python-vault')
ACCOUNT = b'local-fernet-key-v1'


@lru_cache(maxsize=1)
def framework():
    library = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    library.SecKeychainFindGenericPassword.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
        ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    library.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    library.SecKeychainAddGenericPassword.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
        ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
        ctypes.c_char_p, ctypes.c_void_p,
    ]
    library.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    library.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.SecKeychainItemFreeContent.restype = ctypes.c_int32
    return library


def read_key():
    library = framework()
    length = ctypes.c_uint32()
    pointer = ctypes.c_void_p()
    status = library.SecKeychainFindGenericPassword(
        None, len(SERVICE), SERVICE, len(ACCOUNT), ACCOUNT,
        ctypes.byref(length), ctypes.byref(pointer), None,
    )
    if status == -25300:  # errSecItemNotFound
        return None
    if status != 0:
        raise ValueError('无法访问 macOS 钥匙串，请解锁登录钥匙串并允许本应用访问（状态 %s）' % status)
    try:
        key = ctypes.string_at(pointer, length.value)
        if len(base64.urlsafe_b64decode(key)) != 32:
            raise ValueError('钥匙串中的应用加密密钥无效，请保留数据并检查钥匙串')
        return key
    finally:
        library.SecKeychainItemFreeContent(None, pointer)


@lru_cache(maxsize=1)
def master_key(create=False):
    key = read_key()
    if key is not None:
        return key
    if not create:
        raise ValueError('本机钥匙串中没有应用加密密钥，请在当前设备重新填写凭据')
    key = base64.urlsafe_b64encode(os.urandom(32))
    status = framework().SecKeychainAddGenericPassword(
        None, len(SERVICE), SERVICE, len(ACCOUNT), ACCOUNT, len(key), key, None,
    )
    if status == -25299:  # Another process created it while we were checking.
        existing = read_key()
        if existing is not None:
            return existing
    if status != 0:
        raise ValueError('无法将加密密钥保存到 macOS 钥匙串（状态 %s）' % status)
    return key
