"""Optional local-vs-external call counts from a log the user points at.

Nothing is read until glance.path is set. The built-in reader accepts JSON lines::

    {"time":"2026-01-02 03:04:05","model":"example","provider":"ollama","route":"local"}

`route`, when present, must be local, external or unknown. Otherwise the provider
name is classified with the configured provider lists. Unknown providers stay unknown.

format "pattern" uses glance.line_pattern (a Python regex with optional named groups
time, route, provider, model). format "module" imports glance.module and calls
read_glance(path, options). The module name must be a dotted Python identifier.
"""
import importlib
import json
import re
from pathlib import Path


TAIL_BYTES = 2_000_000
MAX_LINES = 6000
ROUTES = ('local', 'external', 'unknown')
MODULE_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*')


def _tail(path):
    file = Path(path)
    with file.open('rb') as handle:
        size = handle.seek(0, 2)
        handle.seek(max(0, size - TAIL_BYTES))
        if size > TAIL_BYTES:
            handle.readline()
        return handle.read().decode('utf-8', 'replace').splitlines()[-MAX_LINES:]


def _classify(route, provider, options):
    if route in ROUTES:
        return route
    name = (provider or '').strip().lower()
    if name and name in set(options.get('local_providers') or []):
        return 'local'
    if name and name in set(options.get('external_providers') or []):
        return 'external'
    return 'unknown'


def _empty():
    return {'local': 0, 'external': 0, 'unknown': 0}


def _record(counts, times, when, route):
    counts[route] += 1
    if isinstance(when, str) and when.strip():
        times.append(when.strip()[:40])


def read_jsonl(path, options):
    counts, times = _empty(), []
    for line in _tail(path):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        route = _classify(row.get('route') if isinstance(row.get('route'), str) else '',
                          row.get('provider') if isinstance(row.get('provider'), str) else '', options)
        when = row.get('time') if isinstance(row.get('time'), str) else ''
        _record(counts, times, when, route)
    return counts, times


def read_pattern(path, options):
    pattern = options.get('line_pattern') or ''
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError('Glance line pattern is not a valid regular expression') from exc
    counts, times = _empty(), []
    for line in _tail(path):
        match = compiled.search(line)
        if not match:
            continue
        groups = match.groupdict()
        route = _classify(groups.get('route') or '', groups.get('provider') or '', options)
        _record(counts, times, groups.get('time') or '', route)
    return counts, times


def _call_module(path, options):
    name = options.get('module') or ''
    if not MODULE_NAME.fullmatch(name):
        raise ValueError('Glance module name is not allowed')
    reader = importlib.import_module(name)
    fn = getattr(reader, 'read_glance', None)
    if not callable(fn):
        raise ValueError('Glance module has no read_glance function')
    result = fn(path, options)
    if not isinstance(result, dict):
        raise ValueError('Glance module returned an unexpected result')
    return result


def _from_counts(counts, times, source):
    routing = {'all': {key: int(counts.get(key, 0)) for key in ROUTES},
               'first_time': min(times) if times else None,
               'last_time': max(times) if times else None,
               'source': source}
    return routing


def collect(cfg, readers=None):
    """Return {enabled, routing, detail}. Never raises to the caller."""
    glance = (cfg or {}).get('glance') or {}
    path = glance.get('path') or ''
    if not path:
        return {'enabled': False, 'routing': None, 'detail': 'No log source configured.'}
    fmt = glance.get('format') or 'jsonl'
    try:
        if fmt == 'module':
            raw = _call_module(path, glance)
            if 'all' in raw and isinstance(raw['all'], dict):
                routing = _from_counts(raw['all'], [t for t in (raw.get('first_time'), raw.get('last_time')) if t], 'module')
                if raw.get('first_time'):
                    routing['first_time'] = str(raw['first_time'])[:40]
                if raw.get('last_time'):
                    routing['last_time'] = str(raw['last_time'])[:40]
                return {'enabled': True, 'routing': routing, 'detail': ''}
            raise ValueError('Glance module returned an unexpected result')
        if readers and fmt in readers:
            counts, times = readers[fmt](path, glance)
        elif fmt == 'pattern':
            counts, times = read_pattern(path, glance)
        else:
            counts, times = read_jsonl(path, glance)
        return {'enabled': True, 'routing': _from_counts(counts, times, fmt if fmt in ('jsonl', 'pattern') else 'jsonl'), 'detail': ''}
    except (OSError, ValueError):
        return {'enabled': True, 'routing': None, 'detail': 'The log source could not be read.'}
