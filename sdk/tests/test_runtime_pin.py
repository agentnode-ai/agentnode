"""The service refuses to run as something nobody tested.

The alpha ran its gateway on python 3.14 while CI tested 3.10-3.12, and five install-transaction
tests fail there. Nothing noticed, because nothing was looking. These are the tests for the thing
that looks.

Three mistakes, three refusals, each naming itself -- an operator told only "mismatch" has to
guess between an interpreter, an artefact and a commit.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from agentnode_sdk.gateway import runtime_pin as rp

COMMIT = "04a528ed91015500264335fcead6d5600677775a"
ARTEFACT = "952e41bccbf6ae63491a1fd4714b43258c0a6b6500b8579f4197fa8b2c915059"


@pytest.fixture()
def pinned(tmp_path):
    rp.write_pin(tmp_path, python_version="3.12.13", artefact_sha256=ARTEFACT, commit=COMMIT)
    return tmp_path


class TestWhatCountsAsTheTestedInterpreter:

    @pytest.mark.parametrize("version", ["3.12", "3.12.0", "3.12.13"])
    def test_the_tested_family_is_supported(self, version):
        assert rp.is_supported(version)

    @pytest.mark.parametrize("version", ["3.14", "3.14.6", "3.13.1", "3.11.9", "3.10.0"])
    def test_and_nothing_else_is(self, version):
        assert not rp.is_supported(version)

    def test_the_prefix_trap_that_this_module_exists_because_of(self):
        """`"3.14".startswith("3.1")` is TRUE. A string comparison would have called the
        untested interpreter supported, which is the exact shape of the original defect -- so
        the comparison is numeric and this test is what keeps it that way."""
        assert "3.14".startswith("3.1")
        assert not rp.is_supported("3.14")
        assert not rp.is_supported("3.1")

    def test_a_version_that_is_not_a_version_is_not_supported(self):
        for nonsense in ("", "three point twelve", "3", "3.x", None):
            assert not rp.is_supported(nonsense or "")


class TestTheBuildIdentity:

    def test_it_is_made_of_the_commit_and_the_artefact(self):
        said = rp.build_id(COMMIT, ARTEFACT)
        assert COMMIT[:12] in said and ARTEFACT[:12] in said

    def test_and_two_builds_of_one_version_are_different(self):
        """The whole point. `0.24.1` was installed before and after a deployment that changed
        the code; an identity that cannot tell those apart is not an identity."""
        assert rp.build_id(COMMIT, ARTEFACT) != rp.build_id(COMMIT, "f" * 64)
        assert rp.build_id(COMMIT, ARTEFACT) != rp.build_id("a" * 40, ARTEFACT)

    def test_and_it_does_not_contain_a_version_number(self):
        assert "0.24" not in rp.build_id(COMMIT, ARTEFACT)


class TestAbsentIsNotUnreadable:

    def test_no_pin_at_all_says_so(self, tmp_path):
        with pytest.raises(rp.NoPinAtAll):
            rp.read_pin(tmp_path)

    def test_an_unreadable_pin_is_a_refusal_and_a_different_one(self, tmp_path):
        (tmp_path / rp.PIN_NAME).write_text("{ this is not json", encoding="utf-8")
        with pytest.raises(rp.NotWhatWasPinned) as no:
            rp.read_pin(tmp_path)
        assert no.value.which == "pin"

    def test_and_a_pin_that_is_not_an_object_is_refused(self, tmp_path):
        (tmp_path / rp.PIN_NAME).write_text('["not", "an", "object"]', encoding="utf-8")
        with pytest.raises(rp.NotWhatWasPinned):
            rp.read_pin(tmp_path)


class TestEachMistakeNamesItself:

    def test_the_right_thing_passes(self, pinned, monkeypatch):
        monkeypatch.setattr(rp, "running_python", lambda: "3.12.13")
        said = rp.check(pinned, artefact_sha256=ARTEFACT, commit=COMMIT)
        assert said["build_id"] == rp.build_id(COMMIT, ARTEFACT)

    def test_a_wrong_interpreter_is_refused_and_named(self, pinned, monkeypatch):
        monkeypatch.setattr(rp, "running_python", lambda: "3.14.6")
        with pytest.raises(rp.NotWhatWasPinned) as no:
            rp.check(pinned, artefact_sha256=ARTEFACT, commit=COMMIT)
        assert no.value.which == "interpreter"
        assert "3.14.6" in no.value.said and "3.12" in no.value.said

    def test_a_wrong_artefact_is_refused_and_named(self, pinned, monkeypatch):
        monkeypatch.setattr(rp, "running_python", lambda: "3.12.13")
        with pytest.raises(rp.NotWhatWasPinned) as no:
            rp.check(pinned, artefact_sha256="b" * 64, commit=COMMIT)
        assert no.value.which == "artefact"

    def test_a_wrong_commit_is_refused_and_named(self, pinned, monkeypatch):
        monkeypatch.setattr(rp, "running_python", lambda: "3.12.13")
        with pytest.raises(rp.NotWhatWasPinned) as no:
            rp.check(pinned, artefact_sha256=ARTEFACT, commit="a" * 40)
        assert no.value.which == "commit"

    def test_the_three_are_separate_so_an_operator_knows_what_to_fix(self, pinned, monkeypatch):
        """All three wrong at once still names ONE, and names it first -- the interpreter, which
        is the one that makes every other answer untrustworthy."""
        monkeypatch.setattr(rp, "running_python", lambda: "3.14.6")
        with pytest.raises(rp.NotWhatWasPinned) as no:
            rp.check(pinned, artefact_sha256="b" * 64, commit="a" * 40)
        assert no.value.which == "interpreter"

    def test_a_pin_naming_an_untested_interpreter_is_refused_even_if_we_are_running_it(
            self, tmp_path, monkeypatch):
        """The case that would otherwise be the loudest hole: pin 3.14, run 3.14, agree with
        yourself. The pin is checked against what is TESTED, not only against what is running."""
        rp.write_pin(tmp_path, python_version="3.14.6", artefact_sha256=ARTEFACT, commit=COMMIT)
        monkeypatch.setattr(rp, "running_python", lambda: "3.14.6")
        with pytest.raises(rp.NotWhatWasPinned) as no:
            rp.check(tmp_path, artefact_sha256=ARTEFACT, commit=COMMIT)
        assert no.value.which == "interpreter"

    def test_a_refusal_says_what_to_do_about_it(self, pinned, monkeypatch):
        monkeypatch.setattr(rp, "running_python", lambda: "3.14.6")
        with pytest.raises(rp.NotWhatWasPinned) as no:
            rp.check(pinned, artefact_sha256=ARTEFACT, commit=COMMIT)
        assert len(no.value.what_to_do) > 30


class TestWhatThePinRecords:

    def test_it_records_the_four_things_and_the_extras_it_was_given(self, tmp_path):
        where = rp.write_pin(tmp_path, python_version="3.12.13", artefact_sha256=ARTEFACT,
                             commit=COMMIT, worker_image="sha256:abc", topology="single-host")
        said = json.loads(pathlib.Path(where).read_text(encoding="utf-8"))
        assert said["python_version"] == "3.12.13"
        assert said["artefact_sha256"] == ARTEFACT
        assert said["commit"] == COMMIT
        assert said["build_id"] == rp.build_id(COMMIT, ARTEFACT)
        assert said["worker_image"] == "sha256:abc"

    def test_and_empty_extras_are_left_out_rather_than_recorded_as_empty(self, tmp_path):
        where = rp.write_pin(tmp_path, python_version="3.12.13", artefact_sha256=ARTEFACT,
                             commit=COMMIT, worker_image="", topology=None)
        said = json.loads(pathlib.Path(where).read_text(encoding="utf-8"))
        assert "worker_image" not in said and "topology" not in said

    def test_the_pin_is_readable_only_by_its_owner(self, tmp_path):
        import os
        import stat

        where = rp.write_pin(tmp_path, python_version="3.12.13", artefact_sha256=ARTEFACT,
                             commit=COMMIT)
        if os.name == "posix":
            assert not stat.S_IMODE(pathlib.Path(where).stat().st_mode) & 0o077


class TestStartingRefuses:
    """The refusal has to be in the START, not only in the module.

    A module that can detect a mismatch and a service that starts anyway is a module nobody is
    protected by. These drive the CLI's own entry points.
    """

    @pytest.fixture(autouse=True)
    def _pin_lives_here(self, tmp_path, monkeypatch):
        """The pin is read from the PIN directory, not the state directory.

        It used to be read from the state, and a restore drill -- which destroys the state and
        rebuilds it -- brought back a pin describing the previous build. The worker's, one
        directory away, was untouched and right. Both live outside the state now, and these
        tests point the lookup at a temporary one.
        """
        monkeypatch.setenv("AGENTNODE_PIN_DIR", str(tmp_path))

    def _args(self, root, **extra):
        class Args:
            pass

        args = Args()
        args.dir = str(root)
        args.host = "127.0.0.1"
        args.port = 0
        for k, v in extra.items():
            setattr(args, k, v)
        return args

    def test_the_gateway_refuses_a_wrong_interpreter_before_it_opens_a_port(
            self, tmp_path, monkeypatch, capsys):
        from agentnode_sdk.cli import gateway_commands as gc

        rp.write_pin(tmp_path, python_version="3.99.0", artefact_sha256=ARTEFACT, commit=COMMIT)
        opened = []
        monkeypatch.setattr(gc, "_service", lambda root: opened.append(root) or (None, None))
        assert gc.cmd_start(self._args(tmp_path)) == 1
        said = capsys.readouterr().out
        assert "Not started" in said
        assert "interpreter" in said
        assert not opened, "it built a service before deciding whether it was allowed to run"

    def test_and_names_the_artefact_when_that_is_what_differs(
            self, tmp_path, monkeypatch, capsys):
        from agentnode_sdk.cli import gateway_commands as gc

        rp.write_pin(tmp_path, python_version=rp.running_python(),
                     artefact_sha256=ARTEFACT, commit=COMMIT)
        monkeypatch.setattr(rp, "installed_artefact_digest", lambda *a, **k: "b" * 64)
        monkeypatch.setattr(gc, "_service", lambda root: (None, None))
        assert gc.cmd_start(self._args(tmp_path)) == 1
        assert "artefact" in capsys.readouterr().out

    def test_an_unreadable_pin_refuses_rather_than_assuming_the_best(
            self, tmp_path, monkeypatch, capsys):
        from agentnode_sdk.cli import gateway_commands as gc

        (tmp_path / rp.PIN_NAME).write_text("{ broken", encoding="utf-8")
        monkeypatch.setattr(gc, "_service", lambda root: (None, None))
        assert gc.cmd_start(self._args(tmp_path)) == 1
        assert "Not started" in capsys.readouterr().out

    def test_no_pin_at_all_starts_and_says_so(self, tmp_path, monkeypatch, capsys):
        """Deliberate, and the reason is written in the code: refusing here would stop every
        installation made before this check existed, which is a new outage in the name of
        preventing one. It is LOUD instead."""
        from agentnode_sdk.cli import gateway_commands as gc

        assert gc._refuse_unless_pinned(tmp_path, "gateway") == 0
        said = capsys.readouterr().out
        assert "No runtime pin" in said

    def test_the_worker_refuses_too(self, tmp_path, monkeypatch, capsys):
        from agentnode_sdk.cli import worker_commands as wc

        rp.write_pin(tmp_path, python_version="3.99.0", artefact_sha256=ARTEFACT, commit=COMMIT)
        assert wc._refuse_unless_pinned(tmp_path, "worker") == 1
        assert "Not started" in capsys.readouterr().out

    def test_and_a_matching_pin_lets_it_through_and_says_the_build_id(
            self, tmp_path, monkeypatch, capsys):
        from agentnode_sdk.cli import gateway_commands as gc

        rp.write_pin(tmp_path, python_version=rp.running_python(),
                     artefact_sha256=ARTEFACT, commit=COMMIT)
        monkeypatch.setattr(rp, "installed_artefact_digest", lambda *a, **k: ARTEFACT)
        assert gc._refuse_unless_pinned(tmp_path, "gateway") == 0
        said = capsys.readouterr().out
        assert rp.build_id(COMMIT, ARTEFACT) in said
        assert "0.24" not in said.split("Running as")[1].split("on python")[0]


class TestThePublishedVersionIsNotSilentlyReplaced:
    """0.24.1 is on PyPI. Narrowing `requires-python` under that same number would mean somebody
    who installed it on 3.13 finds the same version suddenly refusing them.

    This is a guard rather than a reminder: a comment in `pyproject.toml` asking the next person
    to remember is the kind of protection that works until the day it matters.
    """

    def _pyproject(self):
        import pathlib

        return (pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
            encoding="utf-8")

    def test_the_narrowed_metadata_does_not_go_out_under_the_published_version(self):
        import agentnode_sdk

        said = self._pyproject()
        narrowed = "<3.13" in said
        # THE VERSION IS READ FROM WHERE IT IS NOW WRITTEN. An earlier version of this test
        # scraped `version =` out of pyproject.toml. When the version moved into
        # `agentnode_sdk/__init__.py` that line stopped existing, the scrape returned "", and the
        # assertion below passed against the empty string -- a test that had stopped testing
        # anything while still reporting a pass.
        version = agentnode_sdk.__version__
        assert version, "the package does not say what version it is"
        if narrowed:
            assert version != "0.24.1", (
                "requires-python is narrowed and the version is still 0.24.1, which is already "
                "published. Bump it, or widen the metadata back.")

    def test_and_there_is_only_one_copy_of_the_version(self):
        """0.24.1 in `__init__.py` and 0.25.0 in `pyproject.toml` is how this was found: the
        gateway stamped 0.24.1 onto every answer it gave while running the 0.25.0 build. The
        wheel's version is built from the module, so there is nothing left to disagree with."""
        said = self._pyproject()
        static = [x for x in said.splitlines() if x.strip().startswith("version =")
                  and "hatch" not in x]
        assert not static, (
            "pyproject.toml carries its own version line again (%r); it is built from "
            "agentnode_sdk.__version__ and a second copy will drift from it" % static)
        assert 'dynamic = ["version"]' in said
        assert 'path = "agentnode_sdk/__init__.py"' in said

    def test_and_the_bound_matches_what_the_pin_calls_supported(self):
        """Two places say which interpreter this supports, and they must not drift: the package
        metadata tells an installer, and the pin tells a running service. A package that installs
        on an interpreter the service then refuses to start on is a worse experience than either
        limit alone."""
        said = self._pyproject()
        assert "<3.13" in said, "the metadata no longer excludes 3.13+"
        assert rp.SUPPORTED == (3, 12), (
            "the pin supports %s while the metadata stops below 3.13" % (rp.SUPPORTED,))


class TestOneInstallationSeenDownTwoPathsIsOne:
    """The defect the runtime work uncovered, and it is not about a python version.

    `post_verify` requires a distribution to be installed EXACTLY ONCE -- the right question,
    answered with the wrong count. On a venv where `lib64` is a symlink to `lib`, which is the
    Fedora/RHEL family and is what the DevelopServer runs, both directories are on `sys.path`.
    An interpreter that walks both reports every distribution twice, and both entries resolve to
    the same directory.

    Measured rather than reasoned about: python 3.12.13 reported pip once and 3.12.14 reported it
    twice, on the same machine, in venvs made minutes apart -- 47 distributions duplicated in one
    environment. Every install transaction on such a machine refused with "Installed distribution
    could not be verified", which was true of what it counted and false of what was there.

    This is also the correction of an earlier conclusion of mine: the five failing tests were
    first attributed to python 3.14. They fail on 3.12.14 too. The version was a coincidence of
    which interpreters happened to be at hand.
    """

    def _probe(self, matches):
        """Run the probe's dedupe over a given list of matches, as the probe does."""
        import json

        from agentnode_sdk import _agent_pip as ap

        source = ap._POSTVERIFY_PROBE
        start = source.index("seen = {}")
        end = source.index("print(json.dumps(")
        namespace = {"matches": list(matches), "json": json}
        exec(compile(source[start:end], "<probe-dedupe>", "exec"), namespace)   # noqa: S102
        return namespace["matches"]

    def test_two_paths_to_one_place_count_once(self):
        both = [{"version": "1.0", "where": "/v/lib/python3.12/site-packages/x-1.0.dist-info"},
                {"version": "1.0", "where": "/v/lib/python3.12/site-packages/x-1.0.dist-info"}]
        assert len(self._probe(both)) == 1

    def test_but_two_real_installations_still_count_twice(self):
        """The half that must not move. Two copies in different places is the thing the
        uniqueness check exists to catch, and deduping must not swallow it."""
        two = [{"version": "1.0", "where": "/v/lib/python3.12/site-packages/x-1.0.dist-info"},
               {"version": "2.0", "where": "/other/site-packages/x-2.0.dist-info"}]
        assert len(self._probe(two)) == 2

    def test_and_an_unlocatable_match_is_kept_rather_than_folded_away(self):
        """A match with no resolvable location is not a duplicate of anything. Folding those
        together would hide exactly the case somebody should look at."""
        odd = [{"version": "1.0", "where": ""}, {"version": "2.0", "where": ""}]
        assert len(self._probe(odd)) == 2


class TestWhichBuildIsAnswering:
    """R5: the service has to identify itself by something a version number cannot be.

    0.24.1 was installed before AND after the R2 deployment. Anything that read the version saw
    one build where there were two, and the deployment that changed the code was invisible to it.
    """

    def _identity(self, **kw):
        from agentnode_sdk.gateway.identity import GatewayIdentity

        base = {"gateway_id": "a" * 32, "version": "0.25.0",
                "build_id": "managed-af4e6d8f06b4+4fa7277b3ac3"}
        base.update(kw)
        return GatewayIdentity(**base)

    def test_the_build_id_is_in_what_the_gateway_says_it_is(self):
        said = self._identity().as_dict()
        assert said["build_id"] == "managed-af4e6d8f06b4+4fa7277b3ac3"

    def test_two_builds_of_one_version_are_told_apart(self):
        """The failure this exists for, stated as a test: same version, different code."""
        one = self._identity(version="0.24.1", build_id="managed-04a528edcafe+952e41bccbf6")
        two = self._identity(version="0.24.1", build_id="managed-af4e6d8f06b4+4fa7277b3ac3")
        assert one.as_dict()["version"] == two.as_dict()["version"]
        assert one.as_dict()["build_id"] != two.as_dict()["build_id"]

    def test_the_build_id_is_computed_from_the_commit_and_the_artefact(self):
        """Not written down beside them. `build_id` takes both and derives the value, so it
        cannot say one thing while the pin says another."""
        assert rp.build_id(COMMIT, ARTEFACT) == "managed-%s+%s" % (COMMIT[:12], ARTEFACT[:12])

    def test_a_gateway_that_cannot_tell_says_nothing_rather_than_guessing(self):
        assert self._identity(build_id="").as_dict()["build_id"] == ""

    def test_a_new_build_does_not_unpair_every_device(self):
        """The fingerprint is a client's pinned answer to "is this the same gateway", and it is
        re-checked on every later answer. If the build id were folded into it, every deployment
        would tell every paired device that something else is now answering -- true of the code,
        false of the machine, and it is the machine a person paired with."""
        before = self._identity(build_id="managed-04a528edcafe+952e41bccbf6")
        after = self._identity(build_id="managed-af4e6d8f06b4+4fa7277b3ac3")
        assert before.fingerprint == after.fingerprint

    def test_but_the_fingerprint_still_moves_when_the_gateway_does(self):
        assert self._identity().fingerprint != self._identity(version="0.24.1").fingerprint
        assert self._identity().fingerprint != self._identity(gateway_id="b" * 32).fingerprint

    def test_the_stamp_a_client_recomputes_is_unchanged_by_the_new_field(self):
        """A pairing client recomputes the fingerprint from the gateway id and the version it was
        told. Adding a field to that object must not change the value it arrives at, or every
        pairing breaks on the honest answer."""
        import hashlib

        identity = self._identity()
        said = identity.as_dict()
        named = str(said["gateway_id"]) + "\n" + str(said["version"])
        assert hashlib.sha256(named.encode()).hexdigest() == identity.fingerprint
