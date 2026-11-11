"""Optional features. Everything is off until a config file or environment variable turns it on.

No secrets belong in the repository. Token files, log paths and remote URLs are read
from the environment or from observatory.json (see observatory.example.json).
"""
import json
import os
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent

DEFAULTS = {
    'remote': {
        'enabled': False,
        'label': 'Remote host',
        'base_url': '',
        'host_header': '',
        'origin': '',
        'timeout_seconds': 120,
    },
    'live_tap': {
        'token_file': '',
        'listen_host': '127.0.0.1',
        'listen_port': 8768,
        'local_model': '',
        'route_label_pattern': r'\A\*\*([^*\n]{1,80})\*\*\n\n',
        'local_label_prefix': 'local',
        'allowed_networks': ['127.0.0.0/8', '::1/128'],
    },
    'glance': {
        'path': '',
        'format': 'jsonl',
        'line_pattern': '',
        'module': '',
        'local_providers': ['ollama', 'local'],
        'external_providers': ['openai', 'anthropic', 'google', 'openrouter', 'groq'],
    },
}


def _merge(base, extra):
    if not isinstance(extra, dict):
        return base
    for key, value in extra.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _merge(base[key], value)
        elif key in base:
            base[key] = value
    return base


def _loopback_url(url):
    """http://127.0.0.1:port or http://localhost:port, with no path, user or query."""
    if not isinstance(url, str) or not url:
        return ''
    parts = urlsplit(url.strip())
    host = (parts.hostname or '').lower()
    if parts.scheme != 'http' or host not in ('127.0.0.1', 'localhost'):
        return ''
    if parts.username or parts.password or parts.query or parts.fragment:
        return ''
    if parts.path not in ('', '/'):
        return ''
    if parts.port is None:
        return ''
    return 'http://%s:%d' % (host, parts.port)


def _header_value(value, limit=200):
    if not isinstance(value, str):
        return ''
    text = value.strip()
    if not text or len(text) > limit or any(ch in text for ch in '\r\n\x00'):
        return ''
    return text


def _label(value):
    text = _header_value(value, 80)
    return text or 'Remote host'


def _providers(value, fallback):
    if not isinstance(value, list):
        return list(fallback)
    out = []
    for item in value[:40]:
        if isinstance(item, str) and item.strip() and len(item) <= 80:
            out.append(item.strip().lower())
    return out or list(fallback)


def _networks(value, fallback):
    if not isinstance(value, list):
        return list(fallback)
    out = []
    for item in value[:16]:
        if isinstance(item, str) and item.strip() and len(item) <= 64 and all(ch not in item for ch in ' \r\n'):
            out.append(item.strip())
    return out or list(fallback)


def load_config(environ=None, root=None):
    """Return a sanitized config. Invalid remote URLs leave the remote host disabled."""
    env = os.environ if environ is None else environ
    root = Path(root) if root is not None else ROOT
    data = deepcopy(DEFAULTS)
    path = _header_value(env.get('OBSERVATORY_CONFIG', ''), 400)
    if not path:
        candidate = root / 'observatory.json'
        if candidate.is_file():
            path = str(candidate)
    if path:
        try:
            loaded = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            _merge(data, loaded)
    remote = data['remote']
    if _header_value(env.get('OBSERVATORY_REMOTE_URL', ''), 200):
        remote['base_url'] = env['OBSERVATORY_REMOTE_URL']
        remote['enabled'] = True
    if env.get('OBSERVATORY_REMOTE_LABEL'):
        remote['label'] = env['OBSERVATORY_REMOTE_LABEL']
    if env.get('OBSERVATORY_REMOTE_HOST'):
        remote['host_header'] = env['OBSERVATORY_REMOTE_HOST']
    if env.get('OBSERVATORY_REMOTE_ORIGIN'):
        remote['origin'] = env['OBSERVATORY_REMOTE_ORIGIN']
    live = data['live_tap']
    if env.get('OBSERVATORY_LIVE_TOKEN_FILE'):
        live['token_file'] = env['OBSERVATORY_LIVE_TOKEN_FILE']
    if env.get('OBSERVATORY_LIVE_HOST'):
        live['listen_host'] = env['OBSERVATORY_LIVE_HOST']
    if env.get('OBSERVATORY_LIVE_PORT', '').isdigit():
        live['listen_port'] = int(env['OBSERVATORY_LIVE_PORT'])
    if env.get('OBSERVATORY_LIVE_LOCAL_MODEL'):
        live['local_model'] = env['OBSERVATORY_LIVE_LOCAL_MODEL']
    if env.get('OBSERVATORY_ROUTE_LABEL_PATTERN'):
        live['route_label_pattern'] = env['OBSERVATORY_ROUTE_LABEL_PATTERN']
    if env.get('OBSERVATORY_LOCAL_LABEL_PREFIX'):
        live['local_label_prefix'] = env['OBSERVATORY_LOCAL_LABEL_PREFIX']
    glance = data['glance']
    if env.get('OBSERVATORY_GLANCE_LOG'):
        glance['path'] = env['OBSERVATORY_GLANCE_LOG']
    if env.get('OBSERVATORY_GLANCE_FORMAT'):
        glance['format'] = env['OBSERVATORY_GLANCE_FORMAT']
    if env.get('OBSERVATORY_GLANCE_MODULE'):
        glance['module'] = env['OBSERVATORY_GLANCE_MODULE']
    return normalize(data)


def normalize(data):
    remote = data['remote']
    url = _loopback_url(remote.get('base_url', ''))
    enabled = bool(remote.get('enabled')) and bool(url)
    detail = ''
    raw = remote.get('base_url') or ''
    if remote.get('enabled') and raw and not url:
        enabled = False
        detail = 'Remote base URL must be http://127.0.0.1:port or http://localhost:port.'
    origin = _loopback_url(remote.get('origin', ''))
    if remote.get('origin') and not origin:
        origin = ''
        if enabled:
            detail = 'Remote origin must be an http loopback URL with a port.'
    timeout = remote.get('timeout_seconds', 120)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 120
    timeout = min(180, max(10, timeout))
    remote.update({
        'enabled': enabled,
        'label': _label(remote.get('label')),
        'base_url': url,
        'host_header': _header_value(remote.get('host_header', '')),
        'origin': origin,
        'timeout_seconds': timeout,
        'detail': detail,
    })
    live = data['live_tap']
    port = live.get('listen_port', 8768)
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 8768
    if not 1 <= port <= 65535:
        port = 8768
    host = _header_value(live.get('listen_host', ''), 80) or '127.0.0.1'
    prefix = _header_value(live.get('local_label_prefix', ''), 40) or 'local'
    pattern = live.get('route_label_pattern') if isinstance(live.get('route_label_pattern'), str) else ''
    if not pattern or len(pattern) > 200:
        pattern = DEFAULTS['live_tap']['route_label_pattern']
    model = _header_value(live.get('local_model', ''), 200)
    token_file = _header_value(live.get('token_file', ''), 400)
    live.update({
        'token_file': token_file,
        'listen_host': host,
        'listen_port': port,
        'local_model': model,
        'route_label_pattern': pattern,
        'local_label_prefix': prefix.lower(),
        'allowed_networks': _networks(live.get('allowed_networks'), DEFAULTS['live_tap']['allowed_networks']),
    })
    glance = data['glance']
    fmt = glance.get('format') if glance.get('format') in ('jsonl', 'pattern', 'module') else 'jsonl'
    glance.update({
        'path': _header_value(glance.get('path', ''), 400),
        'format': fmt,
        'line_pattern': glance.get('line_pattern') if isinstance(glance.get('line_pattern'), str) and len(glance.get('line_pattern')) <= 300 else '',
        'module': _header_value(glance.get('module', ''), 200),
        'local_providers': _providers(glance.get('local_providers'), DEFAULTS['glance']['local_providers']),
        'external_providers': _providers(glance.get('external_providers'), DEFAULTS['glance']['external_providers']),
    })
    return data


def public_view(cfg, version, live_enabled=False):
    """Fields the browser may see. No URLs, paths or tokens."""
    remote = cfg['remote']
    return {
        'version': version,
        'remote': {
            'enabled': bool(remote['enabled']),
            'label': remote['label'] if remote['enabled'] else 'Remote host',
            'detail': remote.get('detail') or '',
        },
        'live_tap': {'enabled': bool(live_enabled)},
        'glance': {'enabled': bool(cfg['glance']['path'])},
    }
