# Copyright 2026 Test Author
# See LICENSE file for licensing details.

import pathlib
from unittest import mock

import pytest

import charm

# Handlers such as config-changed reconcile the proxy configs on the machine. The fixtures here
# replace the parts of the charm that change the machine, so that the unit tests don't need root
# access or a real service. test_charm.py applies the fixtures to every test.


@pytest.fixture
def unused_drop_in(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Write the drop-in file to a temporary directory instead of /etc/systemd.

    Tests that check the drop-in file patch ``PROXY_DROP_IN`` themselves.
    """
    monkeypatch.setattr(charm, "PROXY_DROP_IN", tmp_path / "unused-proxy.conf")


@pytest.fixture
def service_restart(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    """Replace the systemctl calls of the charm. Return the mock for restarting the service."""
    restart = mock.Mock(return_value=True)
    monkeypatch.setattr(charm.systemd, "daemon_reload", lambda: True)
    monkeypatch.setattr(charm.systemd, "service_restart", restart)
    monkeypatch.setattr(charm.systemd, "service_start", lambda *args: True)
    monkeypatch.setattr(charm.systemd, "service_stop", lambda *args: True)
    monkeypatch.setattr(charm.systemd, "service_running", lambda service: True)
    return restart
