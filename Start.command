#!/bin/bash
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  printf 'Python 3.10 or newer is required. Install it from python.org, then retry.\n'
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else "Python 3.10 or newer required")'
if [ ! -x .venv/bin/python3 ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python3 -c 'import psutil; assert psutil.__version__ == "7.2.2"' >/dev/null 2>&1; then
  printf 'Installing the pinned dependency in this folder only; internet required.\n'
  .venv/bin/python3 -m pip install -r requirements.txt
fi
printf 'Local AI Observatory 0.1.0-preview\nOpen http://127.0.0.1:8767. Keep this window open; Control-C stops it.\n'
.venv/bin/python3 server.py --port 8767
