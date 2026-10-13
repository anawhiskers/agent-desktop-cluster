# Public feature status

This checklist tracks the portable parts of the source stack. "Implemented" means
code exists in this repository; it is not a claim that the owner's Linux host,
physical hardware, browser, vault or AI client has passed acceptance. The private
deployment has additional integrations that are deliberately not copied here.

| Capability | Public baseline | Verification still needed |
| --- | --- | --- |
| 1. Configurable fleet of one to seven hosts | Generic inventory; strict known SSH aliases | Each owner's real SSH identity and host fingerprint |
| 2. Persistent Linux desktops | Dedicated owner-started X11 session; configurable display/auth paths | OS/board and desktop-session startup/recovery |
| 3. Owner-authenticated MCP | Local stdio plus SSH authentication; no public listener | Agent-specific stdio setup; any remote OAuth layer is unported |
| 4. Native cursor, text and keyboard actions | Bounded X11 input; public identity and six cursor presets | Real pointer/text effect, Unicode, layout and modifiers |
| 5. Human priority | Explicit owner pause and expiring input grant; Take control pauses agents | Physical-human detection is absent; no automatic Together mode |
| 6. Finite presentation motion | V68 presentation helpers; reduced-motion and slow-paint fallback | Browser performance and visual QA on actual devices |
| 7. Responsive watch view | Fit/native display scaling; does not resize the host | Mobile/zoom/browser layouts and noVNC session behavior |
| 8. Clipboard bridge | Custom copy/paste bridge is unported | Use owner-controlled clients; don't infer it from typing support |
| 9. File and terminal bridges | Custom UI bridges are unported; approved shell/SSH work remains available | Real file-transfer and terminal workflows |
| 10. Protected vault find/stage | Official CLI metadata search and host-local single-use staging | Official CLI version, private owner login and account behavior |
| 11. Vault save/update/sync | No AI tools for these operations | Owner uses official CLI/UI; no save/update/sync acceptance claimed |
| 12. Origin-bound password fill | Default blocked; adapter contract requires exact origin and focused role | Build/review a real browser origin/focus adapter, then verify a harmless field |
| 13. Passkeys | Bounded passkey inventory metadata when the CLI supplies it | Assertions are manual through official clients; no hardware-key export |
| 14. Persistent browser profiles | Owner can use a browser's normal profile on its own host | Host-local browser persistence; no profiles/cookies/accounts included |
| 15. Multi-host observation | One to seven explicit hosts; at most four normal calls in flight, with up to four separate exact cancellation calls | Real SSH network/device behavior and actual client output |
| 16. Background dispatch | Up to four independent user-systemd job launches in parallel | Actual systemd, logout policy, receipt and cgroup cleanup |
| 17. Shared work and short GUI turns | Background jobs shared by default; native input serialized per host | Application/resource conflicts remain the caller's responsibility |
| 18. Explicit critical TTL hold | Idle-host exclusive claim, renew, release and anonymous status | TTL bounds and token routing are offline-tested; real work still needs acceptance |
| 19. Durable no-replay receipts | Unique request IDs and persisted results; uncertain mutations never retried automatically | Inspect real effects after interruption and service-manager failure |
| 20. Still/video/audio discovery | JPEG capture and sampled frame clips; optional noVNC viewer | Native model video/audio and microphone capture are absent |
| 21. CAD/reverse-engineering hooks | Ordinary approved background scripts can call separately installed tools | No CAD/RE binaries, models, licenses or integrations bundled or validated |
| 22. Bounded metadata history | Worker/MCP action IDs, tool kinds, public agent IDs, status, time and request hashes | Human VNC input is not logged; private output is opt-in and filters are heuristic |
| 23. Bootstrap, catalog and recovery | Setup guide, capability catalog, fixtures, pause/reconnect flow, manual reconcile and cleanup guidance | Actual native cursor/control/reconnect proof and owned-process cleanup on each host |

The fixture Studio is always labeled as a demo and refuses all host mutations.
Additional Studio views are sampled screens, not additional real-time VNC streams.
The repository makes no speed, TLS, all-device compatibility, authentication
certification, or complete original-deployment parity claim.
