import http.client
import json
import platform
import threading
import unittest
from pathlib import Path
import mac_metrics
import server

class CommunityTests(unittest.TestCase):
    def test_no_private_defaults_or_remote_assets(self):
        root=Path(__file__).parent
        for name in ('server.py','app.js','index.html','Start.command'):
            text=(root/name).read_text()
            for forbidden in ('ChatGPT Plus','/api/dragon','Connect-Dragon-AI','ssh -','scp ','TYPESAFE_API_KEY'):
                self.assertNotIn(forbidden,text,name)
        self.assertNotIn('https://',(root/'index.html').read_text())

    def test_removed_remote_endpoints_and_live_hardware(self):
        s=server.make_server(0)
        threading.Thread(target=s.serve_forever,daemon=True).start()
        try:
            for route in ('/api/dragon/account','/api/dragon/snapshot','/.env','/server.py'):
                c=http.client.HTTPConnection('127.0.0.1',s.server_port,timeout=15)
                c.request('GET',route);r=c.getresponse();self.assertEqual(r.status,404);r.read();c.close()
            c=http.client.HTTPConnection('127.0.0.1',s.server_port,timeout=15)
            c.request('GET','/api/hardware');r=c.getresponse();self.assertEqual(r.status,200)
            data=json.loads(r.read());self.assertGreater(data['memory']['total'],0);self.assertIn('ollama',data);c.close()
            if platform.system()=='Linux':
                self.assertEqual(data['chip'], mac_metrics.linux_cpu_model())
                parsed=mac_metrics.parse_cpuinfo(Path('/proc/cpuinfo').read_text(encoding='utf-8', errors='replace'))
                if parsed: self.assertEqual(data['chip'], parsed)
                if not mac_metrics.gpu_present():
                    self.assertNotIn('vram', json.dumps(data['ollama']))
        finally:
            s.shutdown();s.server_close()

if __name__=='__main__': unittest.main()
