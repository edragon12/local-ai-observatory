import unittest
import mac_metrics

class MacTests(unittest.TestCase):
    def test_vm_pages_and_pressure(self):
        text = 'Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 100.\nPages inactive: 200.\nPages speculative: 10.\nPages occupied by compressor: 50.\n'
        d = mac_metrics.parse_vm(text)
        self.assertEqual(d['reclaimable_estimate'], 310 * 16384)
        self.assertEqual(d['compressed'], 50 * 16384)
        self.assertEqual(mac_metrics.parse_swap('total = 1024.00M used = 12.50M free = 1011.50M'), 12.5 * 1024**2)
    def test_missing_output_is_unknown(self):
        self.assertIsNone(mac_metrics.parse_vm('')['reclaimable_estimate'])
        self.assertIsNone(mac_metrics.parse_swap(''))
    def test_cpuinfo_model_name(self):
        text='processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: Intel(R) Xeon(R) Processor\ncpu MHz\t\t: 2400.000\n'
        self.assertEqual(mac_metrics.parse_cpuinfo(text), 'Intel(R) Xeon(R) Processor')
        self.assertEqual(mac_metrics.parse_cpuinfo('processor : 0\n'), '')
    def test_loaded_model_fields_hide_vram_without_a_gpu(self):
        row={'name':'qwen2.5:0.5b','size':648722512,'size_vram':236432916,'context_length':4096,
             'expires_at':'2026-11-09T20:32:50Z','details':{'parameter_size':'494.03M','quantization_level':'Q4_K_M'}}
        hidden=mac_metrics.loaded_row(row, False)
        self.assertNotIn('vram', hidden)
        self.assertEqual(hidden['size'], 648722512)
        self.assertEqual(hidden['context_length'], 4096)
        self.assertEqual(hidden['expires_at'], '2026-11-09T20:32:50Z')
        self.assertEqual(mac_metrics.loaded_row(row, True)['vram'], 236432916)
        self.assertNotIn('vram', mac_metrics.loaded_row({'name':'m','size':1,'size_vram':0,'details':{}}, True))
    def test_installed_row_is_not_a_running_model(self):
        row={'name':'qwen2.5:0.5b','size':397821319,'context_length':32768,'expires_at':'later','size_vram':5,
             'details':{'parameter_size':'494.03M','quantization_level':'Q4_K_M'}}
        item=mac_metrics.installed_row(row)
        self.assertEqual(item['quantization'], 'Q4_K_M')
        self.assertNotIn('context_length', item)
        self.assertNotIn('expires_at', item)
        self.assertNotIn('vram', item)

if __name__ == '__main__': unittest.main()
