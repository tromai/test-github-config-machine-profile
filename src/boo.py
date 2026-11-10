# Copyright 2026 Test Author
# See LICENSE file for licensing details.

"""Functions for managing and interacting with the workload.

The workload is a tiny web server, run as a systemd service. When it starts, it writes the proxy
variables from its own environment to 'proxy.txt', and the time to 'started.txt', then serves
both files over HTTP. The files are plain text, so that the integration tests can see which
proxy settings the running workload actually received, and whether it was restarted.

The intention is that this module could be used outside the context of a charm.
"""

import logging
import pathlib
import subprocess
import urllib.request

logger = logging.getLogger(__name__)

SERVICE_NAME = "boo-workload"
PORT = 8000
UNIT_FILE = pathlib.Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
# A drop-in file adds to the unit file. The charm owns this file, and only writes the proxy
# settings to it, so removing it removes the settings.
PROXY_DROP_IN = pathlib.Path(f"/etc/systemd/system/{SERVICE_NAME}.service.d/proxy.conf")

# systemd creates /run/boo-workload for the service, owned by the service's user.
UNIT_FILE_CONTENT = f"""\
[Unit]
Description=A web server that shows its proxy settings

[Service]
User=nobody
RuntimeDirectory={SERVICE_NAME}
WorkingDirectory=/run/{SERVICE_NAME}
ExecStart=/bin/sh -c 'date +%%s%%N > started.txt \
&& {{ env | grep -i _proxy= | sort > proxy.txt || true; }} \
&& {{ [ -s proxy.txt ] || echo no proxy configured > proxy.txt; }} \
&& exec /usr/bin/python3 -m http.server {PORT}'
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/systemctl", *args], check=True, capture_output=True, text=True
    )


# Functions for managing the workload process on the local machine:


def install() -> None:
    """Install the workload's systemd unit."""
    UNIT_FILE.write_text(UNIT_FILE_CONTENT)
    _systemctl("daemon-reload")


def start() -> None:
    """Start the workload, and make it start when the machine boots."""
    _systemctl("enable", "--now", SERVICE_NAME)


def stop() -> None:
    """Stop the workload."""
    _systemctl("stop", SERVICE_NAME)


def set_proxy_environment(environment: dict[str, str]) -> bool:
    """Make the workload's service use these environment variables, the next time it starts.

    Args:
        environment: The proxy variables for the workload. Pass an empty dictionary to remove
            all of them.

    Returns:
        True if the configuration changed (and the workload needs a restart to use it). The
        function leaves the files alone if nothing changed, so it's safe to call on every event.
    """
    content = None
    if environment:
        # systemd expands '%' specifiers in values, so escape them. Sorting keeps the file stable.
        lines = [
            f'Environment="{k}={v.replace("%", "%%")}"' for k, v in sorted(environment.items())
        ]
        content = "[Service]\n" + "\n".join(lines) + "\n"
    current = PROXY_DROP_IN.read_text() if PROXY_DROP_IN.exists() else None
    if content == current:
        return False
    if content is None:
        PROXY_DROP_IN.unlink()
    else:
        PROXY_DROP_IN.parent.mkdir(parents=True, exist_ok=True)
        # The proxy URL can contain credentials, so restrict who can read the file.
        PROXY_DROP_IN.touch(mode=0o600)
        PROXY_DROP_IN.write_text(content)
    _systemctl("daemon-reload")
    return True


def restart_if_running() -> None:
    """Restart the workload, unless it's stopped (for example, by the pause action)."""
    _systemctl("try-restart", SERVICE_NAME)


# Functions for interacting with the workload, for example over HTTP:


def get_version() -> str | None:
    """Get the running version of the workload."""
    # The workload is the Python standard library's web server, which has no version of its own.
    return None


def read_report(name: str) -> str:
    """Read a plain-text file that the workload serves over HTTP.

    The request must never go through the model's proxy.

    Raises:
        OSError: If the workload can't be reached. This includes urllib.error.URLError.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://localhost:{PORT}/{name}", timeout=5) as response:
        return response.read().decode().strip()
