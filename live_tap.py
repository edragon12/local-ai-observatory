"""Optional "Live from Open WebUI" receiver.

An Open WebUI filter (openwebui_live_filter.py) can POST chat-turn events here.
The receiver starts only when its token file exists. Text stays in memory
(newest 3 turns, dropped after 10 minutes) and is never written to disk or logged.

A route label is a leading chunk matching route_label_pattern (default: a bold
markdown label such as "**Local · name**" followed by a blank line). That label
is recorded as the route and is not the first generated token: the first-token
clock starts on the first chunk that contains reply content after the label.
Direct streams, with no label, still time the first chunk.

A caller's own proxy may add route_reason (short text) and timing with
route_seconds and left_network_seconds. Missing values stay null. This
receiver does not invent them and does not include a router.

GET /api/live returns text only when text=1. Otherwise it returns counts and
per-token character lengths, not token ids.
"""
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HOLD_SECONDS = 600
MAX_TURNS = 3
MAX_BODY = 256 * 1024
MAX_PROMPT_CHARS = 20000
MAX_CONTEXT_CHARS = 20000
MAX_REPLY_CHARS = 20000
MAX_EVENTS = 500
TURN_ID = re.compile(r'[A-Za-z0-9_-]{1,80}')
MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,199}')
DEFAULT_ROUTE_LABEL = re.compile(r'\A\*\*([^*\n]{1,80})\*\*\n\n')
DEFAULT_NETS = ('127.0.0.0/8', '::1/128')
TIMING_KEYS = ('route_seconds', 'left_network_seconds')
MAX_TIMING = 86400.0


def default_token_path(environ=None):
    env = os.environ if environ is None else environ
    override = env.get('OBSERVATORY_LIVE_TOKEN_FILE')
    if override:
        return Path(override)
    return Path.home() / '.config' / 'local-ai-observatory' / 'live-tap.token'


def init_token(path=None):
    """Create the token file (mode 0600) if it is missing. Returns the token."""
    path = Path(path or default_token_path())
    if path.exists():
        return path.read_text(encoding='utf-8').strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    token = secrets.token_urlsafe(32)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(token + '\n')
    return token


def read_token(path=None):
    try:
        token = Path(path or default_token_path()).read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return token if len(token) >= 24 else None


def compile_label(pattern):
    if hasattr(pattern, 'match'):
        return pattern
    try:
        compiled = re.compile(pattern or '')
    except re.error:
        return DEFAULT_ROUTE_LABEL
    return compiled if compiled.groups >= 1 else DEFAULT_ROUTE_LABEL


def clean_route_reason(value):
    """Short text, or None. Whitespace is collapsed. Nothing is inferred."""
    if not isinstance(value, str):
        return None
    text = ' '.join(value.split())
    return text[:120] if text else None


def clean_timing(value):
    """Both known keys, null when absent or not a finite non-negative number."""
    out = {key: None for key in TIMING_KEYS}
    if not isinstance(value, dict):
        return out
    for key in TIMING_KEYS:
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        if raw != raw or raw < 0 or raw > MAX_TIMING:
            continue
        out[key] = round(float(raw), 3)
    return out


def merge_timing(current, incoming):
    """Fill keys the new event actually reports. Invalid keys do not erase a prior value."""
    base = clean_timing(current)
    if not isinstance(incoming, dict):
        return base
    parsed = clean_timing(incoming)
    for key in TIMING_KEYS:
        if key in incoming and parsed[key] is not None:
            base[key] = parsed[key]
    return base


def classify_route(label, local_prefix):
    """local, external, or direct. A missing label is direct, not a guess."""
    if label and str(label).lower().startswith((local_prefix or 'local').lower()):
        return 'local'
    if label:
        return 'external'
    return 'direct'


def split_label(text, pattern):
    """Return (label or None, reply text without a leading label)."""
    if not text:
        return None, ''
    match = pattern.match(text)
    if not match:
        return None, text
    label = match.group(1).strip()[:80] if match.lastindex else ''
    if not label:
        return None, text
    return label, text[match.end():]


def networks(items):
    out = []
    for item in items or DEFAULT_NETS:
        try:
            out.append(ipaddress.ip_network(item))
        except ValueError:
            continue
    return out or [ipaddress.ip_network(item) for item in DEFAULT_NETS]


class Turn:
    __slots__ = ('id', 'model', 'started', 'first', 'last', 'ended', 'prompt', 'context', 'messages', 'reply', 'seq',
                 'route_reason', 'timing')

    def __init__(self, turn_id, model, started):
        self.id, self.model, self.started = turn_id, model, started
        self.first = self.last = self.ended = None
        self.prompt, self.context, self.messages, self.reply, self.seq = '', '', 0, '', 0
        self.route_reason = None
        self.timing = {key: None for key in TIMING_KEYS}


class LiveHub:
    def __init__(self, clock=time.time, route_label=None, local_prefix='local', local_model=''):
        self.clock = clock
        self.lock = threading.Lock()
        self.turns = []
        self.seq = 0
        self.events = 0
        self.last_event = None
        self.listening = False
        self.listen_error = None
        self._cache = {}
        self.route_label = compile_label(route_label or DEFAULT_ROUTE_LABEL)
        self.local_prefix = (local_prefix or 'local').lower()
        self.local_model = local_model or ''

    def configure(self, route_label=None, local_prefix='local', local_model=''):
        self.route_label = compile_label(route_label or DEFAULT_ROUTE_LABEL)
        self.local_prefix = (local_prefix or 'local').lower()
        self.local_model = local_model or ''

    def _turn(self, turn_id):
        return next((turn for turn in self.turns if turn.id == turn_id), None)

    def _expire(self, now):
        self.turns = [turn for turn in self.turns if now - (turn.ended or turn.last or turn.started) < HOLD_SECONDS][-MAX_TURNS:]
        live = {turn.id for turn in self.turns}
        self._cache = {key: value for key, value in self._cache.items() if key[0] in live}

    def accept(self, events):
        """Apply a batch of filter events. Invalid events are skipped."""
        if not isinstance(events, list):
            return 0
        now, applied = self.clock(), 0
        with self.lock:
            for event in events[:MAX_EVENTS]:
                if not isinstance(event, dict) or not isinstance(event.get('turn'), str) or not TURN_ID.fullmatch(event['turn']):
                    continue
                raw = event.get('t')
                at = raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) and abs(raw - now) < 5 else now
                kind, turn = event.get('type'), self._turn(event['turn'])
                if kind == 'start':
                    model = event.get('model') if isinstance(event.get('model'), str) and MODEL_ID.fullmatch(event.get('model')) else 'unknown'
                    if turn is None:
                        turn = Turn(event['turn'], model, at)
                        self.turns.append(turn)
                    turn.prompt = event.get('prompt')[:MAX_PROMPT_CHARS] if isinstance(event.get('prompt'), str) else ''
                    turn.context = event.get('context')[-MAX_CONTEXT_CHARS:] if isinstance(event.get('context'), str) else turn.prompt
                    messages = event.get('messages')
                    turn.messages = messages if type(messages) is int and 0 <= messages < 10000 else 0
                elif kind == 'delta' and turn is not None and turn.ended is None and isinstance(event.get('text'), str):
                    if not event['text']:
                        continue
                    turn.reply = (turn.reply + event['text'])[:MAX_REPLY_CHARS + 400]
                    turn.last = at
                    if turn.first is None:
                        # A label-only chunk is the route, not the first generated token.
                        _label, rest = split_label(turn.reply, self.route_label)
                        if _label is None or rest:
                            turn.first = at
                elif kind == 'end' and turn is not None:
                    turn.ended = at
                else:
                    continue
                self._apply_route_meta(turn, event)
                self.seq += 1
                turn.seq = self.seq
                applied += 1
            if applied:
                self.events += applied
                self.last_event = now
            self._expire(now)
        return applied

    def _apply_route_meta(self, turn, event):
        if 'route_reason' in event:
            reason = clean_route_reason(event.get('route_reason'))
            if reason:
                turn.route_reason = reason
        if 'timing' in event and isinstance(event.get('timing'), dict):
            turn.timing = merge_timing(turn.timing, event['timing'])

    def _recent(self):
        rows = []
        for item in self.turns:
            label, _rest = split_label(item.reply, self.route_label)
            rows.append({'id': item.id, 'route': classify_route(label, self.local_prefix)})
        return rows

    def snapshot(self, include_text=False, tokenize=None, placement=None, since=None):
        now = self.clock()
        with self.lock:
            self._expire(now)
            status = {'listening': self.listening, 'error': self.listen_error, 'events': self.events,
                      'last_event_age': round(now - self.last_event, 1) if self.last_event else None}
            recent = self._recent()
            if since is not None and since == self.seq and self.turns and self.turns[-1].ended:
                return {'seq': self.seq, 'unchanged': True, 'tap': status, 'recent': recent}
            turn = self.turns[-1] if self.turns else None
            if turn is None:
                return {'seq': self.seq, 'state': 'idle', 'tap': status, 'turn': None, 'recent': recent}
            data = {key: getattr(turn, key) for key in ('id', 'model', 'started', 'first', 'last', 'ended', 'prompt', 'context', 'messages', 'reply')}
            data['route_reason'] = turn.route_reason
            data['timing'] = dict(turn.timing)
        route_label, reply = split_label(data['reply'], self.route_label)
        reply = reply[:MAX_REPLY_CHARS]
        route = classify_route(route_label, self.local_prefix)
        model = self.local_model if route == 'local' and self.local_model else data['model']
        quiet = data['first'] and not data['ended'] and data['last'] and now - data['last'] > 8
        state = 'done' if (data['ended'] or quiet) else ('streaming' if data['first'] else 'waiting')
        if quiet:
            data['ended'] = data['last']
        out = {'seq': self.seq, 'tap': status, 'state': state, 'route': route, 'route_label': route_label,
               'route_reason': data['route_reason'], 'timing': clean_timing(data['timing']), 'recent': recent,
               'openwebui_model': data['model'], 'model': model, 'messages': data['messages'],
               'started_ago': round(now - data['started'], 2),
               'first_token_seconds': round(data['first'] - data['started'], 3) if data['first'] else None,
               'elapsed_seconds': round(((data['ended'] or now) if data['first'] else now) - data['started'], 2),
               'stream_seconds': round((data['last'] or 0) - data['first'], 3) if data['first'] and data['last'] else None}
        tok_model = model if route != 'external' else (self.local_model or data['model'])
        out['tokenizer_model'] = tok_model
        out['prompt'] = self._tokens(data['id'], 'prompt', data['prompt'], tok_model, tokenize, include_text)
        out['context'] = self._count(data['id'], 'context', data['context'], tok_model, tokenize)
        out['reply'] = self._tokens(data['id'], 'reply', reply, tok_model, tokenize, include_text)
        count = out['reply'].get('count') if isinstance(out['reply'], dict) else None
        if count and count > 1 and out['stream_seconds']:
            out['tokens_per_second'] = round((count - 1) / out['stream_seconds'], 2)
        else:
            out['tokens_per_second'] = None
        out['placement'] = placement(model) if (placement and route != 'external') else None
        out['text_included'] = bool(include_text)
        return out

    def _tokenized(self, turn_id, part, text, model, tokenize):
        key = (turn_id, part)
        hit = self._cache.get(key)
        if hit and hit[0] == text and hit[1] == model:
            return hit[2]
        try:
            result = tokenize(model, text) if (tokenize and text) else None
        except Exception:
            result = None
        with self.lock:
            if any(turn.id == turn_id for turn in self.turns):
                self._cache[key] = (text, model, result)
        return result

    def _count(self, turn_id, part, text, model, tokenize):
        result = self._tokenized(turn_id, part, text, model, tokenize)
        return {'count': result.get('count') if result else None, 'chars': len(text), 'clipped': len(text) >= MAX_CONTEXT_CHARS}

    def _tokens(self, turn_id, part, text, model, tokenize, include_text):
        result = self._tokenized(turn_id, part, text, model, tokenize)
        base = {'chars': len(text), 'count': None, 'accuracy': None, 'context': None}
        if not result:
            if include_text:
                base['text'] = text
            return base
        base.update(count=result.get('count'), accuracy=result.get('accuracy'), context=result.get('context'),
                    truncated=bool(result.get('truncated')))
        specials = set(result.get('special_ids') or [])
        if include_text:
            base['tokens'] = result.get('tokens') or []
            base['special_ids'] = sorted(specials)
        else:
            base['lengths'] = [[len(piece), 1 if token_id in specials else 0] for token_id, piece in (result.get('tokens') or [])]
        return base


HUB = LiveHub()


class TapHandler(BaseHTTPRequestHandler):
    hub = HUB
    token_file = None
    allowed = networks(DEFAULT_NETS)

    def log_message(self, *args):
        pass

    def _done(self, status):
        self.send_response(status)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        self._done(404)

    def do_POST(self):
        try:
            source = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return self._done(403)
        if not any(source in net for net in self.allowed):
            return self._done(403)
        token = read_token(self.token_file)
        given = self.headers.get('Authorization', '')
        if not token or not hmac.compare_digest(given.encode(), ('Bearer ' + token).encode()):
            return self._done(401)
        if self.path != '/live/events' or not str(self.headers.get('Content-Type', '')).startswith('application/json') \
                or self.headers.get('Transfer-Encoding'):
            return self._done(400)
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return self._done(400)
        if length <= 0 or length > MAX_BODY:
            return self._done(413)
        try:
            body = json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return self._done(400)
        self.hub.accept(body.get('events') if isinstance(body, dict) else None)
        return self._done(204)


def start_receiver(hub=None, host='127.0.0.1', port=8768, token_file=None, allowed=None):
    """Start the receiver thread when the token file exists. Never raises."""
    hub = hub or HUB
    token_file = Path(token_file or default_token_path())
    if not read_token(token_file):
        hub.listening, hub.listen_error = False, 'not enabled (no token file)'
        return None
    try:
        handler = type('BoundTapHandler', (TapHandler,), {
            'hub': hub, 'token_file': token_file, 'allowed': networks(allowed)})
        server = ThreadingHTTPServer((host, int(port)), handler)
    except OSError as exc:
        hub.listening, hub.listen_error = False, 'could not listen (%s)' % (exc.strerror or 'error')
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name='live-tap', daemon=True).start()
    hub.listening, hub.listen_error = True, None
    return server


def ollama_placement(model, opener=None):
    """size versus size_vram for one loaded model. Read-only."""
    from urllib.request import Request, build_opener, ProxyHandler
    try:
        with (opener or build_opener(ProxyHandler({}))).open(Request('http://127.0.0.1:11434/api/ps'), timeout=3) as response:
            models = json.load(response).get('models') or []
    except Exception:
        return {'loaded': None}
    for row in models:
        if isinstance(row, dict) and (row.get('name') == model or row.get('model') == model):
            size, vram = row.get('size'), row.get('size_vram')
            return {'loaded': True,
                    'size': size if type(size) is int else None,
                    'vram': vram if type(vram) is int else None,
                    'context': row.get('context_length') if type(row.get('context_length')) is int else None}
    return {'loaded': False}


_PLACEMENT = {'at': 0.0, 'model': None, 'value': None}


def cached_placement(model):
    now = time.time()
    if _PLACEMENT['model'] != model or now - _PLACEMENT['at'] > 1.5:
        _PLACEMENT.update(at=now, model=model, value=ollama_placement(model))
    return _PLACEMENT['value']


def live_response(query, hub=None):
    """JSON body for GET /api/live?text=0|1&since=N."""
    from tokenize_api import tokenize
    hub = hub or HUB
    include = query.get('text', ['0'])[0] == '1'
    since = query.get('since', [''])[0]
    since = int(since) if str(since).isdigit() else None
    return hub.snapshot(include_text=include, tokenize=tokenize, placement=cached_placement, since=since)


def apply_config(cfg, hub=None):
    live = (cfg or {}).get('live_tap') or {}
    (hub or HUB).configure(live.get('route_label_pattern'), live.get('local_label_prefix') or 'local', live.get('local_model') or '')


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--init']:
        path = default_token_path()
        existed = path.exists()
        token = init_token(path)
        print(('Token file already existed: ' if existed else 'Created ') + str(path))
        print('Paste this token into the Open WebUI filter token valve. It is not stored by this script beyond that file.')
        print(token)
    else:
        print('usage: python3 live_tap.py --init')
