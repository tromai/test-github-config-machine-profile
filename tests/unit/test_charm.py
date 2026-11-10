# Copyright 2026 Test Author
# See LICENSE file for licensing details.
#
# To learn more about testing, see https://canonical.com/juju/docs/ops/latest/explanation/testing/

import dataclasses
import functools
import http.server
import pathlib
import threading
import urllib.error

import ops
import pytest
from ops import testing

import boo
from charm import API_TOKEN_LABEL, APP_SECRET_LABEL, BooMachineCharm, proxy_environment


@pytest.fixture(autouse=True)
def fake_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> list[tuple[str, ...]]:
    """Run the workload module against temporary files, with a recording fake of systemctl.

    The test returns the list of systemctl commands that the charm ran, in order.
    """
    systemctl_calls: list[tuple[str, ...]] = []

    def fake_systemctl(*args: str):
        systemctl_calls.append(args)

    monkeypatch.setattr(boo, "UNIT_FILE", tmp_path / "boo-workload.service")
    monkeypatch.setattr(boo, "PROXY_DROP_IN", tmp_path / "boo-workload.service.d" / "proxy.conf")
    monkeypatch.setattr(boo, "_systemctl", fake_systemctl)
    return systemctl_calls


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


PROXY_CONFIG = testing.ProxyConfig(
    http="http://squid.internal:3128",
    https="http://squid.internal:3129",
    no=("127.0.0.1", "localhost", "::1", "10.0.0.0/8"),
)

# What the workload should see for PROXY_CONFIG:
EXPECTED_PROXY_ENVIRONMENT = {
    "HTTP_PROXY": "http://squid.internal:3128",
    "http_proxy": "http://squid.internal:3128",
    "HTTPS_PROXY": "http://squid.internal:3129",
    "https_proxy": "http://squid.internal:3129",
    "NO_PROXY": "127.0.0.1,localhost,::1,10.0.0.0/8",
    "no_proxy": "127.0.0.1,localhost,::1,10.0.0.0/8",
}

RELOAD_AND_RESTART = [("daemon-reload",), ("try-restart", boo.SERVICE_NAME)]


def make_state(proxy_config: testing.ProxyConfig | None) -> testing.State:
    """Build a state in which the model has these proxy settings.

    The model's settings are what 'juju model-config juju-*-proxy' would set.
    """
    return testing.State(model=testing.Model(proxy_config=proxy_config))


def with_proxy_config(
    state: testing.State, proxy_config: testing.ProxyConfig | None
) -> testing.State:
    """Change the model's proxy settings, like 'juju model-config' does between two events."""
    return dataclasses.replace(state, model=testing.Model(proxy_config=proxy_config))


def get_workload_environment() -> dict[str, str]:
    """Get the environment that systemd will give the workload's service, from the drop-in file."""
    if not boo.PROXY_DROP_IN.exists():
        return {}
    lines = boo.PROXY_DROP_IN.read_text().splitlines()
    assert lines[0] == "[Service]"
    assignments = [line.removeprefix('Environment="').removesuffix('"') for line in lines[1:]]
    return dict(assignment.split("=", 1) for assignment in assignments)


def test_proxy_environment():
    """Test that all of the model's proxy settings become upper- and lower-case variables."""
    proxy = ops.ProxyConfig(
        http="http://a:1",
        https="http://b:2",
        no=("localhost",),
    )
    assert proxy_environment(proxy) == {
        "HTTP_PROXY": "http://a:1",
        "http_proxy": "http://a:1",
        "HTTPS_PROXY": "http://b:2",
        "https_proxy": "http://b:2",
        "NO_PROXY": "localhost",
        "no_proxy": "localhost",
    }


def test_proxy_environment_omits_missing_settings():
    """Test that settings that the model doesn't provide are left out."""
    assert proxy_environment(ops.ProxyConfig()) == {}
    # NO_PROXY alone has no effect, so the workload doesn't get it. This matters because Juju's
    # default for 'juju-no-proxy' is not empty.
    assert proxy_environment(ops.ProxyConfig(no=("localhost",))) == {}
    assert proxy_environment(ops.ProxyConfig(https="http://b:2", no=())) == {
        "HTTPS_PROXY": "http://b:2",
        "https_proxy": "http://b:2",
    }


def test_install_writes_the_unit_file(fake_machine: list[tuple[str, ...]]):
    """Test that the install event creates the systemd unit for the web server."""
    ctx = testing.Context(BooMachineCharm)

    ctx.run(ctx.on.install(), testing.State())

    assert "python3 -m http.server" in boo.UNIT_FILE.read_text()
    assert fake_machine == [("daemon-reload",)]


@pytest.mark.parametrize(
    "make_event",
    [
        lambda ctx: ctx.on.start(),
        lambda ctx: ctx.on.config_changed(),
        lambda ctx: ctx.on.update_status(),
        lambda ctx: ctx.on.upgrade_charm(),
        lambda ctx: ctx.on.action("resume"),
    ],
    ids=["start", "config-changed", "update-status", "upgrade-charm", "resume"],
)
def test_event_applies_proxy_settings(make_event):
    """Test that each reconciling event gives the workload the model's proxy settings."""
    ctx = testing.Context(BooMachineCharm)

    ctx.run(make_event(ctx), make_state(PROXY_CONFIG))

    assert get_workload_environment() == EXPECTED_PROXY_ENVIRONMENT


@pytest.mark.parametrize("proxy_config", [None, testing.ProxyConfig()])
def test_no_proxy_settings(proxy_config: testing.ProxyConfig | None, fake_machine):
    """Test that the workload gets no proxy variables if the model has no proxy settings."""
    # Both None and the default ProxyConfig mean that the user hasn't set a proxy.
    ctx = testing.Context(BooMachineCharm)

    ctx.run(ctx.on.update_status(), make_state(proxy_config))

    assert get_workload_environment() == {}
    assert not boo.PROXY_DROP_IN.exists()
    assert fake_machine == []  # Nothing changed, so the workload isn't touched.


def test_update_status_picks_up_set_change_and_unset(fake_machine: list[tuple[str, ...]]):
    """Test that the workload follows the model's settings as they are set, changed and unset."""
    ctx = testing.Context(BooMachineCharm)
    state = ctx.run(ctx.on.update_status(), make_state(None))
    assert get_workload_environment() == {}
    assert fake_machine == []

    # The user sets the proxy settings. The charm doesn't get an event for this.
    state = ctx.run(ctx.on.update_status(), with_proxy_config(state, PROXY_CONFIG))
    assert get_workload_environment() == EXPECTED_PROXY_ENVIRONMENT
    assert fake_machine == RELOAD_AND_RESTART

    # The user changes one of the settings.
    fake_machine.clear()
    changed = testing.ProxyConfig(
        http=PROXY_CONFIG.http, https="http://other.internal:3129", no=()
    )
    state = ctx.run(ctx.on.update_status(), with_proxy_config(state, changed))
    environment = get_workload_environment()
    assert environment["HTTPS_PROXY"] == "http://other.internal:3129"
    assert environment["HTTP_PROXY"] == PROXY_CONFIG.http
    assert fake_machine == RELOAD_AND_RESTART

    # The user resets the settings: nothing is left behind.
    fake_machine.clear()
    state = ctx.run(ctx.on.update_status(), with_proxy_config(state, None))
    assert get_workload_environment() == {}
    assert not boo.PROXY_DROP_IN.exists()
    assert fake_machine == RELOAD_AND_RESTART


def test_update_status_unsets_one_setting_at_a_time():
    """Test that removing one setting leaves the others in place."""
    ctx = testing.Context(BooMachineCharm)
    state = ctx.run(ctx.on.update_status(), make_state(PROXY_CONFIG))

    without_https = testing.ProxyConfig(http=PROXY_CONFIG.http, no=PROXY_CONFIG.no)
    ctx.run(ctx.on.update_status(), with_proxy_config(state, without_https))

    environment = get_workload_environment()
    assert "HTTPS_PROXY" not in environment
    assert "https_proxy" not in environment
    assert environment["HTTP_PROXY"] == "http://squid.internal:3128"
    assert environment["NO_PROXY"] == "127.0.0.1,localhost,::1,10.0.0.0/8"


def test_unchanged_settings_do_not_restart_the_workload(fake_machine: list[tuple[str, ...]]):
    """Test that handling events with unchanged settings leaves the workload alone."""
    ctx = testing.Context(BooMachineCharm)
    state = ctx.run(ctx.on.update_status(), make_state(PROXY_CONFIG))
    assert fake_machine == RELOAD_AND_RESTART
    content = boo.PROXY_DROP_IN.read_text()
    fake_machine.clear()

    for make_event in (ctx.on.update_status, ctx.on.config_changed, ctx.on.update_status):
        state = ctx.run(make_event(), state)

    assert fake_machine == []
    assert boo.PROXY_DROP_IN.read_text() == content


def test_start_does_not_restart_a_workload_that_is_not_running(fake_machine):
    """Test that the first start only starts the workload, with the settings already in place."""
    ctx = testing.Context(BooMachineCharm)

    ctx.run(ctx.on.start(), make_state(PROXY_CONFIG))

    assert get_workload_environment() == EXPECTED_PROXY_ENVIRONMENT
    assert fake_machine[-1] == ("enable", "--now", boo.SERVICE_NAME)


def test_drop_in_is_private_and_escapes_specifiers():
    """Test that the file with the proxy URL is private, and systemd can't expand its values."""
    ctx = testing.Context(BooMachineCharm)
    proxy_config = testing.ProxyConfig(https="http://user:p%40ss@proxy.internal:3128")

    ctx.run(ctx.on.update_status(), make_state(proxy_config))

    assert boo.PROXY_DROP_IN.stat().st_mode & 0o777 == 0o600
    assert "user:p%%40ss@proxy.internal" in boo.PROXY_DROP_IN.read_text()


def test_show_proxy_action(monkeypatch: pytest.MonkeyPatch):
    """Test that the show-proxy action reports what the workload serves."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    served = {"proxy.txt": "http_proxy=http://squid.internal:3128", "started.txt": "123"}
    monkeypatch.setattr("charm.boo.read_report", served.__getitem__)

    # Act:
    ctx.run(ctx.on.action("show-proxy"), testing.State())

    # Assert:
    assert ctx.action_results == {"proxy": served["proxy.txt"], "started": "123"}


def test_show_proxy_action_workload_down(monkeypatch: pytest.MonkeyPatch):
    """Test that the show-proxy action fails if the workload can't be reached."""

    def unreachable(name: str) -> str:
        raise urllib.error.URLError("connection refused")

    # Arrange:
    ctx = testing.Context(BooMachineCharm)
    monkeypatch.setattr("charm.boo.read_report", unreachable)

    # Act & assert:
    with pytest.raises(testing.ActionFailed) as exc_info:
        ctx.run(ctx.on.action("show-proxy"), testing.State())
    assert "cannot read from the workload" in exc_info.value.message


def test_proxy_settings_are_visible_in_the_charm():
    """Test that the charm reads the settings from the model."""
    # Arrange:
    ctx = testing.Context(BooMachineCharm)

    # Act and assert:
    with ctx(ctx.on.config_changed(), make_state(PROXY_CONFIG)) as manager:
        assert manager.charm.model.proxy_config.http == "http://squid.internal:3128"
        manager.run()


def test_read_report_bypasses_the_proxy(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    """Test that the charm reads the workload directly, even if the environment has a proxy."""
    # Arrange — a real web server, like the workload, and a proxy variable that points nowhere:
    (tmp_path / "proxy.txt").write_text("no proxy configured\n")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(boo, "PORT", server.server_port)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")

    # Act & assert:
    try:
        assert boo.read_report("proxy.txt") == "no proxy configured"
    finally:
        server.shutdown()
        server.server_close()
