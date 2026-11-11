"""P13 (task #28): a worker rollback across the refusal-code change needs EVIDENCE, not a flag.

`rollback-one-host.sh` refuses to roll a worker back to a build whose `ERRORS` table lacks
"no-lease", and used to let `--control-plane-already-rolled-back` waive that: an operator's word
that the control plane is already on the same generation. This file holds the replacement.

THE PROTOCOL, with nothing outside the deployment's own trust:

1. the WORKER issues a single-use challenge and keeps it in its own state: a random nonce, its own
   identity (from its certificate), the acceptance run it belongs to, and the generation and build
   of the keep it is about to go back to;
2. the CONTROL PLANE, after its own rollback was confirmed (the running process announced the kept
   build), signs a statement with the gateway's TLS key: the challenge's nonce, worker and run, the
   worker build it was asked about, the generation it now runs (read from its installed ERRORS
   table), the build it now runs, the gateway's identity, the time, and its certificate;
3. the WORKER verifies it offline against its own anchor, revocation list, floor and withdrawn
   identities, exactly as it judges a gateway on a connection, and only then waives the guard --
   consuming the challenge so that the same statement can never waive it twice.

Refused, each with its own code: no statement, no challenge, a bare assertion, a replay, a
statement for another challenge or run, another worker, another worker build, another generation,
a stale or premature statement, a signer that is not an accepted gateway of this CA, and a body
changed after it was signed.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from agentnode_sdk.pki import floor as floors
from agentnode_sdk.pki import identity as ids
from agentnode_sdk.pki.trust import TrustView
from tests.test_mtls_transport import World, _one_boot  # noqa: F401 - autouse: one boot throughout

SDK = Path(__file__).resolve().parents[1]
PROTOCOL_SOURCE = (SDK / "agentnode_sdk" / "worker" / "protocol.py").read_text(encoding="utf-8")
ROLLBACK = (SDK / "deploy" / "separate-worker-host" / "rollback-one-host.sh").read_text(
    encoding="utf-8")

KEEP_BUILD = "managed-0123456789ab+cdef01234567"
RUN = "crosshost-r4/M8"
T0 = 2_000_000_000.0          # challenge time; every check is given its clock explicitly


def _pre_fix_protocol() -> str:
    """The same file, with NO_LEASE taken out of the ERRORS tuple -- the pre-fix generation.
    The name stays DEFINED, as it always was, so a grep for "no-lease" cannot tell them apart."""
    tree = ast.parse(PROTOCOL_SOURCE)
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ERRORS":
            start, end = node.lineno, node.end_lineno
    lines = PROTOCOL_SOURCE.splitlines()
    block = "\n".join(lines[start - 1:end]).replace("NO_LEASE, ", "")
    assert "NO_LEASE" not in block
    return "\n".join(lines[:start - 1] + [block] + lines[end:])


@pytest.fixture
def rp():
    from agentnode_sdk.worker import rollback_proof

    return rollback_proof


class Pair:
    def __init__(self, root: Path, rp) -> None:
        self.rp = rp
        self.world = World(root)
        self.gateway = self.world.service("gateway", "g1")
        self.worker = self.world.service("worker", "w1")
        self.state = root / "worker-state"
        self.state.mkdir()
        self.generation = rp.generation_of(PROTOCOL_SOURCE)

    def trust(self, worker_folder=None):
        folder = worker_folder or self.worker
        me = ids._uri_sans(_cert(folder))[0]
        return TrustView.read(anchor=self.world.anchor, revocation_list=self.world.revocation_list,
                              floor=floors.path_for(self.world.floor_dir, "worker"),
                              role="worker", identity=me)

    def challenge(self, *, run=RUN, build=KEEP_BUILD, generation=None, at=T0, worker=None):
        return self.rp.issue_challenge(
            self.state, worker_certificate_pem=((worker or self.worker) / "cert.pem").read_bytes(),
            run_id=run, keep_generation=generation or self.generation, keep_build=build, now=at)

    def sign(self, challenge, *, by=None, generation=None, build="managed-feedfeedfeed+0000", at=T0 + 60):
        signer = by or self.gateway
        return self.rp.sign_statement(
            challenge, certificate_pem=(signer / "cert.pem").read_bytes(),
            key_pem=(signer / "key.pem").read_bytes(),
            control_generation=generation or self.generation, control_build=build, now=at)

    def verify(self, statement, *, build=KEEP_BUILD, generation=None, at=T0 + 120,
               worker=None, accept=("g1",)):
        folder = worker or self.worker
        return self.rp.verify_and_consume(
            self.state, statement, own_certificate_pem=(folder / "cert.pem").read_bytes(),
            keep_generation=generation or self.generation, keep_build=build,
            deployment=self.world.deployment, accept_gateways=accept,
            trust=self.trust(folder), now=at)


def _cert(folder: Path):
    from cryptography import x509

    return x509.load_pem_x509_certificate((folder / "cert.pem").read_bytes())


@pytest.fixture
def pair(tmp_path, rp):
    return Pair(tmp_path, rp)


def _refused(pair, code, call):
    with pytest.raises(pair.rp.ProofRefused) as caught:
        call()
    assert caught.value.code == code, (caught.value.code, str(caught.value))
    return caught.value


# ---- the one that passes ------------------------------------------------------------------------ #


def test_a_valid_statement_is_accepted_once_and_names_what_it_proves(pair):
    statement = pair.sign(pair.challenge())
    body = pair.verify(statement)
    assert body["worker"] == "agentnode://%s/worker/w1" % pair.world.deployment
    assert body["gateway"] == "agentnode://%s/gateway/g1" % pair.world.deployment
    assert body["run_id"] == RUN and body["worker_keep_build"] == KEEP_BUILD
    assert body["control_generation"] == pair.generation


def test_the_statement_survives_a_round_trip_through_a_file(pair, tmp_path):
    path = tmp_path / "statement.json"
    path.write_text(json.dumps(pair.sign(pair.challenge())), encoding="utf-8")
    assert pair.verify(json.loads(path.read_text(encoding="utf-8")))["run_id"] == RUN


# ---- missing proof ---------------------------------------------------------------------------- #


@pytest.mark.parametrize("nothing", [None, {}, ""])
def test_no_statement_is_refused(pair, nothing):
    pair.challenge()
    _refused(pair, pair.rp.NO_STATEMENT, lambda: pair.verify(nothing))


def test_a_statement_without_a_pending_challenge_is_refused(pair, tmp_path, rp):
    other = Pair(tmp_path / "elsewhere", rp)
    statement = other.sign(other.challenge())
    _refused(pair, rp.NO_CHALLENGE, lambda: pair.verify(statement))


# ---- the operator's word is not evidence ------------------------------------------------------- #


@pytest.mark.parametrize("assertion", [
    {"assert": "control-plane-already-rolled-back"},
    {"body": {"control_generation": "whatever"}, "signature": "", "certificate": ""},
    "--control-plane-already-rolled-back"])
def test_a_bare_assertion_is_not_a_statement(pair, assertion):
    pair.challenge()
    _refused(pair, pair.rp.MALFORMED, lambda: pair.verify(assertion))


def test_the_flag_no_longer_waives_the_guard():
    sets = [line.strip() for line in ROLLBACK.splitlines()
            if re.search(r"\bPAIRED=yes\b", line) and not line.strip().startswith("#")]
    assert sets, "the waiver is gone altogether -- read the script again"
    for line in sets:
        assert "--control-plane-already-rolled-back" not in line, line
    assert "cannot see the other machine, so it cannot check" not in ROLLBACK


def test_the_flag_is_refused_with_the_way_that_works():
    flag = ROLLBACK.index('"--control-plane-already-rolled-back")')
    branch = ROLLBACK[flag:ROLLBACK.index(";;", flag)]     # exactly this case branch, nothing after
    assert branch.split(")", 1)[1].lstrip().startswith("died"), branch
    assert "--control-plane-statement" in branch and "PAIRED" not in branch


# ---- replay ------------------------------------------------------------------------------------ #


def test_the_same_statement_cannot_waive_twice(pair):
    statement = pair.sign(pair.challenge())
    pair.verify(statement)
    _refused(pair, pair.rp.REPLAYED, lambda: pair.verify(statement))


def test_a_statement_for_an_earlier_challenge_is_refused(pair):
    earlier = pair.sign(pair.challenge())
    pair.challenge(at=T0 + 10)
    _refused(pair, pair.rp.WRONG_CHALLENGE, lambda: pair.verify(earlier, at=T0 + 120))


def test_a_statement_for_another_acceptance_run_is_refused(pair):
    challenge = dict(pair.challenge(run=RUN))
    challenge["run_id"] = "crosshost-r3/M8"
    _refused(pair, pair.rp.WRONG_RUN, lambda: pair.verify(pair.sign(challenge)))


# ---- old generation, old build ----------------------------------------------------------------- #


def test_the_generation_is_read_from_the_errors_table_not_from_a_grep(rp):
    assert rp.generation_of(PROTOCOL_SOURCE) != rp.generation_of(_pre_fix_protocol())
    assert rp.generation_of(PROTOCOL_SOURCE) == rp.generation_of(PROTOCOL_SOURCE + "\n# x\n")
    with pytest.raises(rp.ProofRefused):
        rp.generation_of("NO_LEASE = 'no-lease'\n")


def test_a_control_plane_on_another_generation_is_refused(pair, rp):
    old = rp.generation_of(_pre_fix_protocol())
    statement = pair.sign(pair.challenge(generation=old), generation=pair.generation)
    _refused(pair, rp.WRONG_GENERATION, lambda: pair.verify(statement, generation=old))


def test_a_statement_about_another_worker_build_is_refused(pair):
    statement = pair.sign(pair.challenge(build=KEEP_BUILD))
    _refused(pair, pair.rp.WRONG_BUILD,
             lambda: pair.verify(statement, build="managed-999999999999+999999999999"))


def test_a_keep_of_another_generation_than_the_challenge_is_refused(pair, rp):
    statement = pair.sign(pair.challenge())
    _refused(pair, rp.WRONG_GENERATION,
             lambda: pair.verify(statement, generation=rp.generation_of(_pre_fix_protocol())))


def test_a_stale_statement_is_refused(pair):
    statement = pair.sign(pair.challenge(), at=T0 + 60)
    _refused(pair, pair.rp.STALE,
             lambda: pair.verify(statement, at=T0 + 60 + pair.rp.MAX_AGE_SECONDS + 1))


def test_a_statement_signed_before_its_challenge_is_refused(pair):
    statement = pair.sign(pair.challenge(at=T0), at=T0 - pair.rp.CLOCK_SKEW_SECONDS - 1)
    _refused(pair, pair.rp.STALE, lambda: pair.verify(statement))


# ---- wrong worker identity --------------------------------------------------------------------- #


def test_a_statement_made_out_to_another_worker_is_refused(pair):
    other = pair.world.service("worker", "w2")
    statement = pair.sign(pair.challenge(worker=other))
    _refused(pair, pair.rp.WRONG_WORKER, lambda: pair.verify(statement))


# ---- who signed it ----------------------------------------------------------------------------- #


def test_a_statement_signed_by_a_worker_is_refused(pair):
    other = pair.world.service("worker", "w2")
    statement = pair.sign(pair.challenge(), by=other)
    _refused(pair, pair.rp.BAD_SIGNER, lambda: pair.verify(statement))


def test_a_statement_signed_under_a_foreign_ca_is_refused(pair):
    key, cert, _ = pair.world.foreign_ca()
    forged = pair.world.forge("gw-foreign", uri="agentnode://%s/gateway/g1" % pair.world.deployment,
                              usages=[ids.CLIENT_AUTH], ca=(key, cert))
    statement = pair.sign(pair.challenge(), by=forged)
    _refused(pair, pair.rp.BAD_SIGNER, lambda: pair.verify(statement))


def test_a_gateway_this_worker_does_not_accept_is_refused(pair):
    other = pair.world.service("gateway", "g2")
    statement = pair.sign(pair.challenge(), by=other)
    _refused(pair, pair.rp.BAD_SIGNER, lambda: pair.verify(statement))


@pytest.mark.parametrize("field,value", [
    ("control_generation", "errors:" + "0" * 64), ("worker_keep_build", "managed-x+y"),
    ("issued_at", T0 + 90), ("control_build", "managed-other+other")])
def test_a_body_changed_after_signing_is_refused(pair, field, value):
    statement = pair.sign(pair.challenge())
    statement["body"][field] = value
    _refused(pair, pair.rp.BAD_SIGNATURE, lambda: pair.verify(statement))


def test_a_refused_statement_does_not_use_up_the_challenge(pair):
    challenge = pair.challenge()
    tampered = pair.sign(challenge)
    tampered["body"]["control_build"] = "managed-other+other"
    _refused(pair, pair.rp.BAD_SIGNATURE, lambda: pair.verify(tampered))
    assert pair.verify(pair.sign(challenge))["run_id"] == RUN


# ---- the script uses it ------------------------------------------------------------------------- #


def test_the_worker_waives_only_after_the_statement_verified():
    verify = ROLLBACK.index("rollback_proof verify")
    waive = [m.start() for m in re.finditer(r"\bPAIRED=yes\b", ROLLBACK)]
    stop = ROLLBACK.index('systemctl stop "$UNIT".service')
    assert waive and all(verify < w for w in waive), "PAIRED is set before the statement is checked"
    assert verify < stop, "the statement is checked after the worker was already stopped"


def test_the_control_plane_signs_only_after_its_rollback_was_confirmed():
    confirmed = ROLLBACK.index('[ "$SAID_IT_IS" = "$WAS_BUILD_ID" ]')
    sign = ROLLBACK.index("rollback_proof sign")
    assert confirmed < sign
    assert ROLLBACK.index("rollback_proof challenge") > 0


# ---- the command line the script calls ---------------------------------------------------------- #


def _keep(root: Path, protocol_source: str, build: str) -> Path:
    import io
    import tarfile

    keep = root / "keep"
    keep.mkdir()
    data = protocol_source.encode("utf-8")
    with tarfile.open(keep / "installed-package.tar", "w") as tar:
        info = tarfile.TarInfo("agentnode_sdk/worker/protocol.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    (keep / "build-id.txt").write_text(build + "\n", encoding="utf-8")
    return keep


def test_the_command_line_runs_challenge_sign_verify_and_refuses_a_second_use(pair, tmp_path, capsys):
    rp = pair.rp
    world = pair.world
    env = tmp_path / "worker.env"
    env.write_text("\n".join([
        "AGENTNODE_DEPLOYMENT=%s" % world.deployment, "AGENTNODE_GATEWAY_INSTANCE=g1",
        "AGENTNODE_TLS_DIR=%s" % pair.worker, "AGENTNODE_TRUST=%s" % world.anchor,
        "AGENTNODE_REVOCATION_LIST=%s" % world.revocation_list,
        "AGENTNODE_TOMBSTONES=%s" % (world.root / "trust" / "revoked-identities.json"),
        "AGENTNODE_FLOOR=%s" % floors.path_for(world.floor_dir, "worker")]), encoding="utf-8")
    keep = _keep(tmp_path, PROTOCOL_SOURCE, KEEP_BUILD)
    site = tmp_path / "site" / "agentnode_sdk" / "worker"
    site.mkdir(parents=True)
    (site / "protocol.py").write_text(PROTOCOL_SOURCE, encoding="utf-8")
    challenge, statement = tmp_path / "challenge.json", tmp_path / "statement.json"
    common = ["--env", str(env), "--state", str(pair.state), "--keep", str(keep)]

    assert rp.main(["challenge", *common, "--run-id", RUN, "--out", str(challenge)]) == 0
    assert rp.main(["sign", "--challenge", str(challenge),
                    "--certificate", str(pair.gateway / "cert.pem"),
                    "--key", str(pair.gateway / "key.pem"), "--site", str(tmp_path / "site"),
                    "--build", "managed-feedfeedfeed+0000", "--out", str(statement)]) == 0
    assert rp.main(["verify", *common, "--statement", str(statement)]) == 0
    assert "VERIFIED" in capsys.readouterr().out
    assert rp.main(["verify", *common, "--statement", str(statement)]) == 1
    assert "REFUSED replayed" in capsys.readouterr().out
    assert rp.main(["verify", *common, "--statement", str(tmp_path / "absent.json")]) == 1
    assert "REFUSED no-statement" in capsys.readouterr().out
    out = (challenge.read_text() + statement.read_text())
    assert "PRIVATE KEY" not in out, "key material ended up in a file that travels"


def test_the_command_line_refuses_a_control_plane_on_the_pre_fix_generation(pair, tmp_path, capsys):
    rp = pair.rp
    world = pair.world
    env = tmp_path / "worker.env"
    env.write_text("\n".join([
        "AGENTNODE_DEPLOYMENT=%s" % world.deployment, "AGENTNODE_GATEWAY_INSTANCE=g1",
        "AGENTNODE_TLS_DIR=%s" % pair.worker, "AGENTNODE_TRUST=%s" % world.anchor,
        "AGENTNODE_REVOCATION_LIST=%s" % world.revocation_list,
        "AGENTNODE_TOMBSTONES=%s" % (world.root / "trust" / "revoked-identities.json"),
        "AGENTNODE_FLOOR=%s" % floors.path_for(world.floor_dir, "worker")]), encoding="utf-8")
    keep = _keep(tmp_path, _pre_fix_protocol(), KEEP_BUILD)
    site = tmp_path / "site" / "agentnode_sdk" / "worker"
    site.mkdir(parents=True)
    (site / "protocol.py").write_text(PROTOCOL_SOURCE, encoding="utf-8")   # gateway NOT rolled back
    challenge, statement = tmp_path / "challenge.json", tmp_path / "statement.json"
    common = ["--env", str(env), "--state", str(pair.state), "--keep", str(keep)]
    assert rp.main(["challenge", *common, "--run-id", RUN, "--out", str(challenge)]) == 0
    assert rp.main(["sign", "--challenge", str(challenge),
                    "--certificate", str(pair.gateway / "cert.pem"),
                    "--key", str(pair.gateway / "key.pem"), "--site", str(tmp_path / "site"),
                    "--build", "managed-feedfeedfeed+0000", "--out", str(statement)]) == 0
    capsys.readouterr()
    assert rp.main(["verify", *common, "--statement", str(statement)]) == 1
    assert "REFUSED wrong-generation" in capsys.readouterr().out
