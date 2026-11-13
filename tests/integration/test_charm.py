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

import jubilant
import pytest

logger = logging.getLogger(__name__)


def has_log_level_status(status: jubilant.Status, log_level: str) -> bool:
    """Return whether the unit handled config-changed with the given log level."""
    unit = status.apps["boo-machine"].units["boo-machine/0"]
    return unit.workload_status.current == "active" and unit.workload_status.message == (
        f"config-changed: log-level={log_level}"
    )


@pytest.mark.juju_setup
def test_deploy(charm: pathlib.Path, juju: jubilant.Juju):
    """Deploy the charm under test."""
    juju.deploy(charm, app="boo-machine")
    juju.wait(jubilant.all_active)


def test_config_reset_fires_config_changed(juju: jubilant.Juju):
    """Resetting config must notify the unit, including on Juju 4.x."""
    juju.config("boo-machine", {"log-level": "debug"})
    juju.wait(
        lambda status: has_log_level_status(status, "debug"),
        error=jubilant.any_error,
        timeout=60,
    )

    # This invokes `juju config boo-machine --reset log-level`. The server-side value returns
    # to its default on affected Juju 4.x versions, but no config-changed hook is dispatched.
    juju.config("boo-machine", reset=["log-level"])
    config = juju.config("boo-machine")
    assert config is not None
    assert config["log-level"] == "info"

    # Passes on Juju 3.x. On affected Juju 4.x versions this times out with the workload status
    # still saying "debug", reproducing the missing config-changed notification.
    juju.wait(
        lambda status: has_log_level_status(status, "info"),
        error=jubilant.any_error,
        timeout=30,
    )


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
    assert "api-token resolved (10 chars)" in juju.debug_log()

    # Act — add a new revision of the same secret. Juju notifies the charm with
    # a secret-changed event, and the charm re-reads the content.
    juju.update_secret(secret_uri, {"token": "a-much-longer-token-value"})
    juju.wait(jubilant.all_active)

    # Assert — the charm picked up the new revision, not the original one.
    assert "api-token resolved (25 chars)" in juju.debug_log()

    # Clean up: reset the config option so subsequent tests start clean.
    juju.config("boo-machine", reset=["api-token"])
    juju.wait(jubilant.all_active)


# If you implement boo.get_version in the charm source,
# remove the @pytest.mark.skip line to enable this test.
# Alternatively, remove this test if you don't need it.
@pytest.mark.skip(reason="boo.get_version is not implemented")
def test_workload_version_is_set(charm: pathlib.Path, juju: jubilant.Juju):
    """Check that the correct version of the workload is running."""
    version = juju.status().apps["boo-machine"].version
    assert version == "3.14"  # Replace 3.14 by the expected version of the workload.
