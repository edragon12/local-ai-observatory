"""GGUF metadata tokenizer. No weights and no network."""
import json
import struct
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import gguf_tokenizer
import tokenize_api


def _string(text):
    raw = text.encode()
    return struct.pack('<Q', len(raw)) + raw


def _kv_string(key, value):
    return _string(key) + struct.pack('<I', 8) + _string(value)


def _kv_uint(key, value):
    return _string(key) + struct.pack('<I', 4) + struct.pack('<I', value)


def _kv_bool(key, value):
    return _string(key) + struct.pack('<I', 7) + struct.pack('<?', value)


def _kv_strings(key, values):
    body = struct.pack('<IQ', 8, len(values))
    for value in values:
        body += _string(value)
    return _string(key) + struct.pack('<I', 9) + body


def _kv_ints(key, values):
    body = struct.pack('<IQ', 4, len(values)) + struct.pack('<%dI' % len(values), *values)
    return _string(key) + struct.pack('<I', 9) + body


def write_gguf(path, pre='llama-bpe', extra_token='ab', merges=None, types=None):
    tokens = [gguf_tokenizer.BYTE_ENCODER[i] for i in range(256)]
    if extra_token:
        tokens.append(extra_token)
    if types is None:
        types = [1] * len(tokens)
    parts = [
        _kv_string('tokenizer.ggml.model', 'gpt2'),
        _kv_string('tokenizer.ggml.pre', pre),
        _kv_strings('tokenizer.ggml.tokens', tokens),
        _kv_strings('tokenizer.ggml.merges', merges or []),
        _kv_ints('tokenizer.ggml.token_type', types),
        _kv_uint('tokenizer.ggml.bos_token_id', 1),
        _kv_bool('tokenizer.ggml.add_bos_token', pre == 'llama-bpe'),
        _kv_string('general.architecture', 'example'),
        _kv_uint('example.context_length', 4096),
    ]
    blob = b''.join(parts)
    header = b'GGUF' + struct.pack('<IQQQ', 3, 0, len(parts), 0)  # placeholder, rewritten below
    # kv count is the third uint64 after version. Rebuild cleanly.
    header = b'GGUF' + struct.pack('<IQQ', 3, 0, len(parts))
    path.write_bytes(header + blob)


class TokenizerTests(unittest.TestCase):
    def test_pre_tokenizer_digit_groups(self):
        self.assertEqual(gguf_tokenizer.pre_tokenize('1234', 3), ['123', '4'])
        self.assertEqual(gguf_tokenizer.pre_tokenize('1234', 1), ['1', '2', '3', '4'])

    def test_llama_bpe_uses_a_whole_word_token_without_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'model.gguf'
            write_gguf(path, pre='llama-bpe', extra_token='ab')
            meta = gguf_tokenizer.read_metadata(path)
            self.assertNotIn('tokenizer.ggml.scores', meta)
            tok = gguf_tokenizer.Tokenizer(meta)
            pieces = tok.encode('ab')
            self.assertEqual(''.join(piece for _, piece in pieces), 'ab')
            self.assertEqual(len(pieces), 1)
            self.assertTrue(tok.exact)
            self.assertTrue(tok.add_bos)

    def test_qwen_merge_and_rejected_file(self):
        enc = gguf_tokenizer.BYTE_ENCODER
        merge = enc[ord('a')] + ' ' + enc[ord('b')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'model.gguf'
            write_gguf(path, pre='qwen2', extra_token=enc[ord('a')] + enc[ord('b')], merges=[merge])
            pieces = gguf_tokenizer.Tokenizer(gguf_tokenizer.read_metadata(path)).encode('ab')
            self.assertEqual([(piece) for _, piece in pieces], ['ab'])
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'nope.bin'
            bad.write_bytes(b'not a model')
            with self.assertRaises(gguf_tokenizer.GGUFError):
                gguf_tokenizer.read_metadata(bad)

    def test_api_reads_metadata_only_and_reports_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blob = root / 'blobs' / ('sha256-' + 'ab' * 32)
            blob.parent.mkdir()
            write_gguf(blob, pre='qwen2', extra_token='ab', merges=['a b'])
            show = {'modelfile': 'FROM %s\n' % blob, 'parameters': 'num_ctx 2048\n',
                    'model_info': {'example.context_length': 4096}}

            class opener:
                def open(self, req, timeout=None):
                    url = req.full_url
                    if url.endswith('/api/show'):
                        payload = show
                    elif url.endswith('/api/ps'):
                        payload = {'models': [{'name': 'example:1b', 'context_length': 1024}]}
                    else:
                        raise AssertionError(url)
                    return BytesIO(json.dumps(payload).encode())

            result = tokenize_api.tokenize('example:1b', 'ab', opener())
            self.assertEqual(result['count'], 1)
            self.assertEqual(result['tokens'], [[256, 'ab']])
            self.assertEqual(result['accuracy'], 'verified')
            self.assertEqual(result['context']['loaded'], 1024)
            self.assertEqual(result['context']['modelfile'], 2048)
            self.assertTrue(result['source'].startswith('GGUF tokenizer.ggml.'))
            self.assertNotIn('weight', json.dumps(result).lower())

    def test_fixed_errors_do_not_echo_paths(self):
        with self.assertRaises(tokenize_api.TokenizeError) as error:
            tokenize_api.tokenize('../secret', 'hi')
        self.assertTrue(tokenize_api.is_fixed_message(str(error.exception)))
        self.assertNotIn('secret', str(error.exception))


if __name__ == '__main__':
    unittest.main()
