"""What a host needs before any of this runs, and whether the two readers of that can disagree.

The cross-host run of 2026-09-29 was brought up by hand: `tar` was absent on both machines --
which is how the artefact is unpacked -- and the worker had no container runtime. Nothing in the
deploy path checked, so nothing in the deploy path could have told anyone. These tests are about
the table that now says what each role needs, the two places that read it, and the properties that
would make it worthless: a role that quietly acquires the other role's runtime, a second run that
changes a correct host, a package name that could come from an argument, and a missing checker
reported as a missing prerequisite.
"""
from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SDK = HERE.parent
TABLE_AT = SDK / "deploy" / "separate-worker-host" / "prerequisites.py"


def _the_table():
    """The table, loaded from its path -- the way both of its real readers load it."""
    spec = importlib.util.spec_from_file_location("prerequisites_under_test", str(TABLE_AT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def table():
    return _the_table()


class TestTheRolesAreNotUnified:
    """The whole point of two machines is that one of them runs foreign code and the other does not.

    A table that gave both roles the same list would undo that in one line, and it is the kind of
    line that gets written to make an installer stop complaining.
    """

    def test_only_the_worker_is_given_a_container_runtime(self, table):
        worker = {need.program for need in table.NEEDS[table.WORKER]}
        control = {need.program for need in table.NEEDS[table.CONTROL_PLANE]}
        assert "podman" in worker
        # Not "podman is absent from the control plane list" alone: every program that exists to
        # run or support containers is named, so adding one of them later trips this.
        for runtime_thing in ("podman", "docker", "crun", "runc", "conmon", "netavark", "pasta",
                              "aardvark-dns"):
            assert runtime_thing not in control, (
                "%s is on the control plane's list. That host runs no foreign code; a runtime "
                "there is how the two-machine arrangement becomes one machine." % runtime_thing)

    def test_the_control_plane_is_not_given_a_root_equivalent_group(self, table):
        """Nothing in the control plane's list is a package whose purpose is group membership.

        The rule being protected is not about a package name, it is that this host must not gain a
        way to reach a runtime. The table cannot add a user to a group -- it installs packages --
        so what this asserts is the installer: it refuses this host outright if a runtime is here.
        """
        text = (SDK / "deploy" / "separate-worker-host" / "install-control-plane.sh").read_text(
            encoding="utf-8")
        assert "no container runtime on this host" in text
        assert '"$PREREQ" --role control-plane' in text
        # Narrowly about the TABLE's role. `--role worker` also appears in this script as
        # `agentnode pki add --role worker`, which is the PKI's word for whose certificate is
        # being issued and has nothing to do with what gets installed here.
        assert '"$PREREQ" --role worker' not in text, (
            "the control-plane installer asked for the worker's table, which would install the "
            "worker's runtime on the control plane")

    def test_both_roles_need_tar_because_that_is_what_was_actually_missing(self, table):
        for role in (table.WORKER, table.CONTROL_PLANE):
            assert "tar" in {need.program for need in table.NEEDS[role]}

    def test_every_need_says_what_it_is_for(self, table):
        for role, needs in table.NEEDS.items():
            for need in needs:
                assert need.why.strip(), "%s/%s has no reason given" % (role, need.program)
                assert len(need.why) > 20, (
                    "%s/%s has a reason too short to help anyone decide whether to install it"
                    % (role, need.program))


class TestTheTableIsDerivedAndNotRemembered:
    """PR1, as something that can fail rather than something claimed in a docstring.

    Two directions, because each catches a different mistake:

    * every program the table says our files invoke must really be invoked by them -- this is what
      caught `openssl` and `getent`, which an earlier draft of the table required and which nothing
      in this repository calls. They are present on both real hosts, so believing it cost nothing
      until the day a host did not have them;
    * every program our files do invoke must be in the table, or be named here as deliberately
      excluded with a reason. That is the direction that catches the next `tar`.
    """

    #: Programs the deploy files invoke that are deliberately NOT prerequisites, each with why. A
    #: name may only be in here because of what it is, never because adding it was inconvenient.
    NOT_PREREQUISITES = {
        "command": "a shell builtin: it is how the scripts ASK whether something is there",
        "printf": "a shell builtin",
        "cd": "a shell builtin",
        "echo": "a shell builtin",
        "exit": "a shell builtin",
        "local": "a shell builtin",
        "return": "a shell builtin",
        "shift": "a shell builtin",
        "set": "a shell builtin",
        "read": "a shell builtin",
        "export": "a shell builtin",
        "eval": "a shell builtin",
        "trap": "a shell builtin",
        "umask": "a shell builtin",
        "true": "a shell builtin",
        "false": "a shell builtin",
        "then": "shell syntax",
        "else": "shell syntax",
        "elif": "shell syntax",
        "fi": "shell syntax",
        "done": "shell syntax",
        "do": "shell syntax",
        "esac": "shell syntax",
        "case": "shell syntax",
        "if": "shell syntax",
        "for": "shell syntax",
        "while": "shell syntax",
        "die": "a function defined in the script itself",
        "say": "a function defined in the script itself",
        "ok": "a function defined in the script itself",
        "stop_here": "a function defined in the script itself",
        "unit_file": "a function defined in the script itself",
        "dirname": "coreutils, and the script cannot reach its own check without it",
        "basename": "coreutils, same",
        "id": "coreutils, used by the very first line, which refuses a non-root run",
        "mkdir": "coreutils, same package as sha256sum, which IS checked",
        "chmod": "coreutils, same package as sha256sum, which IS checked",
        "chown": "coreutils, same package as sha256sum, which IS checked",
        "rm": "coreutils, same package as sha256sum, which IS checked",
        "cp": "coreutils, same package as sha256sum, which IS checked",
        "mv": "coreutils, same package as sha256sum, which IS checked",
        "ln": "coreutils, same package as sha256sum, which IS checked",
        "cat": "coreutils, same package as sha256sum, which IS checked",
        "head": "coreutils, same package as sha256sum, which IS checked",
        "tail": "coreutils, same package as sha256sum, which IS checked",
        "sort": "coreutils, same package as sha256sum, which IS checked",
        "uniq": "coreutils, same package as sha256sum, which IS checked",
        "wc": "coreutils, same package as sha256sum, which IS checked",
        "cut": "coreutils, same package as sha256sum, which IS checked",
        "tr": "coreutils, same package as sha256sum, which IS checked",
        "date": "coreutils, same package as sha256sum, which IS checked",
        "ls": "coreutils, same package as sha256sum, which IS checked",
        "du": "coreutils, same package as sha256sum, which IS checked",
        "stat": "coreutils, same package as sha256sum, which IS checked",
        "touch": "coreutils, same package as sha256sum, which IS checked",
        "mktemp": "coreutils, same package as sha256sum, which IS checked",
        "readlink": "coreutils, same package as sha256sum, which IS checked",
        "sleep": "coreutils, same package as sha256sum, which IS checked",
        "test": "a shell builtin",
        "sed": "sed, and no script branches on it: a host without sed cannot run the installer at all",
        "grep": "grep, same",
        "awk": "gawk, same",
        "find": "findutils, same",
        "xargs": "findutils, same",
        "runuser": "util-linux. The worker installer no longer invokes it -- the worker account has "
                   "no home, so setpriv with an explicit HOME replaced it -- and what is left is "
                   "inside a printed instruction",
        "setpriv": "util-linux, the same base package as the rest of what the script needs",
        "journalctl": "systemd, the same package as systemctl, which IS checked",
        "host": "a FALSE POSITIVE of the scan: the English word host is all over these files and "
                "there is no DNS lookup anywhere in the deploy path",
        "dnf": "the package manager this table own fix command uses. A host whose package manager "
               "is missing cannot be fixed by a command that calls it, so reporting it would be "
               "advice nobody can take",
        "rpm": "read for versions and provenance, and its absence is handled: _rpm returns empty "
               "and readiness is decided by what is on PATH anyway",
        "python": "the interpreter is checked as python3, which is what every invocation names",
        "pip": "it lives inside the venv this script builds, so it is not a host prerequisite",
        "agentnode": "our own entry point, inside the venv this script builds",
        "env": "coreutils, and here only as the shebang /usr/bin/env bash and as the filename "
               "worker.env. A host without it cannot run any of these scripts at all, so a check "
               "written in one could not be the thing that reports it",
        "hostname": "invoked once, by diagnose.sh, to print this machine's name. A diagnostic that "
                    "prints one blank line is not a host that cannot run the role, and gating an "
                    "install on it would refuse a working machine",
        "which": "prose: the English word appears in several comments. Nothing invokes it",
    }

    def _what_the_files_invoke(self):
        """Every external program the deploy scripts invoke, tokenised rather than looked up.

        REPLACED, and the reason is a review finding rather than a preference. This used to search for an
        explicit vocabulary of program names, and a scan like that cannot establish the criterion's
        "every external program": a program nobody listed is invisible to it. `tests/shell_commands.py`
        tokenises instead -- quotes, escapes, comments, heredocs, command substitution, arithmetic, case
        patterns -- and returns the word in every command position, classified as a shell builtin, a
        function defined in the same file, or EXTERNAL. Nothing is filtered out of the last group.

        Two earlier attempts at this without a vocabulary used regular expressions and were worse: they
        read prose out of the installers' own refusal messages and reported programs called `address`,
        `and` and `refusing`. The lesson was that a regular expression cannot do this, not that a
        vocabulary was needed.
        """
        from tests.shell_commands import what_one_file_invokes

        deploy = SDK / "deploy" / "separate-worker-host"
        found, self.not_understood, self.from_a_variable = {}, {}, {}
        for path in sorted(deploy.glob("*.sh")):
            text = path.read_text(encoding="utf-8")
            externals, _functions, _builtins, variable, not_a_name = what_one_file_invokes(text)
            for program in externals:
                found.setdefault(program, set()).add(path.name)
            if not_a_name:
                self.not_understood[path.name] = sorted(not_a_name)
            if variable:
                self.from_a_variable[path.name] = sorted(variable)
        return found

    def test_everything_the_table_claims_we_invoke_is_really_invoked(self, table):
        invoked = self._what_the_files_invoke()
        # The product run-time invocations count too: podman and its helpers are reached while
        # serving, which an installer check cannot cover by itself.
        product = ((SDK / "agentnode_sdk" / "sandbox" / "container_backend.py").read_text(
            encoding="utf-8")
            + (SDK / "agentnode_sdk" / "worker" / "service.py").read_text(encoding="utf-8"))
        for role, needs in table.NEEDS.items():
            for need in needs:
                if need.how != table.INVOKED:
                    continue
                quoted = chr(34) + need.program + chr(34)
                assert need.program in invoked or quoted in product, (
                    "the table says %s/%s is invoked by our own files, and a scan of those files "
                    "does not find it. Either it is not needed -- which is what openssl and getent "
                    "turned out to be -- or it is reached some other way and should say so."
                    % (role, need.program))

    def test_everything_our_files_invoke_is_in_the_table_or_named_as_excluded(self, table):
        in_table = set()
        for needs in table.NEEDS.values():
            in_table.update(need.program for need in needs)
        invoked = self._what_the_files_invoke()
        unexplained = sorted(name for name in invoked
                             if name not in in_table and name not in self.NOT_PREREQUISITES)
        assert unexplained == [], (
            "the deploy path invokes %s and the table neither requires them nor says why not. "
            "That is the shape of the gap tar was: invoked by upgrade and rollback, checked by "
            "nothing, absent on both machines." % unexplained)

    def test_and_what_the_tokeniser_could_not_understand_is_small_and_known(self):
        """The honest limit, asserted rather than described.

        A word that reaches a command position and is not a plausible command name is something the
        tokeniser did not understand, and it is handed back rather than dropped. These are the ones it
        hands back today: fragments of `$(cd "$(dirname "$0")" && pwd)`, where the inner substitution
        sits inside a double-quoted string, and two redirection digits. If that set grows, this test
        fails and whoever changed a script finds out here rather than never.
        """
        self._what_the_files_invoke()
        everything = sorted({w for words in self.not_understood.values() for w in words})
        assert everything == ["1", "2"], everything
        # And neither could be a program: a command name is not a bare number. These two are the file
        # descriptors of redirections the tokeniser splits at.
        for word in everything:
            assert word.isdigit(), word

    def test_the_derivation_would_notice_the_next_tar(self, table):
        """THE CONTROL for the two tests above. `tar` is the program this whole arc exists because of.

        If the derivation cannot find it in the files that use it, neither test above means anything:
        both would pass against a table that had drifted away from the code entirely.

        `podman` is here for a second reason. It is reached as
        `runuser -u X -- env HOME=... TMPDIR=... podman image exists`, through two nested privilege
        wrappers -- and a tokeniser that stopped at the first of them reported `env` and left the
        program that matters most on a worker invisible. That it is found, and found in the file that
        invokes it, is what says the wrappers are followed.
        """
        invoked = self._what_the_files_invoke()
        assert "tar" in invoked, "the derivation cannot see tar, so it cannot see the next one either"
        assert {"upgrade-one-host.sh", "rollback-one-host.sh"} <= invoked["tar"], (
            "it found tar but not in the two scripts that actually unpack with it: %s"
            % sorted(invoked["tar"]))
        assert "podman" in invoked, (
            "podman is not found, so the privilege wrappers are not being followed")
        assert invoked["podman"] == {"install-worker-host.sh"}, sorted(invoked["podman"])
        assert "sha256sum" in invoked, (
            "sha256sum is not found, so a command substitution inside a quoted string is still hiding "
            "its programs")
        assert "systemctl" in invoked

    def test_the_three_that_were_invented_are_gone(self, table):
        """Named, because removing them is the finding and a later edit could put them back.

        Nothing in this repository calls any of the three, and all three are present on both real hosts
        -- which is precisely why requiring them read as correct for as long as it did. The first two
        were found by reading; `nft` only by an instrument that did not have to be told what to look
        for, which is the whole reason the derivation was rewritten.
        """
        for role, needs in table.NEEDS.items():
            programs = {need.program for need in needs}
            assert "openssl" not in programs, (
                "openssl is back in the %s table. The PKI uses the cryptography library; no script "
                "and no module runs the command." % role)
            assert "getent" not in programs, (
                "getent is back in the %s table. No script invokes it." % role)
            assert "nft" not in programs, (
                "nft is back in the %s table. The only nft in the deploy path is inside a printed "
                "instruction about a firewall the script says is somebody else's responsibility, and "
                "diagnose.sh -- which the old reason named -- does not mention it at all." % role)

    def test_an_indirect_need_says_so_rather_than_passing_as_ours(self, table):
        """crun, conmon, netavark and pasta are invoked by nothing we wrote. Listing them as if they
        were would make the derivation test above pass while being false."""
        through = {need.program for need in table.NEEDS[table.WORKER]
                   if need.how == table.THROUGH_THE_RUNTIME}
        assert through == {"crun", "conmon", "netavark", "pasta", "aardvark-dns"}, through
        assert not any(need.how == table.THROUGH_THE_RUNTIME
                       for need in table.NEEDS[table.CONTROL_PLANE]), (
            "the control plane has a need that exists only because of a container runtime, and it "
            "has no container runtime")


class TestAProgramIsNotOnlyFoundOnPath:
    """The finding a PATH-only check produced: netavark and aardvark-dns are NOT on PATH on Rocky 10
    -- podman keeps them in /usr/libexec/podman -- so the first draft of this table reported the
    working worker host of this arc as NOT READY. A correct host refused by its own preflight is
    worse than no check, because the refusal is believed."""

    def test_the_two_that_live_off_path_say_where_they_are(self, table):
        # WITH A MESSAGE, and the reason is about counter-checks rather than about readability: an
        # assertion with none leaves the failure text up to pytest, which truncated it, so no
        # prediction derived from this source could be matched against the run. A named message is
        # what makes the property predictable from here.
        by_name = {need.program: need for need in table.NEEDS[table.WORKER]}
        assert by_name["netavark"].at == ("/usr/libexec/podman/netavark",), (
            "netavark does not say where podman keeps it, so a PATH-only check refuses a host that "
            "has it")
        assert by_name["aardvark-dns"].at == ("/usr/libexec/podman/aardvark-dns",), (
            "aardvark-dns does not say where podman keeps it")

    def test_a_program_off_path_is_found_there(self, table, tmp_path, monkeypatch):
        helper = tmp_path / "netavark"
        helper.write_text("#!/bin/sh" + chr(10), encoding="utf-8")
        helper.chmod(0o755)
        need = table.Need("netavark", "netavark", "the network backend",
                          how=table.THROUGH_THE_RUNTIME, at=(str(helper),))
        monkeypatch.setattr(table.shutil, "which", lambda program: None)
        assert table._where(need) == str(helper)

    def test_path_still_wins_when_both_exist(self, table, tmp_path, monkeypatch):
        """If it is on PATH that is the one that will run, so that is the one reported."""
        helper = tmp_path / "netavark"
        helper.write_text("#!/bin/sh" + chr(10), encoding="utf-8")
        helper.chmod(0o755)
        need = table.Need("netavark", "netavark", "the network backend", at=(str(helper),))
        monkeypatch.setattr(table.shutil, "which", lambda program: "/usr/bin/netavark")
        assert table._where(need) == "/usr/bin/netavark"

    def test_a_directory_at_that_path_is_not_a_program(self, table, tmp_path, monkeypatch):
        need = table.Need("netavark", "netavark", "the network backend", at=(str(tmp_path),))
        monkeypatch.setattr(table.shutil, "which", lambda program: None)
        assert table._where(need) == ""

    def test_the_extra_places_are_absolute_and_come_only_from_this_file(self, table):
        """A check that can be told where to look is a check that can be told to find anything."""
        source = TABLE_AT.read_text(encoding="utf-8")
        for needs in table.NEEDS.values():
            for need in needs:
                for candidate in need.at:
                    assert candidate.startswith("/"), candidate
                    assert candidate in source, (
                        "%s is searched at %r and that path is not written in this file"
                        % (need.program, candidate))
        # READ FROM THE CODE AND NOT FROM THE TEXT. The first version of this matched the word
        # "environment" in the sentence about virtual environments, which is a test that fails for
        # a reason that has nothing to do with the property.
        import ast

        tree = ast.parse(source)
        reached_for = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                reached_for.add(node.attr)
        assert "environ" not in reached_for and "getenv" not in reached_for, (
            "the table reads the environment to decide something, and an installer runs it as root")


class TestWhatTheReportSays:

    def test_a_missing_program_is_missing_and_a_present_one_is_present(self, table, monkeypatch):
        monkeypatch.setattr(table, "_where",
                            lambda need: "" if need.program == "tar" else "/usr/bin/" + need.program)
        monkeypatch.setattr(table, "_rpm", lambda package: "")
        report = table.inspect(table.WORKER)
        assert [entry["program"] for entry in report["missing"]] == ["tar"]
        assert report["ready"] is False
        assert "tar" in {entry["program"] for entry in report["required_missing"]}
        assert "python3" in {entry["program"] for entry in report["present"]}

    def test_presence_is_decided_by_what_is_on_path_not_by_the_package_database(self, table,
                                                                               monkeypatch):
        """A package can be installed and the program still not be callable, and the deploy path
        calls programs. So `rpm -q` answering is never allowed to count as present."""
        monkeypatch.setattr(table, "_where", lambda need: "")
        monkeypatch.setattr(table, "_rpm", lambda package: "%s-1.0-1.x86_64|Rocky" % package)
        report = table.inspect(table.WORKER)
        assert report["ready"] is False
        assert report["present"] == []

    def test_the_fix_is_one_command_over_only_what_is_missing(self, table, monkeypatch):
        monkeypatch.setattr(table, "_where",
                            lambda need: "" if need.program in ("tar", "podman") else "/usr/bin/x")
        monkeypatch.setattr(table, "_rpm", lambda package: "")
        report = table.inspect(table.WORKER)
        assert report["to_install"] == ["dnf", "install", "-y", "podman", "tar"]

    def test_the_packages_are_deduplicated(self, table, monkeypatch):
        """Several programs come from one package. Asking dnf for it twice is noise that makes an
        operator wonder whether the report is reading the host or just printing a list."""
        monkeypatch.setattr(table, "_where",
                            lambda need: "" if need.program in ("useradd", "usermod") else "/x")
        monkeypatch.setattr(table, "_rpm", lambda package: "")
        report = table.inspect(table.WORKER)
        assert report["to_install"] == ["dnf", "install", "-y", "shadow-utils"]

    def test_nothing_in_the_command_comes_from_outside_the_table(self, table, monkeypatch):
        """No argument, environment variable or host reading can put a package name in the command.

        A check that installs what somebody tells it to install is a way to install anything.
        """
        monkeypatch.setattr(table, "_where", lambda need: "")
        monkeypatch.setattr(table, "_rpm", lambda package: "")
        for role in (table.WORKER, table.CONTROL_PLANE):
            report = table.inspect(role)
            declared = {need.package for need in table.NEEDS[role]}
            assert set(report["to_install"][3:]) <= declared
            assert report["to_install"][:3] == ["dnf", "install", "-y"]

    def test_an_optional_absence_does_not_make_the_host_unready(self, table, monkeypatch):
        """aardvark-dns is in the table so a reader knows why podman pulled it in. The payload
        network is created with DNS disabled, so the egress path does not need it, and a host
        without it is not broken."""
        optional = [need.program for need in table.NEEDS[table.WORKER] if need.optional]
        assert optional, "the test is meaningless if nothing in the table is optional"
        monkeypatch.setattr(table, "_where",
                            lambda need: "" if need.program in optional else "/usr/bin/x")
        monkeypatch.setattr(table, "_rpm", lambda package: "")
        report = table.inspect(table.WORKER)
        assert report["ready"] is True
        assert report["to_install"] == []
        assert optional[0] in {entry["program"] for entry in report["missing"]}

    def test_a_ready_host_is_json_and_exits_zero(self, table, monkeypatch):
        monkeypatch.setattr(table, "_where", lambda need: "/usr/bin/" + need.program)
        monkeypatch.setattr(table, "_rpm", lambda package: "%s-9-1.x86_64|Rocky" % package)
        report = table.inspect(table.CONTROL_PLANE)
        assert report["ready"] is True
        assert json.loads(json.dumps(report))["role"] == "control-plane"

    def test_an_unknown_role_is_refused_rather_than_answered_emptily(self, table):
        with pytest.raises(SystemExit):
            table.inspect("gateway-and-worker-at-once")

    def test_the_table_says_which_version_it_is(self, table, monkeypatch):
        monkeypatch.setattr(table, "_where", lambda need: "/usr/bin/x")
        monkeypatch.setattr(table, "_rpm", lambda package: "")
        assert table.inspect(table.WORKER)["table_version"] == table.TABLE_VERSION


class TestASecondRunChangesNothing:
    """Idempotence, and not as an intention: the package manager is not started at all when there
    is nothing to install, so a correct host cannot be changed by running the check again."""

    def test_the_package_manager_is_not_started_when_nothing_is_missing(self, table, monkeypatch):
        monkeypatch.setattr(table, "_where", lambda need: "/usr/bin/" + need.program)
        monkeypatch.setattr(table, "_rpm", lambda package: "")
        started = []
        monkeypatch.setattr(table.subprocess, "run",
                            lambda *a, **k: started.append(a) or pytest.fail("it ran dnf"))
        got = table.install(table.WORKER)
        assert got["ran"] is False
        assert started == []
        assert got["after"]["ready"] is True

    def test_a_package_manager_that_succeeded_is_not_the_same_as_a_program_being_there(
            self, table, monkeypatch):
        """dnf can exit 0 and the program still not be callable -- a package from the wrong
        repository, a path not in PATH, an exclusion. The report after an install is a fresh
        reading of the host, never the exit code."""
        monkeypatch.setattr(table, "_where",
                            lambda need: "" if need.program == "podman" else "/usr/bin/x")
        monkeypatch.setattr(table, "_rpm", lambda package: "")

        class _Done:
            returncode = 0
            stdout = "Complete!"
            stderr = ""

        monkeypatch.setattr(table.subprocess, "run", lambda *a, **k: _Done())
        got = table.install(table.WORKER)
        assert got["ran"] is True
        assert got["exit_code"] == 0
        assert got["after"]["ready"] is False, (
            "dnf exited 0 while podman was still not callable, and the report said the host was "
            "ready. That is a host that looks installed and is not.")

    def test_a_partial_failure_leaves_the_host_reported_as_not_ready(self, table, monkeypatch):
        """Two things missing, one installed. The host must not come out of this looking ready."""
        state = {"missing": {"tar", "podman"}}
        monkeypatch.setattr(table, "_where",
                            lambda need: "" if need.program in state["missing"] else "/usr/bin/x")
        monkeypatch.setattr(table, "_rpm", lambda package: "")

        class _Done:
            returncode = 1
            stdout = ""
            stderr = "Error: Unable to find a match: podman"

        def _half(*argv, **kwargs):
            state["missing"] = {"podman"}
            return _Done()

        monkeypatch.setattr(table.subprocess, "run", _half)
        got = table.install(table.WORKER)
        assert got["after"]["ready"] is False
        assert [e["program"] for e in got["after"]["required_missing"]] == ["podman"]
        assert got["before"]["to_install"] == ["dnf", "install", "-y", "podman", "tar"]
        # And the second run asks only for what is still missing, not for what already landed.
        assert got["after"]["to_install"] == ["dnf", "install", "-y", "podman"]

    def test_no_secret_can_be_in_the_command_because_it_is_three_words_and_package_names(
            self, table, monkeypatch):
        monkeypatch.setattr(table, "_where", lambda need: "")
        monkeypatch.setattr(table, "_rpm", lambda package: "")
        for role in table.ROLES:
            for word in table.inspect(role)["to_install"]:
                assert "://" not in word and "@" not in word and "=" not in word, (
                    "%r in the install command could carry a credential or a repository" % word)


class TestTheProductsOwnPreflightReadsTheSameTable:
    """The install path checks once; the preflight checks on every start, as ExecStartPre. They must
    read the same bytes, and the preflight must tell three answers apart."""

    def test_the_installer_puts_the_table_where_the_preflight_looks(self):
        from agentnode_sdk.cli import worker_commands

        where = worker_commands.WHERE_THE_TABLE_IS
        for name in ("install-worker-host.sh", "install-control-plane.sh"):
            text = (SDK / "deploy" / "separate-worker-host" / name).read_text(encoding="utf-8")
            assert "prerequisites.py" in text
            assert "$PREFIX/deploy/prerequisites.py" in text
        assert where == "/opt/agentnode/deploy/prerequisites.py"

    def test_both_artefacts_ship_it(self):
        text = (SDK / "deploy" / "separate-worker-host" / "build_artefacts.py").read_text(
            encoding="utf-8")
        assert text.count('"prerequisites.py": HERE / "prerequisites.py"') == 2, (
            "an artefact that does not carry the table cannot check anything before it unpacks")

    def test_no_table_at_all_is_a_note_and_not_a_refusal(self, monkeypatch, capsys):
        """A checkout is not a broken host. A missing checker is not a missing prerequisite, and
        reporting one as the other would make every developer's preflight fail for a lie."""
        from agentnode_sdk.cli import worker_commands

        monkeypatch.setattr(worker_commands, "WHERE_THE_TABLE_IS",
                            str(HERE / "no-such-table-anywhere.py"))
        assert worker_commands._the_prerequisite_table() is None

    def test_a_table_that_is_there_and_unreadable_is_a_string_and_therefore_a_refusal(
            self, monkeypatch, tmp_path):
        """Absent and unreadable are different statements about the host. The one that is there
        and cannot be used is the only one where carrying on would hide a real answer."""
        from agentnode_sdk.cli import worker_commands

        broken = tmp_path / "prerequisites.py"
        broken.write_text("this is not python at all (((\n", encoding="utf-8")
        monkeypatch.setattr(worker_commands, "WHERE_THE_TABLE_IS", str(broken))
        answer = worker_commands._the_prerequisite_table()
        assert isinstance(answer, str)
        assert "SyntaxError" in answer

    def test_a_table_that_is_there_and_wrong_shaped_is_also_a_refusal(self, monkeypatch, tmp_path):
        from agentnode_sdk.cli import worker_commands

        wrong = tmp_path / "prerequisites.py"
        wrong.write_text("VERSION = 1\n", encoding="utf-8")  # imports fine, has no inspect()
        monkeypatch.setattr(worker_commands, "WHERE_THE_TABLE_IS", str(wrong))
        answer = worker_commands._the_prerequisite_table()
        assert isinstance(answer, str)
        assert "AttributeError" in answer

    def test_a_real_table_is_read_and_answers_for_the_worker_role(self, monkeypatch):
        from agentnode_sdk.cli import worker_commands

        monkeypatch.setattr(worker_commands, "WHERE_THE_TABLE_IS", str(TABLE_AT))
        answer = worker_commands._the_prerequisite_table()
        assert isinstance(answer, dict)
        assert answer["role"] == "worker"
        # Whether THIS machine is ready is not the assertion -- it is a Windows developer box in
        # CI as often as a Rocky host. That the table answered for the right role is.
        assert "ready" in answer and "to_install" in answer


class TestTheCommandLine:
    """It is run by a shell script with `python3 prerequisites.py`, so the exit code is the
    interface and has to mean what the script assumes it means."""

    def _run(self, *argv):
        return subprocess.run([sys.executable, str(TABLE_AT)] + list(argv),
                              capture_output=True, text=True, timeout=300)

    def test_a_json_report_is_json_on_stdout(self):
        done = self._run("--role", "control-plane", "--json")
        assert done.returncode in (0, 1)
        parsed = json.loads(done.stdout)
        assert parsed["role"] == "control-plane"
        assert parsed["table_version"] >= 1

    def test_an_unknown_role_is_refused_by_the_argument_parser(self):
        done = self._run("--role", "both")
        assert done.returncode == 2
        assert "role" in (done.stderr or "")

    def test_the_exit_code_follows_readiness(self):
        done = self._run("--role", "worker", "--json")
        parsed = json.loads(done.stdout)
        assert done.returncode == (0 if parsed["ready"] else 1), (
            "the install scripts branch on this exit code; if it does not follow readiness they "
            "either install nothing on a broken host or stop on a good one")

    def test_a_report_without_json_names_each_missing_program_and_the_one_command(self):
        done = self._run("--role", "worker")
        text = done.stdout
        assert "Prerequisites for the worker role" in text
        if done.returncode == 1:
            assert "dnf install -y" in text
            assert "MISSING" in text
        else:
            assert "Everything this role needs is here" in text


class TestTheShellScriptsActuallyGateOnIt:
    """A check whose result is printed and ignored is not a gate. These read the scripts, because
    the behaviour under test is a shell branch and there is no way to assert it from python."""

    @pytest.mark.parametrize("name,role", [("install-worker-host.sh", "worker"),
                                           ("install-control-plane.sh", "control-plane")])
    def test_the_installer_stops_when_the_table_says_the_host_is_not_ready(self, name, role):
        text = (SDK / "deploy" / "separate-worker-host" / name).read_text(encoding="utf-8")
        assert 'python3 "$PREREQ" --role %s' % role in text
        # The failing branch either installs on request or dies. What it must never do is carry on.
        after = text.split('python3 "$PREREQ" --role %s; then' % role, 1)[1][:1200]
        assert "die " in after, "%s prints the report and continues anyway" % name
        assert "AGENTNODE_INSTALL_PREREQUISITES" in after

    @pytest.mark.parametrize("name", ["install-worker-host.sh", "install-control-plane.sh"])
    def test_the_installer_checks_the_host_before_it_builds_anything(self, name):
        """Order matters: a partial failure must not leave a host with half an installation on it.
        The table is read before the venv, the accounts and the units."""
        text = (SDK / "deploy" / "separate-worker-host" / name).read_text(encoding="utf-8")
        at_table = text.index('"$PREREQ" --role')
        for later in ("python3 -m venv", "install -d -o"):
            if later in text:
                assert at_table < text.index(later), (
                    "%s starts building before it knows whether this host can run the role" % name)

    def test_the_worker_installer_no_longer_asks_for_podman_in_two_places(self):
        """The ad-hoc `command -v podman` was removed when the table took the question over. Two
        checks of the same thing are two wordings of the same refusal, and they drift."""
        text = (SDK / "deploy" / "separate-worker-host" / "install-worker-host.sh").read_text(
            encoding="utf-8")
        assert "command -v podman" not in text
        # python3 stays by hand, because it is what reads the table.
        assert "command -v python3" in text


class TestAnUpgradedHostGetsTheTableToo:
    """The gap this nearly shipped with, and the one that would have hidden itself.

    The table was wired into install.sh only. A host that was UPGRADED rather than installed afresh
    would never receive one -- and the preflight, which treats an absent table as "this host was not
    set up by the deploy path", would have said exactly that for ever, so the check would have
    stopped happening without once reporting that it had. Both machines of this arc are that case:
    both were installed before the table existed.
    """

    def _upgrade(self):
        return (SDK / "deploy" / "separate-worker-host" / "upgrade-one-host.sh").read_text(
            encoding="utf-8")

    def test_the_upgrade_installs_the_table_where_the_preflight_looks(self):
        from agentnode_sdk.cli import worker_commands

        text = self._upgrade()
        # THE INSTALL, not a mention of the path. The upgrade names that path twice -- once to put the
        # table there and once to run it -- so asking whether the path appears was answered by the
        # second one even with the first removed. Counter-check 12 stayed green on exactly that, which
        # is a counter-check saying the test cannot see what it is about.
        assert 'install -o root -g root -m 0644 "$THE_TABLE" "$PREFIX/deploy/prerequisites.py"' \
            in text, (
                "the upgrade does not install the prerequisite table, so an upgraded host never gets "
                "one and its preflight reports that nothing was checked for ever")
        assert 'install -d -o root -g root -m 0755 "$PREFIX/deploy"' in text, (
            "the directory the table goes in is not made, so installing it would fail")
        assert worker_commands.WHERE_THE_TABLE_IS == "/opt/agentnode/deploy/prerequisites.py"

    def test_it_installs_the_table_before_it_runs_the_preflight(self):
        """Order, not presence. Installed after the gate, the gate reads the OLD table or none, and
        an upgrade that changed what a role needs would pass its own check against the old answer."""
        text = self._upgrade()
        at_install = text.index('"$PREFIX/deploy/prerequisites.py"')
        at_preflight = text.index("worker preflight")
        assert at_install < at_preflight, (
            "the upgrade runs the preflight before it installs the table it is supposed to read")

    def test_it_asks_for_the_role_this_host_actually_is(self):
        text = self._upgrade()
        # The role comes from $UNIT, which this script already worked out from the host, and NOT
        # from an argument -- an upgrade told the wrong role would install the other's prerequisites.
        assert 'if [ "$UNIT" = "agentnode-worker" ]; then THE_ROLE=worker; else THE_ROLE=control-plane; fi' \
            in text
        assert '--role "$THE_ROLE"' in text
        assert '--role worker' not in text.split("THE_ROLE=worker")[1], (
            "the upgrade names a fixed role somewhere after working out the real one")

    def test_and_it_gates_rather_than_only_reporting(self):
        text = self._upgrade()
        after = text.split('--role "$THE_ROLE"', 1)[1][:600]
        assert "died " in after, "the upgrade prints the report and restarts the service anyway"

    def test_an_artefact_without_a_table_does_not_make_the_upgrade_fail(self):
        """Absent from the ARTEFACT is a third answer again, and it is not a broken host.

        An artefact built before the table existed carries none. Dying there would make every older
        artefact uninstallable, and silently skipping would hide that no check ran. It says so.
        """
        text = self._upgrade()
        assert 'if [ -n "$THE_TABLE" ]; then' in text
        assert "carries no prerequisite table" in text


class TestASecondInstallWritesTheSameBytecode:
    """A second run of the install path changed 188 files on a real host and nothing else: every
    .pyc under the virtualenv. The code was identical. pip compiles with the default
    mtime-and-size invalidation, and a reinstall gives every source file a new mtime, so the
    derived bytecode moved while the sources did not -- and an install path whose second run
    cannot be shown to change nothing is not idempotent, whatever the reason.

    Two tests, because one alone would be worth little: the first is about the mechanism and
    would notice if a Python release stopped behaving this way; the second is about the three
    installers actually using it.
    """

    INSTALLERS = ("install-worker-host.sh", "install-control-plane.sh", "upgrade-one-host.sh")

    def _text(self, name):
        return io.open(SDK / "deploy" / "separate-worker-host" / name, encoding="utf-8").read()

    def test_checked_hash_bytecode_survives_a_new_mtime_and_the_default_does_not(self, tmp_path):
        """The mechanism itself. Compile, give the source a new mtime as a reinstall does,
        compile again, and compare the bytes -- in both invalidation modes, so the test says
        which one the difference comes from."""
        import compileall
        import os
        import py_compile

        source = tmp_path / "a_module.py"
        source.write_text("VALUE = 1" + chr(10), encoding="utf-8")

        def compile_it(mode):
            for leftover in tmp_path.rglob("*.pyc"):
                leftover.unlink()
            assert compileall.compile_dir(
                str(tmp_path), quiet=2, force=True, invalidation_mode=mode)
            written = list(tmp_path.rglob("*.pyc"))
            assert len(written) == 1, written
            return written[0].read_bytes()

        def touch_it():
            was = os.stat(source)
            os.utime(source, (was.st_atime + 10, was.st_mtime + 10))

        checked = py_compile.PycInvalidationMode.CHECKED_HASH
        timestamp = py_compile.PycInvalidationMode.TIMESTAMP

        before = compile_it(timestamp)
        touch_it()
        after = compile_it(timestamp)
        assert before != after, (
            "the default mode no longer depends on the mtime; this test is now measuring nothing"
            " and the installers' reason for compiling by hash needs re-reading")

        before = compile_it(checked)
        touch_it()
        after = compile_it(checked)
        assert before == after, "checked-hash bytecode moved although the source did not"

    def test_every_installer_stops_pip_compiling_and_compiles_by_hash_itself(self):
        """The three installers of this topology. Each one must take BOTH halves: pip not
        compiling, and the installer compiling afterwards -- because --no-compile alone would
        leave a host with no bytecode at all, which it would then write as root at the first
        run, unverifiable and at a moment nobody is watching."""
        for name in self.INSTALLERS:
            text = self._text(name)
            installs = [line for line in text.splitlines()
                        if "/venv/bin/pip" in line and " install " in line]
            assert installs, "no pip install line in %s; this test has stopped seeing it" % name
            for line in installs:
                assert "--no-compile" in line, (
                    "%s lets pip compile: %s" % (name, line.strip()))
            assert "--invalidation-mode checked-hash" in text, (
                "%s never compiles the code it installed" % name)
            assert "-m compileall" in text, "%s has no compile step at all" % name


class TestNoScriptJudgesAServiceThreeSecondsIn:
    """Four times on a real worker, the install path reported "the worker did not start" on a host
    that was serving a few seconds later. The rootless runtime's stored namespace can be stale at
    start, the service repairs it, and the repair takes effect in the NEXT start -- which the unit
    performs by itself, because it restarts on failure. Each premature verdict also spent one of
    the unit's five allowed start attempts, and after the fifth systemd refused to start it at all
    and replaced the real message with "Start request repeated too quickly".

    This holds the shape of the fix rather than its wording: after a restart, each script waits
    for the unit's own verdict and reports how long it waited. A bare `sleep` followed by
    `is-active` is the thing that was wrong, so it is the thing named here.
    """

    SCRIPTS = ("install-worker-host.sh", "install-control-plane.sh", "upgrade-one-host.sh")

    def _lines(self, name):
        text = io.open(SDK / "deploy" / "separate-worker-host" / name, encoding="utf-8").read()
        return text, text.splitlines()

    def test_every_start_is_followed_by_a_bounded_wait_and_not_a_bare_sleep(self):
        for name in self.SCRIPTS:
            text, lines = self._lines(name)
            starts = [i for i, line in enumerate(lines)
                      if "systemctl restart" in line or "systemctl start agentnode" in line]
            assert starts, "no service start in %s; this test has stopped seeing it" % name
            for i in starts:
                after = lines[i + 1:i + 4]
                bare = [line for line in after
                        if line.strip().startswith("sleep ") and "WAITED" not in line]
                assert not bare, (
                    "%s sleeps and then judges the service: %r" % (name, bare))
            assert 'while [ "$WAITED" -lt' in text, (
                "%s never waits for the unit to reach its own verdict" % name)
            assert "NRestarts" in text, (
                "%s does not say how many attempts the service needed, so a host that needed"
                " three would look like one that needed none" % name)

    def test_the_wait_is_bounded_and_still_fails_when_the_service_never_comes_up(self):
        """A wait that cannot give up would turn a dead service into a hanging install."""
        for name in self.SCRIPTS:
            text, _ = self._lines(name)
            bounds = [int(part.split()[0]) for part in text.split('"$WAITED" -lt ')[1:]]
            assert bounds, "%s has no bound on its wait" % name
            for bound in bounds:
                assert 0 < bound <= 300, "%s waits up to %ss, which is not a bound" % (name, bound)
            assert ("die " in text or "died " in text), (
                "%s has no failure path left after waiting" % name)


def test_the_table_is_stdlib_only_so_the_first_reader_can_use_it():
    """It runs on a host where nothing of ours is installed, before the virtual environment
    exists. One third-party import would make it unrunnable exactly when it is needed."""
    import ast

    tree = ast.parse(io.open(TABLE_AT, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"argparse", "json", "os", "shutil", "subprocess", "sys", "__future__"}, (
        "the table imports %s, which may not be there when it is read" % (imported,))
