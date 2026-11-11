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

    def test_unified_memory_keeps_gpu_share_separate_from_vram(self):
        row = {'name': 'm', 'size': 100, 'size_vram': 80, 'context_length': 2048, 'details': {}}
        hidden = mac_metrics.loaded_row(row, False, unified=True)
        self.assertNotIn('vram', hidden)
        self.assertEqual(hidden['gpu_resident'], 80)
        self.assertNotIn('gpu_resident', mac_metrics.loaded_row(row, False, unified=False))

    def test_nvidia_query_parses_power_and_skips_missing_fields(self):
        text = 'Example GPU, 8192, 2048, 37, 54, 120.50, 250\n"Board, laptop", 4096, 100, 0, [N/A], [N/A], [N/A]\nbad row\n'
        rows = mac_metrics.parse_nvidia_query(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['name'], 'Example GPU')
        self.assertEqual(rows[0]['total'], 8192 * 1024**2)
        self.assertEqual(rows[0]['temperature'], 54)
        self.assertEqual(rows[0]['power_draw'], 120.5)
        self.assertEqual(rows[0]['power_limit'], 250)
        self.assertEqual(rows[1]['name'], 'Board, laptop')
        self.assertIsNone(rows[1]['temperature'])
        self.assertIsNone(rows[1]['power_draw'])
        self.assertEqual(mac_metrics.parse_nvidia_query(''), [])

if __name__ == '__main__': unittest.main()
