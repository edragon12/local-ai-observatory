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


def route_meta(body):
    """Copy optional route_reason and timing when a caller attached them.

    Unknown timing keys are dropped. Absent fields are omitted, not filled in.
    """
    if not isinstance(body, dict):
        return {}
    extra = {}
    if isinstance(body.get('route_reason'), str):
        extra['route_reason'] = body['route_reason']
    timing = body.get('timing')
    if isinstance(timing, dict):
        kept = {key: timing[key] for key in ('route_seconds', 'left_network_seconds') if key in timing}
        if kept:
            extra['timing'] = kept
    return extra


def start_event(body, message_id, send_conversation=True):
    """Describe one chat turn. Returns None for Open WebUI task requests (titles, tags, follow-ups)."""
    if not isinstance(body, dict):
        return None
    messages = [row for row in (body.get('messages') or []) if isinstance(row, dict)]
    prompt = next((text_of(row.get('content')) for row in reversed(messages) if row.get('role') == 'user'), '')
    context = '\n\n'.join(text_of(row.get('content')) for row in messages) if send_conversation else prompt
    turn = message_id if isinstance(message_id, str) and message_id else uuid.uuid4().hex
    event = {'type': 'start', 'turn': turn, 'model': str(body.get('model') or '')[:200],
             'prompt': prompt[:20000], 'context': context[-20000:], 'messages': len(messages)}
    event.update(route_meta(body))
    return event


def stream_events(event, message_id):
    """Delta and end events for one streamed chunk. Empty when there is nothing to report."""
    if not isinstance(message_id, str) or not message_id or not isinstance(event, dict):
        return []
    out = []
    for choice in (event.get('choices') or []):
        if not isinstance(choice, dict):
            continue
        piece = ((choice.get('delta') or {}) if isinstance(choice.get('delta'), dict) else {}).get('content')
        if isinstance(piece, str) and piece:
            out.append({'type': 'delta', 'turn': message_id, 'text': piece})
        if choice.get('finish_reason'):
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

        def inlet(self, body: dict, __metadata__: dict = None, __message_id__: str = None) -> dict:
            try:
                if (__metadata__ or {}).get('task'):
                    return body
                event = start_event(body, __message_id__, self.valves.send_conversation)
                if event:
                    _emit(self.valves, event)
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
                    _emit(self.valves, {'type': 'end', 'turn': turn})
            except Exception:
                pass
            return body
