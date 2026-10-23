#!/usr/bin/env python3
# Copyright 2026 Test Author
# See LICENSE file for licensing details.

"""Charm the application."""

import datetime
import logging
import os
import secrets

import ops

# A standalone module for workload-specific logic (no charming concerns):
import boo

logger = logging.getLogger(__name__)

# This charm demonstrates the two independent roles a charm plays with Juju secrets.
# The two secrets below are unrelated: one is observed, the other is owned.
#  - api-token: granted by the user and referenced in a config option.
#  - workload-password: created and rotated by this charm for its own workload.
API_TOKEN_LABEL = "api-token"  # Any label works; matching the config option name is clearest.
APP_SECRET_LABEL = "workload-password"


def proxy_environment(proxy: ops.ProxyConfig) -> dict[str, str]:
    """Convert the model's proxy settings to environment variables.

    Tools disagree on the case of the variable names (curl only honours the lower-case
    'http_proxy', for example), so each setting is provided in both cases. Settings that the
    model doesn't provide are left out.
    """
    settings = {
        "HTTP_PROXY": proxy.http,
        "HTTPS_PROXY": proxy.https,
        "NO_PROXY": ",".join(proxy.no),
    }
    environment: dict[str, str] = {}
    for name, value in settings.items():
        if value:
            environment[name] = value
            environment[name.lower()] = value
    return environment


def proxy_status_message(http_proxy: str | None) -> str:
    """Format the model's HTTP proxy as a status message.

    The integration tests read this message from the unit's status, which is more reliable than
    searching the debug log. Proxy URLs can contain credentials, so don't use a real proxy URL
    with credentials in a model that other people can read.
    """
    return f"http_proxy={http_proxy or 'unset'}"


class BooMachineCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        framework.observe(self.on.collect_unit_status, self._on_collect_status)
        framework.observe(self.on["pause"].action, self._on_pause_action)
        framework.observe(self.on["resume"].action, self._on_resume_action)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.secret_changed, self._on_secret_changed)
        framework.observe(self.on.secret_rotate, self._on_secret_rotate)
        framework.observe(self.on.secret_expired, self._on_secret_expired)
        framework.observe(self.on.secret_remove, self._on_secret_remove)

    def _apply_proxy_settings(self) -> None:
        """Export the model's proxy settings to the environment of this hook.

        Juju doesn't emit an event when the 'juju-*-proxy' model config changes, so call this at
        the start of every handler that runs the workload or installs software. Subprocesses
        (for example, pip, apt or snap) inherit the environment.
        """
        new_environment = proxy_environment(self.model.proxy_config)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            for variable in (name, name.lower()):
                if variable in new_environment:
                    os.environ[variable] = new_environment[variable]
                else:
                    os.environ.pop(variable, None)
        # Proxy URLs can contain credentials, so only log which settings are in use.
        names = [name for name in new_environment if name.isupper()]
        logger.info("proxy environment updated: %s", ", ".join(names) or "none set")

    def _on_collect_status(self, event: ops.CollectStatusEvent):
        """Report the model's HTTP proxy in the unit's active status message."""
        # Leave any other status (blocked, maintenance, waiting) as the handlers set it. For
        # example, the pause action leaves the unit in maintenance status until it's resumed.
        if isinstance(self.unit.status, ops.ActiveStatus):
            http_proxy = self.model.proxy_config.http
            event.add_status(ops.ActiveStatus(proxy_status_message(http_proxy)))

    def _on_install(self, event: ops.InstallEvent):
        """Install the workload on the machine."""
        # The proxy settings must be in place before installing anything from the network.
        self._apply_proxy_settings()
        boo.install()

    def _on_upgrade_charm(self, event: ops.UpgradeCharmEvent):
        """Handle upgrade-charm event — the model's proxy settings might have changed."""
        self._apply_proxy_settings()

    def _on_start(self, event: ops.StartEvent):
        """Handle start event."""
        self._apply_proxy_settings()
        self.unit.status = ops.MaintenanceStatus("starting workload")
        boo.start()
        version = boo.get_version()
        if version is not None:
            self.unit.set_workload_version(version)
        # Create the app-managed workload secret on first start (leader only).
        if self.unit.is_leader():
            secret = self._ensure_app_secret()
            # A secret's owner reads the latest revision with 'peek_content', not 'get_content'.
            password = secret.peek_content()["password"]
            self._configure_workload_password(password)
        # A non-leader unit should get the secret ID from a peer relation, then use
        # 'get_content' to read the latest revision. This charm doesn't demonstrate that; see
        # https://canonical.com/juju/docs/ops/latest/howto/manage-secrets/
        self.unit.status = ops.ActiveStatus()

    def _on_pause_action(self, event: ops.ActionEvent):
        """Handle the pause action: stop the workload, but leave the unit deployed."""
        boo.stop()
        self.unit.status = ops.MaintenanceStatus("paused: run the resume action to restart")

    def _on_resume_action(self, event: ops.ActionEvent):
        """Handle the resume action: start the workload again."""
        self._apply_proxy_settings()
        self.unit.status = ops.MaintenanceStatus("starting workload")
        boo.start()
        self.unit.status = ops.ActiveStatus()

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        """Handle config-changed event — api-token might now hold a different secret's ID."""
        self._apply_proxy_settings()
        self._configure_api_token()

    def _on_secret_changed(self, event: ops.SecretChangedEvent):
        """Handle secret-changed event — the api-token secret has a new revision."""
        if event.secret.label == API_TOKEN_LABEL:
            self._configure_api_token()

    def _configure_api_token(self) -> None:
        """Read the user-provided api-token secret and configure the workload with it."""
        api_token_id = self.config.get("api-token")
        # A 'secret' config option is always a string, but self.config is typed as the union
        # of every config type, so narrow it here to satisfy static type checks.
        if not isinstance(api_token_id, str):
            return
        try:
            # Attaching a label lets the secret-changed handler recognise this secret.
            secret = self.model.get_secret(id=api_token_id, label=API_TOKEN_LABEL)
            # 'refresh' tells Juju to start tracking the latest revision.
            content = secret.get_content(refresh=True)
        except ops.SecretNotFoundError:
            self.unit.status = ops.BlockedStatus("api-token secret not found or not granted")
            return
        if "token" not in content:
            self.unit.status = ops.BlockedStatus("api-token secret has no 'token' key")
            return
        token = content["token"]
        # Reconfiguring the workload can be slow, so tell the user what's happening.
        self.unit.status = ops.MaintenanceStatus("reconfiguring workload")
        logger.info("api-token resolved (%d chars); reconfiguring workload", len(token))
        # Use 'token' to configure your workload, for example:
        # boo.configure(api_token=token)
        self.unit.status = ops.ActiveStatus()

    def _ensure_app_secret(self) -> ops.Secret:
        """Get or create the app-managed workload password (idempotent, leader only)."""
        try:
            return self.model.get_secret(label=APP_SECRET_LABEL)
        except ops.SecretNotFoundError:
            return self.app.add_secret(
                {"password": secrets.token_hex(32)},
                label=APP_SECRET_LABEL,
                rotate=ops.SecretRotate.MONTHLY,
                expire=datetime.timedelta(days=90),
            )

    def _configure_workload_password(self, password: str) -> None:
        """Tell the workload which password it should accept."""
        # Use 'password' to configure your workload, for example:
        # boo.configure(workload_password=password)
        # If this 'configure' call is slow, set maintenance status before the call.
        logger.info("workload password configured (%d chars)", len(password))

    def _on_secret_rotate(self, event: ops.SecretRotateEvent):
        """Handle secret-rotate event — the rotation policy says it's time for a new password."""
        if event.secret.label == APP_SECRET_LABEL:
            self._set_new_password(event.secret)

    def _on_secret_expired(self, event: ops.SecretExpiredEvent):
        """Handle secret-expired event — the current password must not be used any more."""
        if event.secret.label == APP_SECRET_LABEL:
            self._set_new_password(event.secret)

    def _set_new_password(self, secret: ops.Secret) -> None:
        """Add a new revision of the app-managed workload password."""
        password = secrets.token_hex(32)
        # Don't remove the old revision here: other units might still be using it.
        # Juju emits secret-remove once no unit is tracking it any more.
        secret.set_content({"password": password})
        self._configure_workload_password(password)

    def _on_secret_remove(self, event: ops.SecretRemoveEvent):
        """Handle secret-remove event — no unit is using this revision any more."""
        # Without this, every rotation leaves another revision behind.
        if event.secret.label == APP_SECRET_LABEL:
            event.remove_revision()


if __name__ == "__main__":  # pragma: nocover
    ops.main(BooMachineCharm)
