"""Two roles, two hosts: what has to be true before the wheel can be split, and the artefacts.

Stage 12 of the remote-worker arc. The property underneath a two-host deployment is not that
there are two wheels -- there is one -- but that the worker's start path does not NEED the
control plane's code. While that holds, splitting the distribution is packaging work; the moment
it stops holding, it becomes a rewrite, and nobody finds out until they try.

`agentnode_sdk/roles.py` walks the import graph statically, over the source, including imports
written inside functions -- which is where the last crossing was: `worker/service.py` reached
`gateway.boot` from inside a function, so nothing at import time would have caught it and the
worker needed the control plane's package to learn its own kernel's boot id.

NOT A HOST-ISOLATION TEST: one process, one kernel. Nothing here runs on two machines and
nothing here is evidence that two machines isolate anything.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

from agentnode_sdk import roles

DEPLOY = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "separate-worker-host"


class TestTheImportGraph:

    def test_the_worker_does_not_reach_the_control_plane(self):
        """Exactly the known crossings, and no others. A ratchet in both directions: a new one
        fails here, and a fixed one has to be taken out of the manifest before this goes green
        again -- so the list cannot quietly describe a worse state than the code is in."""
        found = roles.crossings(roles.WORKER_ENTRY, roles.NOT_ON_THE_WORKER)
        unexpected = {module: " -> ".join(path) for module, path in found.items()
                      if module not in roles.KNOWN_CROSSINGS}
        assert unexpected == {}, "a new crossing from the worker into the control plane"
        stale = [m for m in roles.KNOWN_CROSSINGS
                 if m.startswith("agentnode_sdk.gateway") and m not in found]
        assert stale == [], "these crossings are gone; take them out of KNOWN_CROSSINGS"

    def test_the_control_plane_does_not_reach_what_runs_code(self):
        found = roles.crossings(roles.CONTROL_PLANE_ENTRY, roles.NOT_ON_THE_CONTROL_PLANE)
        unexpected = {module: " -> ".join(path) for module, path in found.items()
                      if module not in roles.KNOWN_CROSSINGS}
        assert unexpected == {}

    def test_the_boot_identity_is_nobodys_role(self):
        """The one that was fixed rather than recorded. A worker asking which boot of ITS OWN
        machine it is on must not go through the gateway's package to find out."""
        reached = roles.reached_from(("agentnode_sdk.worker.service",))
        assert "agentnode_sdk.gateway.boot" not in reached
        assert "agentnode_sdk.machine" in reached

    def test_and_the_old_name_still_works(self):
        """Other code and other people's imports point at it."""
        from agentnode_sdk.gateway.boot import boot_identity, describe
        from agentnode_sdk import machine

        assert boot_identity is machine.boot_identity
        assert describe is machine.describe

    def test_there_is_one_reader_of_the_kernels_boot_id(self):
        """There were two, with different answers for a machine that has none: one returned a
        per-process value and the other an empty string. Two implementations of one fact drift."""
        import inspect

        from agentnode_sdk.gateway import lifecycle

        assert "machine" in inspect.getsource(lifecycle.this_boot)

    def test_and_the_command_line_the_worker_actually_starts_through(self):
        """The honest one. A worker host does not import `worker.service`; it runs
        `agentnode worker serve`, and that command line DOES reach the control plane's package --
        one `runtime_pin` import and the `gateway/__init__` it drags in behind it. Recorded
        exactly, so nobody reads the graph above as "the worker needs nothing from the gateway"."""
        found = roles.crossings(roles.THE_WORKERS_COMMAND_LINE, roles.NOT_ON_THE_WORKER)
        assert set(found) == set(roles.KNOWN_CLI_CROSSINGS)

    def test_every_known_crossing_says_why(self):
        for module, because in dict(roles.KNOWN_CROSSINGS,
                                    **roles.KNOWN_CLI_CROSSINGS).items():
            assert len(because) > 40, "%s is listed without a reason worth reading" % module


class TestPreflight:
    """The same checks `serve` makes, before anything is opened -- and what the unit runs."""

    def _args(self, **over):
        class Args:
            pass

        args = Args()
        for name, value in dict(
                topology="separate-worker-host", listen="tcps://10.0.1.5:8443", socket="",
                keyring="", journal="", tls_dir="", trust="", deployment="",
                accept_gateway=[], revocation_list="", floor="", tombstones="",
                trust_reload_seconds=10.0, reevaluate_seconds=5.0, key="").items():
            setattr(args, name, value)
        for name, value in over.items():
            setattr(args, name, value)
        return args

    def test_a_loopback_address_on_a_remote_worker_is_refused(self, capsys):
        from agentnode_sdk.cli.worker_commands import cmd_preflight

        assert cmd_preflight(self._args(listen="tcps://127.0.0.1:8443")) == 1
        assert "topology_disagrees_with_address" in capsys.readouterr().out

    def test_a_wildcard_bind_is_refused(self, capsys):
        from agentnode_sdk.cli.worker_commands import cmd_preflight

        assert cmd_preflight(self._args(listen="tcps://0.0.0.0:8443")) == 1
        assert "address_is_every_interface" in capsys.readouterr().out

    def test_a_remote_worker_without_a_keyring_is_refused(self, capsys):
        from agentnode_sdk.cli.worker_commands import cmd_preflight

        assert cmd_preflight(self._args()) == 1
        said = capsys.readouterr().out
        assert "--keyring" in said and "shared by everything" in said

    def test_a_remote_worker_without_a_journal_is_refused(self, capsys):
        from agentnode_sdk.cli.worker_commands import cmd_preflight

        assert cmd_preflight(self._args()) == 1
        assert "--journal" in capsys.readouterr().out

    def test_a_remote_worker_without_the_withdrawn_list_is_refused(self, capsys, tmp_path):
        """Stage 9 built the list and nothing reached it. A mechanism that exists and is never
        configured is not a check, and the first place that shows is here."""
        from agentnode_sdk.cli.worker_commands import cmd_preflight

        assert cmd_preflight(self._args(
            tls_dir=str(tmp_path), trust=str(tmp_path / "ca.pem"), deployment="alpha",
            accept_gateway=["g1"], revocation_list=str(tmp_path / "revoked.crl"),
            floor=str(tmp_path / "worker.floor"))) == 1
        assert "--tombstones" in capsys.readouterr().out

    def test_it_opens_nothing(self):
        """The one property that makes it safe to run from ExecStartPre and by hand."""
        import inspect

        from agentnode_sdk.cli import worker_commands

        source = inspect.getsource(worker_commands.cmd_preflight)
        for forbidden in ("serve(", "listener.open", "bind(", "prove_its_ceilings"):
            assert forbidden not in source

    def test_the_two_commands_read_the_arguments_with_the_same_code(self):
        import inspect

        from agentnode_sdk.cli import worker_commands

        assert "_tls_from(args)" in inspect.getsource(worker_commands.cmd_serve)
        assert "_tls_from(args)" in inspect.getsource(worker_commands.cmd_preflight)

    def test_it_prints_no_key_material(self, tmp_path, capsys):
        from agentnode_sdk.cli.worker_commands import cmd_preflight
        from agentnode_sdk.worker import pairkeys

        secret = bytes(range(32))
        ring = pairkeys.empty().add(gateway="g1", worker="w1", key=secret)
        ring.write(tmp_path / "keys.json")
        cmd_preflight(self._args(keyring=str(tmp_path / "keys.json"),
                                 journal=str(tmp_path / "journal")))
        said = capsys.readouterr().out
        assert secret.hex() not in said
        import base64

        assert base64.b64encode(secret).decode() not in said


class TestTheArtefacts:
    """What is shipped for the two hosts, checked for the things a reader cannot see."""

    def _unit(self, name):
        return (DEPLOY / name).read_text(encoding="utf-8")

    def test_both_units_and_all_the_scripts_are_there(self):
        for name in ("README.md", "worker-host.service", "control-plane.service",
                     "install-control-plane.sh", "install-worker-host.sh",
                     "upgrade-one-host.sh", "rollback-one-host.sh", "diagnose.sh"):
            assert (DEPLOY / name).is_file(), name

    def _what_it_does(self, name):
        """The unit's directives, without its comments. Both files explain at length what they
        deliberately do NOT have, so a search over the whole text finds every word they are
        arguing against -- which would make the assertions below pass or fail on prose."""
        return "\n".join(line for line in self._unit(name).splitlines()
                         if not line.strip().startswith("#"))

    def test_the_worker_unit_declares_its_topology_and_opens_no_socket(self):
        does = self._what_it_does("worker-host.service")
        assert "--topology separate-worker-host" in does
        assert "--socket" not in does, "there is no local caller on a worker host to open one"
        assert "agentnode-bridge" not in does, "that group exists to share a socket"
        assert "--for-user" not in does, "it names a local uid, and the caller is elsewhere"

    def test_the_worker_unit_checks_before_it_starts(self):
        """And with the SAME flags: an ExecStartPre that validated a different configuration
        from the one ExecStart uses would be worse than none."""
        unit = self._unit("worker-host.service")

        def flags(prefix):
            body = unit.split(prefix, 1)[1].split("\n\n", 1)[0]
            return set(re.findall(r"--[a-z-]+ (\S+)", body.replace("\\\n", " ")))

        before, start = flags("ExecStartPre="), flags("ExecStart=")
        assert before == start, "preflight and serve disagree about the configuration"

    def test_the_control_plane_unit_has_no_worker_on_it(self):
        does = self._what_it_does("control-plane.service")
        assert "agentnode-worker" not in does
        assert "agentnode-bridge" not in does
        assert "worker.sock" not in does
        assert "User=agentnode-gateway" in does

    @pytest.mark.skipif(shutil.which("bash") is None, reason="no bash to parse the scripts with")
    def test_the_scripts_parse(self):
        for name in ("install-control-plane.sh", "install-worker-host.sh",
                     "upgrade-one-host.sh", "rollback-one-host.sh", "diagnose.sh"):
            done = subprocess.run([shutil.which("bash"), "-n", str(DEPLOY / name)],
                                  capture_output=True, text=True)
            assert done.returncode == 0, "%s: %s" % (name, done.stderr)

    def _commands_in(self, script):
        """The lines a shell would RUN: no comments, and nothing inside a here-document.

        The here-document matters. `install-worker-host.sh` PRINTS the firewall rule that has to
        exist, which is the opposite of applying it, and a plain search cannot tell the two
        apart. So the text that is only printed is removed before looking.
        """
        out, in_heredoc, closer = [], False, ""
        for line in script.read_text(encoding="utf-8").splitlines():
            if in_heredoc:
                if line.strip() == closer:
                    in_heredoc = False
                continue
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            found = re.search(r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?", stripped)
            if found:
                in_heredoc, closer = True, found.group(1)
                continue
            out.append(stripped)
        return out

    def test_nothing_here_opens_a_port(self):
        """Private addressing is configuration. Every one of these runs as root on a machine
        with a firewall, and none of them may reach for it: the rule is printed, for whoever is
        responsible for that network to apply."""
        for script in sorted(DEPLOY.glob("*.sh")):
            for line in self._commands_in(script):
                for opener in ("ufw allow", "firewall-cmd --add", "iptables -I", "iptables -A",
                               "nft add rule", "systemctl start firewalld"):
                    assert opener not in line, "%s: %s" % (script.name, line)

    def test_and_the_worker_install_does_print_the_rule_it_will_not_write(self):
        """The other half of the same property: not writing it is only honest if it is said."""
        said = (DEPLOY / "install-worker-host.sh").read_text(encoding="utf-8")
        assert "nft add rule" in said
        assert "FROM THE CONTROL PLANE'S PRIVATE" in said

    def test_the_readme_does_not_claim_the_measurement(self):
        said = (DEPLOY / "README.md").read_text(encoding="utf-8")
        assert "**Nothing here has been run across two machines.**" in said
        for claim in ("proves isolation", "isolated from each other",
                      "has been measured on two machines"):
            assert claim not in said
