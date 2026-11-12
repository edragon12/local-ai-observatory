"""Optional JSON-lines log a router already writes.

Nothing is read until route_log.path is set. The file is tailed, not followed
off the machine. Each line is either::

    {"time": 1710000000.0, "turn": "optional-id", "observatory": {"v": 1, ...}}

or the same object with the observatory fields at the top level next to
``time``. Lines that are not observatory/v1 are ignored. Matching a line to a
chat turn is the receiver's job, and a match is always labelled as matched
by time.
"""
import importlib
import json
from pathlib import Path


TAIL_BYTES = 2_000_000
MAX_LINES = 2000
MODULE_NAME = __import__('re').compile(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*')


def _tail(path):
    file = Path(path)
    with file.open('rb') as handle:
        size = handle.seek(0, 2)
        handle.seek(max(0, size - TAIL_BYTES))
        if size > TAIL_BYTES:
            handle.readline()
        return handle.read().decode('utf-8', 'replace').splitlines()[-MAX_LINES:]


def _row(value):
    if not isinstance(value, dict):
        return None
    when = value.get('time')
    if isinstance(when, bool) or not isinstance(when, (int, float)):
        return None
    obs = value.get('observatory') if isinstance(value.get('observatory'), dict) else value
    turn = value.get('turn') if isinstance(value.get('turn'), str) else None
    return {'time': float(when), 'turn': turn, 'observatory': obs}


def read_jsonl(path):
    rows = []
    for line in _tail(path):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        row = _row(parsed)
        if row:
            rows.append(row)
    return rows


def read_route_log(options):
    """Return parsed rows. An empty path, a missing file, or a bad reader yields []."""
    options = options or {}
    path = options.get('path') or ''
    if not path:
        return []
    fmt = options.get('format') or 'jsonl'
    try:
        if fmt == 'module' and options.get('module') and MODULE_NAME.fullmatch(options['module']):
            reader = importlib.import_module(options['module']).read_route_log
            raw = reader(path, options)
            return [row for row in (_row(item) for item in (raw or [])) if row]
        if fmt == 'jsonl':
            return read_jsonl(path)
    except Exception:
        return []
    return []
