"""Probe a fresh service over HTTP without invoking external providers."""
import json
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


def check(base_url):
    deadline = time.monotonic() + 60
    while True:
        try:
            with urlopen(base_url + '/api/health', timeout=2) as response:
                assert response.status == 200
                assert json.load(response) == {
                    'status': 'ok', 'service': 'cloudops-sentinel-self-rag'}
            break
        except (URLError, TimeoutError, ConnectionError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)
    for path, expected in [('/', b'CloudOps'), ('/static/app.js', b'/api/chat'),
                           ('/static/styles.css', b'{')]:
        with urlopen(base_url + path, timeout=5) as response:
            assert response.status == 200, path
            assert expected in response.read(), path
    with urlopen(base_url + '/api/audits', timeout=5) as response:
        assert response.status == 200
        assert json.load(response) == [], 'Fresh audit database should be empty'
    print('HTTP health, page, assets and fresh SQLite audit checks passed')


if __name__ == '__main__':
    check(sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8080')
