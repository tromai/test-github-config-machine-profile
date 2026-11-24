# Copyright 2026 Test Author
# See LICENSE file for licensing details.
#
# To learn more about testing, see https://canonical.com/juju/docs/ops/latest/explanation/testing/

import pytest
from charmlibs import pathops
from ops import testing

import charm
from charm import API_TOKEN_LABEL, APP_SECRET_LABEL, BooMachineCharm

# Don't let any test change the machine. See conftest.py.
pytestmark = pytest.mark.usefixtures("drop_in", "restarted")


def mock_get_version():
    """Get a mock version string without executing the workload code."""
    return "1.0.0"


def test_start(monkeypatch: pytest.MonkeyPatch):
    """Test that the charm has the correct state after handling the start event."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    monkeypatch.setattr("charm.boo.get_version", mock_get_version)
    # Act:
    state_out = ctx.run(ctx.on.start(), testing.State())
    # Assert:
    assert state_out.workload_version is not None
    assert state_out.unit_status == testing.ActiveStatus()


def test_pause_action():
    """Test that the unit reports being paused after the pause action runs."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    # Act:
    state_out = ctx.run(ctx.on.action("pause"), testing.State())
    # Assert:
    assert isinstance(state_out.unit_status, testing.MaintenanceStatus)


def test_resume_action():
    """Test that the unit is active again after the resume action runs."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    state_in = testing.State(unit_status=testing.MaintenanceStatus("paused"))
    # Act:
    state_out = ctx.run(ctx.on.action("resume"), state_in)
    # Assert:
    assert state_out.unit_status == testing.ActiveStatus()


def test_start_creates_app_secret(monkeypatch: pytest.MonkeyPatch):
    """Test that the charm creates an app-managed workload secret on start (leader)."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    monkeypatch.setattr("charm.boo.get_version", mock_get_version)
    state_in = testing.State(leader=True)

    # Act:
    state_out = ctx.run(ctx.on.start(), state_in)

    # Assert — the app-owned secret should appear in the output state with a 'password' key:
    app_secrets = [s for s in state_out.secrets if s.label == APP_SECRET_LABEL]
    assert len(app_secrets) == 1
    assert app_secrets[0].latest_content is not None
    assert "password" in app_secrets[0].latest_content


def test_start_app_secret_already_exists(monkeypatch: pytest.MonkeyPatch):
    """Test that start is idempotent when the app secret already exists."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    monkeypatch.setattr("charm.boo.get_version", mock_get_version)
    existing_secret = testing.Secret(
        tracked_content={"password": "existing-password"},
        label=APP_SECRET_LABEL,
        owner="app",
    )
    state_in = testing.State(leader=True, secrets={existing_secret})

    # Act:
    state_out = ctx.run(ctx.on.start(), state_in)

    # Assert — the existing secret is still present; no extra secret was created:
    app_secrets = [s for s in state_out.secrets if s.label == APP_SECRET_LABEL]
    assert len(app_secrets) == 1


def test_config_changed_with_api_token():
    """Test that config-changed resolves a user-provided secret and goes active."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    secret = testing.Secret(
        tracked_content={"token": "my-secret-api-token"},
        owner=None,  # user-owned secret (not app- or unit-owned)
    )
    state_in = testing.State(
        secrets={secret},
        config={"api-token": secret.id},
    )

    # Act:
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Assert:
    assert state_out.unit_status == testing.ActiveStatus()


def test_config_changed_api_token_not_found():
    """Test that config-changed blocks when the secret URI is not accessible."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    state_in = testing.State(
        config={"api-token": "secret:doesnotexist00000000"},
        # No matching secret in state — simulates the secret not being granted.
    )

    # Act:
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Assert:
    assert isinstance(state_out.unit_status, testing.BlockedStatus)


def test_config_changed_api_token_missing_key():
    """Test that config-changed blocks when the secret has no 'token' key."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    secret = testing.Secret(
        tracked_content={"not-token": "my-secret-api-token"},
        owner=None,  # user-owned secret (not app- or unit-owned)
    )
    state_in = testing.State(
        secrets={secret},
        config={"api-token": secret.id},
    )

    # Act:
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Assert:
    assert isinstance(state_out.unit_status, testing.BlockedStatus)


def test_config_changed_no_token():
    """Test that config-changed without api-token set does nothing."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    state_in = testing.State()

    # Act:
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Assert — status unchanged (charm hasn't done anything yet):
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)


def test_secret_rotate():
    """Test that secret-rotate generates a new revision of the workload password."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    secret = testing.Secret(
        tracked_content={"password": "old-password"},
        label=APP_SECRET_LABEL,
        owner="app",
    )
    state_in = testing.State(secrets={secret}, leader=True)

    # Act:
    state_out = ctx.run(ctx.on.secret_rotate(secret), state_in)

    # Assert — a new revision with a different password should have been added:
    out_secret = state_out.get_secret(label=APP_SECRET_LABEL)
    assert out_secret.latest_content is not None
    assert "password" in out_secret.latest_content
    assert out_secret.latest_content["password"] != "old-password"


def test_secret_expired():
    """Test that secret-expired replaces the expired password with a new revision."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    secret = testing.Secret(
        tracked_content={"password": "expired-password"},
        label=APP_SECRET_LABEL,
        owner="app",
    )
    state_in = testing.State(secrets={secret}, leader=True)

    # Act — simulate the tracked revision expiring:
    state_out = ctx.run(ctx.on.secret_expired(secret, revision=1), state_in)

    # Assert — a new revision should have been added, not the old one removed:
    out_secret = state_out.get_secret(label=APP_SECRET_LABEL)
    assert out_secret.latest_content is not None
    assert out_secret.latest_content["password"] != "expired-password"


def test_secret_changed_new_api_token_revision():
    """Test that the charm picks up a new revision of the user-provided api-token."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    secret = testing.Secret(
        tracked_content={"token": "old-api-token"},
        latest_content={"token": "new-api-token"},
        label=API_TOKEN_LABEL,
        owner=None,  # user-owned secret (not app- or unit-owned)
    )
    state_in = testing.State(secrets={secret}, config={"api-token": secret.id})

    # Act:
    state_out = ctx.run(ctx.on.secret_changed(secret), state_in)

    # Assert — the charm refreshed, so it now tracks the latest revision:
    out_secret = state_out.get_secret(label=API_TOKEN_LABEL)
    assert out_secret.tracked_content == {"token": "new-api-token"}
    assert state_out.unit_status == testing.ActiveStatus()


def test_secret_remove():
    """Test that secret-remove cleans up a revision that no unit is using."""
    # Arrange — rotate twice, so that revision 2 is neither the oldest revision
    # nor the newest one:
    ctx = testing.Context(BooMachineCharm)
    secret = testing.Secret(
        tracked_content={"password": "first-password"},
        label=APP_SECRET_LABEL,
        owner="app",
    )
    state = testing.State(secrets={secret}, leader=True)
    for _ in range(2):
        state = ctx.run(ctx.on.secret_rotate(state.get_secret(label=APP_SECRET_LABEL)), state)

    # Act — Juju reports that revision 2 is no longer tracked by any unit:
    ctx.run(ctx.on.secret_remove(state.get_secret(label=APP_SECRET_LABEL), revision=2), state)

    # Assert — the charm asked Juju to remove that revision:
    assert ctx.removed_secret_revisions == [2]


PROXY_URL = "http://proxy.example.com:3128"


def test_proxy_configs_reach_the_workload(drop_in: pathops.LocalPath, restarted: list[str]):
    """Test that the proxy configs of the model are in the drop-in file of the service."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    proxy_config = testing.ProxyConfig(https=PROXY_URL, no=("localhost", "10.0.0.0/8"))
    state_in = testing.State(model=testing.Model(proxy_config=proxy_config))

    # Act:
    ctx.run(ctx.on.config_changed(), state_in)

    # Assert:
    content = drop_in.read_text()
    assert f'Environment="HTTPS_PROXY={PROXY_URL}"' in content
    assert f'Environment="https_proxy={PROXY_URL}"' in content
    assert 'Environment="NO_PROXY=10.0.0.0/8,localhost"' in content
    assert drop_in.stat().st_mode & 0o777 == 0o600
    assert restarted == [charm.SERVICE]


def test_removed_proxy_configs_are_cleared(drop_in: pathops.LocalPath, restarted: list[str]):
    """Test that the drop-in file loses proxy configs that the user removed from the model."""
    # Arrange: the drop-in file has proxy configs from an earlier event.
    drop_in.write_text(f'[Service]\nEnvironment="HTTPS_PROXY={PROXY_URL}"\n')
    ctx = testing.Context(BooMachineCharm)
    state_in = testing.State(model=testing.Model(proxy_config=testing.ProxyConfig()))

    # Act:
    ctx.run(ctx.on.update_status(), state_in)

    # Assert:
    content = drop_in.read_text()
    assert PROXY_URL not in content
    assert 'Environment="HTTPS_PROXY="' in content
    assert restarted == [charm.SERVICE]


def test_unchanged_proxy_configs_dont_restart_the_workload(
    drop_in: pathops.LocalPath, restarted: list[str]
):
    """Test that handling an event with unchanged proxy configs leaves the workload alone."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    state_in = testing.State(
        model=testing.Model(proxy_config=testing.ProxyConfig(https=PROXY_URL)),
    )
    ctx.run(ctx.on.config_changed(), state_in)
    restarted.clear()

    # Act:
    ctx.run(ctx.on.update_status(), state_in)

    # Assert:
    assert restarted == []


def test_stopped_workload_is_not_restarted(
    monkeypatch: pytest.MonkeyPatch, drop_in: pathops.LocalPath, restarted: list[str]
):
    """Test that new proxy configs don't start a workload that the user paused."""
    # Arrange:
    monkeypatch.setattr(charm.systemd, "service_running", lambda service: False)
    ctx = testing.Context(BooMachineCharm)
    state_in = testing.State(
        model=testing.Model(proxy_config=testing.ProxyConfig(https=PROXY_URL)),
    )

    # Act:
    ctx.run(ctx.on.update_status(), state_in)

    # Assert — the next start of the workload reads the drop-in file:
    assert PROXY_URL in drop_in.read_text()
    assert restarted == []


def test_percent_in_proxy_config_is_escaped(drop_in: pathops.LocalPath):
    """Test that systemd doesn't expand percent-encoded credentials in the proxy URL."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    url = "http://user:p%40ss@proxy.example.com:3128"
    state_in = testing.State(model=testing.Model(proxy_config=testing.ProxyConfig(http=url)))

    # Act:
    ctx.run(ctx.on.config_changed(), state_in)

    # Assert:
    assert 'Environment="HTTP_PROXY=http://user:p%%40ss@proxy.example.com:3128"' in (
        drop_in.read_text()
    )
