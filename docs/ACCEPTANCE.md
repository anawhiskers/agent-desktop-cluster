# Acceptance evidence

Offline fixtures are separated from real host checks. Keep the owner-run evidence
private if it contains host/account details. Record versions, request IDs,
receipt states and actual effects; a success message or tool listing is not proof.

## Offline

Run `python -m unittest discover -s tests -v` from the isolated environment.
Tests use temporary private state, public fake vault data and mocked X11/systemd
calls. They cover configured inventory, SSH quoting, replay refusal, reservations,
pause, history bounds, output budgets, origin gates and viewer authentication.
The optional MCP SDK test uses only local stdio and a disabled desktop fixture.

Run `python scripts/check_public_tree.py`. It checks symlinks, forbidden runtime
artifacts, credential patterns and private-looking addresses. Add task-specific
deny patterns during a publication review. Manually inspect the exact Git diff;
the scanner cannot classify every personal identifier or secret.

Run the Studio fixture and review Watch, host selection, additional watch tiles,
history, cursor labels, viewport fit, narrow screens and reduced motion. Demo
values are deliberately labeled; no fixture interaction sends real input.

## On each real host

1. Confirm the returned configured host ID, actual display size, focus, generation
   and owner pause. Record OS/architecture and installed package versions.
2. In an empty scratch app, privately grant input and test one move/click, ASCII,
   newline, Unicode, modifiers and a small drag. Check actual rendered text and
   pointer, then the metadata receipt. Reusing the ID must not dispatch again.
3. Pause before human input. Verify new agent actions are refused; interrupt an
   in-flight scratch action and inspect partial effects. General physical-human
   detection and Together must still report unavailable.
4. Run a harmless systemd user job, disconnect the client, reconnect and read the
   same receipt. Verify bounded output, exit status, process-group cleanup and
   actual target effect. Test timeout/cancel without important data involved.
5. On separate scratch hosts, dispatch at most four independent jobs/observations.
   Verify each result belongs to the right host and no uncertain mutation retries.
6. Claim a short exclusive hold, verify another agent is refused, renew/release
   correctly, then test expiration. Running jobs must not disappear because a
   coordination lease expires.
7. Start the private viewer, confirm authenticated VNC Watch is read-only, then
   Take control pauses agents. Reconnect/Watch must not clear pause or auto-grant
   input. Check mobile sizing without changing the native resolution.
8. If enabling vault support, owner-login/unlock privately with the official CLI.
   Use a synthetic harmless login item, test origin refusal and one-use staging.
   Inspect stdout/history/process argv for leaks. Run the actual trusted origin
   adapter before enabling automated typing. Counted passkey metadata is not an
   assertion test.
9. Close the owned viewer/tunnels, lock the vault, retire/cancel exact owned jobs,
   and verify remaining processes and saved artifacts. Do not stop unrelated work.

No remote install, actual Linux desktop, VNC, physical hardware, real vault
account, origin adapter, native audio, actual model video path or all-device
support is established by this repository's offline tests alone.
