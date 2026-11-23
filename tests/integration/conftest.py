# Copyright 2026 Test Author
# See LICENSE file for licensing details.
#
# The integration tests use the Jubilant library and the pytest-jubilant plugin.
# See https://canonical.com/juju/docs/ops/latest/howto/write-integration-tests-for-a-charm/

import os
import pathlib
import subprocess
from collections.abc import Generator
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
def ssh_access(juju: jubilant.Juju, ssh_private_key: pathlib.Path) -> Generator[dict[str, Any]]:
    """Make 'juju ssh' work in the test model, and yield the keyword arguments for 'Juju.ssh'.

    Juju 3.6 adds the SSH key of the client to every new model. Juju 4 doesn't, so the fixture
    adds its own key to the model. This follows the integration tests of Jubilant:

    * A machine model needs '-i' for the connection to the machine. Before Juju 4.1, that's a
      direct connection. From Juju 4.1, it's the final hop after the controller.
    * From Juju 4.1, 'juju ssh' connects through the SSH server of the controller, and '-i'
      doesn't apply to that connection. A machine model and a Kubernetes model both need
      'ssh_key' for it.
    * Before Juju 4.1, a Kubernetes model needs no key, because Juju uses Kubernetes credentials.
    """
    kwargs: dict[str, Any] = {}
    if juju.show_model().model_type == "iaas":
        kwargs["ssh_options"] = ["-i", str(ssh_private_key)]
    if juju.version().tuple >= (4, 1, 0):
        kwargs["ssh_key"] = ssh_private_key
    if not kwargs:
        yield kwargs
        return

    public_key = pathlib.Path(f"{ssh_private_key}.pub").read_text().strip()
    key_data = public_key.split()[1]
    juju.add_ssh_key(public_key)
    # 'juju add-ssh-key' exits with 0 even if the controller rejects the key.
    assert key_data in juju.cli("ssh-keys", "--full"), "Juju didn't add the SSH key"
    try:
        yield kwargs
    finally:
        juju.remove_ssh_key(public_key)
