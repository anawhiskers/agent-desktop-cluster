#!/usr/bin/env python3
"""Catch common accidental publication artifacts; manual review remains required."""
import argparse
import ipaddress
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
PUBLIC_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"tskey-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{12,})", re.I)
SECRET_LITERAL = re.compile(r"(?:password|passwd|api[_ -]?key|access[_ -]?token|refresh[_ -]?token)\s*[=:]\s*[\"']([^\"'\r\n]{3,})[\"']", re.I)


def scan(root, extra=()):
    findings = []
    forbidden = {"private", "runtime", "node_modules", ".venv", "sessions", "profiles", "cookies"}
    forbidden_suffixes = {".sqlite3", ".db", ".log", ".key", ".pem", ".p12", ".zip", ".tar", ".gz"}
    allowed = {".py", ".js", ".mjs", ".css", ".html", ".svg", ".json", ".toml", ".md", ".txt"}
    patterns = [re.compile(pattern, re.I) for pattern in extra]
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts or "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            findings.append((str(relative), "symlink")); continue
        if any(part in forbidden for part in relative.parts) or path.suffix in forbidden_suffixes:
            findings.append((str(relative), "runtime/private artifact")); continue
        if not path.is_file():
            continue
        if path.suffix not in allowed and path.name not in {"LICENSE", ".gitignore", "ssh.example"}:
            findings.append((str(relative), "unexpected file type")); continue
        try:
            source = path.read_text()
        except UnicodeError:
            findings.append((str(relative), "binary artifact")); continue
        literal_matches = SECRET_LITERAL.findall(source)
        fixture_literals = relative.parts[0] == "tests" and all(value.startswith("fixture-") for value in literal_matches)
        if PUBLIC_SECRET.search(source) or literal_matches and not fixture_literals:
            findings.append((str(relative), "credential-like text"))
        if any(pattern.search(source) for pattern in patterns):
            findings.append((str(relative), "task-specific deny pattern"))
        for match in re.findall(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", source):
            try:
                address = ipaddress.ip_address(match)
                carrier_range = ipaddress.ip_network(((100 << 24) + (64 << 16), 10))
                if not address.is_loopback and (address.is_private or address in carrier_range):
                    findings.append((str(relative), "private-looking IP address"))
            except ValueError:
                pass
    return sorted(set(findings))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--deny-pattern", action="append", default=[])
    args = parser.parse_args()
    findings = scan(args.root, args.deny_pattern)
    for path, reason in findings:
        print(path + ": " + reason)
    print("FAIL" if findings else "PASS: no common publication artifacts found; manual privacy review still required")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
