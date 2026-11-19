# Copyright 2026 Test Author
# See LICENSE file for licensing details.

import pathlib

import pytest
from charmlibs import pathops

import boo
import charm

# Handlers such as config-changed reconcile the proxy configs on the machine. The fixtures here
# replace the parts of the charm that change the machine, so that the unit tests don't need root
# access or a real service. test_charm.py applies the fixtures to every test.


@pytest.fixture
def drop_in(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathops.LocalPath:
    """Write the drop-in file to a temporary directory instead of /etc/systemd."""
    path = pathops.LocalPath(tmp_path / "proxy.conf")
    monkeypatch.setattr(charm, "PROXY_DROP_IN", path)
    monkeypatch.setattr(boo, "SERVICE_FILE", pathops.LocalPath(tmp_path / "boo.service"))
    return path


@pytest.fixture
def restarted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the services that the charm restarts, instead of calling systemctl."""
    services: list[str] = []

    def service_restart(*args: str) -> bool:
        services.extend(args)
        return True

    monkeypatch.setattr(charm.systemd, "daemon_reload", lambda: True)
    monkeypatch.setattr(charm.systemd, "service_restart", service_restart)
    monkeypatch.setattr(charm.systemd, "service_start", lambda *args: True)
    monkeypatch.setattr(charm.systemd, "service_stop", lambda *args: True)
    monkeypatch.setattr(charm.systemd, "service_running", lambda service: True)
    return services
