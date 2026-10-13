# DIY Agent Stack

Run a small Linux fleet through an AI agent, then watch its desktop work in your
own browser. Start with one machine and add up to seven. SSH carries worker calls;
the MCP server runs locally over stdio. No paid AI provider, cloud account or public
HTTP endpoint is required by this project.

This is a portable baseline derived from an owner-operated stack. Its offline
fixtures are test evidence for the public code. They do not establish that your
hardware, desktop, vault account or AI client works. See [feature status](docs/FEATURE_STATUS.md)
and the [acceptance checks](docs/ACCEPTANCE.md).

## What you can use

* Configurable inventory, SSH worker calls and at most four normal host requests
  in parallel. Exact input cancellations have a separate channel capped at four.
* Shared user shell jobs with bounded private output, persistent receipts and no
  automatic replay. Jobs use the host's systemd user manager.
* Explicit exclusive work reservations for critical tasks. A reservation is
  coordination between trusted agents, not a new permission or a sandbox.
* Default-off dedicated X11 screenshots and native input with focus/generation
  checks, short owner input grants, public cursor identities and metadata history.
* Loopback Desktop Studio with a clearly labeled fixture demo, fresh screenshot
  views and optional authenticated noVNC Watch/Take control. Take control pauses
  agents and never resumes them automatically.
* Official Bitwarden CLI login-field staging as short-lived, single-use protected
  references. Unlock happens privately on the host. Automated credential typing
  requires a separately reviewed origin/focus adapter.

Together mode, native audio capture, model video input, automated passkey assertions
and phone/Windows/macOS workers are gated or absent in this baseline. The UI says
so. See [media and passkeys](docs/MEDIA_AND_PASSKEYS.md).

## Quick start

Use an owner-controlled Debian-family Linux host with Python 3.11+ and systemd.
Debian x86-64 and Raspberry Pi OS ARM64 are intended targets; this release has not
been installed on every OS/board combination. Keep this project separate from any
desktop or service you already depend on.

1. Clone this repository and create an isolated environment:

   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install -e '.[mcp,desktop]'
   ```

2. Run offline fixtures, then inspect the public Studio demo:

   ```sh
   .venv/bin/python -m unittest discover -s tests -v
   .venv/bin/diy-stack viewer --fixture
   ```

   Open the private loopback URL printed in your own terminal. The URL contains a
   temporary viewer key. Don't paste it into AI prompts, logs or public issues.
   The demo connects to no host and sends no input.

   If Node.js 18+ is already installed, `node tests/test_motion.mjs` also checks the
   portable cursor/presentation helpers. Node is not needed to run the Python stack.

3. Follow [AIAGENT_SETUP.md](AIAGENT_SETUP.md) to create private worker and
   coordinator configs, verify SSH, then register the stdio MCP server in your
   agent's supported MCP configuration.

## Before real work

These tools run with the configured Linux user's authority. `machine_start` is
arbitrary shell access, so only connect agents you trust. File mode checks and
receipt guards prevent common mistakes; they do not isolate an agent from other
files owned by the same user. Vault runtime files are protected from other Unix
users, not from an authorized shell under that same account. Use a separate
account or reviewed broker if you need a stronger boundary.

Enter secrets yourself on the host or in the official vault UI. Keep passwords,
session material, hardware keys, native browser profiles and account exports out
of the repo and out of AI prompts. Screenshot and opt-in command output can expose
private information; inspect the intended screen and command before sharing them.

The owner controls installation, SSH trust, desktop startup, vault login, input
grants and approval for consequential actions. The package creates no persistent
service on installation and changes no firewall, DNS, login, preference or secret.

See [host prerequisites](docs/HOST_SETUP.md), [operations and rollback](docs/OPERATIONS.md), [security](SECURITY.md),
[third-party notices](THIRD_PARTY_NOTICES.md) and [MIT license](LICENSE).
