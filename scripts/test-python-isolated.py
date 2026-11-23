"""Noninteractive regression runner; never reads a developer's macOS Keychain.

Only the macOS master-key boundary is replaced. Real Fernet encryption and all
business tests still run. Windows keeps its native DPAPI behavior. This is not
acceptance testing for macOS Keychain access or a signed application.
"""
from contextlib import nullcontext
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root / 'backend')
    sys.path.insert(0, str(root / 'backend'))
    boundary = nullcontext()
    if sys.platform == 'darwin':
        from cryptography.fernet import Fernet
        boundary = patch('task_backend.mac_keychain.master_key', return_value=Fernet.generate_key())
    with boundary:
        unittest.main(module=None, argv=['unittest', *(sys.argv[1:] or ['discover', '-s', 'tests', '-v'])])


if __name__ == '__main__':
    main()
