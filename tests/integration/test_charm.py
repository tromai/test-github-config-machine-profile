# Copyright 2026 Test Author
# See LICENSE file for licensing details.
#
# The integration tests use the Jubilant library and the pytest-jubilant plugin.
# See https://canonical.com/juju/docs/ops/latest/howto/write-integration-tests-for-a-charm/
#
# pytest-jubilant provides a module-scoped `juju` fixture that creates a temporary Juju model.
# The `charm` fixture is defined in conftest.py.

import logging
import pathlib
import shlex
from typing import Any

import jubilant
import pytest

logger = logging.getLogger(__name__)


@pytest.mark.juju_setup
def test_deploy(charm: pathlib.Path, juju: jubilant.Juju):
    """Deploy the charm under test."""
    juju.deploy(charm, app="boo-machine")
    juju.wait(jubilant.all_active)


def test_pause_and_resume(charm: pathlib.Path, juju: jubilant.Juju):
    """Take the unit out of service with the pause action, then put it back."""
    juju.run("boo-machine/0", "pause")
    juju.wait(jubilant.all_maintenance)
    juju.run("boo-machine/0", "resume")


def test_app_managed_secret_created(charm: pathlib.Path, juju: jubilant.Juju):
    """Check that the charm created the app-managed workload password on start."""
    labels = [secret.label for secret in juju.secrets()]
    assert "workload-password" in labels, (
        "Expected the charm to create a 'workload-password' app secret on start. "
        "Check that the leader unit ran the start hook successfully."
    )


def test_user_secret(charm: pathlib.Path, juju: jubilant.Juju):
    """Test that the charm resolves a user-provided api-token secret."""
    # Create a user secret with a 'token' key, grant the charm access, and
    # point the charm at it.
    secret_uri = juju.add_secret("test-api-token", {"token": "shortvalue"})
    juju.grant_secret(secret_uri, "boo-machine")
    juju.config("boo-machine", {"api-token": secret_uri})
    juju.wait(jubilant.all_active)

    # The charm logs the length of the token it resolved.
    juju.wait(lambda _: "api-token resolved (10 chars)" in juju.debug_log(), timeout=30)

    # Act — add a new revision of the same secret. Juju notifies the charm with
    # a secret-changed event, and the charm re-reads the content.
    juju.update_secret(secret_uri, {"token": "a-much-longer-token-value"})
    juju.wait(jubilant.all_active)

    # Assert — the charm picked up the new revision, not the original one.
    juju.wait(lambda _: "api-token resolved (25 chars)" in juju.debug_log(), timeout=30)

    # Clean up: reset the config option so subsequent tests start clean.
    juju.config("boo-machine", reset=["api-token"])
    juju.wait(jubilant.all_active)


SERVICE = "snapd.service"
UNIT = "boo-machine/0"
PROXY_URL = "http://proxy.example.com:3128"


@pytest.fixture(scope="module", autouse=True)
def frequent_update_status(juju: jubilant.Juju):
    """Run update-status every minute, so the charm reads the new proxy configs sooner."""
    juju.model_config({"update-status-hook-interval": "1m"})
    yield
    juju.model_config(reset="update-status-hook-interval")


def workload_environment(juju: jubilant.Juju, ssh_access: dict[str, Any]) -> dict[str, str]:
    """Return the environment that systemd has loaded for the workload service."""
    try:
        output = juju.ssh(
            UNIT, "systemctl", "show", SERVICE, "--property=Environment", "--value", **ssh_access
        )
    except jubilant.CLIError as exc:
        # Juju 4 can refuse SSH while the machine's authorised keys are still being updated.
        if "Permission denied (publickey)" not in str(exc):
            raise
        return {}
    # Systemctl separates the entries with spaces, and quotes an entry that contains a space.
    return dict(entry.split("=", 1) for entry in shlex.split(output))


def wait_for_workload_environment(
    juju: jubilant.Juju, ssh_access: dict[str, Any], expected: dict[str, str]
) -> None:
    """Wait for the proxy environment, and include the last value in a failure message."""
    seen: dict[str, str] = {}

    def environment_matches(_) -> bool:
        nonlocal seen
        seen = workload_environment(juju, ssh_access)
        return seen.items() >= expected.items()

    try:
        juju.wait(environment_matches, delay=5, timeout=300)
    except TimeoutError:
        pytest.fail(f"The workload didn't get {expected}. Last seen: {seen}")


def test_proxy_configs(juju: jubilant.Juju, ssh_access: dict[str, Any]):
    """Test that the workload gets and clears the proxy configs of the model."""
    try:
        juju.model_config({"juju-https-proxy": PROXY_URL, "juju-no-proxy": "localhost,10.0.0.0/8"})
        expected = {
            "HTTP_PROXY": "",
            "HTTPS_PROXY": PROXY_URL,
            "NO_PROXY": "10.0.0.0/8,localhost",
            "http_proxy": "",
            "https_proxy": PROXY_URL,
            "no_proxy": "10.0.0.0/8,localhost",
        }
        wait_for_workload_environment(juju, ssh_access, expected)

        # Removed proxy configs are cleared, and juju-no-proxy returns to the Juju default.
        juju.model_config(reset=["juju-https-proxy", "juju-no-proxy"])
        expected = {
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "NO_PROXY": "127.0.0.1,::1,localhost",
            "http_proxy": "",
            "https_proxy": "",
            "no_proxy": "127.0.0.1,::1,localhost",
        }
        wait_for_workload_environment(juju, ssh_access, expected)
    finally:
        # Reset the proxy configs, even if the test fails, so later tests start clean.
        juju.model_config(reset=["juju-https-proxy", "juju-no-proxy"])


# If you implement boo.get_version in the charm source,
# remove the @pytest.mark.skip line to enable this test.
# Alternatively, remove this test if you don't need it.
@pytest.mark.skip(reason="boo.get_version is not implemented")
def test_workload_version_is_set(charm: pathlib.Path, juju: jubilant.Juju):
    """Check that the correct version of the workload is running."""
    version = juju.status().apps["boo-machine"].version
    assert version == "3.14"  # Replace 3.14 by the expected version of the workload.
