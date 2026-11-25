import io
import unittest
from http.client import RemoteDisconnected
from unittest.mock import patch

from scripts.smoke_http import check


class Response(io.BytesIO):
    status = 200


class SmokeTests(unittest.TestCase):
    def test_startup_connection_resets_retry_until_real_responses(self):
        responses = [
            ConnectionResetError('port forwarded before app is ready'),
            RemoteDisconnected('server is still starting'),
            Response(b'{"status":"ok","service":"cloudops-sentinel-self-rag"}'),
            Response(b'CloudOps'), Response(b'/api/chat'), Response(b'{}'), Response(b'[]'),
        ]
        with patch('scripts.smoke_http.urlopen', side_effect=responses) as request, \
                patch('scripts.smoke_http.time.sleep') as sleep:
            check('http://127.0.0.1:8090')
        self.assertEqual(request.call_count, 7)
        self.assertEqual(sleep.call_count, 2)

    def test_startup_retries_stop_at_deadline(self):
        with patch('scripts.smoke_http.urlopen', side_effect=ConnectionResetError('not ready')), \
                patch('scripts.smoke_http.time.monotonic', side_effect=[0, 61]), \
                patch('scripts.smoke_http.time.sleep') as sleep:
            with self.assertRaises(ConnectionResetError):
                check('http://127.0.0.1:8090')
        sleep.assert_not_called()
