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


def wait_for_http_proxy(juju: jubilant.Juju, expected: str, timeout: float = 120) -> None:
    """Wait until the unit's status message reports the expected HTTP proxy.

    The charm reports the model's HTTP proxy in its active status message, in the
    'collect-status' handler. 'juju.wait' can return before Juju has queued the config-changed
    hook (the unit is still active and idle), so wait for the message itself rather than only
    for the unit to be active.
    """
    message = f"http_proxy={expected}"

    def has_message(status: jubilant.Status) -> bool:
        unit_status = status.apps["boo-machine"].units["boo-machine/0"].workload_status
        return unit_status.current == "active" and unit_status.message == message

    juju.wait(has_message, timeout=timeout)


def test_model_proxy_settings(charm: pathlib.Path, juju: jubilant.Juju):
    """Test that the charm sees the model's proxy settings."""
    # Juju emits no event when 'juju-*-proxy' model config changes. The charm picks up the new
    # settings the next time that it handles an event, so trigger config-changed by changing a
    # charm config option.
    juju.model_config(
        {
            "juju-http-proxy": "http://squid.internal:3128",
            "juju-https-proxy": "http://squid.internal:3129",
            "juju-no-proxy": "127.0.0.1,localhost,::1,10.0.0.0/8",
        }
    )
    try:
        juju.config("boo-machine", {"log-level": "debug"})
        wait_for_http_proxy(juju, "http://squid.internal:3128")
    finally:
        # Clean up: restore the defaults so that subsequent tests start clean.
        juju.model_config(reset=["juju-http-proxy", "juju-https-proxy", "juju-no-proxy"])
        juju.config("boo-machine", reset=["log-level"])

    # Without the model's proxy settings, the charm must report that none is set.
    wait_for_http_proxy(juju, "unset")


# If you implement boo.get_version in the charm source,
# remove the @pytest.mark.skip line to enable this test.
# Alternatively, remove this test if you don't need it.
@pytest.mark.skip(reason="boo.get_version is not implemented")
def test_workload_version_is_set(charm: pathlib.Path, juju: jubilant.Juju):
    """Check that the correct version of the workload is running."""
    version = juju.status().apps["boo-machine"].version
    assert version == "3.14"  # Replace 3.14 by the expected version of the workload.
