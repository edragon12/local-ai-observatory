"""Loopback-only Local AI Observatory community preview. No accounts required."""
import argparse
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
import mac_metrics
import performance
from urllib.request import Request, build_opener, ProxyHandler


ROOT=Path(__file__).resolve().parent
SECURITY={'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'no-referrer',
          'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}

def failure_message(exc):
    """User-facing test failure. ValueError text is ours; connection text is fixed."""
    if isinstance(exc, ValueError) and str(exc):
        return str(exc)
    if isinstance(exc, OSError) and getattr(exc, 'code', None):
        return 'Ollama returned an error. The test was not recorded.'
    if isinstance(exc, OSError):
        return 'Ollama is not reachable at 127.0.0.1:11434.'
    return 'Test could not complete.'

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, status, data, mime='application/json'):
        body=json.dumps(data).encode() if mime=='application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        for k,v in SECURITY.items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)
    def no_content(self):
        self.send_response(204)
        self.send_header('Content-Length', '0')
        for k,v in SECURITY.items(): self.send_header(k,v)
        self.end_headers()
    def local(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
    def do_GET(self):
        if not self.local(): return self.reply(403,{'error':'Local Host required'})
        path=urlsplit(self.path).path
        if path=='/api/hardware':
            try: return self.reply(200,mac_metrics.collect())
            except Exception: return self.reply(503,{'error':'Telemetry unavailable'})
        if path=='/api/performance': return self.reply(200,performance.history())
        if path=='/favicon.ico': return self.no_content()
        files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
        if path not in files: return self.reply(404,{'error':'Not found'})
        name,mime=files[path]; self.reply(200,(ROOT/name).read_bytes(),mime)
    def do_POST(self):
        if not self.local() or self.headers.get('Origin')!='http://'+self.headers.get('Host',''):
            return self.reply(403,{'error':'Same-origin request required'})
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length','0')!='0':
            return self.reply(400,{'error':'Empty body required'})
        target=urlsplit(self.path)
        if target.path!='/api/performance': return self.reply(404,{'error':'Not found'})
        try: self.reply(200,performance.run(parse_qs(target.query).get('model',[''])[0]))
        except (ValueError, OSError) as exc: self.reply(400, {'error': failure_message(exc)})

def make_server(port): return ThreadingHTTPServer(('127.0.0.1',port),Handler)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8767)
    args=parser.parse_args()
    print(f'Local AI Observatory: http://127.0.0.1:{args.port}',flush=True)
    try:
        make_server(args.port).serve_forever()
    except KeyboardInterrupt:
        print('\nDashboard stopped.')
    except OSError as exc:
        parser.exit(1, f'Could not start on port {args.port}: {exc}\nStop the earlier dashboard or choose --port.\n')
