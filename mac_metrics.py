"""Read-only Apple Silicon metrics. No sudo or external requests."""
import re
import platform
import shutil
import subprocess
import time
import json
from urllib.request import build_opener, ProxyHandler


def command(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return p.stdout.strip() if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def parse_vm(text):
    match = re.search(r'page size of (\d+) bytes', text)
    if not match:
        return {'reclaimable_estimate': None, 'compressed': None}
    size = int(match[1])
    pages = dict((k, int(v)) for k, v in re.findall(r'^([^:\n]+):\s+(\d+)\.', text, re.M))
    keys = ['Pages free', 'Pages inactive', 'Pages speculative']
    return {'reclaimable_estimate': sum(pages.get(k, 0) for k in keys)*size,
            'compressed': pages.get('Pages occupied by compressor', 0)*size}


def parse_swap(text):
    m = re.search(r'used\s*=\s*([\d.]+)([KMG])', text)
    return float(m[1])*{'K':1024, 'M':1024**2, 'G':1024**3}[m[2]] if m else None


def ollama():
    data = {'installed': [], 'loaded': [], 'status': 'Available'}
    opener = build_opener(ProxyHandler({}))
    for endpoint, key in [('tags', 'installed'), ('ps', 'loaded')]:
        try:
            with opener.open('http://127.0.0.1:11434/api/'+endpoint, timeout=2) as r:
                rows = json.load(r)['models']
            data[key] = [{'name': r.get('name', 'Unknown'), 'size': r.get('size'),
                          'parameters': (r.get('details') or {}).get('parameter_size', 'Unknown'),
                          'quantization': (r.get('details') or {}).get('quantization_level', 'Unknown')} for r in rows]
        except (OSError, ValueError, KeyError, TypeError):
            data['status'] = 'Unavailable or partial — start Ollama locally'
    return data


def collect():
    import psutil
    vm = psutil.virtual_memory()
    is_mac = platform.system() == 'Darwin'
    extra = parse_vm(command(['vm_stat'])) if is_mac else {'reclaimable_estimate':None, 'compressed':None}
    # kern.memorystatus_vm_pressure_level is a kernel status, not a guessed percentage.
    pressure_raw = command(['sysctl', '-n', 'kern.memorystatus_vm_pressure_level']) if is_mac else ''
    pressure = {'1':'Normal', '2':'Warning', '4':'Critical'}.get(pressure_raw, 'Unavailable')
    disk = shutil.disk_usage('/')
    chip = command(['sysctl', '-n', 'machdep.cpu.brand_string']) if is_mac else platform.machine()
    return {'sampled_at':time.time(), 'supported_host':is_mac,
            'chip':chip or platform.machine(), 'os':platform.platform(),
            'unified_memory':is_mac and platform.machine() == 'arm64',
            'cpu_percent':psutil.cpu_percent(interval=0.2),
            'memory':{'total':vm.total, 'used':vm.total-vm.available, 'available':vm.available,
                      'percent':vm.percent, 'pressure':pressure, **extra,
                      'swap_used':parse_swap(command(['sysctl', '-n', 'vm.swapusage'])) if is_mac else psutil.swap_memory().used},
            'disk':{'total':disk.total, 'used':disk.used, 'free':disk.free}, 'ollama':ollama()}
