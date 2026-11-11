"""Loopback-only Local AI Observatory. No accounts required.

Optional pieces (a remote host, the Open WebUI live tap, a glance log) stay off
until observatory.json or environment variables enable them. See config.py.
"""
import argparse
import json
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import config
import glance
import live_tap
import mac_metrics
import performance
import remote_host
import tokenize_api


ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
SECURITY = {
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}
FILES = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
TOKEN_LOCK = threading.Lock()


def failure_message(exc):
    """User-facing test failure. ValueError text is ours; connection text is fixed."""
    if isinstance(exc, ValueError) and str(exc):
        return str(exc)
    if isinstance(exc, OSError) and getattr(exc, 'code', None):
        return 'Ollama returned an error. The test was not recorded.'
    if isinstance(exc, OSError):
        return 'Ollama is not reachable at 127.0.0.1:11434.'
    return 'Test could not complete.'


def token_path(cfg):
    configured = (cfg.get('live_tap') or {}).get('token_file') or ''
    return Path(configured) if configured else live_tap.default_token_path()


def glance_payload(cfg):
    if cfg['glance']['path']:
        return glance.collect(cfg)
    if remote_host.enabled(cfg):
        try:
            return remote_host.glance(cfg)
        except remote_host.RemoteError as exc:
            return {'enabled': False, 'routing': None, 'detail': str(exc)}
    return glance.collect(cfg)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, data, mime='application/json'):
        body = json.dumps(data).encode() if mime == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        for key, value in SECURITY.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def no_content(self):
        self.send_response(204)
        self.send_header('Content-Length', '0')
        for key, value in SECURITY.items():
            self.send_header(key, value)
        self.end_headers()

    def local(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def _cfg(self):
        return config.load_config()

    def _remote_error(self, exc):
        self.reply(exc.status, {'error': str(exc)})

    def do_GET(self):
        if not self.local():
            return self.reply(403, {'error': 'Local Host required'})
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)
        cfg = self._cfg()
        if path == '/api/hardware':
            try:
                return self.reply(200, mac_metrics.collect())
            except Exception:
                return self.reply(503, {'error': 'Telemetry unavailable'})
        if path == '/api/performance':
            return self.reply(200, performance.history())
        if path == '/api/config':
            live_on = live_tap.read_token(token_path(cfg)) is not None or live_tap.HUB.listening
            return self.reply(200, config.public_view(cfg, VERSION, live_on))
        if path == '/api/glance':
            return self.reply(200, glance_payload(cfg))
        if path == '/api/live':
            live_tap.apply_config(cfg, live_tap.HUB)
            return self.reply(200, live_tap.live_response(query))
        if path == '/api/remote/hardware':
            try:
                return self.reply(200, remote_host.hardware(cfg))
            except remote_host.RemoteError as exc:
                return self._remote_error(exc)
        if path == '/api/remote/performance':
            try:
                return self.reply(200, remote_host.history(cfg))
            except remote_host.RemoteError as exc:
                return self._remote_error(exc)
        if path == '/api/remote/live':
            try:
                since = query.get('since', [''])[0]
                return self.reply(200, remote_host.live(cfg, query.get('text', ['0'])[0] == '1', int(since) if since.isdigit() else None))
            except remote_host.RemoteError as exc:
                return self._remote_error(exc)
        if path == '/api/remote/glance':
            try:
                return self.reply(200, remote_host.glance(cfg))
            except remote_host.RemoteError as exc:
                return self._remote_error(exc)
        if path == '/favicon.ico':
            return self.no_content()
        if path not in FILES:
            return self.reply(404, {'error': 'Not found'})
        name, mime = FILES[path]
        self.reply(200, (ROOT / name).read_bytes(), mime)

    def _tokenize(self, remote):
        cfg = self._cfg()
        try:
            model, text = tokenize_api.read_request(self.headers, self.rfile)
        except tokenize_api.TokenizeError as exc:
            return self.reply(400, {'error': str(exc)})
        if not TOKEN_LOCK.acquire(blocking=False):
            return self.reply(409, {'error': 'A tokenizer request is already running.'})
        try:
            if not remote:
                try:
                    return self.reply(200, remote_host.sanitize_tokens(tokenize_api.tokenize(model, text)))
                except tokenize_api.TokenizeError as exc:
                    return self.reply(400, {'error': str(exc)})
                except (OSError, ValueError, remote_host.RemoteError):
                    return self.reply(503, {'error': 'This model’s tokenizer could not be read.'})
            try:
                return self.reply(200, remote_host.tokenize(cfg, model, text))
            except remote_host.RemoteError as exc:
                return self._remote_error(exc)
        finally:
            TOKEN_LOCK.release()

    def do_POST(self):
        if not self.local() or self.headers.get('Origin') != 'http://' + self.headers.get('Host', ''):
            return self.reply(403, {'error': 'Same-origin request required'})
        target = urlsplit(self.path)
        if target.path in ('/api/tokenize', '/api/remote/tokenize'):
            return self._tokenize(target.path == '/api/remote/tokenize')
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
            return self.reply(400, {'error': 'Empty body required'})
        if target.path == '/api/performance':
            try:
                self.reply(200, performance.run(parse_qs(target.query).get('model', [''])[0]))
            except (ValueError, OSError) as exc:
                self.reply(400, {'error': failure_message(exc)})
            return
        if target.path == '/api/remote/performance':
            try:
                self.reply(200, remote_host.run_test(self._cfg(), parse_qs(target.query).get('model', [''])[0]))
            except remote_host.RemoteError as exc:
                self._remote_error(exc)
            return
        return self.reply(404, {'error': 'Not found'})


def make_server(port):
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args(argv)
    cfg = config.load_config()
    live_tap.apply_config(cfg, live_tap.HUB)
    live = cfg['live_tap']
    path = token_path(cfg)
    if live_tap.read_token(path):
        live_tap.start_receiver(live_tap.HUB, live['listen_host'], live['listen_port'], path, live['allowed_networks'])
    print(f'Local AI Observatory {VERSION}: http://127.0.0.1:{args.port}', flush=True)
    try:
        make_server(args.port).serve_forever()
    except KeyboardInterrupt:
        print('\nDashboard stopped.')
    except OSError as exc:
        parser.exit(1, f'Could not start on port {args.port}: {exc}\nStop the earlier dashboard or choose --port.\n')


if __name__ == '__main__':
    main()
