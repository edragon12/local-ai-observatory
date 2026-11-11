"""Read-only host metrics. No sudo or external requests."""
import csv
import io
import re
import platform
import shutil
import subprocess
import time
import json
from pathlib import Path
from urllib.request import build_opener, ProxyHandler


def command(args, timeout=5):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip() if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def parse_cpuinfo(text):
    for line in text.splitlines():
        key, sep, value = line.partition(':')
        if sep and key.strip().lower() == 'model name' and value.strip():
            return value.strip()
    return ''


def linux_cpu_model():
    try:
        text = Path('/proc/cpuinfo').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return platform.machine()
    return parse_cpuinfo(text) or platform.machine()


def gpu_present():
    """A GPU Ollama can use. size_vram alone is not evidence on CPU-only hosts."""
    if platform.system() == 'Darwin' and platform.machine() in ('arm64', 'aarch64'):
        return True
    for args in (['nvidia-smi', '-L'], ['rocm-smi', '-i']):
        if command(args, timeout=1):
            return True
    return False


def _details(row):
    details = row.get('details')
    return details if isinstance(details, dict) else {}


def installed_row(row):
    details = _details(row)
    size = row.get('size')
    return {'name': row.get('name') or 'Unknown',
            'size': size if isinstance(size, int) else None,
            'parameters': details.get('parameter_size') or 'Unknown',
            'quantization': details.get('quantization_level') or 'Unknown'}


def loaded_row(row, include_vram, unified=False):
    """Resident size, runtime context, and keep-alive expiry come from /api/ps.
    VRAM is attached only when the caller has an independent GPU signal.
    On unified memory, gpu_resident is the GPU-mapped share of that same pool."""
    item = installed_row(row)
    context = row.get('context_length')
    if isinstance(context, int) and context > 0:
        item['context_length'] = context
    expires = row.get('expires_at')
    if isinstance(expires, str) and expires:
        item['expires_at'] = expires
    vram = row.get('size_vram')
    if include_vram and isinstance(vram, int) and vram > 0:
        item['vram'] = vram
    if unified and isinstance(vram, int) and vram > 0:
        item['gpu_resident'] = vram
    return item


def _optional_float(value):
    """nvidia-smi prints '[N/A]' when a device does not report a field."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and number >= 0 else None


def parse_nvidia_query(text):
    """Parse `nvidia-smi --format=csv,noheader,nounits` rows.

    Columns: name, memory.total MiB, memory.used MiB, utilization.gpu,
    temperature.gpu, power.draw, power.limit.
    """
    output = []
    for row in csv.reader(io.StringIO(text or '')):
        if len(row) < 5:
            continue
        try:
            output.append({
                'name': row[0].strip()[:120],
                'total': int(float(row[1])) * 1024**2,
                'used': int(float(row[2])) * 1024**2,
                'percent': float(row[3]),
                'temperature': _optional_float(row[4]),
                'power_draw': _optional_float(row[5]) if len(row) > 5 else None,
                'power_limit': _optional_float(row[6]) if len(row) > 6 else None,
            })
        except (ValueError, IndexError):
            continue
    return output


def query_gpus():
    text = command([
        'nvidia-smi',
        '--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw,power.limit',
        '--format=csv,noheader,nounits',
    ], timeout=3)
    return parse_nvidia_query(text)


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
    include_vram = gpu_present()
    unified = platform.system() == 'Darwin' and platform.machine() in ('arm64', 'aarch64')
    jobs = (('tags', 'installed', installed_row),
            ('ps', 'loaded', lambda row: loaded_row(row, include_vram, unified=unified)))
    for endpoint, key, build in jobs:
        try:
            with opener.open('http://127.0.0.1:11434/api/'+endpoint, timeout=2) as r:
                rows = json.load(r)['models']
            if not isinstance(rows, list):
                raise TypeError('models')
            data[key] = [build(row) for row in rows if isinstance(row, dict)]
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
    chip = command(['sysctl', '-n', 'machdep.cpu.brand_string']) if is_mac else linux_cpu_model()
    return {'sampled_at':time.time(), 'supported_host':is_mac,
            'chip':chip or platform.machine(), 'os':platform.platform(),
            'unified_memory':is_mac and platform.machine() == 'arm64',
            'cpu_percent':psutil.cpu_percent(interval=0.2),
            'memory':{'total':vm.total, 'used':vm.total-vm.available, 'available':vm.available,
                      'percent':vm.percent, 'pressure':pressure, **extra,
                      'swap_used':parse_swap(command(['sysctl', '-n', 'vm.swapusage'])) if is_mac else psutil.swap_memory().used},
            'disk':{'total':disk.total, 'used':disk.used, 'free':disk.free},
            'gpus':query_gpus(), 'ollama':ollama()}
