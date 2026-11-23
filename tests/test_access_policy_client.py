"""Offline regression checks for GitHub request pacing and rate-limit recovery."""
import importlib.util
import io
from email.message import Message
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

spec = importlib.util.spec_from_file_location(
    'access_policy', Path(__file__).resolve().parents[1] / 'scripts/sync-access-policy.py'
)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def error(code=429, body='You have exceeded a secondary rate limit.', **headers):
    message = Message()
    for name, value in headers.items():
        message[name] = value
    return HTTPError('https://api.github.com/test', code, 'error', message, io.BytesIO(body.encode()))


def success():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"ok":true}'
    return response


class RequestTests(unittest.TestCase):
    def setUp(self):
        client._last_write_finished = None
        self.sleep = patch.object(client.time, 'sleep').start()
        self.clock = patch.object(client.time, 'monotonic', return_value=100.0).start()
        self.addCleanup(patch.stopall)

    def test_secondary_limit_recovers_without_headers(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=[error(), success()]) as request:
            self.assertEqual(client.request_json('test-token', 'PUT', '/test', {'permission': 'push'}), {'ok': True})
            self.assertEqual(request.call_count, 2)
            self.assertEqual(request.call_args_list[0].args[0].data, request.call_args_list[1].args[0].data)
        self.sleep.assert_any_call(60.0)

    def test_retry_after_is_respected(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=[error(**{'Retry-After': '150'}), success()]):
            client.request_json('test-token', 'GET', '/test')
        self.sleep.assert_called_once_with(150.0)

    def test_primary_reset_is_respected(self):
        exc = error(403, **{'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1250'})
        with patch.object(client.time, 'time', return_value=1000):
            self.assertEqual(client.rate_limit_delay(exc, '', 0), 251)

    def test_repeated_throttles_stop_after_three_retries(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=[error() for _ in range(4)]) as request:
            with self.assertRaises(client.PolicyError):
                client.request_json('test-token', 'GET', '/test')
            self.assertEqual(request.call_count, 4)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [60, 120, 240])

    def test_long_server_wait_fails_without_early_retry(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=error(**{'Retry-After': '601'})) as request:
            with self.assertRaises(client.PolicyError):
                client.request_json('test-token', 'GET', '/test')
            self.assertEqual(request.call_count, 1)
        self.sleep.assert_not_called()

    def test_permission_failure_is_not_retried(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=error(403, 'Resource not accessible by integration')) as request:
            with self.assertRaises(client.PolicyError):
                client.request_json('test-token', 'GET', '/test')
            self.assertEqual(request.call_count, 1)
        self.sleep.assert_not_called()

    def test_network_failure_is_not_replayed(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=URLError('connection lost')) as request:
            with self.assertRaises(client.PolicyError):
                client.request_json('test-token', 'POST', '/test', {})
            self.assertEqual(request.call_count, 1)

    def test_writes_are_spaced_even_with_intervening_read(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=[success(), success(), success()]):
            client.request_json('test-token', 'PATCH', '/test', {})
            client.request_json('test-token', 'GET', '/test')
            client.request_json('test-token', 'DELETE', '/test')
        self.sleep.assert_called_once_with(1.0)

    def test_allowed_404_does_not_retry(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=error(404, 'Not Found')):
            self.assertIsNone(client.request_json('test-token', 'GET', '/test', allow_404=True))
        self.sleep.assert_not_called()

    def test_invalid_headers_use_default_backoff(self):
        self.assertEqual(client.rate_limit_delay(error(**{'Retry-After': 'nan'}), '', 1), 120)
        self.assertEqual(client.rate_limit_delay(error(**{'Retry-After': 'invalid'}), '', 0), 60)


if __name__ == '__main__':
    unittest.main()
