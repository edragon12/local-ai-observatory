"""Layout order, and a scan of every tracked file for personal identifiers."""
import re
import struct
import subprocess
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
# Split so this file does not itself contain a forbidden string.
NEEDLES = (
    'her' + 'mes',
    'je' + 'v',
    'type' + 'safe',
    'dragon' + '-router',
    'dragon' + '-ai',
    'dragon' + '_ai',
    'fl' + 'aim',
    'mon' + 'arch',
    'tail6d9' + 'ce8',
    '100.98' + '.',
    '172.17' + '.0.1',
    'ed' + 'ragon',
    'ell' + 'is',
    'mar' + 'tin',
    'tul' + 'ly',
    'openai-' + 'codex',
    'gpt-' + '6',
)
# Exact line text only. A longer line that merely contains one of these is not allowed.
ALLOW_LINES = (
    'git clone https://github.com/' + 'ed' + 'ragon12/local-ai-observatory.git',
    'Copyright (c) 2026 ' + 'Mar' + 'tin ' + 'Tul' + 'ly',
)
# Carrier-grade range used by Tailscale. A bare "100." is not an address.
TAILSCALE_IP = re.compile(
    r'(?<!\d)100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}(?:\.\d{1,3})?(?!\d)'
)
PNG_SIG = b'\x89PNG\r\n\x1a\n'
META_CHUNKS = {b'tEXt', b'iTXt', b'zTXt', b'eXIf'}


def line_hits(line):
    """Forbidden needles on one line, unless the line is an exact allowlist entry."""
    if line in ALLOW_LINES:
        return []
    lowered = line.lower()
    hits = [needle for needle in NEEDLES if needle in lowered]
    if TAILSCALE_IP.search(line):
        hits.append('tailscale-address')
    return hits


def text_hits(text):
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for hit in line_hits(line):
            found.append('%s:%s' % (number, hit))
    return found


def png_chunks(data):
    """Return (kind, payload, crc) tuples. kind is 4 bytes."""
    if not data.startswith(PNG_SIG):
        raise ValueError('not a png')
    chunks = []
    index = len(PNG_SIG)
    while index + 8 <= len(data):
        length = struct.unpack('>I', data[index:index + 4])[0]
        kind = data[index + 4:index + 8]
        payload = data[index + 8:index + 8 + length]
        crc = data[index + 8 + length:index + 12 + length]
        if len(payload) != length or len(crc) != 4:
            raise ValueError('truncated png')
        chunks.append((kind, payload, crc))
        index += 12 + length
        if kind == b'IEND':
            break
    return chunks


def metadata_chunks(data):
    """tEXt, iTXt, zTXt and EXIF chunk types. These can carry names, paths or hostnames."""
    return [kind.decode('ascii') for kind, _payload, _crc in png_chunks(data) if kind in META_CHUNKS]


def strip_png_metadata(data):
    """Drop text and EXIF chunks. Image chunks are copied with their original CRCs."""
    kept = bytearray(PNG_SIG)
    for kind, payload, crc in png_chunks(data):
        if kind in META_CHUNKS:
            continue
        kept += struct.pack('>I', len(payload)) + kind + payload + crc
    return bytes(kept)


def _chunk(kind, payload):
    crc = zlib.crc32(kind + payload) & 0xffffffff
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', crc)


def _png_with(kind, payload):
    ihdr = _chunk(b'IHDR', b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00')
    extra = _chunk(kind, payload)
    idat = _chunk(b'IDAT', b'x')
    iend = _chunk(b'IEND', b'')
    return PNG_SIG + ihdr + extra + idat + iend


class SurfaceTests(unittest.TestCase):
    def test_layout_puts_live_first_and_prompt_tokens_below_metrics(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        js = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertLess(html.index('id="glance"'), html.index('id="local-view"'))
        self.assertLess(html.index('data-part="live"'), html.index('data-part="workspace"'))
        self.assertLess(html.index('data-part="workspace"'), html.index('data-part="compare"'))
        self.assertLess(html.index('data-part="compare"'), html.index('data-part="tokens"'))
        self.assertLess(html.index('data-part="tokens"'), html.index('data-part="tokenizer"'))
        self.assertIn('<details class="panel-live" data-part="live" open>', html)
        self.assertIn('<details class="panel-tokenizer" data-part="tokenizer" open>', html)
        self.assertIn('id="post-mode"', html)
        self.assertIn('id="routing-strip"', html)
        self.assertNotIn('account-strip', html)
        self.assertNotIn('OpenAI', html)
        self.assertNotIn('quota', html.lower())
        self.assertNotIn('https://', html)
        self.assertIn(':root[data-post=on] .private', css)
        self.assertIn('.glance-tile', css)
        self.assertIn('function routingGlance(', js)
        self.assertIn('function memoryFit(', js)
        self.assertIn('function sparkGeometry(', js)
        self.assertEqual(js.count("method:'POST'"), 2)
        post = js[js.index('function setPost('):js.index("for(const v of Object.values(views))")]
        self.assertNotIn('runTest', post)
        self.assertNotIn("method:'POST'", post)
        self.assertNotIn('fetch(', post)

    def test_allowlist_is_an_exact_line_and_a_planted_name_fails(self):
        clone, notice = ALLOW_LINES
        self.assertEqual(text_hits(clone), [])
        self.assertEqual(text_hits(notice), [])
        self.assertTrue(text_hits(notice + ' and more'))
        self.assertTrue(text_hits('  ' + notice))
        planted = 'already on main: ' + NEEDLES[0]
        self.assertTrue(any(NEEDLES[0] in hit for hit in text_hits('context\n' + planted + '\n')))
        self.assertEqual(text_hits('Pages free: 100.\n'), [])
        self.assertTrue(text_hits('100.98' + '.1.2'))
        self.assertTrue(text_hits('100.' + '64.0.1'))
        self.assertEqual(text_hits('100.128.1.1'), [])
        self.assertTrue(text_hits('172.17' + '.0.1'))

    def test_no_personal_identifiers_in_the_tree(self):
        listed = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT)
        for raw in listed.split(b'\0'):
            if not raw:
                continue
            path = ROOT / raw.decode()
            if path.suffix.lower() == '.png':
                self.assertEqual(metadata_chunks(path.read_bytes()), [], str(path.relative_to(ROOT)))
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            hits = text_hits(text)
            self.assertEqual(hits, [], '%s %s' % (path.relative_to(ROOT), hits))

    def test_png_metadata_is_detected_and_stripped(self):
        for kind in META_CHUNKS:
            dirty = _png_with(kind, b'Comment\x00/Users/example')
            self.assertEqual(metadata_chunks(dirty), [kind.decode('ascii')])
            clean = strip_png_metadata(dirty)
            self.assertEqual(metadata_chunks(clean), [])
            self.assertNotIn(b'/Users/example', clean)
            self.assertTrue(clean.startswith(PNG_SIG))
            self.assertIn(b'IHDR', clean)
            self.assertIn(b'IDAT', clean)
            self.assertTrue(clean.endswith(_chunk(b'IEND', b'')))

    def test_readme_matches_version_and_shows_example_screenshots(self):
        version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        privacy = (ROOT / 'PRIVACY.md').read_text(encoding='utf-8')
        self.assertEqual(readme.splitlines()[2], f'Version {version} — standalone community evaluation package.')
        self.assertNotIn('0.1.0', readme)
        self.assertNotIn(NEEDLES[0], readme.lower())
        self.assertNotIn(NEEDLES[0], privacy.lower())
        features = readme.split('## Features', 1)[1].split('## ', 1)[0]
        self.assertIn('](#version-020)', features)
        for phrase in ('tok/s sparkline', 'FITS', 'SPILLS', 'CPU', 'nvidia-smi', 'Post mode', 'Prompt tokens'):
            self.assertIn(phrase, features)
        self.assertIn('Example data shown', readme)
        for name in ('screenshots/dashboard-dark.png', 'screenshots/dashboard-light.png'):
            self.assertIn(name, readme)
            self.assertTrue((ROOT / name).is_file(), name)
        self.assertNotIn('linux-results-', readme)


if __name__ == '__main__':
    unittest.main()
