"""Prompt-token inspection for this host's own Ollama models.

Flow: model name -> Ollama /api/show (metadata only, loads nothing) -> the GGUF blob path ->
read only the tokenizer metadata from that file -> tokenize in pure Python.
/api/ps is read only to learn the current num_ctx of an already-loaded model.
Never calls /api/generate, /api/chat or /api/pull; never loads or downloads a model.
Prompt text is held in memory for the request only and is never logged or stored.
"""
import json
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler

import gguf_tokenizer

OLLAMA = 'http://127.0.0.1:11434'
MODEL_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}')
BLOB_NAME = re.compile(r'sha256-[0-9a-f]{64}')
MAX_CHARS = gguf_tokenizer.MAX_PROMPT_CHARS
MAX_RETURNED_TOKENS = 6000
PROBE_CTX = 4096
# Pre-tokenizers whose output was checked token-for-token against the official Hugging Face
# tokenizers (and, for qwen2, against Ollama's own prompt_eval_count). Others are "approximate".
VERIFIED_PRE = {'qwen2', 'qwen35', 'llama-bpe'}
_CACHE = OrderedDict()          # blob digest -> Tokenizer (at most 2; each vocabulary is large)
_LOCK = threading.Lock()


MAX_BODY = 262144


class TokenizeError(ValueError):
    """Fixed, application-authored messages only."""


FIXED_MESSAGES = {
    'Select an installed model.', 'Text must be a string.', 'Text is limited to %d characters.' % MAX_CHARS,
    'Ollama could not describe that model. Is it installed and is Ollama running?',
    'Could not locate this model’s GGUF file.', 'This model’s tokenizer could not be read.',
    'The model file is not readable by the dashboard user.', 'A JSON body with model and text is required.',
    'A tokenizer request is already running.'}
UNSUPPORTED = re.compile(r"Only byte-level BPE \(gpt2\) tokenizers are supported; this model uses '[A-Za-z0-9_.-]{1,40}'|Only byte-level BPE \(gpt2\) tokenizers are supported; this model uses None")


def is_fixed_message(text):
    return isinstance(text, str) and (text in FIXED_MESSAGES or bool(UNSUPPORTED.fullmatch(text)))


def read_request(headers, rfile):
    """Bounded JSON body {model, text}. Raises TokenizeError with a fixed message."""
    if headers.get('Transfer-Encoding') or not str(headers.get('Content-Type', '')).startswith('application/json'):
        raise TokenizeError('A JSON body with model and text is required.')
    try:
        length = int(headers.get('Content-Length', ''))
    except ValueError:
        raise TokenizeError('A JSON body with model and text is required.') from None
    if length <= 0 or length > MAX_BODY:
        raise TokenizeError('Text is limited to %d characters.' % MAX_CHARS if length > MAX_BODY else 'A JSON body with model and text is required.')
    try:
        body = json.loads(rfile.read(length).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise TokenizeError('A JSON body with model and text is required.') from None
    if not isinstance(body, dict) or set(body) - {'model', 'text'}:
        raise TokenizeError('A JSON body with model and text is required.')
    return body.get('model'), body.get('text')


def _opener():
    return build_opener(ProxyHandler({}))


def _ollama(path, body=None, opener=None):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(OLLAMA + path, data=data, headers={'Content-Type': 'application/json'} if data else {})
    with (opener or _opener()).open(req, timeout=10) as r:
        return json.loads(r.read(4 << 20))


def _blob_from_modelfile(modelfile):
    for line in (modelfile or '').splitlines():
        if line.startswith('FROM '):
            path = Path(line[5:].strip())
            if BLOB_NAME.fullmatch(path.name) and path.parent.name == 'blobs':
                return path
    return None


def _blob_from_manifest(name):
    """Fallback: resolve through the local manifest store (OLLAMA_MODELS or ~/.ollama/models)."""
    root = Path(os.environ.get('OLLAMA_MODELS') or Path.home() / '.ollama' / 'models')
    model, _, tag = name.partition(':')
    parts = model.split('/')
    if len(parts) == 1:
        parts = ['registry.ollama.ai', 'library'] + parts
    elif len(parts) == 2:
        parts = ['registry.ollama.ai'] + parts
    if any(p in ('', '.', '..') for p in parts + [tag or 'latest']):
        return None
    manifest = root.joinpath('manifests', *parts, tag or 'latest')
    try:
        layers = json.loads(manifest.read_text()).get('layers') or []
    except (OSError, ValueError):
        return None
    for layer in layers:
        if isinstance(layer, dict) and layer.get('mediaType') == 'application/vnd.ollama.image.model':
            digest = str(layer.get('digest', '')).replace(':', '-')
            if BLOB_NAME.fullmatch(digest):
                return root / 'blobs' / digest
    return None


def _num(value):
    return value if type(value) is int and value > 0 else None


def model_context(name, show, opener=None):
    info = show.get('model_info') if isinstance(show.get('model_info'), dict) else {}
    trained = next((_num(v) for k, v in info.items() if k.endswith('.context_length')), None)
    modelfile_ctx = None
    for line in str(show.get('parameters') or '').splitlines():
        key, _, value = line.strip().partition(' ')
        if key == 'num_ctx' and value.strip().isdigit():
            modelfile_ctx = int(value.strip())
    loaded = None
    try:
        for m in _ollama('/api/ps', opener=opener).get('models') or []:
            if isinstance(m, dict) and m.get('name') == name:
                loaded = _num(m.get('context_length'))
    except (OSError, ValueError):
        pass
    return {'loaded': loaded, 'modelfile': modelfile_ctx, 'trained_max': trained, 'probe': PROBE_CTX}


def _tokenizer(blob):
    key = blob.name
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    tok = gguf_tokenizer.Tokenizer(gguf_tokenizer.read_metadata(blob))
    with _LOCK:
        _CACHE[key] = tok
        while len(_CACHE) > 2:
            _CACHE.popitem(last=False)
    return tok


def tokenize(name, text, opener=None):
    if not isinstance(name, str) or not MODEL_NAME.fullmatch(name):
        raise TokenizeError('Select an installed model.')
    if not isinstance(text, str):
        raise TokenizeError('Text must be a string.')
    if len(text) > MAX_CHARS:
        raise TokenizeError('Text is limited to %d characters.' % MAX_CHARS)
    try:
        show = _ollama('/api/show', {'model': name}, opener)
    except (OSError, ValueError):
        raise TokenizeError('Ollama could not describe that model. Is it installed and is Ollama running?') from None
    blob = _blob_from_modelfile(show.get('modelfile')) or _blob_from_manifest(name)
    if blob is None:
        raise TokenizeError('Could not locate this model’s GGUF file.')
    try:
        tok = _tokenizer(blob)
    except gguf_tokenizer.GGUFError as exc:
        msg = str(exc)
        raise TokenizeError(msg if msg.startswith('Only byte-level BPE') else 'This model’s tokenizer could not be read.') from None
    except OSError:
        raise TokenizeError('The model file is not readable by the dashboard user.') from None
    pairs = tok.encode(text)
    verified = tok.exact and tok.pre in VERIFIED_PRE
    return {'model': name, 'count': len(pairs), 'chars': len(text),
            'tokens': [[i, p] for i, p in pairs[:MAX_RETURNED_TOKENS]], 'truncated': len(pairs) > MAX_RETURNED_TOKENS,
            'special_ids': sorted({i for i, _ in pairs if i in tok.special_ids})[:200],
            'pre': tok.pre[:40], 'family': tok.family_label[:80], 'vocab_size': len(tok.tokens),
            'accuracy': 'verified' if verified else 'approximate',
            'bos_added_by_runtime': bool(tok.add_bos),
            'context': model_context(name, show, opener),
            'source': 'GGUF tokenizer.ggml.* in blob ' + blob.name[:19] + '…'}
