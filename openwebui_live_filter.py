"""
title: Observatory Live Tap
author: Local AI Observatory
version: 0.3.0
description: Sends each chat turn's text and streamed reply pieces to a Local AI Observatory receiver on a host you choose. Fails open: if the receiver is unreachable the chat is unchanged. Stores nothing.
"""
# Open WebUI filter. Admin Panel -> Functions -> New Function -> paste this file -> Save.
# Turn the function on, set it to Global (or attach it to one model), then set the token valve
# to the token from `python3 live_tap.py --init` on the dashboard host.
# Nothing is sent while the token is empty. The hooks return their input unchanged.
import json
import queue
import threading
import time
import uuid
import urllib.request

_QUEUE = queue.Queue(maxsize=5000)
_STATE = {'thread': None, 'valves': None, 'pause_until': 0.0}
_LOCK = threading.Lock()
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part.get('text', '') for part in content
                         if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str))
    return ''


_DECISION_KEYS = ('target', 'label', 'decide_ms', 'upstream_ms')
_REASON_KEYS = ('code', 'text')
_CLASSIFIER_KEYS = ('result', 'why', 'ms', 'flags')
_STEP_KEYS = ('id', 'kind', 'name', 'server', 'where', 'status', 'start_ms', 'end_ms', 'in_tokens', 'out_tokens')
_BUILTIN_FLAGS = ('web_search', 'image_generation', 'code_interpreter', 'memory')


def _pick(source, keys):
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in keys if key in source}


def allow_observatory(value):
    """Copy only observatory/v1 fields off a response chunk. Everything else is dropped."""
    if not isinstance(value, dict) or value.get('v') != 1:
        return None
    out = {'v': 1}
    decision = _pick(value.get('decision'), _DECISION_KEYS)
    reason = _pick((value.get('decision') or {}).get('reason') if isinstance(value.get('decision'), dict) else None, _REASON_KEYS)
    if reason:
        decision['reason'] = reason
    if decision:
        out['decision'] = decision
    classifier = _pick(value.get('classifier'), _CLASSIFIER_KEYS)
    if classifier:
        out['classifier'] = classifier
    steps = []
    for item in value.get('steps') or []:
        if isinstance(item, dict):
            kept = _pick(item, _STEP_KEYS)
            if kept.get('kind'):
                steps.append(kept)
        if len(steps) >= 60:
            break
    if steps:
        out['steps'] = steps
    return out if len(out) > 1 else None


def ollama_usage(usage):
    """Ollama's duration fields are nanoseconds. Counts pass through. Absent fields are omitted."""
    if not isinstance(usage, dict):
        return None
    out = {}
    for src, dest in (('prompt_tokens', 'prompt_tokens'), ('completion_tokens', 'completion_tokens')):
        if type(usage.get(src)) is int and usage[src] >= 0:
            out[dest] = usage[src]
    for src, dest in (('load_duration', 'load_ms'), ('prompt_eval_duration', 'prompt_eval_ms'), ('eval_duration', 'eval_ms')):
        raw = usage.get(src)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
            continue
        out[dest] = round(float(raw) / 1_000_000, 3)
    return out or None


def context_event(body, message_id, model_info=None):
    """Built-in flags and counts, plus how Open WebUI classifies the model connection."""
    if not isinstance(body, dict) or not isinstance(message_id, str) or not message_id:
        return None
    features = body.get('features') if isinstance(body.get('features'), dict) else {}
    event = {'type': 'context', 'turn': message_id}
    for key in _BUILTIN_FLAGS:
        if isinstance(features.get(key), bool):
            event[key] = features[key]
    if isinstance(body.get('files'), list):
        event['files'] = min(len(body['files']), 10000)
    if isinstance(body.get('tool_ids'), list):
        event['tool_ids'] = min(len(body['tool_ids']), 10000)
    owned = model_info.get('owned_by') if isinstance(model_info, dict) else None
    if isinstance(owned, str) and owned.strip():
        event['owned_by'] = 'ollama' if owned.strip().lower() == 'ollama' else 'external'
    return event if len(event) > 2 else None


def task_event(metadata, message_id):
    """A background task name and nothing else. No prompt text."""
    task = (metadata or {}).get('task') if isinstance(metadata, dict) else None
    if not task or not isinstance(message_id, str) or not message_id:
        return None
    name = str(task)
    name = name if __import__('re').fullmatch(r'[A-Za-z0-9_.:-]{1,40}', name) else 'task'
    return {'type': 'task', 'turn': message_id, 'name': name}


def outlet_facts(body):
    """Source count and tool names only. Arguments and results are not copied."""
    if not isinstance(body, dict):
        return {}
    out = {}
    if isinstance(body.get('sources'), list):
        out['sources'] = min(len(body['sources']), 10000)
    names = []
    messages = body.get('messages') if isinstance(body.get('messages'), list) else []
    for row in messages:
        if not isinstance(row, dict):
            continue
        calls = row.get('tool_calls') if isinstance(row.get('tool_calls'), list) else []
        for call in calls:
            fn = call.get('function') if isinstance(call, dict) and isinstance(call.get('function'), dict) else {}
            name = fn.get('name')
            if isinstance(name, str) and name.strip():
                names.append(' '.join(name.split())[:80])
        if row.get('role') == 'tool' and isinstance(row.get('name'), str) and row['name'].strip():
            names.append(' '.join(row['name'].split())[:80])
    if names:
        out['tool_names'] = names[:60]
    return out


def start_event(body, message_id, send_conversation=True):
    """Describe one chat turn. Returns None for Open WebUI task requests (titles, tags, follow-ups)."""
    if not isinstance(body, dict):
        return None
    messages = [row for row in (body.get('messages') or []) if isinstance(row, dict)]
    prompt = next((text_of(row.get('content')) for row in reversed(messages) if row.get('role') == 'user'), '')
    context = '\n\n'.join(text_of(row.get('content')) for row in messages) if send_conversation else prompt
    turn = message_id if isinstance(message_id, str) and message_id else uuid.uuid4().hex
    return {'type': 'start', 'turn': turn, 'model': str(body.get('model') or '')[:200],
            'prompt': prompt[:20000], 'context': context[-20000:], 'messages': len(messages)}


def stream_events(event, message_id):
    """Delta, tool, usage, router and end events for one streamed chunk.

    Router data is read from the chunk, never from the chat request. Tool names
    are the names Open WebUI showed the filter, with no arguments.
    """
    if not isinstance(message_id, str) or not message_id or not isinstance(event, dict):
        return []
    out = []
    observed = allow_observatory(event.get('observatory'))
    if observed:
        out.append({'type': 'observatory', 'turn': message_id, 'observatory': observed, 'via': 'router stream'})
    usage = ollama_usage(event.get('usage'))
    if usage:
        out.append({'type': 'usage', 'turn': message_id, 'usage': usage})
    for choice in (event.get('choices') or []):
        if not isinstance(choice, dict):
            continue
        delta = choice.get('delta') if isinstance(choice.get('delta'), dict) else {}
        for call in delta.get('tool_calls') or []:
            fn = call.get('function') if isinstance(call, dict) and isinstance(call.get('function'), dict) else {}
            name = fn.get('name')
            if isinstance(name, str) and name.strip():
                out.append({'type': 'tool', 'turn': message_id, 'name': ' '.join(name.split())[:80], 'phase': 'start'})
        piece = delta.get('content')
        if isinstance(piece, str) and piece:
            out.append({'type': 'tool', 'turn': message_id, 'phase': 'end'})
            out.append({'type': 'delta', 'turn': message_id, 'text': piece})
        if choice.get('finish_reason'):
            out.append({'type': 'tool', 'turn': message_id, 'phase': 'end'})
            out.append({'type': 'end', 'turn': message_id})
    return out


def _sender():
    while True:
        batch = [_QUEUE.get()]
        deadline = time.time() + 0.1
        while len(batch) < 400:
            left = deadline - time.time()
            if left <= 0:
                break
            try:
                batch.append(_QUEUE.get(timeout=left))
            except queue.Empty:
                break
        valves = _STATE['valves']
        if valves is None or not getattr(valves, 'enabled', False) or not getattr(valves, 'token', '') or time.time() < _STATE['pause_until']:
            continue
        if not getattr(valves, 'endpoint', ''):
            continue
        data = json.dumps({'events': batch}).encode()
        request = urllib.request.Request(valves.endpoint, data=data, method='POST', headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + valves.token})
        try:
            with _OPENER.open(request, timeout=1.0) as response:
                response.read(0)
        except Exception:
            _STATE['pause_until'] = time.time() + 5


def _emit(valves, event):
    try:
        _STATE['valves'] = valves
        with _LOCK:
            if _STATE['thread'] is None or not _STATE['thread'].is_alive():
                _STATE['thread'] = threading.Thread(target=_sender, name='observatory-live-tap', daemon=True)
                _STATE['thread'].start()
        event['t'] = time.time()
        _QUEUE.put_nowait(event)
    except Exception:
        pass


try:
    from pydantic import BaseModel, Field
except ImportError:  # the dashboard tests do not need the Open WebUI class
    BaseModel = None


if BaseModel is not None:
    class Filter:
        class Valves(BaseModel):
            priority: int = Field(default=100, description='Run after other filters so the observed text is what is sent.')
            enabled: bool = Field(default=True, description='Turn the live tap off without uninstalling it.')
            endpoint: str = Field(default='http://127.0.0.1:8768/live/events',
                                  description='Receiver URL. From inside Docker this is the host address, not the container loopback.')
            token: str = Field(default='', description='From live_tap.py --init. Nothing is sent while this is empty.')
            send_conversation: bool = Field(default=True, description='Also send earlier messages so the whole prompt can be counted.')

        def __init__(self):
            self.valves = self.Valves()

        def inlet(self, body: dict, __metadata__: dict = None, __message_id__: str = None, __model__: dict = None) -> dict:
            try:
                if (__metadata__ or {}).get('task'):
                    event = task_event(__metadata__, __message_id__)
                    if event:
                        _emit(self.valves, event)
                    return body
                event = start_event(body, __message_id__, self.valves.send_conversation)
                if event:
                    _emit(self.valves, event)
                    ctx = context_event(body, event['turn'], __model__)
                    if ctx:
                        _emit(self.valves, ctx)
            except Exception:
                pass
            return body

        def stream(self, event: dict, __message_id__: str = None) -> dict:
            try:
                for item in stream_events(event, __message_id__):
                    _emit(self.valves, item)
            except Exception:
                pass
            return event

        def outlet(self, body: dict, __metadata__: dict = None, __message_id__: str = None) -> dict:
            try:
                turn = __message_id__ if isinstance(__message_id__, str) and __message_id__ else None
                if not turn and isinstance(body, dict) and isinstance(body.get('id'), str):
                    turn = body['id']
                if not turn:
                    turn = ((__metadata__ or {}).get('message_id') if isinstance((__metadata__ or {}).get('message_id'), str) else None)
                if turn:
                    facts = outlet_facts(body)
                    if 'sources' in facts:
                        _emit(self.valves, {'type': 'sources', 'turn': turn, 'count': facts['sources']})
                    for name in facts.get('tool_names') or []:
                        _emit(self.valves, {'type': 'tool', 'turn': turn, 'name': name, 'phase': 'name'})
                    _emit(self.valves, {'type': 'end', 'turn': turn})
            except Exception:
                pass
            return body
