# Media, vault and passkey gates

## Still images and video

`desktop_capture` returns a fresh JPEG plus the native size, focus, generation,
image digest and image-to-native scale. Public cursor overlays are presentation
over that capture. They do not create another input stream or replay the cursor.
Screens can contain private information; the owner decides what may be shared.

`desktop_frame_clip` returns up to four sampled images. It does not encode a video
file or establish that an AI client understands video. Optional noVNC delivers a
browser framebuffer, which is a separate viewer path. Check the actual intended
AI client and user-visible result before claiming multimodal support.

Native system audio, microphone capture, realtime speech, phone calling and model
video are absent in this baseline. Capability discovery reports that directly.
Do not install a listener or begin recording based on a tool catalog alone.

## Protected login fields

The official Bitwarden CLI owns account login and unlock. The owner enters secrets
privately on the host or in the official UI. The wrapper captures `bw unlock --raw`
internally and stores a mode-600, expiring session in that owner's runtime
directory. It never returns raw session material. Staged fields are mode 600,
become unusable after 90 seconds and are consumed once. Expired staging files are
removed on the next vault access; owner lock or logout clears the runtime. `vault-lock` removes this wrapper's
session and staging files and asks the official CLI to lock.

The same Unix user can read its own runtime files, especially through authorized
arbitrary shell tools. These references are a mechanism for keeping values out of
AI prompts and action logs, not a sandbox against an agent running as that owner.
Use a separate broker/account if that threat matters.

`vault_prepare` accepts an exact HTTPS origin that must occur in the selected
item's URI metadata. This checks staging policy; it does not prove the desktop is
currently on that site. Automated credential input stays blocked until a trusted
origin/focus adapter is configured and tested.

## Origin adapter contract

The owner may configure an absolute executable and fixed arguments as
`desktop.origin_adapter`. It receives only this JSON on stdin:

```json
{"window":"CURRENT_X11_WINDOW_ID","generation":"CURRENT_GENERATION"}
```

It must inspect the actual owned browser and focused field, then return bounded
JSON with the exact current window ID, HTTPS origin and focused role:

```json
{"window":"CURRENT_X11_WINDOW_ID","origin":"https://example.invalid","role":"password"}
```

Use `textbox` for a username field. The origin must come from the actual browser,
not a caller string, title match, DOM page text or saved note. Match port/scheme
exactly. The worker checks this adapter before consuming a reference and again
before each typing segment. No credential enters argv or history. An uncertain
dispatch consumes the reference and requires a fresh reviewed staging request.

No real origin adapter is bundled. Test a harmless nonsecret field in a throwaway
browser profile before enabling it. The synthetic test adapter is only a fixture;
do not use it as an attestation source for production.

## Passkeys

`vault_find` may report a count of `fido2Credentials` metadata if the installed CLI
supplies it. That count is inventory, not proof of a working passkey assertion.
The baseline performs no passkey assertion or authenticator emulation.

Use the official browser extension, OS or hardware authenticator for assertions
and any owner/MFA prompt. No Apple device key, hardware-backed key, native
credential database, browser cookie bundle or private profile is copied or
exported by this repo. CLI/server compatibility and passkey behavior need an
owner-run test with the actual versions in use.

Vault save/update/sync tools are not exposed to AI in this release. Use the
official CLI or UI privately and verify the saved item yourself.
