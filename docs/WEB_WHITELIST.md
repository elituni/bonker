# Website whitelist and agents

In whitelist mode, allowed hostnames (and optional subdomains) can be opened in
Firefox; unlisted HTTP(S) pages and frames redirect to Bonker. More-specific rules
win. Local file PDFs are outside the HTTP(S) listener and need no hostname entry.
Keep Firefox itself selected among allowed applications.

To use ChatGPT or Gemini in Firefox, allow chatgpt.com or gemini.google.com as
appropriate. Bonker does not intercept API/fetch/XHR/WebSocket requests: the
extension registers only main_frame and sub_frame navigation. Server-side agent
searches therefore do not require allowing every result hostname. Following a
result link or an agent navigating this Firefox to an unlisted site remains
blocked. Login frames and other third-party pages may require explicit additional
hostname rules; full ChatGPT/Gemini site workflows have not been certified here.

Desktop agents such as Codex must be in the allowed app selection. CLI agents
launched through an allowed terminal can use their ordinary network connections;
Bonker does not install a system-wide network firewall. An unsupported browser is
still subject to the existing website-session browser guard. No broad AI, search
engine or User-Agent exemption is introduced.

Node regression tests simulate whitelist navigation, existing/new tabs, local
files, request filters and cached expiry. These do not certify live whitelist
enforcement. Use the disposable account/VM and saved-work/recovery safeguards in
the [Fedora acceptance checklist](FEDORA_TESTING.md) for live checks.
