import http.client
import json
import threading
import unittest
from unittest.mock import patch
import server

class ServerTests(unittest.TestCase):
    def test_http_protections(self):
        s = server.make_server(0)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        port=s.server_port
        def request(method, path, headers=None):
            c=http.client.HTTPConnection('127.0.0.1',port)
            c.request(method,path,headers=headers or {})
            r=c.getresponse(); result=(r.status,r.read()); c.close(); return result
        try:
            self.assertEqual(request('GET','/')[0],200)
            self.assertEqual(request('GET','/../server.py')[0],404)
            self.assertEqual(request('GET','/',{'Host':'evil.example'})[0],403)
            self.assertEqual(request('POST','/api/performance?model=x')[0],403)
            origin='http://127.0.0.1:'+str(port)
            with patch('performance.run',return_value={'tokens_per_second':12}) as run:
                code,body=request('POST','/api/performance?model=test',{'Origin':origin})
                self.assertEqual(code,200); run.assert_called_once_with('test')
        finally:
            s.shutdown(); s.server_close()

    def test_specific_failure_reasons_and_favicon(self):
        s = server.make_server(0)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        port=s.server_port
        origin='http://127.0.0.1:'+str(port)
        def post(side_effect):
            with patch('performance.run', side_effect=side_effect):
                c=http.client.HTTPConnection('127.0.0.1', port)
                c.request('POST', '/api/performance?model=test', headers={'Origin': origin, 'Content-Length': '0'})
                r=c.getresponse(); body=json.loads(r.read()); c.close()
                return r.status, body['error']
        class responded(OSError):
            code = 500
        try:
            c=http.client.HTTPConnection('127.0.0.1', port)
            c.request('GET', '/favicon.ico')
            r=c.getresponse(); body=r.read(); c.close()
            self.assertEqual(r.status, 204)
            self.assertEqual(body, b'')
            self.assertEqual(post(ValueError('A performance test is already running')), (400, 'A performance test is already running'))
            self.assertEqual(post(ValueError('Model must already be loaded; refresh the model list')), (400, 'Model must already be loaded; refresh the model list'))
            self.assertEqual(post(OSError('connection refused')), (400, 'Ollama is not reachable at 127.0.0.1:11434.'))
            self.assertEqual(post(responded()), (400, 'Ollama returned an error. The test was not recorded.'))
        finally:
            s.shutdown(); s.server_close()

if __name__=='__main__': unittest.main()
