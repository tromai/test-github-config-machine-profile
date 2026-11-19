# Copyright 2026 Test Author
# See LICENSE file for licensing details.

"""Functions for managing and interacting with the workload.

The intention is that this module could be used outside the context of a charm.
"""

import logging

from charmlibs import pathops, systemd

logger = logging.getLogger(__name__)

SERVICE = "boo-workload.service"
SERVICE_FILE = pathops.LocalPath(f"/etc/systemd/system/{SERVICE}")
# A long-running placeholder. Replace the command with your workload.
SERVICE_DEFINITION = """\
[Unit]
Description=A service that runs the workload

[Service]
ExecStart=/usr/bin/sleep infinity
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""


# Functions for managing the workload process on the local machine:


def install() -> None:
    """Install the workload (by installing a snap, for example)."""
    # This function defines a systemd service. Replace it with the installation of your workload.
    pathops.ensure_contents(SERVICE_FILE, SERVICE_DEFINITION)
    systemd.daemon_reload()


def start() -> None:
    """Start the workload (by running a command, for example)."""
    # Ideally, this function should only return once the workload is ready to use.
    systemd.service_start(SERVICE)


def stop() -> None:
    """Stop the workload (by running a command, for example)."""
    # Ideally, this function should only return once the workload has stopped.
    systemd.service_stop(SERVICE)


# Functions for interacting with the workload, for example over HTTP:


def get_version() -> str | None:
    """Get the running version of the workload."""
    # You'll need to implement this function (or remove it if not needed).
    return None
