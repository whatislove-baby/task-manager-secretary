"""Windows DPAPI or macOS Keychain-backed Fernet; no plaintext key files."""
import base64
import ctypes
from ctypes import wintypes
import os
import sys

PREFIX=b'TM-PY1\x00'
MAC_PREFIX=b'TM-MAC1\x00'


def is_native(value):
    data=base64.b64decode(value,validate=True)
    return data.startswith((PREFIX,MAC_PREFIX))


def mac_cipher(create=False):
    from cryptography.fernet import Fernet
    from .mac_keychain import master_key
    return Fernet(master_key(create))


class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def transform(data,decrypt=False):
    if os.name!='nt': raise ValueError('当前加密适配器需要Windows系统加密')
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    result=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if decrypt:
        function=crypt.CryptUnprotectData
        function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        success=function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result))
    else:
        function=crypt.CryptProtectData
        function.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        success=function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result))
    if not success: raise ValueError('系统加密或解密失败，请在当前设备重新保存凭据')
    try: return ctypes.string_at(result.data,result.size)
    finally: kernel.LocalFree(ctypes.cast(result.data,ctypes.c_void_p))


def encrypt(value):
    if sys.platform=='darwin':
        return base64.b64encode(MAC_PREFIX+mac_cipher(True).encrypt(value.encode('utf-8'))).decode('ascii')
    return base64.b64encode(PREFIX+transform(value.encode('utf-8'))).decode('ascii')


def decrypt(value):
    try:
        data=base64.b64decode(value,validate=True)
        if data.startswith(MAC_PREFIX):
            if sys.platform!='darwin': raise ValueError('此凭据由 macOS 钥匙串保护')
            return mac_cipher().decrypt(data[len(MAC_PREFIX):]).decode('utf-8')
        if data.startswith(PREFIX): data=data[len(PREFIX):]
        # Legacy Electron on Windows can wrap DPAPI with a v10 header.
        elif data.startswith(b'v10'): data=data[3:]
        return transform(data,True).decode('utf-8')
    except Exception:
        raise ValueError('凭据无法解密，请在当前设备重新保存或重新授权') from None
