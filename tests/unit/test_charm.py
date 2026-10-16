# Copyright 2026 Test Author
# See LICENSE file for licensing details.
#
# To learn more about testing, see https://canonical.com/juju/docs/ops/latest/explanation/testing/

import dataclasses
import os
import pathlib
from collections.abc import Callable, Iterator
from typing import Any

import ops
import pytest
import yaml
from ops import testing

from charm import API_TOKEN_LABEL, APP_SECRET_LABEL, BooMachineCharm, proxy_environment

PROXY_VARIABLES = [
    variable
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "NO_PROXY")
    for variable in (name, name.lower())
]


@pytest.fixture(autouse=True)
def isolated_proxy_environment() -> Iterator[None]:
    """Start each test without proxy variables, and undo the changes that the charm makes.

    The charm exports proxy settings to os.environ, which the tests share, because Scenario runs
    the charm in the same process as the tests.
    """
    saved = os.environ.copy()
    for variable in PROXY_VARIABLES:
        os.environ.pop(variable, None)
    yield
    os.environ.clear()
    os.environ.update(saved)


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
    assert state_out.unit_status == testing.ActiveStatus("http_proxy=unset")


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
    assert state_out.unit_status == testing.ActiveStatus("http_proxy=unset")


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
    assert state_out.unit_status == testing.ActiveStatus("http_proxy=unset")


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
    assert state_out.unit_status == testing.ActiveStatus("http_proxy=unset")


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


CHARMCRAFT = yaml.safe_load((pathlib.Path(__file__).parents[2] / "charmcraft.yaml").read_text())


class RecordingCharm(BooMachineCharm):
    """The charm under test, which also records the environment at the end of each handler.

    Scenario restores os.environ when a test event finishes, so the tests can't inspect the
    changes that the charm makes after 'ctx.run'. The charm's own handlers are observed first,
    so the recorded environment includes their changes.
    """

    environment: dict[str, str] = {}

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        for event in (
            self.on.install,
            self.on.start,
            self.on.upgrade_charm,
            self.on.config_changed,
            self.on["resume"].action,
        ):
            framework.observe(event, self._record_environment)

    def _record_environment(self, event: ops.EventBase):
        RecordingCharm.environment = dict(os.environ)


def run_and_get_environment(
    make_event: Callable[[testing.Context[RecordingCharm]], Any],
    state: testing.State,
) -> dict[str, str]:
    """Run an event and return the environment as it was when the charm's handlers finished."""
    # Scenario can't find charmcraft.yaml from this module, so pass the charm's metadata.
    ctx = testing.Context(RecordingCharm, meta=CHARMCRAFT)
    RecordingCharm.environment = {}
    ctx.run(make_event(ctx), state)
    return RecordingCharm.environment


def make_proxy_state() -> testing.State:
    """Build a state in which the model has proxy settings (like 'juju model-config')."""
    proxy_config = testing.ProxyConfig(
        http_proxy="http://squid.internal:3128",
        https_proxy="http://squid.internal:3129",
        no_proxy="127.0.0.1,localhost,::1,10.0.0.0/8",
    )
    return testing.State(model=testing.Model(proxy_config=proxy_config))


def assert_proxy_environment_set(environment: dict[str, str]):
    """Check that the variables for make_proxy_state's settings are in the environment."""
    expected = {
        "HTTP_PROXY": "http://squid.internal:3128",
        "HTTPS_PROXY": "http://squid.internal:3129",
        "NO_PROXY": "127.0.0.1,localhost,::1,10.0.0.0/8",
    }
    for name, value in expected.items():
        assert environment[name] == value
        assert environment[name.lower()] == value
    # The model has no FTP proxy, so the charm must not export one.
    assert "FTP_PROXY" not in environment
    assert "ftp_proxy" not in environment


def test_proxy_environment():
    """Test that all of the model's proxy settings become upper- and lower-case variables."""
    proxy = ops.ProxyConfig(
        http_proxy="http://a:1",
        https_proxy="http://b:2",
        ftp_proxy="http://c:3",
        no_proxy="localhost",
    )
    assert proxy_environment(proxy) == {
        "HTTP_PROXY": "http://a:1",
        "http_proxy": "http://a:1",
        "HTTPS_PROXY": "http://b:2",
        "https_proxy": "http://b:2",
        "FTP_PROXY": "http://c:3",
        "ftp_proxy": "http://c:3",
        "NO_PROXY": "localhost",
        "no_proxy": "localhost",
    }


def test_proxy_environment_omits_missing_settings():
    """Test that settings that the model doesn't provide are left out."""
    assert proxy_environment(ops.ProxyConfig()) == {}
    assert proxy_environment(ops.ProxyConfig(https_proxy="http://b:2")) == {
        "HTTPS_PROXY": "http://b:2",
        "https_proxy": "http://b:2",
    }


def test_install_uses_proxy_settings(monkeypatch: pytest.MonkeyPatch):
    """Test that the proxy variables are in place while the workload is being installed."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    seen: dict[str, str] = {}
    monkeypatch.setattr("charm.boo.install", lambda: seen.update(os.environ))
    # Act:
    ctx.run(ctx.on.install(), make_proxy_state())
    # Assert:
    assert seen["HTTP_PROXY"] == "http://squid.internal:3128"
    assert seen["https_proxy"] == "http://squid.internal:3129"


def test_start_exports_proxy_settings(monkeypatch: pytest.MonkeyPatch):
    """Test that the start event exports the model's proxy settings."""
    # Arrange:
    monkeypatch.setattr("charm.boo.get_version", mock_get_version)
    # Act:
    environment = run_and_get_environment(lambda ctx: ctx.on.start(), make_proxy_state())
    # Assert:
    assert_proxy_environment_set(environment)


def test_resume_action_exports_proxy_settings():
    """Test that the resume action exports the model's proxy settings."""
    # Act:
    environment = run_and_get_environment(lambda ctx: ctx.on.action("resume"), make_proxy_state())
    # Assert:
    assert_proxy_environment_set(environment)


def test_upgrade_charm_exports_proxy_settings():
    """Test that upgrade-charm picks up the model's current proxy settings."""
    # Act:
    environment = run_and_get_environment(lambda ctx: ctx.on.upgrade_charm(), make_proxy_state())
    # Assert:
    assert_proxy_environment_set(environment)


def test_config_changed_exports_proxy_settings():
    """Test that config-changed exports the model's proxy settings."""
    # Act:
    environment = run_and_get_environment(lambda ctx: ctx.on.config_changed(), make_proxy_state())
    # Assert:
    assert_proxy_environment_set(environment)


def test_config_changed_without_proxy_settings():
    """Test that the charm exports nothing when the model has no proxy settings."""
    # Act:
    environment = run_and_get_environment(lambda ctx: ctx.on.config_changed(), testing.State())
    # Assert:
    assert not [variable for variable in PROXY_VARIABLES if variable in environment]


def test_config_changed_removes_stale_proxy_settings():
    """Test that variables are removed when the model no longer provides the setting."""
    # Arrange — the previous values are still in the environment:
    for variable in PROXY_VARIABLES:
        os.environ[variable] = "http://stale.internal:3128"
    # Act — the model has no proxy settings:
    environment = run_and_get_environment(lambda ctx: ctx.on.config_changed(), testing.State())
    # Assert:
    assert not [variable for variable in PROXY_VARIABLES if variable in environment]


def test_collect_status_reports_model_http_proxy():
    """Test that the active status message shows the model's HTTP proxy."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    state_in = make_proxy_state()
    state_in = dataclasses.replace(state_in, unit_status=testing.ActiveStatus())
    # Act:
    state_out = ctx.run(ctx.on.update_status(), state_in)
    # Assert:
    assert state_out.unit_status == testing.ActiveStatus("http_proxy=http://squid.internal:3128")


def test_collect_status_leaves_other_statuses_alone():
    """Test that collect-status keeps a paused unit in maintenance status."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    state_in = testing.State(unit_status=testing.MaintenanceStatus("paused"))
    # Act:
    state_out = ctx.run(ctx.on.update_status(), state_in)
    # Assert:
    assert state_out.unit_status == testing.MaintenanceStatus("paused")


def test_proxy_settings_are_visible_in_the_charm():
    """Test that the charm reads the settings from the model, not from the test process."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    # Act and assert:
    with ctx(ctx.on.config_changed(), make_proxy_state()) as manager:
        assert manager.charm.model.proxy_config.http_proxy == "http://squid.internal:3128"
        assert manager.charm.model.proxy_config.ftp_proxy is None
        manager.run()
