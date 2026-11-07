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

if __name__ == '__main__': unittest.main()
