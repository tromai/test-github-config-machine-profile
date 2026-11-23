# Copyright 2026 Test Author
# See LICENSE file for licensing details.
#
# The integration tests use the Jubilant library and the pytest-jubilant plugin.
# See https://canonical.com/juju/docs/ops/latest/howto/write-integration-tests-for-a-charm/

import os
import pathlib
import subprocess
from typing import Any

import jubilant
import pytest


@pytest.fixture(scope="session")
def charm():
    """Return the absolute path of the charm under test."""
    charm = os.environ.get("CHARM_PATH")
    if not charm:
        charm_dir = pathlib.Path()  # Assume the current working directory is the charm root.
        charms = list(charm_dir.glob("*.charm"))
        assert charms, f"No charms were found in {charm_dir.absolute()}"
        assert len(charms) == 1, f"Found more than one charm {charms}"
        charm = charms[0]
    path = pathlib.Path(charm).resolve()
    assert path.is_file(), f"{path} is not a file"
    return path


@pytest.fixture(scope="session")
def ssh_private_key(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Generate an SSH key pair for 'juju ssh', and return the path of the private key."""
    path = tmp_path_factory.mktemp("ssh") / "id_ed25519"
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "integration-tests",
            "-f",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture(scope="module", autouse=True)
def ssh_access(juju: jubilant.Juju, ssh_private_key: pathlib.Path) -> dict[str, Any]:
    """Make 'juju ssh' work in the test model, and return the keyword arguments for 'Juju.ssh'.

    Juju 3.6 adds the SSH key of the client to every new model. Juju 4 doesn't, so the fixture
    adds a key to the model when 'juju ssh' needs one:

    * Juju 4.0 connects to a machine directly, so a machine model needs a key. A Kubernetes model
      doesn't, because Juju uses Kubernetes credentials.
    * From Juju 4.1, 'juju ssh' connects through the SSH server of the controller. Machine and
      Kubernetes models both need a key, and 'ssh_key' passes the key to the controller.
    """
    version = juju.version().tuple
    if version >= (4, 1, 0):
        kwargs: dict[str, Any] = {"ssh_key": ssh_private_key}
    elif version >= (4, 0, 0) and juju.show_model().model_type == "iaas":
        kwargs = {"ssh_options": ["-i", str(ssh_private_key)]}
    else:
        return {}

    public_key = pathlib.Path(f"{ssh_private_key}.pub").read_text().strip()
    key_data = public_key.split()[1]
    if key_data not in juju.cli("ssh-keys", "--full"):
        juju.add_ssh_key(public_key)
        # 'juju add-ssh-key' exits with 0 even if the controller rejects the key.
        assert key_data in juju.cli("ssh-keys", "--full"), "Juju didn't add the SSH key"
    return kwargs
