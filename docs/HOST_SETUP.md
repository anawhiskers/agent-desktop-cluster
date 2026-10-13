# Host prerequisites

Begin with one Linux host. Debian-family x86-64 and Raspberry Pi OS ARM64 are
intended deployment targets; install-time and native acceptance remain owner-run.
Use existing hardware and free local tools where practical. This project calls no
paid model/provider and includes no paid-service enrollment or installer.

## Base packages

The owner approves installation. A typical Debian-family base package set is:

```sh
sudo apt-get update
sudo apt-get install python3 python3-venv python3-pip openssh-client
```

Add the distro's OpenSSH server only if the host will receive remote worker calls.
Inspect its existing configuration and owner firewall policy first. Keep account
names, addresses and private keys in your private host/SSH setup. Verify the actual
host fingerprint before adding it to a known-hosts file; don't accept an unknown
fingerprint just because an agent or page tells you to.

Verify Python is 3.11+ and `systemctl --user show` reaches that account's user
manager. Job persistence through logout requires the owner's chosen lingering
policy; installation does not enable it. Keep real worker configuration mode 600,
state directories mode 700 and the checkout/environment separate from other work.

## Optional dedicated X11 desktop

Choose an X11 desktop/VNC setup supported by your distribution. Typical package
names include `xfce4`, `tigervnc-standalone-server` and `xdotool`; names and startup
configuration vary by distro/version. Use the installed server's help and official
documentation rather than overwriting an existing desktop setup.

Bind VNC to loopback, require an owner-set password, and use the SSH tunnel in the
setup guide. Start it manually until you've tested startup, reconnect, focus,
Unicode, geometry and cleanup. Set the actual display/Xauthority path in the
private config. Xauthority must be a private owned regular file. Keep a fresh
configured session ID whenever the X server starts again.

The optional Pillow build must support X11/XCB capture on that host. ARM64 wheels,
native libraries and image capture must be verified on the actual OS. A Python
import or fixture image does not prove native capture works.

## Optional browser viewer and vault

Install noVNC and websockify separately through the distro or their official
upstream distributions. Point `--novnc` at the installed root containing
`core/rfb.js`. Keep upstream licenses intact. The project does not vendor their
source, copy a proprietary desktop client or expose a public VNC listener.

Install the official Bitwarden CLI only if vault staging is needed. Inspect its
version, account/server choice and local `XDG_RUNTIME_DIR` on that host. Owner
login and master-password/MFA entry remain private. Do not copy a vault export,
browser credential database or another device's profile into this repo.

For an AI provider, use the client's existing authorized model or a separately
configured lawful local model. The stack itself needs neither an API key nor an
AI-provider subscription. CAD, RE, document, speech and other host programs can
be installed separately under their own licenses; no integration is proved until
its actual task is tested.
