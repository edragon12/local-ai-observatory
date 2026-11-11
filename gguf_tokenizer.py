"""Local, read-only tokenizer for Ollama models.

Reads the tokenizer that is already embedded in a model's GGUF file
(tokenizer.ggml.* metadata) and runs byte-level BPE in pure Python.
It never loads weights, never runs inference, never touches a GPU and
never downloads anything. Only the metadata section of the GGUF is read.

Supported exactly: byte-level BPE ("gpt2" tokenizer model) with the
"llama-bpe" (Llama 3), "qwen2" (Qwen2.5/Qwen3) and "qwen35" (Qwen3.5)
pre-tokenizers. No Unicode normalization is applied, matching llama.cpp/Ollama. Anything else is
tokenized with the closest known pre-tokenizer and marked approximate.
"""
import struct
import unicodedata

# GGUF value types
_SCALARS = {0: '<B', 1: '<b', 2: '<H', 3: '<h', 4: '<I', 5: '<i', 6: '<f', 7: '<?', 10: '<Q', 11: '<q', 12: '<d'}
_STRING, _ARRAY = 8, 9
WANTED_PREFIXES = ('tokenizer.ggml.', 'general.architecture', 'general.name', 'general.basename')
SKIP_ARRAYS = {'tokenizer.ggml.scores'}   # not needed for BPE; skipped without decoding
MAX_PROMPT_CHARS = 20000


class GGUFError(ValueError):
    pass


class _Reader:
    def __init__(self, fh):
        self.fh = fh

    def read(self, n):
        data = self.fh.read(n)
        if len(data) != n:
            raise GGUFError('GGUF metadata is truncated or unreadable')
        return data

    def scalar(self, fmt):
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self):
        n = self.scalar('<Q')
        if n > 1 << 24:
            raise GGUFError('Implausible GGUF string length')
        return self.read(n).decode('utf-8', 'replace')

    def skip(self, n):
        self.fh.seek(n, 1)


def read_metadata(path):
    """Return the GGUF key/value metadata needed for tokenization (no tensors, no weights)."""
    meta = {}
    with open(path, 'rb') as fh:
        r = _Reader(fh)
        if r.read(4) != b'GGUF':
            raise GGUFError('Not a GGUF file')
        version = r.scalar('<I')
        if version not in (2, 3):
            raise GGUFError('Unsupported GGUF version %d' % version)
        r.scalar('<Q')                       # tensor count (tensors are never read)
        kv_count = r.scalar('<Q')
        if kv_count > 100000:
            raise GGUFError('Implausible GGUF metadata count')
        for _ in range(kv_count):
            key = r.string()
            vtype = r.scalar('<I')
            wanted = key.startswith(WANTED_PREFIXES) or key.endswith('.context_length')
            value = _value(r, vtype, keep=wanted and key not in SKIP_ARRAYS)
            if wanted and key not in SKIP_ARRAYS:
                meta[key] = value
        end = fh.tell()
        fh.seek(0, 2)
        if end > fh.tell():
            raise GGUFError('GGUF metadata is truncated or unreadable')
    return meta


def _value(r, vtype, keep):
    if vtype in _SCALARS:
        return r.scalar(_SCALARS[vtype])
    if vtype == _STRING:
        if keep:
            return r.string()
        r.skip(r.scalar('<Q'))
        return None
    if vtype == _ARRAY:
        itype, n = r.scalar('<I'), r.scalar('<Q')
        if n > 1 << 24:
            raise GGUFError('Implausible GGUF array length')
        if itype in _SCALARS:
            size = struct.calcsize(_SCALARS[itype])
            if not keep:
                r.skip(size * n)
                return None
            return list(struct.unpack('<%d%s' % (n, _SCALARS[itype][1]), r.read(size * n)))
        if itype == _STRING:
            out = [] if keep else None
            read, unpack = r.fh.read, struct.unpack
            for _ in range(n):
                head = read(8)
                if len(head) != 8:
                    raise GGUFError('GGUF metadata is truncated or unreadable')
                length = unpack('<Q', head)[0]
                if length > 1 << 24:
                    raise GGUFError('Implausible GGUF string length')
                data = read(length)
                if len(data) != length:
                    raise GGUFError('GGUF metadata is truncated or unreadable')
                if keep:
                    out.append(data.decode('utf-8', 'replace'))
            return out
        return [_value(r, itype, keep) for _ in range(n)]
    raise GGUFError('Unknown GGUF value type %d' % vtype)


# ---------------- Pre-tokenizers (hand-written equivalents of the published regexes) ----------------
# llama3 ("llama-bpe"):
#   (?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+
# qwen2 / qwen35: identical except \p{N} (single digits).
# Python's re module has no \p{L}/\p{N}, so the alternatives are evaluated in order by hand.

def _cat(ch):
    return unicodedata.category(ch)[0]


def pre_tokenize(text, max_digits, marks=False):
    """Split text the way the model's pre-tokenizer regex does. marks=True is the Qwen3.5
    variant, where combining marks (Unicode category M) count with letters."""
    word = ('L', 'M') if marks else ('L',)
    non_symbol = ('L', 'M', 'N') if marks else ('L', 'N')
    out, i, n = [], 0, len(text)
    while i < n:
        j = _match_at(text, i, n, max_digits, word, non_symbol)
        out.append(text[i:j])
        i = j
    return out


def _match_at(text, i, n, max_digits, word, non_symbol):
    c = text[i]
    # 1. (?i:'s|'t|'re|'ve|'m|'ll|'d)
    if c == "'" and i + 1 < n:
        one, two = text[i + 1].lower(), text[i + 1:i + 3].lower()
        if one in ('s', 't'):
            return i + 2
        if two in ('re', 've'):
            return i + 3
        if one == 'm':
            return i + 2
        if two == 'll':
            return i + 3
        if one == 'd':
            return i + 2
    cat = _cat(c)
    # 2. [^\r\n\p{L}\p{N}]?<word>+
    if c not in '\r\n' and cat not in ('L', 'N') and i + 1 < n and _cat(text[i + 1]) in word:
        j = i + 2
        while j < n and _cat(text[j]) in word:
            j += 1
        return j
    if cat in word:
        j = i + 1
        while j < n and _cat(text[j]) in word:
            j += 1
        return j
    # 3. \p{N}{1,max_digits}
    if cat == 'N':
        j = i + 1
        while j < n and j - i < max_digits and _cat(text[j]) == 'N':
            j += 1
        return j
    # 4. ' ?[^\s<non_symbol>]+[\r\n]*'
    def symbol(ch):
        return not ch.isspace() and _cat(ch) not in non_symbol
    start = None
    if c == ' ' and i + 1 < n and symbol(text[i + 1]):
        start = i + 1
    elif symbol(c):
        start = i
    if start is not None:
        j = start + 1
        while j < n and symbol(text[j]):
            j += 1
        while j < n and text[j] in '\r\n':
            j += 1
        return j
    # 5-7. whitespace: \s*[\r\n]+ | \s+(?!\S) | \s+
    e = i
    while e < n and text[e].isspace():
        e += 1
    if e == i:
        return i + 1                           # defensive: always make progress
    for k in range(e - 1, i - 1, -1):
        if text[k] in '\r\n':
            return k + 1
    if e == n or e - i < 2:
        return e
    return e - 1


# ---------------- Byte-level BPE ----------------

def _bytes_to_unicode():
    bs = list(range(ord('!'), ord('~') + 1)) + list(range(ord('¡'), ord('¬') + 1)) + list(range(ord('®'), ord('ÿ') + 1))
    cs = bs[:]
    k = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + k)
            k += 1
    return dict(zip(bs, map(chr, cs)))


BYTE_ENCODER = _bytes_to_unicode()
BYTE_DECODER = {v: k for k, v in BYTE_ENCODER.items()}

PRE_TYPES = {
    'llama-bpe': {'digits': 3, 'marks': False, 'ignore_merges': True, 'label': 'Llama 3 byte-level BPE'},
    'qwen2': {'digits': 1, 'marks': False, 'ignore_merges': False, 'label': 'Qwen2/Qwen3 byte-level BPE'},
    'qwen35': {'digits': 1, 'marks': True, 'ignore_merges': False, 'label': 'Qwen3.5 byte-level BPE'},
}


class Tokenizer:
    def __init__(self, meta):
        model = meta.get('tokenizer.ggml.model')
        tokens = meta.get('tokenizer.ggml.tokens')
        if model != 'gpt2' or not isinstance(tokens, list) or not tokens:
            raise GGUFError('Only byte-level BPE (gpt2) tokenizers are supported; this model uses %r' % (model,))
        self.pre = meta.get('tokenizer.ggml.pre') or 'default'
        spec = PRE_TYPES.get(self.pre)
        self.exact = spec is not None
        spec = spec or PRE_TYPES['llama-bpe']
        self.digits, self.marks, self.ignore_merges = spec['digits'], spec['marks'], spec['ignore_merges']
        self.family_label = spec['label'] if self.exact else 'Unrecognized pre-tokenizer %r (approximated)' % self.pre
        self.tokens = tokens
        self.vocab = {t: i for i, t in enumerate(tokens)}
        self.ranks = {}
        for rank, merge in enumerate(meta.get('tokenizer.ggml.merges') or []):
            a, sep, b = merge.partition(' ')
            if sep:
                self.ranks[(a, b)] = rank
        types = meta.get('tokenizer.ggml.token_type') or []
        # control (3) and user-defined (4) tokens are matched atomically, like llama.cpp with parse_special.
        self.specials = sorted((t for t, ty in zip(tokens, types) if ty in (3, 4) and t), key=len, reverse=True)
        self.special_ids = {self.vocab[t] for t in self.specials}
        self.special_first = {}
        for t in self.specials:
            self.special_first.setdefault(t[0], []).append(t)
        self.bos_id = meta.get('tokenizer.ggml.bos_token_id')
        self.add_bos = bool(meta.get('tokenizer.ggml.add_bos_token', self.pre == 'llama-bpe'))
        self.cache = {}

    def _bpe(self, word):
        if word in self.cache:
            return self.cache[word]
        if self.ignore_merges and word in self.vocab:
            self.cache[word] = [word]
            return [word]
        parts = list(word)
        while len(parts) > 1:
            best, best_rank = None, None
            for k in range(len(parts) - 1):
                rank = self.ranks.get((parts[k], parts[k + 1]))
                if rank is not None and (best_rank is None or rank < best_rank):
                    best, best_rank = k, rank
            if best is None:
                break
            parts[best:best + 2] = [parts[best] + parts[best + 1]]
        if len(self.cache) < 50000:
            self.cache[word] = parts
        return parts

    def _segments(self, text):
        """Split text into (is_special, piece) using longest-match special tokens."""
        out, buf, i, n = [], [], 0, len(text)
        while i < n:
            hit = None
            for t in self.special_first.get(text[i], ()):
                if text.startswith(t, i):
                    hit = t
                    break
            if hit:
                if buf:
                    out.append((False, ''.join(buf)))
                    buf = []
                out.append((True, hit))
                i += len(hit)
            else:
                buf.append(text[i])
                i += 1
        if buf:
            out.append((False, ''.join(buf)))
        return out

    def encode(self, text, parse_special=True):
        """Return a list of (token_id, text_piece). Pieces concatenate back to the input text."""
        result = []
        segments = self._segments(text) if parse_special else [(False, text)]
        for special, piece in segments:
            if special:
                result.append((self.vocab[piece], piece))
                continue
            for word in pre_tokenize(piece, self.digits, self.marks):
                mapped = ''.join(BYTE_ENCODER[b] for b in word.encode('utf-8'))
                ids = []
                for sub in self._bpe(mapped):
                    tid = self.vocab.get(sub)
                    if tid is None:          # should not happen with byte-level vocabularies; fall back to bytes
                        ids.extend((self.vocab.get(ch, -1), ch) for ch in sub)
                    else:
                        ids.append((tid, sub))
                result.extend(_pieces(ids))
        return result


def _pieces(ids):
    """Give each token the characters it completes. A token holding only the first bytes of a
    multi-byte character (common for emoji and CJK) gets '' and the next token carries the
    character, so the pieces always join back to the exact input text."""
    out, pending = [], b''
    for tid, sub in ids:
        pending += bytes(BYTE_DECODER.get(ch, 63) for ch in sub)
        cut = len(pending)
        while cut > 0:
            try:
                text = pending[:cut].decode('utf-8')
                break
            except UnicodeDecodeError:
                cut -= 1
        else:
            text = ''
        out.append((tid, text))
        pending = pending[cut:]
    if pending and out:              # invalid bytes cannot come from str input, but never drop data
        out[-1] = (out[-1][0], out[-1][1] + pending.decode('utf-8', 'replace'))
    return out
