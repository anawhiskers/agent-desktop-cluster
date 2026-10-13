# Third-party software

The repository's MIT license covers its original code, documentation and fixture
art. It does not relicense installed dependencies or software you run alongside it.

No noVNC, websockify, Bitwarden CLI, browser, OS image, icon pack, model, private
profile or third-party application binary is bundled. The viewer serves noVNC only
from a directory the owner supplies after installing it separately.

| Component | Role | License in the locally inspected version |
| --- | --- | --- |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) 1.23.3 | Optional stdio MCP server | MIT |
| [Pillow](https://github.com/python-pillow/Pillow) 12.3.0 | Optional X11 image capture and encoding | MIT-CMU |
| [Pydantic](https://github.com/pydantic/pydantic) 2.13.4 | MCP transitive dependency | MIT |
| [HTTPX](https://github.com/encode/httpx) 0.28.1 | MCP transitive dependency | BSD-3-Clause |
| [noVNC](https://github.com/novnc/noVNC) | Optional owner-installed browser VNC client | See its MPL-2.0 license and bundled notices |
| [websockify](https://github.com/novnc/websockify) | Optional owner-installed loopback VNC bridge | See its LGPL-3.0 license and bundled notices |
| [Bitwarden clients](https://github.com/bitwarden/clients) | Optional owner-installed vault CLI | See the specific distribution's LICENSE and licensing exceptions |

License metadata for the first four rows was read from installed package metadata.
Exact license texts for those versions are in `licenses/`. Other rows point to
upstream projects; inspect the version you actually install. Transitive packages
keep their own licenses. A Python environment or OS image is not part of this repo.

The desktop stylesheet is adapted from the project's prior owner-created Studio
layout. The cursor motion and identity helpers are owner-created presentation
code. Neither contains third-party images, icons, fonts or executable libraries.
