"""Optional second dashboard, reached only at a loopback URL the user configured.

This is not a general proxy. GET is limited to hardware, probe history, the live
view and glance counts. POST runs only when the user clicks Run test or Tokenize,
and only to the matching path on that one URL. Redirects are not followed.
"""
import json
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import live_tap
import observatory_v1
import tokenize_api


MODEL_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}')
GET_PATHS = {'/api/hardware', '/api/performance', '/api/live', '/api/glance'}
POST_PATHS = {'/api/performance', '/api/tokenize'}
LOCK = threading.Lock()


class RemoteError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise URLError('redirects are not followed')


def _opener():
    return build_opener(ProxyHandler({}), _NoRedirect)


def enabled(cfg):
    remote = (cfg or {}).get('remote') or {}
    return bool(remote.get('enabled') and remote.get('base_url'))


def _headers(cfg, method):
    remote = cfg['remote']
    host = remote['host_header'] or urlsplit(remote['base_url']).netloc
    headers = {'Host': host}
    if method == 'POST':
        headers['Origin'] = remote['origin'] or ('http://' + host)
    return headers


def _call(cfg, method, path, body=None, opener=None, timeout=8, content_type=None):
    if not enabled(cfg):
        raise RemoteError(404, 'Remote host is not configured.')
    if path.split('?', 1)[0] not in (GET_PATHS if method == 'GET' else POST_PATHS):
        raise RemoteError(404, 'Unsupported remote resource.')
    url = cfg['remote']['base_url'].rstrip('/') + path
    headers = _headers(cfg, method)
    if content_type:
        headers['Content-Type'] = content_type
    if body is not None:
        headers['Content-Length'] = str(len(body))
    request = Request(url, data=body, method=method, headers=headers)
    try:
        with (opener or _opener()).open(request, timeout=timeout) as response:
            return json.loads(response.read(8 << 20))
    except HTTPError as exc:
        exc.close()
        raise RemoteError(502, 'The remote dashboard could not complete that request.') from None
    except (TimeoutError, URLError, OSError, ValueError):
        raise RemoteError(503, 'The remote dashboard is not reachable at the configured loopback URL.') from None


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def sanitize_result(row):
    if not isinstance(row, dict):
        return None
    model = row.get('model')
    speed = row.get('tokens_per_second')
    if not isinstance(model, str) or not MODEL_NAME.fullmatch(model) or not isinstance(speed, (int, float)):
        return None
    def rounded(key, digits):
        value = row.get(key)
        return round(value, digits) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None
    prompt_tokens = row.get('prompt_tokens')
    output_tokens = row.get('output_tokens')
    return {
        'model': model,
        'sampled_at': _num(row.get('sampled_at')),
        'tokens_per_second': round(float(speed), 2),
        'prompt_tokens_per_second': rounded('prompt_tokens_per_second', 2),
        'prompt_eval_seconds': rounded('prompt_eval_seconds', 4),
        'eval_seconds': rounded('eval_seconds', 3),
        'first_token_seconds': rounded('first_token_seconds', 3),
        'wall_seconds': rounded('wall_seconds', 3),
        'load_seconds': rounded('load_seconds', 3),
        'output_tokens': output_tokens if type(output_tokens) is int and output_tokens >= 0 else None,
        'prompt_tokens': prompt_tokens if type(prompt_tokens) is int and prompt_tokens >= 0 else None,
    }


def _short(value, limit):
    return value[:limit] if isinstance(value, str) else None


def sanitize_hardware(data):
    if not isinstance(data, dict):
        raise RemoteError(502, 'The remote dashboard returned an unreadable hardware snapshot.')
    memory = data.get('memory') if isinstance(data.get('memory'), dict) else {}
    disk = data.get('disk') if isinstance(data.get('disk'), dict) else {}
    ollama = data.get('ollama') if isinstance(data.get('ollama'), dict) else {}

    def model_row(row, loaded):
        if not isinstance(row, dict) or not isinstance(row.get('name'), str):
            return None
        item = {'name': row['name'][:200],
                'parameters': _short(row.get('parameters'), 40) or 'Unknown',
                'quantization': _short(row.get('quantization'), 40) or 'Unknown'}
        if type(row.get('size')) is int and row['size'] >= 0:
            item['size'] = row['size']
        if loaded:
            if type(row.get('context_length')) is int and row['context_length'] > 0:
                item['context_length'] = row['context_length']
            if isinstance(row.get('expires_at'), str):
                item['expires_at'] = row['expires_at'][:40]
            if type(row.get('vram')) is int and row['vram'] > 0:
                item['vram'] = row['vram']
            if type(row.get('gpu_resident')) is int and row['gpu_resident'] > 0:
                item['gpu_resident'] = row['gpu_resident']
        return item

    gpus = []
    for gpu in (data.get('gpus') or [])[:4]:
        if not isinstance(gpu, dict):
            continue
        gpus.append({
            'name': _short(gpu.get('name'), 120) or 'GPU',
            'total': gpu.get('total') if type(gpu.get('total')) is int and gpu['total'] >= 0 else None,
            'used': gpu.get('used') if type(gpu.get('used')) is int and gpu['used'] >= 0 else None,
            'percent': _num(gpu.get('percent')),
            'temperature': _num(gpu.get('temperature')),
            'power_draw': _num(gpu.get('power_draw')),
            'power_limit': _num(gpu.get('power_limit')),
        })
    return {
        'sampled_at': _num(data.get('sampled_at')),
        'supported_host': bool(data.get('supported_host')),
        'chip': _short(data.get('chip'), 160) or 'Unknown',
        'os': _short(data.get('os'), 160) or '',
        'unified_memory': bool(data.get('unified_memory')),
        'cpu_percent': _num(data.get('cpu_percent')),
        'memory': {key: memory.get(key) if isinstance(memory.get(key), (int, float)) and not isinstance(memory.get(key), bool) else None
                   for key in ('total', 'used', 'available', 'percent', 'swap_used', 'compressed', 'reclaimable_estimate')} | {
                       'pressure': _short(memory.get('pressure'), 40) or 'Unavailable'},
        'disk': {key: disk.get(key) if type(disk.get(key)) is int and disk[key] >= 0 else 0 for key in ('total', 'used', 'free')},
        'gpus': gpus,
        'ollama': {
            'status': _short(ollama.get('status'), 120) or 'Unavailable',
            'installed': [item for item in (model_row(row, False) for row in (ollama.get('installed') or [])[:80]) if item],
            'loaded': [item for item in (model_row(row, True) for row in (ollama.get('loaded') or [])[:40]) if item],
        },
    }


def sanitize_history(data):
    if not isinstance(data, dict):
        raise RemoteError(502, 'The remote dashboard returned an unreadable history.')
    rows = []
    for row in (data.get('results') or [])[-20:]:
        clean = sanitize_result(row)
        if clean:
            rows.append(clean)
    return {'results': rows, 'scope': 'Remote dashboard probes only. In-memory history on that host, latest 20.'}


def sanitize_tokens(data):
    if not isinstance(data, dict) or not isinstance(data.get('model'), str) or not MODEL_NAME.fullmatch(data['model']):
        raise RemoteError(502, 'The remote dashboard returned an unreadable tokenizer result.')
    tokens = []
    for row in (data.get('tokens') or [])[:tokenize_api.MAX_RETURNED_TOKENS]:
        if isinstance(row, list) and len(row) == 2 and type(row[0]) is int and isinstance(row[1], str) and len(row[1]) <= 512:
            tokens.append([row[0], row[1]])
        else:
            raise RemoteError(502, 'The remote dashboard returned an unreadable tokenizer result.')
    ctx = data.get('context') if isinstance(data.get('context'), dict) else {}
    count, chars = data.get('count'), data.get('chars')
    if type(count) is not int or type(chars) is not int or count < 0 or chars < 0:
        raise RemoteError(502, 'The remote dashboard returned an unreadable tokenizer result.')
    text = lambda key, limit: data.get(key)[:limit] if isinstance(data.get(key), str) else None
    return {'model': data['model'], 'count': count, 'chars': chars, 'tokens': tokens,
            'truncated': bool(data.get('truncated')),
            'pre': text('pre', 40), 'family': text('family', 80), 'source': text('source', 80),
            'vocab_size': data.get('vocab_size') if type(data.get('vocab_size')) is int else None,
            'accuracy': 'verified' if data.get('accuracy') == 'verified' else 'approximate',
            'bos_added_by_runtime': bool(data.get('bos_added_by_runtime')),
            'special_ids': [item for item in (data.get('special_ids') or [])[:200] if type(item) is int],
            'context': {key: (ctx.get(key) if type(ctx.get(key)) is int and ctx.get(key) > 0 else None)
                        for key in ('loaded', 'modelfile', 'trained_max', 'probe')}}


def _live_part(part, include_text):
    part = part if isinstance(part, dict) else {}
    out = {'count': part.get('count') if type(part.get('count')) is int and part['count'] >= 0 else None,
           'chars': part.get('chars') if type(part.get('chars')) is int and part['chars'] >= 0 else 0,
           'accuracy': 'verified' if part.get('accuracy') == 'verified' else ('approximate' if part.get('accuracy') else None),
           'truncated': bool(part.get('truncated')), 'clipped': bool(part.get('clipped'))}
    ctx = part.get('context') if isinstance(part.get('context'), dict) else {}
    out['context'] = {key: (ctx.get(key) if type(ctx.get(key)) is int and ctx.get(key) > 0 else None)
                      for key in ('loaded', 'modelfile', 'trained_max', 'probe')}
    if include_text:
        rows = []
        for row in (part.get('tokens') or [])[:tokenize_api.MAX_RETURNED_TOKENS]:
            if isinstance(row, list) and len(row) == 2 and type(row[0]) is int and isinstance(row[1], str) and len(row[1]) <= 512:
                rows.append([row[0], row[1]])
        out['tokens'] = rows
        out['special_ids'] = [item for item in (part.get('special_ids') or [])[:200] if type(item) is int]
        if isinstance(part.get('text'), str) and not rows:
            out['text'] = part['text'][:20000]
    else:
        out['lengths'] = [[row[0], 1 if row[1] else 0] for row in (part.get('lengths') or [])[:tokenize_api.MAX_RETURNED_TOKENS]
                          if isinstance(row, list) and len(row) == 2 and type(row[0]) is int and 0 <= row[0] <= 512 and row[1] in (0, 1)]
    return out


def _recent_rows(data):
    rows = []
    for item in data.get('recent') or []:
        if not isinstance(item, dict):
            continue
        ident = item.get('id')
        if not isinstance(ident, str) or not live_tap.TURN_ID.fullmatch(ident):
            continue
        route = item.get('route') if item.get('route') in ('local', 'external', 'direct') else 'direct'
        rows.append({'id': ident, 'route': route})
        if len(rows) >= 8:
            break
    return rows


def sanitize_live(data, include_text):
    if not isinstance(data, dict) or type(data.get('seq')) is not int:
        raise RemoteError(502, 'The remote dashboard returned an unreadable live view.')
    tap = data.get('tap') if isinstance(data.get('tap'), dict) else {}
    error = tap.get('error') if isinstance(tap.get('error'), str) and len(tap['error']) <= 120 else None
    out = {'seq': data['seq'], 'tap': {'listening': bool(tap.get('listening')),
                                       'events': tap.get('events') if type(tap.get('events')) is int else 0,
                                       'last_event_age': _num(tap.get('last_event_age')),
                                       'error': None if tap.get('listening') else (error or 'not listening')},
           'recent': _recent_rows(data)}
    if data.get('unchanged') is True:
        out['unchanged'] = True
        return out
    state = data.get('state') if data.get('state') in ('idle', 'waiting', 'streaming', 'done') else 'idle'
    out['state'] = state
    if state == 'idle':
        return out
    def model(value):
        return value if isinstance(value, str) and MODEL_NAME.fullmatch(value) else None
    observed = observatory_v1.sanitize_observatory(data.get('observatory'))
    if observed and not include_text:
        observed = observatory_v1.strip_reason_text(observed)
    builtins = data.get('builtins') if isinstance(data.get('builtins'), dict) else {}
    clean_builtins = {key: builtins.get(key) if isinstance(builtins.get(key), bool) else None
                      for key in ('web_search', 'image_generation', 'code_interpreter', 'memory')}
    for key in ('files', 'tool_ids'):
        clean_builtins[key] = builtins.get(key) if type(builtins.get(key)) is int and builtins.get(key) >= 0 else None
    tools = []
    for item in data.get('owui_tools') or []:
        if isinstance(item, dict) and isinstance(item.get('name'), str) and item['name'].strip():
            tools.append({'name': item['name'].strip()[:80],
                          'start_seconds': _num(item.get('start_seconds')),
                          'end_seconds': _num(item.get('end_seconds'))})
            if len(tools) >= 60:
                break
    tasks = []
    for item in data.get('tasks') or []:
        if isinstance(item, dict) and isinstance(item.get('name'), str) and item['name'].strip():
            tasks.append({'name': item['name'].strip()[:40], 'at_seconds': _num(item.get('at_seconds'))})
            if len(tasks) >= 20:
                break
    usage = {}
    raw_usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
    for key in ('load_ms', 'prompt_eval_ms', 'eval_ms', 'prompt_tokens', 'completion_tokens'):
        number = _num(raw_usage.get(key))
        if number is not None and number >= 0:
            usage[key] = number
    out.update({'route': data.get('route') if data.get('route') in ('local', 'external', 'direct') else 'direct',
                'route_label': data.get('route_label')[:80] if isinstance(data.get('route_label'), str) else None,
                'route_source': data.get('route_source') if data.get('route_source') in ('live tap', 'Open WebUI') or observatory_v1.source_name(data.get('route_source')) else None,
                'observatory': observed,
                'observatory_source': observatory_v1.source_name(data.get('observatory_source')),
                'builtins': clean_builtins,
                'connection': data.get('connection') if data.get('connection') in ('local', 'external') else None,
                'tasks': tasks,
                'owui_tools': tools,
                'usage': usage,
                'sources': data.get('sources') if type(data.get('sources')) is int and data.get('sources') >= 0 else None,
                'model': model(data.get('model')), 'openwebui_model': model(data.get('openwebui_model')),
                'tokenizer_model': model(data.get('tokenizer_model')),
                'messages': data.get('messages') if type(data.get('messages')) is int else 0,
                'text_included': bool(include_text)})
    for key in ('started_ago', 'first_token_seconds', 'elapsed_seconds', 'stream_seconds', 'tokens_per_second'):
        out[key] = _num(data.get(key))
    out['prompt'] = _live_part(data.get('prompt'), include_text)
    out['reply'] = _live_part(data.get('reply'), include_text)
    ctx = data.get('context') if isinstance(data.get('context'), dict) else {}
    out['context'] = {'count': ctx.get('count') if type(ctx.get('count')) is int else None,
                      'chars': ctx.get('chars') if type(ctx.get('chars')) is int else 0,
                      'clipped': bool(ctx.get('clipped'))}
    placement = data.get('placement') if isinstance(data.get('placement'), dict) else None
    if placement is None:
        out['placement'] = None
    else:
        out['placement'] = {'loaded': placement.get('loaded') if placement.get('loaded') in (True, False) else None}
        for key in ('size', 'vram', 'context'):
            out['placement'][key] = placement.get(key) if type(placement.get(key)) is int and placement.get(key) >= 0 else None
    return out


def sanitize_glance(data):
    if not isinstance(data, dict):
        raise RemoteError(502, 'The remote dashboard returned an unreadable glance.')
    routing = data.get('routing') if isinstance(data.get('routing'), dict) else None
    clean = None
    if routing and isinstance(routing.get('all'), dict):
        clean = {'all': {key: int(routing['all'].get(key) or 0) for key in ('local', 'external', 'unknown')},
                 'first_time': routing.get('first_time')[:40] if isinstance(routing.get('first_time'), str) else None,
                 'last_time': routing.get('last_time')[:40] if isinstance(routing.get('last_time'), str) else None,
                 'source': 'remote'}
    detail = data.get('detail') if isinstance(data.get('detail'), str) else ''
    return {'enabled': bool(data.get('enabled')), 'routing': clean, 'detail': detail[:160]}


def hardware(cfg, opener=None):
    return sanitize_hardware(_call(cfg, 'GET', '/api/hardware', opener=opener, timeout=8))


def history(cfg, opener=None):
    return sanitize_history(_call(cfg, 'GET', '/api/performance', opener=opener, timeout=8))


def live(cfg, include_text, since=None, opener=None):
    path = '/api/live?text=' + ('1' if include_text else '0')
    if type(since) is int:
        path += '&since=%d' % since
    return sanitize_live(_call(cfg, 'GET', path, opener=opener, timeout=8), include_text)


def glance(cfg, opener=None):
    return sanitize_glance(_call(cfg, 'GET', '/api/glance', opener=opener, timeout=8))


def _loaded_names(cfg, opener):
    snap = hardware(cfg, opener=opener)
    return {row['name'] for row in snap['ollama']['loaded']}


def run_test(cfg, model, opener=None):
    """POST the fixed probe on the remote dashboard. Call only from a user click."""
    if not isinstance(model, str) or not MODEL_NAME.fullmatch(model):
        raise RemoteError(400, 'Select a loaded model')
    if not LOCK.acquire(blocking=False):
        raise RemoteError(400, 'A performance test is already running')
    try:
        try:
            loaded = _loaded_names(cfg, opener)
        except RemoteError:
            raise
        if model not in loaded:
            raise RemoteError(400, 'Model must already be loaded on the remote host; refresh the model list')
        data = _call(cfg, 'POST', '/api/performance?model=' + model, body=b'', opener=opener,
                     timeout=cfg['remote']['timeout_seconds'])
        result = sanitize_result(data)
        if result is None or result['model'] != model:
            raise RemoteError(502, 'The remote dashboard returned an incomplete result. The test was not recorded here.')
        return result
    finally:
        LOCK.release()


def tokenize(cfg, model, text, opener=None):
    """POST text to the remote dashboard's own tokenizer. Call only from a user click."""
    if not isinstance(model, str) or not MODEL_NAME.fullmatch(model) or not isinstance(text, str) or len(text) > tokenize_api.MAX_CHARS:
        raise RemoteError(400, 'Select an installed model and at most %d characters.' % tokenize_api.MAX_CHARS)
    body = json.dumps({'model': model, 'text': text}).encode()
    data = _call(cfg, 'POST', '/api/tokenize', body=body, opener=opener, timeout=30, content_type='application/json')
    return sanitize_tokens(data)
