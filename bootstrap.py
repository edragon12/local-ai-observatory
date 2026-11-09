"""Create this folder's .venv. No network and no system package installs."""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def python_version_label(executable):
    out = subprocess.check_output(
        [executable, '-c', 'import sys; print("%s.%s" % sys.version_info[:2])'],
        text=True, timeout=15)
    label = out.strip()
    if not label or any(ch not in '0123456789.' for ch in label):
        raise ValueError('Unrecognized Python version')
    return label


def pip_works(python):
    try:
        result = subprocess.run(
            [str(python), '-m', 'pip', '--version'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _hint(version):
    return (
        'Could not create .venv because this Python is missing ensurepip/venv support.\n'
        'Debian/Ubuntu, one time (match your Python version):\n'
        f'  sudo apt install python{version}-venv\n'
        'The dashboard does not need administrator privileges after that package is installed.\n'
    )


def ensure_venv(root, python=None, run=None, pip_ok=None, version=None, rmtree=None):
    """Return (ok, message). A failed create leaves no .venv directory behind."""
    root = Path(root)
    python = python or sys.executable
    run = run or subprocess.run
    pip_ok = pip_ok or pip_works
    rmtree = rmtree or shutil.rmtree
    venv = root / '.venv'
    py = venv / 'bin' / 'python3'

    def usable():
        return py.is_file() and os.access(py, os.X_OK) and pip_ok(py)

    if venv.exists() and not usable():
        rmtree(venv)
    if py.exists():
        return True, ''
    try:
        result = run([python, '-m', 'venv', str(venv)], capture_output=True, text=True)
        failed = result.returncode != 0 or not usable()
    except (OSError, subprocess.SubprocessError):
        failed = True
    if not failed:
        return True, ''
    if venv.exists():
        rmtree(venv)
    if version is None:
        try:
            version = python_version_label(python)
        except (OSError, subprocess.SubprocessError, ValueError):
            version = '3'
    return False, _hint(version)


def main():
    ok, message = ensure_venv(Path(__file__).resolve().parent)
    if not ok:
        sys.stderr.write(message)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
