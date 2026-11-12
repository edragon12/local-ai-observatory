"""observatory/v1 parsing: only allowlisted router and filter fields."""
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import live_tap
import observatory_v1
import openwebui_live_filter
import remote_host
import route_log


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

    def test_a_route_label_does_not_invent_router_fields(self):
        self.accept({'type': 'start', 'turn': 'm1', 'model': 'gateway', 'prompt': 'hello there',
                     'route_reason': 'from the request', 'timing': {'route_seconds': 1}})
        self.clock.t += 0.2
        self.accept({'type': 'delta', 'turn': 'm1', 'text': '**Local · example**\n\nHi'})
        data = self.snap(text=True)
        self.assertEqual(data['route'], 'local')
        self.assertEqual(data['route_source'], 'live tap')
        self.assertIsNone(data['observatory'])
        self.assertNotIn('from the request', json.dumps(data))
        self.assertEqual(data['recent'], [{'id': 'm1', 'route': 'local'}])
        self.assertNotIn('hello', json.dumps(data['recent']))

    def test_stream_event_keeps_allowlisted_fields_and_drops_the_rest(self):
        self.accept({'type': 'start', 'turn': 'm1', 'model': 'gateway', 'prompt': 'hi'})
        self.accept({'type': 'observatory', 'turn': 'm1', 'via': 'router stream', 'observatory': {
            'v': 1,
            'decision': {'target': 'external', 'label': 'External · example', 'reason': {'code': 'policy_match', 'text': 'short note'},
                         'decide_ms': 0.3, 'upstream_ms': 12, 'prompt': 'secret prompt'},
            'classifier': {'result': 'skipped', 'why': 'too_short', 'ms': 0, 'flags': ['cached', 'Not A Flag', 'also_ok']},
            'steps': [{'id': 's1', 'kind': 'tool', 'name': 'lookup', 'arguments': {'q': 'secret'}, 'status': 'ok',
                       'url': 'http://example.test', 'start_ms': 4, 'end_ms': 9}],
            'api_key': 'secret',
        }})
        hidden = self.snap(text=False)
        self.assertEqual(hidden['route'], 'external')
        self.assertEqual(hidden['observatory_source'], 'router stream')
        self.assertIsNone(hidden['observatory']['decision']['reason']['text'])
        self.assertEqual(hidden['observatory']['decision']['reason']['code'], 'policy_match')
        self.assertEqual(hidden['observatory']['classifier']['result'], 'skipped')
        self.assertEqual(hidden['observatory']['classifier']['flags'], ['cached', 'also_ok'])
        self.assertNotIn('arguments', json.dumps(hidden['observatory']))
        self.assertNotIn('secret', json.dumps(hidden['observatory']))
        self.assertNotIn('url', json.dumps(hidden['observatory']))
        shown = self.snap(text=True)
        self.assertEqual(shown['observatory']['decision']['reason']['text'], 'short note')

    def test_an_invalid_update_does_not_erase_a_real_one(self):
        self.accept({'type': 'start', 'turn': 'm1', 'model': 'gateway', 'prompt': 'hi'})
        self.accept({'type': 'observatory', 'turn': 'm1', 'via': 'router stream', 'observatory': {
            'v': 1, 'decision': {'target': 'local', 'decide_ms': 1.5, 'reason': {'code': 'keep_local'}}}})
        self.accept({'type': 'observatory', 'turn': 'm1', 'via': 'router stream', 'observatory': {
            'v': 1, 'decision': {'target': 'nope', 'decide_ms': -4, 'upstream_ms': 8}}})
        data = self.snap()
        decision = data['observatory']['decision']
        self.assertEqual(decision['target'], 'local')
        self.assertEqual(decision['decide_ms'], 1.5)
        self.assertEqual(decision['upstream_ms'], 8)
        self.assertEqual(decision['reason']['code'], 'keep_local')

    def test_a_weaker_source_does_not_replace_the_stream(self):
        self.accept({'type': 'start', 'turn': 'm1', 'model': 'gateway', 'prompt': 'hi'})
        self.accept({'type': 'observatory', 'turn': 'm1', 'via': 'router stream', 'observatory': {
            'v': 1, 'decision': {'target': 'local', 'decide_ms': 1}}})
        self.assertTrue(self.hub.accept_route({'turn': 'm1', 'v': 1, 'decision': {'target': 'external', 'decide_ms': 9}}))
        data = self.snap()
        self.assertEqual(data['observatory']['decision']['target'], 'local')
        self.assertEqual(data['observatory_source'], 'router stream')

    def test_time_match_refuses_two_open_turns(self):
        self.accept({'type': 'start', 'turn': 'a1', 'model': 'gateway', 'prompt': 'one'})
        self.accept({'type': 'start', 'turn': 'b2', 'model': 'gateway', 'prompt': 'two'})
        self.assertFalse(self.hub.accept_route({'v': 1, 'decision': {'target': 'local', 'decide_ms': 1}}))
        self.assertIsNone(self.snap()['observatory'])
        self.hub.turns = [turn for turn in self.hub.turns if turn.id == 'b2']
        self.assertTrue(self.hub.accept_route({'v': 1, 'decision': {'target': 'external', 'decide_ms': 2}}))
        data = self.snap()
        self.assertEqual(data['observatory_source'], 'router sidecar (matched by time)')
        self.assertEqual(data['route'], 'external')

    def test_ollama_connection_is_local_without_a_router(self):
        self.accept({'type': 'start', 'turn': 'm1', 'model': 'example:1b', 'prompt': 'hi'})
        self.accept({'type': 'context', 'turn': 'm1', 'owned_by': 'ollama', 'web_search': False, 'files': 2})
        self.clock.t += 0.4
        self.accept({'type': 'delta', 'turn': 'm1', 'text': 'Hello'})
        data = self.snap()
        self.assertEqual((data['route'], data['route_source']), ('local', 'Open WebUI'))
        self.assertEqual(data['builtins']['web_search'], False)
        self.assertEqual(data['builtins']['files'], 2)
        self.assertIsNone(data['builtins']['memory'])
        self.assertIsNone(data['observatory'])

    def test_route_log_matches_one_turn_and_is_labelled(self):
        self.accept({'type': 'start', 'turn': 'm1', 'model': 'example:1b', 'prompt': 'hi'})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'routes.jsonl'
            path.write_text(json.dumps({'time': self.clock.t, 'turn': 'm1', 'observatory': {
                'v': 1, 'decision': {'target': 'local', 'decide_ms': 3}, 'prompt': 'nope'}}) + '\n', encoding='utf-8')
            rows = route_log.read_jsonl(path)
            self.assertEqual(self.hub.ingest_log(rows), 1)
        data = self.snap()
        self.assertEqual(data['observatory_source'], 'router log (matched by time)')
        self.assertEqual(data['observatory']['decision']['decide_ms'], 3)
        self.assertNotIn('nope', json.dumps(data['observatory']))

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

    def test_filter_reads_the_response_chunk_and_ignores_the_request(self):
        body = {'model': 'example', 'messages': [{'role': 'user', 'content': 'Hello'}],
                'route_reason': 'from the request', 'observatory': {'v': 1, 'decision': {'target': 'external'}},
                'features': {'web_search': True, 'memory': False}, 'files': [{}], 'tool_ids': ['a', 'b']}
        event = openwebui_live_filter.start_event(body, 'abc', True)
        self.assertNotIn('route_reason', event)
        self.assertNotIn('observatory', event)
        self.assertEqual(event['prompt'], 'Hello')
        ctx = openwebui_live_filter.context_event(body, 'abc', {'owned_by': 'ollama'})
        self.assertEqual(ctx['owned_by'], 'ollama')
        self.assertEqual(ctx['web_search'], True)
        self.assertEqual(ctx['files'], 1)
        self.assertEqual(ctx['tool_ids'], 2)
        chunk = {'observatory': {'v': 1, 'decision': {'target': 'local', 'prompt': 'hidden'}, 'steps': [
            {'kind': 'mcp', 'name': 'search', 'server': 'notes', 'arguments': {'q': 'x'}}]},
            'usage': {'load_duration': 2_000_000, 'prompt_eval_count': 4, 'eval_duration': 5_000_000, 'prompt_tokens': 4, 'completion_tokens': 2},
            'choices': [{'delta': {'tool_calls': [{'function': {'name': 'lookup', 'arguments': '{}'}}], 'content': ''}, 'finish_reason': None}]}
        streamed = openwebui_live_filter.stream_events(chunk, 'abc')
        kinds = [item['type'] for item in streamed]
        self.assertIn('observatory', kinds)
        self.assertIn('usage', kinds)
        self.assertIn('tool', kinds)
        observed = next(item for item in streamed if item['type'] == 'observatory')
        self.assertNotIn('prompt', json.dumps(observed))
        self.assertNotIn('arguments', json.dumps(observed))
        self.assertEqual(observed['via'], 'router stream')
        usage = next(item for item in streamed if item['type'] == 'usage')['usage']
        self.assertEqual(usage['load_ms'], 2.0)
        self.assertEqual(usage['prompt_tokens'], 4)
        tool = next(item for item in streamed if item['type'] == 'tool')
        self.assertEqual(tool['name'], 'lookup')
        self.assertNotIn('arguments', tool)
        task = openwebui_live_filter.task_event({'task': 'query_generation'}, 'abc')
        self.assertEqual(task, {'type': 'task', 'turn': 'abc', 'name': 'query_generation'})
        facts = openwebui_live_filter.outlet_facts({'sources': [{}, {}], 'messages': [
            {'role': 'assistant', 'tool_calls': [{'function': {'name': 'lookup', 'arguments': 'secret'}}]}]})
        self.assertEqual(facts['sources'], 2)
        self.assertEqual(facts['tool_names'], ['lookup'])
        self.assertNotIn('secret', json.dumps(facts))

    def test_remote_sanitize_keeps_reported_fields_and_drops_the_rest(self):
        cleaned = remote_host.sanitize_live({
            'seq': 3, 'state': 'done', 'route': 'external', 'route_label': 'External · demo',
            'route_source': 'router stream',
            'observatory': {'v': 1, 'decision': {'target': 'external', 'reason': {'code': 'policy_match', 'text': 'note'}},
                            'steps': [{'kind': 'tool', 'name': 'lookup', 'arguments': 'secret'}]},
            'observatory_source': 'router stream',
            'recent': [{'id': 't1', 'route': 'external'}, {'id': 'bad id', 'route': 'local'}, {'id': 't2', 'route': 'cloud'}],
            'model': 'example:1b', 'started_ago': 1, 'text_included': False,
            'prompt': {'count': 2, 'chars': 4}, 'reply': {'count': 3, 'chars': 6}, 'tap': {'listening': True, 'events': 1},
        }, False)
        self.assertIsNone(cleaned['observatory']['decision']['reason']['text'])
        self.assertEqual(cleaned['observatory']['decision']['reason']['code'], 'policy_match')
        self.assertNotIn('arguments', json.dumps(cleaned))
        self.assertNotIn('secret', json.dumps(cleaned))
        self.assertEqual(cleaned['recent'], [{'id': 't1', 'route': 'external'}, {'id': 't2', 'route': 'direct'}])

    def test_sidecar_requires_the_token(self):
        hub = live_tap.LiveHub(clock=Clock())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'token'
            path.write_text('y' * 32 + '\n', encoding='utf-8')
            server = live_tap.start_receiver(hub, host='127.0.0.1', port=0, token_file=path, allowed=['127.0.0.0/8'])
            self.assertIsNotNone(server)
            port = server.server_address[1]
            url = 'http://127.0.0.1:%d/live/route' % port

            def post(auth):
                body = json.dumps({'v': 1, 'decision': {'target': 'local'}}).encode()
                request = urllib.request.Request(url, data=body, method='POST', headers={
                    'Content-Type': 'application/json', 'Authorization': auth})
                try:
                    with urllib.request.urlopen(request, timeout=2) as response:
                        return response.status
                except HTTPError as exc:
                    exc.close()
                    return exc.code

            self.assertEqual(post('Bearer nope'), 401)
            server.shutdown()
            server.server_close()

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
        self.assertIn('screenshots/route-map-example-light.png', readme)
        self.assertIn('observatory/v1', readme)
        self.assertIn('PLAYBACK OF A RECORDED TURN', readme)
        self.assertTrue((ROOT / 'screenshots' / 'route-map-example.png').is_file())
        self.assertTrue((ROOT / 'screenshots' / 'route-map-example-light.png').is_file())


if __name__ == '__main__':
    unittest.main()
