# Local AI Observatory

Version 0.1.0-preview — standalone community evaluation package.

A local-first dashboard for people experimenting with local LLMs. No Hermes installation, SSH server, Tailscale account, or cloud subscription is required.

## Features

- Compact CPU, memory and storage donut charts.
- Apple Silicon unified-memory labeling, macOS memory pressure, swap and compressed memory where available.
- Local Ollama installed and loaded model inventory.
- Explicit, user-triggered inference tests: generation tokens/second, first output, elapsed time, runtime load time and output tokens.
- Input/output token totals for the last 20 dashboard tests only.
- Light and dark themes; no external fonts, analytics or images.

## Quick start — macOS or Linux

Requires Python 3.10+ and a browser. No administrator privileges are needed for the app. First setup downloads psutil from PyPI into an isolated `.venv` in this folder.

1. Extract the ZIP into a writable folder.
2. Open Terminal on the computer you want to monitor.
3. Type `bash `, drag the included `Start.command` into Terminal, then press Return. Linux users can run `bash Start.command` from the extracted folder.
4. Open http://127.0.0.1:8767 in a browser.
5. Keep Terminal open. Press Control-C to stop.

If port 8767 is occupied, stop your older dashboard first. Alternatively, after setup run `.venv/bin/python3 server.py --port 8768` and open the matching address.

Some Linux installations require their distribution's Python venv package before creating a virtual environment. This launcher does not install system packages. Follow your distribution's instructions if Python reports a missing venv component.

## Optional Ollama

Install Ollama separately from https://ollama.com and start it locally. Without Ollama, hardware monitoring still works. Models are not downloaded by this app. Open a chat with a model in Ollama to load it, refresh the dashboard, then choose it for an inference test.

Tests use a fixed prompt, up to 128 output tokens, a 4K context request and five-minute keep-alive. A test consumes local compute and may change runtime allocation. Tests never run automatically. Results remain in process memory, not on disk; restarting clears them. These are small diagnostic probes, not standardized benchmarks or quality assessments.

## Platform scope

Apple Silicon/macOS: hardware was observed working on the owner's M2 MacBook Air in the earlier preview; this cleaned release still needs a fresh Mac launch test. macOS metrics may be unavailable on some OS versions.

Linux: basic CPU, RAM, storage and loopback Ollama paths have been exercised. macOS memory-pressure/compression readings remain unavailable. This preview does not collect NVIDIA/AMD GPU utilization.

Not included: Windows validation, MLX, model-fit estimation, remote-host management, Hermes logs, subscription/OAuth quotas, process ownership collector or all-runtime traffic/token monitoring. These can become optional integrations, but no personal account or server configuration ships here.

## Privacy and security

See PRIVACY.md. The server binds to loopback only. Do not change that binding or place the app behind a public proxy without a separate authentication/security design. Basic operation needs no root, OAuth or API keys.

## Tests

With the launcher environment initialized:

```
.venv/bin/python3 -m unittest -v
```

Optional JavaScript syntax check with Node.js: `node --check app.js`.

## Distribution status

This is an unsigned source preview, not a notarized macOS application or DMG. Review and test before wider distribution. Do not disable Gatekeeper; launch using Terminal as above. No updater, startup service or privileged collector is installed.

A project license must be selected by the owner before public publication. This package does not grant a new license for original project code. Third-party components retain their own licenses; see THIRD_PARTY_NOTICES.md.
