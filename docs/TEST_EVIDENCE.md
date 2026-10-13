# Public code verification

Recorded on 2026-10-13. These checks ran offline using existing interpreters and
libraries. No dependencies were installed, host service was changed, live desktop
was controlled or real vault account was used.

| Check | Result |
| --- | --- |
| Python fixture suite | 45 tests passed |
| Actual MCP stdio protocol | Initialize, list 22 tools, startup brief and disabled local worker status passed |
| Native/systemd behavior | Mocked fixtures; real host acceptance still required |
| Public shell child | Public synthetic output drained, capped and cleaned in a temporary directory |
| Input cancellation | Synthetic detached process groups stopped before their delayed effects; one/four calls, repeated cancellation, TERM-resistant children and worker SIGTERM passed |
| Protected-input role changes | Final and later-segment role changes blocked further dispatch, consumed references and preserved uncertain receipts; valid username/password controls passed |
| Service-manager failures | Unavailable manager, failed cleanup queries and a timed-out cancel persisted uncertainty, withheld cleanup acknowledgement and fenced later jobs |
| Interrupted job mutations | Launch/cancel/receipt cleanup persisted uncertainty and re-raised interrupts; actual worker SIGTERM and cancel SIGKILL fixtures preserved the next-job fence |
| Presentation helpers | 11 Node assertions passed |
| Browser JavaScript | Studio and motion module syntax passed |
| Public-tree scanner | Passed common-artifact and task-specific privacy patterns |

The test environment used Python 3.12.14, MCP SDK 1.23.3, Pydantic 2.13.4,
Pydantic-settings 2.15.0, HTTPX 0.28.1, Pillow 12.3.0 and Node.js 24.21.0.
MCP/Pydantic-settings emitted an upstream incomplete `lifespan` forward-reference
warning. The tested protocol calls still passed. This is recorded as a compatibility
limitation, not suppressed or interpreted as failure of an untested client.

Tests use fake host IDs, reserved example domains and fictional vault fields.
No native browser profile, account, session/history export or private deployment
config is included. Metadata history tests confirm command and typed text are not
logged. Vault fixtures confirm secrets/session material are absent from returned
metadata, staging is bound to an exact origin, and references are one-use/expiring.
The process tests execute harmless fixture children rather than xdotool. They
confirm local cancellation cleanup without proving SSH disconnection or Linux
desktop behavior. Cancellation control RPCs have four separate bounded slots
in addition to the four normal request slots.

The public viewer's real browser layout, noVNC authentication, host installation,
Linux/ARM64 systemd/X11 behavior, physical input, real origin adapter, official
vault login, passkey assertion and actual AI image/video understanding are not
proved by these offline checks. Run [the acceptance checks](ACCEPTANCE.md) before
claiming those capabilities for a deployment.

Reproduce from the checkout:

```sh
python -m unittest discover -s tests -v
node tests/test_motion.mjs
node --input-type=module --check < src/diy_agent_stack/web/studio.js
node --input-type=module --check < src/diy_agent_stack/web/motion_v68.js
python scripts/check_public_tree.py
```

MCP/Pillow and Node tests require the optional dependencies described in the README.
The source manifest records the staged file hashes. A publication reviewer must
recheck it after any edits and manually inspect privacy, licensing and feature
claims before publishing.
