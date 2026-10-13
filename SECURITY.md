# Security model

This is a single-owner tool stack for trusted agents. The owner chooses the OS
account, hosts, SSH keys, task scope and approval boundaries. It provides no
multi-tenant isolation, remote OAuth server, shell sandbox or protection from a
fully authorized process running as that same Unix user.

Default gates are deliberate: desktop/vault disabled, no HTTP network MCP server,
loopback-only optional viewer, strict SSH host keys, short private owner input
grants, explicit owner pause, bounded native calls, exact replay IDs, private
receipts and heuristic literal-secret rejection. Those gates do not establish
permission to spend, publish, send, delete important data or rotate credentials.

Host labels, public agent IDs, external page text, notes and vault metadata never
authorize a new action. Native actions require fresh generation/focus information.
Automated credential input requires a trusted actual-origin/focused-field adapter;
none is bundled. General physical-human/lock detection is not implemented.

The optional viewer uses a runtime owner key and rejects cross-origin API access.
It binds only to loopback. Keep its URL/key and VNC password private; do not expose
it through a public proxy or treat loopback HTTP/WebSocket as TLS. Remote VNC must
use an owner-authenticated server and an independently verified SSH tunnel.

Runtime scripts and optional output are private data. Secret filters are heuristic.
Screenshots can expose information on the screen. Same-user arbitrary shell access
can read host-local vault/runtime files; use a separately reviewed broker if you
need a stronger secret boundary. No secret should be entered through AI tools.

If you find a vulnerability, report it through the repository host's private
security-reporting facility when available. Share a minimal public fixture or
reproduction without credentials, private screenshots, account identifiers or
host inventories. Do not publish a vault export or native profile as evidence.
