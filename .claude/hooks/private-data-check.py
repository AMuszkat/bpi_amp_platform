#!/usr/bin/env python3
"""Refuses text that looks like private data: LAN addresses, real host names, user@host
logins, home paths. The repository is public (see CLAUDE.md).

    private-data-check.py [--label <what>] < text

Exit 0 when the text is clean, 2 with the findings on stderr otherwise. A line that
contains `private-ok` is skipped, for the rare legitimate case.
"""

import ipaddress
import re
import sys

PLACEHOLDER_HOSTS = {"bpi-amp.local", "my-board.local", "example.local", "localhost"}
PLACEHOLDER_USERS = {"user", "root", "you", "me", "noreply"}
PLACEHOLDER_HOMES = {"user", "<user>", "USER", "you", "pi"}

IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")
# A file name such as settings.local.json is not a host: no further extension may follow.
HOST = re.compile(r"\b([a-z0-9][a-z0-9-]*\.(?:local|lan|home|internal|fritz\.box))\b(?!\.[a-z])", re.I)
LOGIN = re.compile(r"\b([a-z0-9_.-]+)@([a-z0-9][a-z0-9.-]*\.[a-z]{2,})\b", re.I)
HOME = re.compile(r"(?:/home|/Users)/([^/\s'\"`]+)/")


def is_public_or_doc_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return True          # not an address at all (e.g. a version number with 4 parts)
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local:
        return True
    for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "255.255.255.255/32"):
        if ip in ipaddress.ip_network(net):
            return True
    return False


def findings(text):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if "private-ok" in line:
            continue
        for m in IPV4.finditer(line):
            if not is_public_or_doc_ip(m.group(1)):
                out.append((n, "IP address", m.group(1)))
        for m in HOST.finditer(line):
            if m.group(1).lower() not in PLACEHOLDER_HOSTS:
                out.append((n, "host name", m.group(1)))
        for m in LOGIN.finditer(line):
            user, host = m.group(1), m.group(2)
            if user.lower() in PLACEHOLDER_USERS or host.lower() in PLACEHOLDER_HOSTS \
                    or host.lower().endswith(("anthropic.com", "github.com", "gmail.com", "example.com")):
                continue
            out.append((n, "login", m.group(0)))
        for m in HOME.finditer(line):
            if m.group(1) not in PLACEHOLDER_HOMES:
                out.append((n, "home path", m.group(0)))
    return out


def main():
    label = "the text"
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--label":
        label = args[1]
    found = findings(sys.stdin.read())
    if not found:
        return 0
    print(f"private data in {label}; the repository is public (CLAUDE.md). Put it in the "
          "gitignored dsp/config/local.env or firmware/local.mk, or use a placeholder "
          "(bpi-amp.local, user@bpi-amp.local, 192.0.2.10):", file=sys.stderr)
    for n, kind, what in found[:10]:
        print(f"  line {n}: {kind} {what}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
