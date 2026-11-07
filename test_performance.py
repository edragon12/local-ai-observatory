import unittest
import importlib

class PerformanceTests(unittest.TestCase):
    def test_stream_metrics_hide_content(self):
        p = importlib.import_module('performance')
        rows = [{'message': {'content':'private text'}}, {'done':True,'eval_count':20,'eval_duration':2_000_000_000,'prompt_eval_count':10,'prompt_eval_duration':500_000_000,'load_duration':100_000_000,'total_duration':3_000_000_000}]
        result = p.consume(rows, 'local-model', clock=iter([0,0.4,3.2]).__next__)
        self.assertEqual(result['tokens_per_second'], 10)
        self.assertEqual(result['first_token_seconds'], 0.4)
        self.assertEqual(result['wall_seconds'], 3.2)
        self.assertNotIn('private text', str(result))

    def test_incomplete_stream_rejected(self):
        p = importlib.import_module('performance')
        with self.assertRaises(ValueError):
            p.consume([], 'local-model')

    def test_performance_api_requires_same_origin(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.request import urlopen, Request
        from urllib.error import HTTPError
        from server import Handler
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'http://127.0.0.1:'+str(server.server_port)+'/api/performance'
        try:
            import json
            self.assertIn('results', json.load(urlopen(url)))
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url, method='POST'))
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url+'?model=not-loaded', method='POST', headers={'Origin':url.split('/api/')[0]}))
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()

if __name__ == '__main__': unittest.main()
