import asyncio
import socket
import unittest
from unittest.mock import patch

from network_guard import no_external_network


class OfflineNetworkTests(unittest.TestCase):
    def test_windows_socketpair_and_event_loop_still_work(self):
        # Windows uses this TCP fallback for asyncio's self-wakeup sockets.
        async def complete():
            await asyncio.sleep(0)
            return 'completed'

        # Older Python exposes the fallback only as socketpair on Windows.
        fallback = getattr(socket, '_fallback_socketpair', socket.socketpair)
        with no_external_network(), patch('socket.socketpair', fallback):
            self.assertEqual(asyncio.run(complete()), 'completed')

    def test_ip_and_hostname_destinations_remain_blocked(self):
        with no_external_network(), socket.socket() as sock:
            for method in (sock.connect, sock.connect_ex):
                for address in (('192.0.2.1', 443), ('example.invalid', 443), ('localhost', 443)):
                    with self.subTest(method=method.__name__, address=address):
                        with self.assertRaisesRegex(AssertionError, 'External network forbidden'):
                            method(address)

    def test_numeric_ipv4_and_ipv6_loopback_delegate_without_dns(self):
        with patch.object(socket.socket, 'connect', return_value=None) as original, \
                patch.object(socket.socket, 'connect_ex', return_value=0) as original_ex, \
                patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')), \
                no_external_network(), socket.socket() as sock:
            for address in (('127.0.0.1', 123), ('::1', 123, 0, 0)):
                sock.connect(address)
                self.assertEqual(sock.connect_ex(address), 0)
            self.assertEqual(original.call_count, 2)
            self.assertEqual(original_ex.call_count, 2)
