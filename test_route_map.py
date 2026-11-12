"""Route-map event fields: only what a live event actually carried."""
import json
import unittest
from pathlib import Path

import live_tap
import openwebui_live_filter
import remote_host


ROOT = Path(__file__).resolve().parent


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def placement(_model):
    return {'loaded': True, 'size': 100, 'vram': 100, 'context': 2048}


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hub = live_tap.LiveHub(clock=self.clock)

    def accept(self, event):
        event.setdefault('t', self.clock.t)
        self.hub.accept([event])

    def snap(self, text=False):
        return self.hub.snapshot(include_text=text, tokenize=lambda model, text: None, placement=placement)

    def test_a_route_label_does_not_invent_a_reason_or_a_time(self):
        self.accept({'type': 'start', 'turn': 'm1', 'model': 'gateway', 'prompt': 'hello there'})
        self.clock.t += 0.2
        self.accept({'type': 'delta', 'turn': 'm1', 'text': '**Local · example**\n\nHi'})
        data = self.snap(text=True)
        self.assertEqual(data['route'], 'local')
        self.assertIsNone(data['route_reason'])
        self.assertEqual(data['timing'], {'route_seconds': None, 'left_network_seconds': None})
        self.assertEqual(data['recent'], [{'id': 'm1', 'route': 'local'}])
        self.assertNotIn('hello', json.dumps(data['recent']))
        self.assertNotIn('Hi', json.dumps(data['recent']))

    def test_optional_fields_are_kept_and_unknown_ones_are_dropped(self):
        self.accept({'type': 'start', 'turn': 'm1', 'model': 'gateway', 'prompt': 'hi',
                     'route_reason': '  policy\nmatch  ',
                     'timing': {'route_seconds': 0.02, 'left_network_seconds': 'nope', 'secret': 4, 'negative': -1}})
        data = self.snap()
        self.assertEqual(data['route_reason'], 'policy match')
        self.assertEqual(data['timing']['route_seconds'], 0.02)
        self.assertIsNone(data['timing']['left_network_seconds'])
        self.assertNotIn('secret', data['timing'])
        self.assertEqual(data['route'], 'direct')

    def test_invalid_reason_does_not_erase_a_real_one(self):
        self.accept({'type': 'start', 'turn': 'm1', 'model': 'gateway', 'prompt': 'hi', 'route_reason': 'policy'})
        self.clock.t += 0.1
        self.accept({'type': 'delta', 'turn': 'm1', 'text': 'Hi', 'route_reason': '   ', 'timing': {'left_network_seconds': True}})
        data = self.snap()
        self.assertEqual(data['route_reason'], 'policy')
        self.assertIsNone(data['timing']['left_network_seconds'])

    def test_a_later_number_fills_one_key_only(self):
        self.accept({'type': 'start', 'turn': 'm1', 'model': 'gateway', 'prompt': 'hi',
                     'timing': {'route_seconds': 0}})
        self.clock.t += 0.4
        self.accept({'type': 'end', 'turn': 'm1', 'timing': {'left_network_seconds': 0.1254, 'route_seconds': 'later'}})
        data = self.snap()
        self.assertEqual(data['timing']['route_seconds'], 0.0)
        self.assertEqual(data['timing']['left_network_seconds'], 0.125)

    def test_recent_routes_follow_each_turn_and_omit_text(self):
        self.accept({'type': 'start', 'turn': 'a1', 'model': 'gateway', 'prompt': 'one'})
        self.clock.t += 0.1
        self.accept({'type': 'delta', 'turn': 'a1', 'text': '**External · demo**\n\nYo'})
        self.clock.t += 0.1
        self.accept({'type': 'start', 'turn': 'b2', 'model': 'example:1b', 'prompt': 'two'})
        data = self.snap(text=True)
        self.assertEqual(data['recent'], [{'id': 'a1', 'route': 'external'}, {'id': 'b2', 'route': 'direct'}])
        self.assertNotIn('prompt', json.dumps(data['recent']))

    def test_idle_recent_is_empty(self):
        data = self.snap()
        self.assertEqual(data['state'], 'idle')
        self.assertEqual(data['recent'], [])

    def test_filter_forwards_only_the_documented_fields(self):
        plain = openwebui_live_filter.start_event({'model': 'example', 'messages': [{'role': 'user', 'content': 'Hello'}]}, 'abc', True)
        self.assertNotIn('route_reason', plain)
        self.assertNotIn('timing', plain)
        body = {'model': 'example', 'messages': [{'role': 'user', 'content': 'Hello'}],
                'route_reason': 'policy', 'timing': {'route_seconds': 0.1, 'left_network_seconds': 0.2, 'other': 9}}
        event = openwebui_live_filter.start_event(body, 'abc', True)
        self.assertEqual(event['route_reason'], 'policy')
        self.assertEqual(event['timing'], {'route_seconds': 0.1, 'left_network_seconds': 0.2})
        self.assertEqual(event['prompt'], 'Hello')

    def test_remote_sanitize_keeps_reported_fields_and_drops_the_rest(self):
        cleaned = remote_host.sanitize_live({
            'seq': 3, 'state': 'done', 'route': 'external', 'route_label': 'External · demo',
            'route_reason': ' policy ', 'timing': {'route_seconds': 0.2, 'note': 'secret'},
            'recent': [{'id': 't1', 'route': 'external'}, {'id': 'bad id', 'route': 'local'}, {'id': 't2', 'route': 'cloud'}],
            'model': 'example:1b', 'started_ago': 1, 'text_included': False,
            'prompt': {'count': 2, 'chars': 4}, 'reply': {'count': 3, 'chars': 6}, 'tap': {'listening': True, 'events': 1},
        }, False)
        self.assertEqual(cleaned['route_reason'], 'policy')
        self.assertEqual(cleaned['timing'], {'route_seconds': 0.2, 'left_network_seconds': None})
        self.assertNotIn('note', cleaned['timing'])
        self.assertEqual(cleaned['recent'], [{'id': 't1', 'route': 'external'}, {'id': 't2', 'route': 'direct'}])
        self.assertNotIn('secret', json.dumps(cleaned))

    def test_panel_stays_hidden_in_the_page_and_version_is_bumped(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        self.assertIn('data-role="route-map" hidden', html)
        self.assertIn('EXAMPLE · FAKE DATA', html)
        self.assertIn('Replay example', html)
        self.assertIn('/route_map.js', html)
        self.assertIn('prefers-reduced-motion', css)
        self.assertEqual((ROOT / 'VERSION').read_text(encoding='utf-8').strip(), '0.3.0')
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('## Route map (0.3.0)', readme)
        self.assertIn('Example data shown', readme)
        self.assertIn('screenshots/route-map-example.png', readme)
        self.assertIn("route_reason", readme)
        self.assertIn('left_network_seconds', readme)
        self.assertTrue((ROOT / 'screenshots' / 'route-map-example.png').is_file())


if __name__ == '__main__':
    unittest.main()
