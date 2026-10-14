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


def last_proxy_log(juju: jubilant.Juju) -> str:
    """Return the most recent log message about the proxy environment."""
    lines = [line for line in juju.debug_log().splitlines() if "proxy environment updated" in line]
    assert lines, "The charm has not logged the proxy environment."
    return lines[-1]


def test_model_proxy_settings(charm: pathlib.Path, juju: jubilant.Juju):
    """Test that the charm exports the model's proxy settings to the environment."""
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
        juju.wait(lambda status: jubilant.all_active(status) and jubilant.all_agents_idle(status))

        # The charm logs the names of the variables that it set, but not their values (proxy
        # URLs can contain credentials).
        line = last_proxy_log(juju)
        assert "HTTP_PROXY, HTTPS_PROXY, NO_PROXY" in line, line
    finally:
        # Clean up: restore the defaults so that subsequent tests start clean.
        juju.model_config(reset=["juju-http-proxy", "juju-https-proxy", "juju-no-proxy"])
        juju.config("boo-machine", reset=["log-level"])
        juju.wait(lambda status: jubilant.all_active(status) and jubilant.all_agents_idle(status))

    # Without the model's proxy settings, the charm must remove the variables that it set before.
    # (The model's no-proxy setting has a default value, so NO_PROXY remains.)
    line = last_proxy_log(juju)
    assert "HTTP_PROXY" not in line, line
    assert "HTTPS_PROXY" not in line, line


# If you implement boo.get_version in the charm source,
# remove the @pytest.mark.skip line to enable this test.
# Alternatively, remove this test if you don't need it.
@pytest.mark.skip(reason="boo.get_version is not implemented")
def test_workload_version_is_set(charm: pathlib.Path, juju: jubilant.Juju):
    """Check that the correct version of the workload is running."""
    version = juju.status().apps["boo-machine"].version
    assert version == "3.14"  # Replace 3.14 by the expected version of the workload.
