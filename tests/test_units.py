"""The systemd units' sandbox (2026-11-17's security pass): every directive is still there, in each
of the three units that run on the server."""
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WANT = {
    "NoNewPrivileges": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes", "ProtectSystem": "strict",
    "ProtectHome": "yes", "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes",
    "ProtectKernelLogs": "yes", "ProtectControlGroups": "yes", "ProtectClock": "yes",
    "ProtectHostname": "yes", "RestrictNamespaces": "yes", "RestrictRealtime": "yes",
    "RestrictSUIDSGID": "yes", "LockPersonality": "yes", "SystemCallArchitectures": "native",
    "SystemCallFilter": "@system-service", "CapabilityBoundingSet": "",
    "RestrictAddressFamilies": "AF_INET AF_INET6 AF_UNIX",
}


def directives(path):
    out = {}
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith(("#", "[")) and "=" in line:
                key, _, value = line.partition("=")
                out.setdefault(key, value)
    return out


@pytest.mark.parametrize("path", ["arcade-tracker.service", "deploy/arcade-tracker-alerts.service",
                                  "deploy/arcade-tracker-library.service"])
def test_each_unit_keeps_its_sandbox(path):
    got = directives(path)
    missing = {k: v for k, v in WANT.items() if got.get(k) != v}
    assert not missing, f"{path} lost {missing}"
