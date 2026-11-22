"""Offline test boundary that preserves Windows asyncio's local self-pipe."""
from contextlib import contextmanager
from ipaddress import ip_address
import socket
from unittest.mock import patch


@contextmanager
def no_external_network():
    """Permit numeric loopback only; do not resolve names or contact providers."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded(original):
        def connect(sock, address):
            try:
                local = isinstance(address, tuple) and ip_address(address[0]).is_loopback
            except (ValueError, TypeError, IndexError):
                local = False
            if not local:
                raise AssertionError('External network forbidden')
            return original(sock, address)
        return connect

    with patch.object(socket.socket, 'connect', guarded(original_connect)), \
            patch.object(socket.socket, 'connect_ex', guarded(original_connect_ex)):
        yield
