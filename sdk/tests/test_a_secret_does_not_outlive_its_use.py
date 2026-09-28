"""Two disclosure paths that were documented rather than closed, and are now closed.

`remote-worker-r1` R11 asks that no secret reaches a log line, an error message, an evidence file
or a usage line -- including on the FAILURE paths, which is where this usually goes wrong. Two
things in this arc were named as open and left that way, and an independent review was right to
treat naming them as not closing them:

  1. ENROLMENT LEFT ITS SECRET ON DISK. `<tls_dir>/secret` and `<tls_dir>/request.json` -- the
     second with the one-shot secret IN CLEAR -- stayed there indefinitely after the certificate
     arrived. 0400 and 0600 is a permission, not a lifetime.
  2. THE WORKER PUT AN ARBITRARY STRING ON THE WIRE. The `internal` refusal carried
     `str(exception)` from anywhere in that process, across to the gateway and on to a client,
     without passing the gateway's scrubber -- which the worker deliberately cannot import,
     because it is on the other side of the role boundary.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations



from agentnode_sdk.pki import enrolment as E
from tests.test_mtls_transport import _one_boot, world  # noqa: F401  (fixtures)


class TestTheResiduesGo:

    def _a_pair(self, folder):
        (folder / "cert.pem").write_text("cert", encoding="ascii")
        (folder / "key.pem").write_text("key", encoding="ascii")

    def test_both_are_removed_once_the_pair_is_in_place(self, tmp_path):
        self._a_pair(tmp_path)
        (tmp_path / "secret").write_text("deadbeef" * 8, encoding="ascii")
        (tmp_path / "request.json").write_text('{"secret": "deadbeef"}', encoding="ascii")

        assert sorted(E.forget_the_enrolment(tmp_path)) == ["request.json", "secret"]
        assert not (tmp_path / "secret").exists()
        assert not (tmp_path / "request.json").exists()

    def test_and_nothing_is_touched_before_it_is(self, tmp_path):
        """A directory mid-enrolment still needs both. Removing them there would break the
        enrolment this is supposed to be cleaning up after."""
        (tmp_path / "secret").write_text("s", encoding="ascii")
        (tmp_path / "request.json").write_text("{}", encoding="ascii")

        assert E.forget_the_enrolment(tmp_path) == []
        assert (tmp_path / "secret").exists()

    def test_it_removes_only_those_two_names(self, tmp_path):
        self._a_pair(tmp_path)
        (tmp_path / "cert.pem.next").write_text("renewal", encoding="ascii")
        (tmp_path / "notes.txt").write_text("mine", encoding="ascii")

        E.forget_the_enrolment(tmp_path)
        assert (tmp_path / "cert.pem.next").exists() and (tmp_path / "notes.txt").exists()

    def test_a_missing_directory_is_not_an_error(self, tmp_path):
        assert E.forget_the_enrolment(tmp_path / "nowhere") == []


class TestTheIssuerDoesItOnDelivery:

    def test_enrolling_a_service_leaves_nothing_behind(self, world):  # noqa: F811  (a pytest fixture, imported)
        """The whole path, through the real issuer: add, request, enroll, deliver."""
        folder = world.service("worker", "w1")

        assert (folder / "cert.pem").is_file(), "the certificate arrived"
        assert not (folder / "secret").exists(), "and the one-shot secret did not survive it"
        assert not (folder / "request.json").exists(), (
            "nor the request, which carries that secret in clear")

    def test_and_the_secret_is_not_in_any_file_that_is_left(self, world):  # noqa: F811  (a pytest fixture, imported)
        """Stated as an absence over the whole directory rather than as two filenames, so that
        a third place to leave it would fail this too."""
        folder = world.service("worker", "w2")
        for path in folder.iterdir():
            body = path.read_bytes()
            assert b'"secret"' not in body, "%s still carries a secret field" % path.name


class TestAnEnrolmentSecretCanBeToldFromAnIdentifier:
    """The third disclosure path, and the one that was left open once.

    The secret was 64 bare lowercase hex characters -- the same shape as a digest, a run id and a
    device id. `gateway/redaction.py` deliberately leaves bare hex alone so those three stay
    readable in a log, so a secret that reached a log line stayed in it. Widening the hex rule
    would cost every identifier; the secret is GENERATED here, so its shape is ours to choose.
    """

    def test_a_minted_secret_carries_the_prefix(self):
        made = E.mint_a_secret()
        assert made.startswith(E.SECRET_PREFIX)
        assert len(made) > len(E.SECRET_PREFIX) + 32

    def test_two_are_not_the_same(self):
        assert E.mint_a_secret() != E.mint_a_secret()

    def test_the_issuer_mints_them_that_way(self, world):  # noqa: F811  (a pytest fixture, imported)
        folder = world.root / "staging"
        folder.mkdir()
        world.issuer.add("worker", "w-prefix", secret_at=folder / "secret",
                         deliver_to=folder / "cert.pem")
        assert (folder / "secret").read_text(encoding="ascii").startswith(E.SECRET_PREFIX)

    def test_and_the_scrubber_takes_it_out_of_a_log_line(self):
        from agentnode_sdk.gateway import redaction

        made = E.mint_a_secret()
        said = redaction.scrub("enrolling w1 failed with %s while reading it" % made)
        assert made not in said
        assert redaction.REDACTED in said

    def test_while_a_digest_a_run_id_and_a_device_id_all_survive(self):
        """The reason the scrubber leaves bare hex alone. An audit whose run ids have been
        replaced by [redacted] answers nothing, and somebody would then turn it off."""
        from agentnode_sdk.gateway import redaction

        digest, run_id, device = "a" * 64, "b" * 32, "c" * 16
        said = redaction.scrub("run %s digest %s device %s" % (run_id, digest, device))
        assert digest in said and run_id in said and device in said

    def test_a_structured_field_holding_one_is_recognised_too(self):
        from agentnode_sdk.gateway import redaction

        assert redaction.looks_like_a_secret(E.mint_a_secret())
        assert not redaction.looks_like_a_secret("a" * 64), "a digest is not a secret"

    def test_the_two_halves_are_held_together(self):
        """The prefix exists FOR the scrubber's rule. Changing one without the other silently
        reopens the hole, so this compares them rather than trusting the comment that says so."""
        import inspect

        from agentnode_sdk.gateway import redaction

        assert E.SECRET_PREFIX.rstrip(".") in inspect.getsource(redaction)


class TestWhatIsPromisedAboutCleanup:
    """R8: the records keep CLEANED, CLEANUP_PENDING and CLEANUP_UNPROVEN apart, and the two
    messages a human reads used to promise the first unconditionally."""

    def test_the_client_message_does_not_promise_more_than_the_record(self):
        import inspect

        from agentnode_sdk.cli import remote_commands

        said = inspect.getsource(remote_commands._explain_protection)
        assert "will be cleaned up afterwards" not in said
        assert "whether that was confirmed" in said

    def test_and_neither_does_the_operator_message(self):
        import inspect

        from agentnode_sdk.cli import gateway_commands

        said = inspect.getsource(gateway_commands._say_protected)
        assert "is cleaned up afterwards" not in said
        assert "whether its cleanup was confirmed" in said


class TestTheWorkerSaysWhatBrokeWithoutSayingWhatItHeld:

    def test_only_the_exception_class_crosses(self):
        import inspect

        from agentnode_sdk.worker import service

        source = inspect.getsource(service.Bench.converse)
        internal = source[source.index("wire.INTERNAL"):]
        assert "type(exc).__name__" in internal
        assert "str(exc)" not in internal.split("finally")[0], (
            "an arbitrary string from this process must not cross to a client")

    def test_the_operator_of_this_machine_still_gets_the_whole_thing(self):
        import inspect

        from agentnode_sdk.worker import service

        source = inspect.getsource(service.Bench.converse)
        assert "internal error while answering" in source

    def test_the_refusal_a_caller_sees(self, tmp_path):
        """Behavioural: the frame that comes back names the class and carries nothing else."""
        import socket
        import threading

        from agentnode_sdk.worker import protocol as wire
        from agentnode_sdk.worker.service import Bench

        key = wire.read_key(_a_key_file(tmp_path))

        class Breaks:
            def __getattr__(self, name):
                raise RuntimeError("the secret is hunter2 and the path is /etc/agentnode/ca")

        bench = Bench(Breaks(), "", key, None, remembers_at=str(tmp_path / "floor.json"))
        client, server = socket.socketpair()
        answering = threading.Thread(target=bench.converse, args=(server,), daemon=True)
        answering.start()
        try:
            client.sendall(wire.seal(wire.request("describe", {}, deadline=__import__("time").time() + 30.0), key))
            with client.makefile("rb") as stream:
                frame = wire.read_frame(stream, key)
        finally:
            answering.join(timeout=5)
            client.close()

        said = str(frame)
        assert frame.get("error") == wire.INTERNAL, said
        assert "hunter2" not in said and "/etc/agentnode/ca" not in said
        assert "RuntimeError" in said, "the class name is what a caller gets"


def _a_key_file(tmp_path):
    from agentnode_sdk.worker import protocol as wire

    path = tmp_path / "worker.key"
    made = wire.new_key()
    path.write_bytes(made if isinstance(made, bytes) else made.encode("ascii"))
    return str(path)
