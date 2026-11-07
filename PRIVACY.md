# Privacy

This standalone preview reads the machine's CPU, memory and root filesystem capacity, and queries only local Ollama at 127.0.0.1:11434. It does not read Hermes records, chats, API credentials, OAuth credentials, SSH configuration or external socket ownership. It has no remote proxy endpoints.

The user-triggered inference test sends a fixed diagnostic prompt to local Ollama. Generated text is discarded. Numeric test results and model names are kept in memory (up to 20), cleared when the process ends. It does not collect other applications' token usage. Browser local storage remembers only the light/dark theme.

The app makes no cloud inference calls, has no analytics, and serves its own assets. Initial dependency installation contacts Python package servers, disclosing the ordinary connection metadata involved in installing psutil. Installing Ollama or models is separate, user-controlled activity.

Hardware/model metadata may be sensitive. The server is loopback-only and checks Host headers. Inference requests require same-origin Origin and empty body. There is no multi-user authentication. Loopback binding is not a security boundary against other software or users on the same computer.

Do not forward its port to untrusted networks. No root or elevated collector is required. Python and dependencies require normal maintenance. Security checks here are targeted tests, not an independent security audit.
