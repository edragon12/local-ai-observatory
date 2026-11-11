"""Live tap timing, redaction and the opt-in receiver."""
import json
import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import live_tap
import openwebui_live_filter


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def fake_tokenize(model, text):
    parts, buf = [], ''
    for ch in text:
        if ch == ' ' and buf and not buf.endswith(' '):
            parts.append(buf)
            buf = ' '
        else:
            buf += ch
    if buf:
        parts.append(buf)
    return {'count': len(parts), 'tokens': [[i, part] for i, part in enumerate(parts)],
            'chars': len(text), 'special_ids': [], 'accuracy': 'verified', 'context': {'loaded': 2048}}


def placement(model):
    return {'loaded': True, 'size': 100, 'vram': 100, 'context': 2048}


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hub = live_tap.LiveHub(clock=self.clock)

    def start(self, model='gateway'):
        self.hub.accept([{'type': 'start', 'turn': 'm1', 'model': model, 'prompt': 'hi', 't': self.clock.t}])

    def delta(self, text, dt=0.0):
        self.clock.t += dt
        self.hub.accept([{'type': 'delta', 'turn': 'm1', 'text': text, 't': self.clock.t}])

    def snap(self, text=False):
        return self.hub.snapshot(include_text=text, tokenize=fake_tokenize, placement=placement)

    def test_label_only_does_not_start_the_clock(self):
        self.start()
        self.delta('**Local · example**\n\n', 0.015)
        data = self.snap(text=True)
        self.assertEqual(data['state'], 'waiting')
        self.assertIsNone(data['first_token_seconds'])
        self.assertIsNone(data['tokens_per_second'])
        self.assertEqual((data['route'], data['route_label']), ('local', 'Local · example'))
        self.assertEqual(data['reply'].get('text', ''), '')

    def test_clock_starts_on_content_after_the_label(self):
        self.start()
        self.delta('**Local · example**\n\n', 0.015)
        self.delta('Sunny', 0.600)
        self.delta(' week.', 0.200)
        data = self.snap(text=True)
        self.assertEqual(data['state'], 'streaming')
        self.assertAlmostEqual(data['first_token_seconds'], 0.615, places=3)
        self.assertAlmostEqual(data['stream_seconds'], 0.200, places=3)
        self.assertEqual([piece for _, piece in data['reply']['tokens']], ['Sunny', ' week.'])
        self.assertAlmostEqual(data['tokens_per_second'], 1 / 0.200, places=2)
        self.assertEqual(data['placement']['vram'], 100)

    def test_external_label_is_skipped_the_same_way(self):
        self.start()
        self.delta('**External · demo**\n\n', 0.012)
        self.assertIsNone(self.snap()['first_token_seconds'])
        self.delta('Hello', 0.400)
        data = self.snap(text=True)
        self.assertEqual((data['route'], data['route_label']), ('external', 'External · demo'))
        self.assertAlmostEqual(data['first_token_seconds'], 0.412, places=3)
        self.assertIsNone(data['placement'])
        self.assertEqual([piece for _, piece in data['reply']['tokens']], ['Hello'])

    def test_label_plus_content_in_one_chunk_starts_immediately(self):
        self.start()
        self.delta('**Local · example**\n\nSunny', 0.800)
        data = self.snap(text=True)
        self.assertEqual(data['state'], 'streaming')
        self.assertAlmostEqual(data['first_token_seconds'], 0.800, places=3)
        self.assertEqual([piece for _, piece in data['reply']['tokens']], ['Sunny'])

    def test_direct_stream_times_the_first_chunk(self):
        self.start(model='example:1b')
        self.delta('Hello', 0.800)
        self.delta(' world', 0.250)
        data = self.snap(text=True)
        self.assertEqual(data['route'], 'direct')
        self.assertIsNone(data['route_label'])
        self.assertAlmostEqual(data['first_token_seconds'], 0.800, places=3)
        self.assertAlmostEqual(data['stream_seconds'], 0.250, places=3)
        self.assertAlmostEqual(data['tokens_per_second'], 1 / 0.250, places=2)

    def test_pattern_is_configurable_and_text_can_be_withheld(self):
        self.hub.configure(r'\AROUTE:(\S+)\n', 'local', 'example:1b')
        self.start(model='gateway')
        self.delta('ROUTE:local\n', 0.010)
        self.assertEqual(self.snap()['state'], 'waiting')
        self.delta('Hi', 0.200)
        hidden = self.snap(text=False)
        self.assertEqual(hidden['model'], 'example:1b')
        self.assertNotIn('tokens', hidden['reply'])
        self.assertNotIn('text', hidden['reply'])
        self.assertEqual(hidden['reply']['lengths'], [[2, 0]])
        self.assertFalse(hidden['text_included'])
        shown = self.snap(text=True)
        self.assertEqual(shown['reply']['tokens'], [[0, 'Hi']])

    def test_tokenizer_failure_keeps_plain_text_when_text_is_requested(self):
        self.start(model='example:1b')
        self.delta('Hello', 0.2)
        data = self.hub.snapshot(include_text=True, tokenize=lambda *args: None, placement=placement)
        self.assertEqual(data['reply']['text'], 'Hello')
        self.assertIsNone(data['reply']['count'])


class ReceiverTests(unittest.TestCase):
    def test_receiver_is_opt_in_and_checks_the_token(self):
        hub = live_tap.LiveHub(clock=Clock())
        missing = live_tap.start_receiver(hub, token_file=Path('/tmp/does-not-exist-observatory-token'))
        self.assertIsNone(missing)
        self.assertFalse(hub.listening)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'live-tap.token'
            token = 'x' * 32
            path.write_text(token + '\n', encoding='utf-8')
            os.chmod(path, 0o600)
            server = live_tap.start_receiver(hub, host='127.0.0.1', port=0, token_file=path, allowed=['127.0.0.0/8'])
            self.assertIsNotNone(server)
            port = server.server_address[1]
            url = 'http://127.0.0.1:%d/live/events' % port

            def post(auth, body=b'{}'):
                request = urllib.request.Request(url, data=body, method='POST', headers={
                    'Content-Type': 'application/json', 'Authorization': auth})
                try:
                    with urllib.request.urlopen(request, timeout=2) as response:
                        return response.status
                except HTTPError as exc:
                    exc.close()
                    return exc.code

            self.assertEqual(post('Bearer nope'), 401)
            body = json.dumps({'events': [{'type': 'start', 'turn': 't1', 'model': 'example:1b', 'prompt': 'hi', 't': 1000}]}).encode()
            self.assertEqual(post('Bearer ' + token, body), 204)
            self.assertEqual(hub.events, 1)
            server.shutdown()
            server.server_close()


class FilterTests(unittest.TestCase):
    def test_events_do_not_change_the_chat_shape(self):
        body = {'model': 'example', 'messages': [{'role': 'user', 'content': 'Hello'}]}
        event = openwebui_live_filter.start_event(body, 'abc', True)
        self.assertEqual(event['prompt'], 'Hello')
        self.assertEqual(event['turn'], 'abc')
        chunk = {'choices': [{'delta': {'content': 'Hi'}, 'finish_reason': None}]}
        self.assertEqual(openwebui_live_filter.stream_events(chunk, 'abc'), [{'type': 'delta', 'turn': 'abc', 'text': 'Hi'}])
        done = {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}
        self.assertEqual(openwebui_live_filter.stream_events(done, 'abc')[0]['type'], 'end')
        source = Path(openwebui_live_filter.__file__).read_text(encoding='utf-8')
        self.assertIn("default=''", source)
        self.assertIn('return body', source)
        self.assertIn('return event', source)
        self.assertIn('http://127.0.0.1:8768/live/events', source)


if __name__ == '__main__':
    unittest.main()
