"""Explicit local-only, loaded-model inference probe. No prompt/output retention."""
import json
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler

LOCK = threading.Lock()
RESULTS = []

def parse_chunk(line):
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        raise ValueError('Ollama returned an unreadable response.')

def consume(rows, model, clock=time.monotonic, started=None):
    started = clock() if started is None else started
    first = None
    final = None
    for row in rows:
        if row.get('error'):
            raise ValueError('Runtime inference failed')
        message = row.get('message') or {}
        if first is None and (message.get('content') or message.get('thinking')):
            first = clock() - started
        if row.get('done'):
            final = row
    elapsed = clock() - started
    if final is None:
        raise ValueError('Incomplete runtime response')
    count = final.get('eval_count', 0)
    duration = final.get('eval_duration', 0) / 1e9
    if not isinstance(count, int) or count <= 0 or duration <= 0:
        raise ValueError('Runtime did not supply generation metrics')
    # Prompt processing (prefill) speed from the same final chunk, when the runtime reports it.
    p_count, p_ns = final.get('prompt_eval_count'), final.get('prompt_eval_duration')
    p_ok = type(p_count) is int and p_count > 0 and isinstance(p_ns, (int, float)) and not isinstance(p_ns, bool) and p_ns > 0
    return {'model':model, 'sampled_at':time.time(), 'output_tokens':count,
            'tokens_per_second':round(count/duration, 2),
            'eval_seconds':round(duration, 3),
            'prompt_eval_seconds':round(p_ns/1e9, 4) if p_ok else None,
            'prompt_tokens_per_second':round(p_count/(p_ns/1e9), 2) if p_ok else None,
            'first_token_seconds':round(first, 3) if first is not None else None,
            'wall_seconds':round(elapsed, 3),
            'load_seconds':round(final.get('load_duration', 0)/1e9, 3),
            'prompt_tokens':final.get('prompt_eval_count'),
            'source':'User-triggered local probe; first token is first visible streamed content (or thinking) chunk'}

def history():
    with LOCK:
        return {'results':list(RESULTS), 'scope':'Dashboard probes only; not all Ollama traffic. In-memory history, latest 20.'}

def run(model):
    if not isinstance(model, str) or not model or len(model)>200:
        raise ValueError('Select a loaded model')
    if not LOCK.acquire(blocking=False):
        raise ValueError('A performance test is already running')
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open('http://127.0.0.1:11434/api/ps', timeout=5) as response:
            loaded = json.load(response).get('models', [])
        if model not in {m.get('name') for m in loaded}:
            raise ValueError('Model must already be loaded; refresh the model list')
        body = {'model':model, 'stream':True, 'think':False,
                'messages':[{'role':'user','content':'Explain in plain language how a computer processes a request. Write about 150 words.'}],
                'options':{'temperature':0, 'num_predict':128, 'num_ctx':4096}, 'keep_alive':'5m'}
        req = Request('http://127.0.0.1:11434/api/chat', data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
        started = time.monotonic()
        with opener.open(req, timeout=90) as response:
            def rows():
                for _ in range(2048):
                    line = response.readline(262145)
                    if not line:
                        break
                    if len(line)>262144 or time.monotonic()-started>90:
                        raise ValueError('Runtime response limit reached')
                    yield parse_chunk(line)
            result = consume(rows(), model, started=started)
        RESULTS.append(result)
        del RESULTS[:-20]
        return result
    finally:
        LOCK.release()
