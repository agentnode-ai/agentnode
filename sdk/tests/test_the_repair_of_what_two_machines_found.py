"""The six defects the cross-host run of 2026-09-29 found, and whether they are gone.

Profile `remote-worker-cross-host-repair-r1` (sha256 97c4c456...), criteria D1-D9. Every test
here names the defect it is about, because a repair whose test does not say what broke is a test
nobody can judge later.

WHAT THESE TESTS ARE NOT. None of them establishes that two machines work. They establish that
the things which stopped two machines from being tried are fixed. The measurement itself needs
the hardware and is a separate run.
"""
from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

from agentnode_sdk.pki import floor as floors
from agentnode_sdk.pki import localfloor

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "separate-worker-host"
BUILDER = DEPLOY / "build_artefacts.py"


# =============================================================== D6: the worker's own floor

class TestAWorkerCanEstablishItsOwnFloor:
    """Defect 6, the one that stopped the run: a worker refuses every connection without a
    floor, a floor must be made on the machine it is for, and making one asked for the CA's
    private key -- the single file a worker must never hold."""

    def test_the_floor_is_made_from_the_public_anchor_and_the_worker_s_own_certificate(
            self, tmp_path, monkeypatch):
        world = _a_deployment(tmp_path)
        monkeypatch.setattr(floors, "_boot", lambda: "this-boot")

        # Exactly what a worker host has: its certificate, the public anchor. No ca.key, no
        # inventory -- and the test proves that by deleting them before the call.
        assert not (world["worker_dir"] / "ca.key").exists()
        path = localfloor.init(tmp_path / "floor", "worker",
                               certificate=world["worker_cert"], anchor=world["anchor"])
        state = floors.parse(Path(path).read_bytes())
        assert state.identity == world["worker_uri"]
        assert state.role == "worker"
        assert state.floor == pytest.approx(localfloor.anchor_not_before(world["anchor"]))

    def test_and_it_can_be_kept_without_the_issuer(self, tmp_path, monkeypatch):
        world = _a_deployment(tmp_path)
        monkeypatch.setattr(floors, "_boot", lambda: "this-boot")
        localfloor.init(tmp_path / "floor", "worker",
                        certificate=world["worker_cert"], anchor=world["anchor"])
        report = localfloor.settle_and_advance(tmp_path / "floor", "worker",
                                               certificate=world["worker_cert"],
                                               anchor=world["anchor"])
        assert report["written"].startswith("generation ")
        assert report["identity"] == world["worker_uri"]
        # And the service can now use it -- which is the whole point of the exercise.
        assert floors.read(floors.path_for(tmp_path / "floor", "worker"), "worker",
                           world["worker_uri"])

    def test_a_floor_carried_from_the_other_machine_is_refused(self, tmp_path, monkeypatch):
        """The negative half D6 asks for. Before the identity field this file was
        indistinguishable from the worker's own: right role, right format, and -- had it been
        written in this boot -- a perfectly good age."""
        world = _a_deployment(tmp_path)
        monkeypatch.setattr(floors, "_boot", lambda: "this-boot")

        # A floor belonging to the GATEWAY's identity, in the worker's role slot -- and written
        # NOW, in this boot. The age has to be irreproachable or the refusal below could be the
        # floor being stale rather than the floor being somebody else's. The first version of
        # this test got that wrong and the counter-check caught it: with the identity check
        # removed it still went red, on `time-floor-too-old`, which proves nothing at all.
        now = floors._monotonic()
        carried = floors.initial("worker", 1.0, world["gateway_uri"])
        carried = floors.advance(carried, system_now=2.0, monotonic_now=now, boot="this-boot",
                                 list_this_update=None)
        where = floors.path_for(tmp_path / "floor", "worker")
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_bytes(carried.to_bytes())

        # THE CONTROL: judged as the identity it actually belongs to, this very file is usable.
        # So age, boot, role and format are all fine, and only one thing can refuse it below.
        assert floors.read(where, "worker", world["gateway_uri"]) == carried.floor

        with pytest.raises(floors.FloorUnusable) as refused:
            floors.read(where, "worker", world["worker_uri"])
        assert refused.value.check == floors.OTHER_IDENTITY

    def test_setting_a_floor_up_twice_does_not_grant_a_second_tolerance(self, tmp_path,
                                                                        monkeypatch):
        world = _a_deployment(tmp_path)
        monkeypatch.setattr(floors, "_boot", lambda: "this-boot")
        localfloor.init(tmp_path / "floor", "worker",
                        certificate=world["worker_cert"], anchor=world["anchor"])
        with pytest.raises(localfloor.FloorRefused) as refused:
            localfloor.init(tmp_path / "floor", "worker",
                            certificate=world["worker_cert"], anchor=world["anchor"])
        assert "second tolerance" in str(refused.value)

    def test_an_upgrade_carries_the_counters_instead_of_starting_them_again(self, tmp_path,
                                                                           monkeypatch):
        """Format 2 names an identity and format 1 does not. Re-initialising would be the easy
        migration and the wrong one: it starts both counters at zero, which hands out a fresh
        tolerance -- the one thing the counters exist to prevent."""
        world = _a_deployment(tmp_path)
        monkeypatch.setattr(floors, "_boot", lambda: "this-boot")
        spent = floors.FloorState(role="worker", identity=world["worker_uri"], generation=9,
                                  floor=1000.0, boot_id="old", anchor_monotonic=1.0,
                                  elapsed_at_boot_start=0.0, elapsed_total=500.0,
                                  granted_total=400.0, tolerance_s=600.0, max_age_s=900.0,
                                  written_monotonic=5.0, written_at=1000.0)
        old = spent.to_bytes().replace(b'"format": 2', b'"format": 1')
        old = old.replace(b'"identity": "%s",\n ' % world["worker_uri"].encode(), b"")

        where = floors.path_for(tmp_path / "floor", "worker")
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_bytes(old)
        localfloor.adopt(tmp_path / "floor", "worker", certificate=world["worker_cert"])

        after = floors.parse(where.read_bytes())
        assert after.identity == world["worker_uri"]
        assert after.granted_total == 400.0, "the spent tolerance was given back"
        assert after.elapsed_total == 500.0


# =============================================================== D2: an artefact that installs

class TestAnArtefactCanInstallItself:
    """Defect 1 and 2: every .sh shipped at mode 666 from a Windows build, and six path
    references that resolved only in the repository."""

    def _built(self, tmp_path):
        wheel = tmp_path / "agentnode_sdk-0.0.0-py3-none-any.whl"
        wheel.write_bytes(b"not a real wheel, and nothing here opens it")
        out = tmp_path / "out"
        done = subprocess.run([sys.executable, str(BUILDER), "--wheel", str(wheel),
                               "--out", str(out), "--version", "0.0.0"],
                              capture_output=True, text=True)
        return done, out

    def test_every_script_is_executable_whatever_built_it(self, tmp_path):
        done, out = self._built(tmp_path)
        assert done.returncode == 0, done.stdout + done.stderr
        for artefact in sorted(out.glob("*.tar.gz")):
            with tarfile.open(artefact) as tar:
                scripts = [m for m in tar.getmembers() if m.name.endswith(".sh")]
                assert scripts, artefact.name
                for member in scripts:
                    assert member.mode & 0o111, "%s in %s is not executable" % (member.name,
                                                                                artefact.name)

    def test_every_unit_a_script_asks_for_is_in_the_artefact_it_ships_with(self, tmp_path):
        done, out = self._built(tmp_path)
        assert done.returncode == 0, done.stdout + done.stderr
        for artefact in sorted(out.glob("*.tar.gz")):
            with tarfile.open(artefact) as tar:
                root = tar.getnames()[0].split("/")[0]
                names = set(tar.getnames())
                text = tar.extractfile(root + "/install.sh").read().decode()
                sys.path.insert(0, str(DEPLOY))
                import build_artefacts

                for alternatives in build_artefacts._units_a_script_needs(text):
                    assert any("%s/unit/%s" % (root, name) in names for name in alternatives), \
                        "%s: none of %s is in %s" % (artefact.name, alternatives, root)

    def test_the_worker_is_never_given_the_issuer_s_timer(self, tmp_path):
        done, out = self._built(tmp_path)
        assert done.returncode == 0, done.stdout + done.stderr
        with tarfile.open(next(out.glob("*worker*.tar.gz"))) as tar:
            assert not [n for n in tar.getnames() if "pki-tick" in n], \
                "the issuer's root run reads the inventory and publishes the revocation list; " \
                "a worker can do neither, and being given the timer is why it had no floor"
            assert [n for n in tar.getnames() if "floor-advance" in n]


# ------------------------------------------------------------------ the counter-check for D2

def test_the_build_refuses_an_artefact_whose_script_asks_for_a_missing_unit(tmp_path):
    """The check above is worth nothing if it cannot fail. This breaks exactly one thing -- a
    script asking for a unit the artefact does not carry -- and requires the BUILD to stop."""
    staged = tmp_path / "deploy"
    staged.mkdir()
    for name in sorted(p.name for p in DEPLOY.iterdir() if p.is_file()):
        (staged / name).write_bytes((DEPLOY / name).read_bytes())
    (staged.parent / "agentnode-pki-tick.service").write_text("[Unit]\n", encoding="utf-8")
    (staged.parent / "agentnode-pki-tick.timer").write_text("[Timer]\n", encoding="utf-8")

    broken = staged / "install-worker-host.sh"
    text = broken.read_text(encoding="utf-8")
    assert "unit_file agentnode-floor-advance.service" in text, "the anchor for this mutation moved"
    broken.write_text(text.replace("unit_file agentnode-floor-advance.service",
                                   "unit_file agentnode-nowhere.service"), encoding="utf-8")

    wheel = tmp_path / "agentnode_sdk-0.0.0-py3-none-any.whl"
    wheel.write_bytes(b"not a real wheel")
    done = subprocess.run([sys.executable, str(staged / "build_artefacts.py"),
                           "--wheel", str(wheel), "--out", str(tmp_path / "out"),
                           "--version", "0.0.0"], capture_output=True, text=True)
    assert done.returncode != 0, "the build produced an artefact that cannot install itself"
    assert "agentnode-nowhere.service" in (done.stdout + done.stderr)


# =============================================================== D1/D5: the documented order

class TestWhatTheDocumentationSays:

    def _readme(self) -> str:
        return (DEPLOY / "README.md").read_text(encoding="utf-8")

    def test_it_names_the_scripts_the_artefact_actually_contains(self):
        """Defect 1: it told the operator to run `./install-control-plane.sh`, and the artefact
        holds `install.sh`. Following it literally could not work."""
        readme = self._readme()
        assert "`install.sh`" in readme
        assert "./install-control-plane.sh" not in readme
        assert "./install-worker-host.sh" not in readme

    def test_it_names_the_variable_the_script_refuses_to_run_without(self):
        """Defect 1, the other half: AGENTNODE_DEPLOYMENT is required and was in no example."""
        readme = self._readme()
        for block in readme.split("```sh")[1:]:
            body = block.split("```")[0]
            if "install.sh" in body and "--verify" not in body:
                assert "AGENTNODE_DEPLOYMENT" in body, body

    def test_it_documents_the_two_phases_rather_than_a_circle(self):
        """Defect 5: install the control plane first because it is the CA -- but its last gate
        needed the worker to be answering already. Neither side could finish first."""
        readme = self._readme()
        assert "--verify" in readme
        assert "install.sh --verify" in readme

    def test_it_does_not_ask_the_worker_to_run_the_issuer(self):
        """Defect 6's documentation half: `sudo agentnode pki tick  # ON THE WORKER HOST`."""
        readme = self._readme()
        worker_tick = [line for line in readme.splitlines()
                       if "pki tick" in line and "WORKER" in line.upper()
                       and "not the issuer" not in line.lower()]
        assert not worker_tick, worker_tick


# =============================================================== D4: the ownership model

def test_the_installer_runs_the_gateway_s_own_check_as_the_gateway():
    """Defect 4: `gateway doctor` was run as root against a directory the same script had just
    made 0700 to agentnode-gateway. `securedir._judge` requires st_uid == getuid(), so the
    installer's final gate refused by construction, on every host."""
    script = (DEPLOY / "install-control-plane.sh").read_text(encoding="utf-8")
    doctor = [line for line in script.splitlines() if "gateway doctor" in line]
    assert doctor, "the check is gone entirely, which is not the repair either"
    for line in doctor:
        assert "runuser -u" in line, line


# =============================================================== D3: resuming a partial install

def test_the_floor_step_is_guarded_like_every_other_step():
    """Defect 3: the script's header promises every step checks before it acts. `pki floor init`
    was the one that did not, so a partially completed install could never be resumed."""
    for name in ("install-control-plane.sh", "install-worker-host.sh"):
        script = (DEPLOY / name).read_text(encoding="utf-8")
        lines = script.splitlines()
        seen = 0
        for number, line in enumerate(lines):
            # The CALL, not a comment that mentions it. Both scripts explain in prose why the
            # floor is made the way it is, and the first version of this test matched the prose.
            if "pki floor init" in line and not line.strip().startswith("#"):
                seen += 1
                # The guard must be the nearest piece of CODE above the call, not merely
                # somewhere above it: comments in between are fine, another statement is not.
                above = [earlier.strip() for earlier in lines[:number]
                         if earlier.strip() and not earlier.strip().startswith("#")]
                assert above and above[-1].startswith("if [ ! -f"), \
                    "%s: floor init at line %d is not guarded; the statement above it is %r" \
                    % (name, number + 1, above[-1] if above else None)
        assert seen == 1, "%s: expected exactly one `pki floor init` call, found %d" % (name,
                                                                                        seen)


# ====================================================================== the shared builder

def _a_deployment(tmp_path) -> dict:
    """A real issuer, a real worker certificate, and the public anchor -- nothing simulated.

    The point of several tests here is that a worker can do something with ONLY its certificate
    and the anchor, so those have to be real files a real issuer produced.
    """
    import json

    from agentnode_sdk.pki.issuer import Issuer

    root = Path(tempfile.mkdtemp(dir=tmp_path))
    issuer = Issuer(root / "ca", root / "trust")
    issuer.initialise("repair")
    worker_dir = root / "worker"
    worker_dir.mkdir()
    issuer.add("worker", "w1", secret_at=worker_dir / "secret",
               deliver_to=worker_dir / "cert.pem")
    from tests.test_mtls_transport import make_request

    request = json.loads(make_request(worker_dir, (worker_dir / "secret").read_text()).read_text())
    issuer.enroll(request["csr"].encode("ascii"), request["secret"])
    return {"anchor": root / "trust" / "ca.pem",
            "worker_cert": worker_dir / "cert.pem",
            "worker_dir": worker_dir,
            "worker_uri": "agentnode://repair/worker/w1",
            "gateway_uri": "agentnode://repair/gateway/g1"}
