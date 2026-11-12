"""Layout order, and a scan of this change for personal identifiers."""
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
NEEDLES = (
    'ed' + 'ragon',
    'dragon' + '-ai',
    'dragon' + '_ai',
    'tail6d9' + 'ce8',
    '100.98' + '.',
    'her' + 'mes',
)


def change_text():
    """Added lines against main, plus new untracked text files.

    Context and deletions stay out of the scan. A one-line edit sits beside
    sentences that already ship in this public tree, and those sentences are
    not part of the change.
    """
    diff = subprocess.check_output(['git', 'diff', '-U3', 'main', '--', '.'], cwd=ROOT, text=True)
    added = []
    for line in diff.splitlines():
        if line.startswith('+++'):
            continue
        if line.startswith('+'):
            added.append(line[1:])
    listed = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=ROOT, text=True)
    extra = []
    for name in listed.splitlines():
        path = ROOT / name
        if path.suffix.lower() in {'.png', '.pyc'} or not path.is_file():
            continue
        try:
            extra.append(path.read_text(encoding='utf-8'))
        except UnicodeDecodeError:
            continue
    return '\n'.join(added) + '\n' + '\n'.join(extra)


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

    def test_no_personal_identifiers_in_the_diff(self):
        lowered = change_text().lower()
        for needle in NEEDLES:
            self.assertNotIn(needle, lowered, needle)

    def test_readme_matches_version_and_shows_example_screenshots(self):
        version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertEqual(readme.splitlines()[2], f'Version {version} — standalone community evaluation package.')
        self.assertNotIn('0.1.0', readme)
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
