# Privacy

This standalone preview reads the machine's CPU, memory and root filesystem capacity, and queries only local Ollama at 127.0.0.1:11434. It does not read an agent framework's records, chats, API credentials, OAuth credentials, SSH configuration or external socket ownership. It has no remote proxy endpoints.

The user-triggered inference test sends a fixed diagnostic prompt to local Ollama. Generated text is discarded. Numeric test results and model names are kept in memory (up to 20), cleared when the process ends. It does not collect other applications' token usage. Browser local storage remembers only the light/dark theme.

The app makes no cloud inference calls, has no analytics, and serves its own assets. Initial dependency installation contacts Python package servers, disclosing the ordinary connection metadata involved in installing psutil. Installing Ollama or models is separate, user-controlled activity.

Hardware/model metadata may be sensitive. The server is loopback-only and checks Host headers. Inference requests require same-origin Origin and empty body. There is no multi-user authentication. Loopback binding is not a security boundary against other software or users on the same computer.

Do not forward its port to untrusted networks. No root or elevated collector is required. Python and dependencies require normal maintenance. Security checks here are targeted tests, not an independent security audit.

## Optional features (off until configured)

Prompt tokenization sends the text you type to this loopback server, which reads tokenizer metadata from a local GGUF file. The text is not logged. "Remember text in this browser" is off until you check it, and then the text stays in this browser's local storage only.

The live receiver starts only when its token file exists. It keeps the newest few turns in memory for about 10 minutes and writes nothing. Ask for text only while Show text is on and Post mode is off. Otherwise the page receives counts and token lengths, not token ids. The Open WebUI filter sends events only after you set a token. It does not change the chat.

A remote host, if enabled, must be a loopback URL. The browser never talks to it directly. Remote probes and tokenization run only after a click. Glance counts come from a log path you set, or from that remote dashboard's own glance response. No account token or plan quota is collected.
