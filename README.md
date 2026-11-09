# Local AI Observatory

Version 0.1.0-preview — standalone community evaluation package.

A local-first dashboard for people experimenting with local LLMs. No Hermes installation, SSH server, Tailscale account, or cloud subscription is required.

## Features

- Compact CPU, memory and storage donut charts.
- Apple Silicon unified-memory labeling, macOS memory pressure, swap and compressed memory where available.
- Local Ollama installed and loaded model inventory, including resident size, context length, and keep-alive expiry for loaded models. GPU memory is shown only when a GPU is present. A CPU-only `size_vram` value is not displayed.
- Explicit, user-triggered inference tests: generation tokens/second, first output, elapsed time, runtime load time and output tokens.
- A visible comparison of the latest 20 dashboard tests, with a Copy results button. Input/output token totals cover those tests only.
- Light and dark themes; no external fonts, analytics or images.

## Quick start — macOS or Linux

Requires Python 3.10+ and a browser. The dashboard does not need administrator privileges, an account, or an API key.

Debian and Ubuntu need the distro virtualenv package once before the first launch. The launcher does not install system packages. If `python3 -m venv` cannot create `.venv`, it deletes any half-built folder and prints the package that matches your Python, for example:

```bash
sudo apt install python3.12-venv
```

Install that package, then run the launcher again. Later launches stay in this folder and do not need root. First setup downloads psutil from PyPI into the `.venv`. That download needs a network.

### From git

```bash
git clone https://github.com/edragon12/local-ai-observatory.git
cd local-ai-observatory
bash Start.command
```

### From a ZIP

1. Extract the ZIP into a writable folder.
2. Open Terminal on the computer you want to monitor.
3. Type `bash `, drag `Start.command` into Terminal, then press Return. From that folder, `bash Start.command` does the same thing.
4. Open http://127.0.0.1:8767 in a browser.
5. Keep Terminal open. Press Control-C to stop.

If port 8767 is occupied, stop your older dashboard first. Alternatively, after setup run `.venv/bin/python3 server.py --port 8768` and open the matching address.

### Ollama, then Run local test

Hardware charts work with Ollama absent. To run a probe, install Ollama from https://ollama.com, start it, and load a small model:

```bash
ollama pull qwen2.5:0.5b
ollama run qwen2.5:0.5b
```

`ollama run` is the local chat that loads the model. The Ollama desktop app's chat does the same thing. Leave the model loaded, refresh the dashboard, choose it, and press Run local test. This app does not download or load models.

Tests use a fixed prompt, up to 128 output tokens, a 4K context request and five-minute keep-alive. A test consumes local compute and may change runtime allocation. Tests never run automatically. Results remain in process memory, not on disk; restarting clears them. These are small diagnostic probes, not standardized benchmarks or quality assessments.

## Platform scope

Apple Silicon/macOS: tested by the owner on an Apple Silicon M2 MacBook Air. macOS metrics may be unavailable on some OS versions.

Linux: basic CPU, RAM, storage and loopback Ollama paths have been exercised. macOS memory-pressure/compression readings remain unavailable. This preview does not collect NVIDIA/AMD GPU utilization.

Not included: Windows validation, MLX, model-fit estimation, remote-host management, Hermes logs, subscription/OAuth quotas, process ownership collector or all-runtime traffic/token monitoring. These can become optional integrations, but no personal account or server configuration ships here.

## Privacy and security

See PRIVACY.md. The server binds to loopback only. Do not change that binding or place the app behind a public proxy without a separate authentication/security design. Basic operation needs no root once Python's venv support is available, and no OAuth or API keys. On Debian/Ubuntu the venv package is a one-time distro install; see Quick start.

## Screenshots

Linux, after a local diagnostic probe:

![Linux dashboard in the dark theme, with a completed local test](screenshots/linux-results-dark.png)

![Linux dashboard in the light theme, with a completed local test](screenshots/linux-results-light.png)

macOS screenshot: add `screenshots/macos-dashboard.png` here. The owner will add that Apple Silicon M2 MacBook Air capture. It is not in the repository yet.

## Tests

With the launcher environment initialized:

```
.venv/bin/python3 -m unittest -v
```

Optional JavaScript syntax check with Node.js: `node --check app.js`.

## Distribution status

This is an unsigned source preview, not a notarized macOS application or DMG. Review and test before wider distribution. Do not disable Gatekeeper; launch using Terminal as above. No updater, startup service or privileged collector is installed.

The original project code is MIT licensed (see LICENSE). Third-party components keep their own licenses (see THIRD_PARTY_NOTICES.md).
