"""Config defaults, glance log counts, and the optional remote host."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import config
import glance
import remote_host


class ConfigTests(unittest.TestCase):
    def test_defaults_are_off_and_public_urls_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load_config(environ={}, root=tmp)
            self.assertFalse(cfg['remote']['enabled'])
            self.assertEqual(cfg['remote']['base_url'], '')
            self.assertEqual(cfg['glance']['path'], '')
            self.assertEqual(cfg['live_tap']['listen_host'], '127.0.0.1')
            path = Path(tmp) / 'observatory.json'
            path.write_text(json.dumps({'remote': {'enabled': True, 'base_url': 'http://example.test:8767'}}), encoding='utf-8')
            rejected = config.load_config(environ={'OBSERVATORY_CONFIG': str(path)}, root=tmp)
            self.assertFalse(rejected['remote']['enabled'])
            self.assertIn('127.0.0.1', rejected['remote']['detail'])
            path.write_text(json.dumps({'remote': {'enabled': True, 'base_url': 'http://127.0.0.1:18766', 'label': 'Bench'}}), encoding='utf-8')
            accepted = config.load_config(environ={'OBSERVATORY_CONFIG': str(path)}, root=tmp)
            self.assertTrue(accepted['remote']['enabled'])
            self.assertEqual(accepted['remote']['label'], 'Bench')
            public = config.public_view(accepted, '0.2.0', False)
            self.assertNotIn('18766', json.dumps(public))
            self.assertNotIn('base_url', json.dumps(public))

    def test_example_file_enables_nothing(self):
        example = json.loads(Path('observatory.example.json').read_text(encoding='utf-8'))
        self.assertFalse(example['remote']['enabled'])
        self.assertEqual(example['remote']['base_url'], '')
        self.assertEqual(example['live_tap']['token_file'], '')
        self.assertEqual(example['glance']['path'], '')


class GlanceTests(unittest.TestCase):
    def test_jsonl_classifies_routes_and_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'calls.jsonl'
            path.write_text(Path('glance.example.jsonl').read_text(encoding='utf-8'), encoding='utf-8')
            cfg = {'glance': {'path': str(path), 'format': 'jsonl',
                              'local_providers': ['ollama', 'local'],
                              'external_providers': ['openai']}}
            data = glance.collect(cfg)
            self.assertTrue(data['enabled'])
            self.assertEqual(data['routing']['all'], {'local': 1, 'external': 1, 'unknown': 1})
            self.assertEqual(data['routing']['first_time'], '2026-01-02 03:04:05')

    def test_pattern_and_module_readers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'calls.log'
            path.write_text('2026-01-02 01:00:00 route=local model=a\n2026-01-02 02:00:00 route=external model=b\n', encoding='utf-8')
            cfg = {'glance': {'path': str(path), 'format': 'pattern',
                              'line_pattern': r'(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*route=(?P<route>local|external|unknown)',
                              'local_providers': [], 'external_providers': []}}
            data = glance.collect(cfg)
            self.assertEqual(data['routing']['all']['local'], 1)
            self.assertEqual(data['routing']['all']['external'], 1)
            module = Path(tmp) / 'sample_reader.py'
            module.write_text('def read_glance(path, options):\n    return {"all": {"local": 4, "external": 1, "unknown": 0}, "first_time": "2026-01-01 00:00:00", "last_time": "2026-01-01 01:00:00"}\n', encoding='utf-8')
            import sys
            sys.path.insert(0, tmp)
            try:
                plugged = glance.collect({'glance': {'path': str(path), 'format': 'module', 'module': 'sample_reader'}})
            finally:
                sys.path.remove(tmp)
            self.assertEqual(plugged['routing']['all']['local'], 4)
            self.assertEqual(plugged['routing']['source'], 'module')

    def test_missing_source_is_disabled(self):
        data = glance.collect({'glance': {'path': ''}})
        self.assertFalse(data['enabled'])
        self.assertIsNone(data['routing'])


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.posts = []
        self.redirects = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload, location=None):
                body = json.dumps(payload).encode()
                self.send_response(status)
                if location:
                    self.send_header('Location', location)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if 'bounce=1' in self.path:
                    self.server.redirects.append(self.path)
                    self._send(302, {'error': 'SECRET-BODY'}, location='http://127.0.0.1:9/should-not-follow')
                elif self.path.startswith('/api/hardware'):
                    self._send(200, {'sampled_at': 10, 'chip': 'Example CPU', 'os': 'Linux', 'unified_memory': False,
                                     'cpu_percent': 12, 'memory': {'total': 1000, 'used': 400, 'available': 600, 'percent': 40, 'pressure': 'Normal'},
                                     'disk': {'total': 5000, 'used': 1000, 'free': 4000},
                                     'gpus': [{'name': 'Example GPU', 'total': 8000, 'used': 2000, 'percent': 25, 'temperature': 55, 'power_draw': 40.5, 'power_limit': 80}],
                                     'ollama': {'status': 'Available', 'installed': [{'name': 'example:1b', 'parameters': '1B', 'quantization': 'Q4'}],
                                                'loaded': [{'name': 'example:1b', 'size': 500, 'vram': 500, 'context_length': 4096, 'parameters': '1B', 'quantization': 'Q4'}]}})
                elif self.path.startswith('/api/performance'):
                    self._send(200, {'results': [{'model': 'example:1b', 'sampled_at': 10, 'tokens_per_second': 40, 'prompt_tokens_per_second': 200,
                                                  'first_token_seconds': 0.2, 'wall_seconds': 1, 'load_seconds': 0.01, 'output_tokens': 8, 'prompt_tokens': 4,
                                                  'message': {'content': 'hidden reply'}}]})
                else:
                    self._send(404, {'error': 'SECRET-BODY'})

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0') or 0)
                raw = self.rfile.read(length) if length else b''
                self.server.posts.append((self.path, self.command, raw, self.headers.get('Origin')))
                if self.path.startswith('/api/performance'):
                    self._send(200, {'model': 'example:1b', 'sampled_at': 11, 'tokens_per_second': 41, 'prompt_tokens_per_second': 180,
                                     'output_tokens': 8, 'prompt_tokens': 4, 'first_token_seconds': 0.3, 'wall_seconds': 1.2, 'load_seconds': 0.01})
                elif self.path.startswith('/api/tokenize'):
                    self._send(200, {'model': 'example:1b', 'count': 1, 'chars': 2, 'tokens': [[1, 'ab']], 'accuracy': 'verified',
                                     'context': {'loaded': 1024}, 'source': 'blob', 'debug': 'SECRET-BODY'})
                else:
                    self._send(400, {'error': 'SECRET-BODY'})

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.posts = self.posts
        self.httpd.redirects = self.redirects
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.cfg = config.load_config(environ={}, root=tempfile.gettempdir())
        self.cfg['remote'].update({'enabled': True, 'base_url': 'http://127.0.0.1:%d' % self.httpd.server_port,
                                    'host_header': '', 'origin': '', 'timeout_seconds': 10, 'label': 'Remote host', 'detail': ''})

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_reads_are_get_only_and_drop_unexpected_fields(self):
        hardware = remote_host.hardware(self.cfg)
        self.assertEqual(hardware['gpus'][0]['temperature'], 55)
        self.assertEqual(hardware['ollama']['loaded'][0]['name'], 'example:1b')
        history = remote_host.history(self.cfg)
        self.assertEqual(history['results'][0]['prompt_tokens_per_second'], 200)
        self.assertNotIn('hidden reply', json.dumps(history))
        self.assertEqual(self.posts, [])

    def test_run_and_tokenize_post_only_when_called_and_strip_extra_fields(self):
        result = remote_host.run_test(self.cfg, 'example:1b')
        self.assertEqual(result['tokens_per_second'], 41)
        tokens = remote_host.tokenize(self.cfg, 'example:1b', 'ab')
        self.assertEqual(tokens['tokens'], [[1, 'ab']])
        self.assertNotIn('SECRET-BODY', json.dumps(tokens))
        methods = [item[1] for item in self.posts]
        self.assertEqual(methods, ['POST', 'POST'])
        self.assertTrue(all(item[3].startswith('http://127.0.0.1:') for item in self.posts))

    def test_redirects_and_unloaded_models_do_not_leak_or_post(self):
        self.cfg['remote']['base_url'] = 'http://127.0.0.1:%d' % self.httpd.server_port
        with self.assertRaises(remote_host.RemoteError):
            remote_host.run_test(self.cfg, 'not-loaded')
        self.assertEqual(self.posts, [])
        with self.assertRaises(remote_host.RemoteError) as error:
            remote_host._call(self.cfg, 'GET', '/api/hardware?bounce=1')
        self.assertNotIn('SECRET-BODY', str(error.exception))
        self.assertEqual(len(self.redirects), 1)

    def test_disabled_remote_does_not_call_out(self):
        off = config.load_config(environ={}, root=tempfile.mkdtemp())
        with self.assertRaises(remote_host.RemoteError) as error:
            remote_host.hardware(off)
        self.assertEqual(error.exception.status, 404)


class ServerRemoteTests(unittest.TestCase):
    def test_unconfigured_remote_routes_are_not_a_proxy(self):
        import server
        httpd = server.make_server(0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with patch.dict(os.environ, {'OBSERVATORY_CONFIG': ''}, clear=False):
                # An empty override still falls through to observatory.json only when that file exists.
                for path in ('/api/remote/hardware', '/api/remote/performance', '/api/dragon/account', '/api/dragon/snapshot'):
                    request = urllib.request.Request('http://127.0.0.1:%d%s' % (httpd.server_port, path))
                    with self.assertRaises(HTTPError) as error:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(error.exception.code, 404)
                    error.exception.close()
                with urllib.request.urlopen('http://127.0.0.1:%d/api/config' % httpd.server_port, timeout=5) as response:
                    body = json.load(response)
                self.assertFalse(body['remote']['enabled'])
                self.assertNotIn('base_url', body['remote'])
                with urllib.request.urlopen('http://127.0.0.1:%d/api/live' % httpd.server_port, timeout=5) as response:
                    live = json.load(response)
                self.assertEqual(live['state'], 'idle')
                self.assertFalse(live['tap']['listening'])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == '__main__':
    unittest.main()
