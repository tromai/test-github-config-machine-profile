# Copyright 2026 Test Author
# See LICENSE file for licensing details.

"""Functions for managing and interacting with the workload.

The intention is that this module could be used outside the context of a charm.
"""

import logging

from charmlibs import systemd

logger = logging.getLogger(__name__)

# Snapd is included in Ubuntu and reads the standard proxy environment variables.
SERVICE = "snapd.service"


# Functions for managing the workload process on the local machine:


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
