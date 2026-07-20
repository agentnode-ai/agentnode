"""Track 0.3A — load_tool / Direct-Mode integrity gate + python-runner dedup.

Public ``load_tool`` enforces the SAME merged two-stage integrity decision as
run_tool (0.2A-2c) BEFORE any module import; ``_internal`` only suppresses the
policy-bypass warning (never the gate). The python runtime imports from the
already-gated entry — no second lockfile read in direct mode, and the subprocess
child receives only ``(module, functions)`` strings (no lockfile reader, no
entry-substitution / TOCTOU window).
"""
from __future__ import annotations

import json
import types

import pytest

from agentnode_sdk import load_tool
from agentnode_sdk.exceptions import LockfileFormatError
from tests.hostpolicy import decision as _dec  # noqa: E402  (F1 test helper)
from agentnode_sdk.installer import LOCKFILE_VERSION
from agentnode_sdk.lock_integrity import LockIntegrityDenied, seal_entry, seal_structure


def _entry(**over) -> dict:
    e = {
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": "python",
        "entrypoint": "mod_x.tool",
        "artifact_hash": "sha256:x",
        "tools": [],
        "permissions": {"network_level": "none"},
        "trust_level": "trusted",
    }
    e.update(over)
    return e


def _sealed(**over) -> dict:
    return seal_entry(_entry(**over))


def _drifted(**over) -> dict:
    e = _sealed(**over)
    e["version"] = e["version"] + "-drift"      # content drift; _integrity unchanged
    return e


def _write_lock(lf, packages, *, structure, updated_at="2026-01-01T00:00:00+00:00"):
    data = {"lockfile_version": LOCKFILE_VERSION, "updated_at": updated_at, "packages": packages}
    if structure:
        data = seal_structure(data)
    lf.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture()
def env_lock(tmp_path, monkeypatch):
    p = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(p))
    return p


@pytest.fixture()
def imp(monkeypatch):
    """Spy installer._import_module: count imports + return a fake tool module.
    A 0-length ``imports`` list proves NO module import ran (deny before import)."""
    import agentnode_sdk.installer as inst
    mod = types.ModuleType("mod_x.tool")
    mod.run = lambda **k: {"ok": True, "kwargs": k}
    mod.describe = lambda **k: {"desc": True, "kwargs": k}
    state: dict = {"imports": [], "mod": mod}

    def _imp(module_path, slug):
        state["imports"].append(module_path)
        return mod
    monkeypatch.setattr(inst, "_import_module", _imp)
    return state


@pytest.fixture()
def audit(monkeypatch):
    """Record lock_integrity audit events (load_tool audits via lock_surface ->
    policy.audit_decision, imported lazily — so patch the policy sink)."""
    import agentnode_sdk.policy as policy
    events: list = []

    def _a(decision, event, slug, **k):
        events.append({"event": event, "action": decision.action, "reason": decision.reason,
                       "source": decision.source, "slug": slug, "extra": k.get("extra")})
    monkeypatch.setattr(policy, "audit_decision", _a)
    return events


def _integrity_audits(audit):
    return [a for a in audit if a["event"] == "lock_integrity_check"]


def _strict(monkeypatch):
    monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")


# --------------------------------------------------------------------------- #
# Public load_tool — verified/verified                                          #
# --------------------------------------------------------------------------- #

class TestPublicVerified:
    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_direct_verified_imports_once_silent(self, env_lock, imp, audit, monkeypatch, caplog, strict):
        if strict:
            _strict(monkeypatch)
        _write_lock(env_lock, {"a-pack": _sealed()}, structure=True)
        with pytest.warns(RuntimeWarning, match="bypasses policy"):
            func = load_tool("a-pack")
        assert func is imp["mod"].run
        assert func(x=1) == {"ok": True, "kwargs": {"x": 1}}
        assert imp["imports"] == ["mod_x.tool"]              # imported exactly once
        assert "Lockfile integrity for" not in caplog.text   # no integrity warning
        assert _integrity_audits(audit) == []                # no migration audit

    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_internal_verified_no_policy_warning(self, env_lock, imp, monkeypatch, strict):
        if strict:
            _strict(monkeypatch)
        _write_lock(env_lock, {"a-pack": _sealed()}, structure=True)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)   # _internal suppresses the policy warning
            func = load_tool("a-pack", _internal=True)
        assert func is imp["mod"].run and imp["imports"] == ["mod_x.tool"]


# --------------------------------------------------------------------------- #
# Public load_tool — readable but not fully verified (core matrix)              #
# --------------------------------------------------------------------------- #

class TestPublicNotVerified:
    def _lock_for(self, env_lock, case):
        if case == "entry_missing":
            _write_lock(env_lock, {"a-pack": _entry()}, structure=False)          # unsealed
        elif case == "entry_mismatch":
            _write_lock(env_lock, {"a-pack": _drifted()}, structure=True)          # struct verified
        elif case == "structure_missing":
            _write_lock(env_lock, {"a-pack": _sealed()}, structure=False)
        elif case in ("structure_mismatch", "structure_unsupported", "structure_invalid"):
            _write_lock(env_lock, {"a-pack": _sealed()}, structure=True)
            d = json.loads(env_lock.read_text(encoding="utf-8"))
            if case == "structure_mismatch":
                d["structure_digest"]["hash"] = "0" * 64
            elif case == "structure_unsupported":
                d["structure_digest"]["canonicalization_version"] = 99
            else:
                d["structure_digest"] = "nope"
            env_lock.write_text(json.dumps(d, indent=2), encoding="utf-8")
        elif case == "integrity_null":
            e = _entry()
            e["_integrity"] = None
            _write_lock(env_lock, {"a-pack": e}, structure=False)
        elif case == "bad_algo":
            e = _entry()
            e["_integrity"] = {"algorithm": "md5", "canonical_version": 1, "hash": "a" * 64}
            _write_lock(env_lock, {"a-pack": e}, structure=False)
        elif case == "bad_cver":
            e = _entry()
            e["_integrity"] = {"algorithm": "sha256", "canonical_version": 99, "hash": "a" * 64}
            _write_lock(env_lock, {"a-pack": e}, structure=False)
        elif case == "invalid_slug":
            _write_lock(env_lock, {"Bad_Slug": _sealed()}, structure=False)
        elif case == "corrupt_other":
            _write_lock(env_lock, {"a-pack": _sealed(), "b-pack": _entry()}, structure=False)

    CASES = ["entry_missing", "entry_mismatch", "structure_missing", "structure_mismatch",
             "structure_unsupported", "structure_invalid", "integrity_null", "bad_algo",
             "bad_cver", "invalid_slug", "corrupt_other"]

    @pytest.mark.parametrize("case", CASES)
    def test_normal_imports_once_with_one_warn_and_allow_audit(self, env_lock, imp, audit, caplog, case):
        self._lock_for(env_lock, case)
        slug = "Bad_Slug" if case == "invalid_slug" else "a-pack"
        with pytest.warns(RuntimeWarning, match="bypasses policy"):
            func = load_tool(slug)
        assert func is imp["mod"].run
        assert imp["imports"] == ["mod_x.tool"]              # imported once (normal allow)
        assert caplog.text.count("Lockfile integrity for") == 1
        aud = _integrity_audits(audit)
        assert len(aud) == 1 and aud[0]["action"] == "allow" and aud[0]["source"] == "lock_integrity"

    @pytest.mark.parametrize("case", CASES)
    def test_strict_denies_before_import(self, env_lock, imp, audit, monkeypatch, case):
        _strict(monkeypatch)
        self._lock_for(env_lock, case)
        slug = "Bad_Slug" if case == "invalid_slug" else "a-pack"
        with pytest.warns(RuntimeWarning):
            with pytest.raises(LockIntegrityDenied):
                load_tool(slug)
        assert imp["imports"] == []                          # NO import, NO getattr
        aud = _integrity_audits(audit)
        assert len(aud) == 1 and aud[0]["action"] == "deny"


# --------------------------------------------------------------------------- #
# Public load_tool — absent + hard read/model errors: no import                 #
# --------------------------------------------------------------------------- #

class TestPublicAbsentAndReadErrors:
    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_absent_is_not_installed_importerror(self, env_lock, imp, audit, monkeypatch, strict):
        if strict:
            _strict(monkeypatch)
        _write_lock(env_lock, {"other": _sealed()}, structure=True)
        with pytest.warns(RuntimeWarning):
            with pytest.raises(ImportError, match="not installed"):
                load_tool("a-pack")
        assert imp["imports"] == []
        aud = _integrity_audits(audit)
        assert len(aud) == 1 and aud[0]["action"] == "deny" and aud[0]["extra"]["entry_status"] == "absent"

    @pytest.mark.parametrize("body", [
        "{not valid json",
        '{"lockfile_version":"0.1","packages":{},"packages":{}}',     # dup key
        '["x"]',
        '{"lockfile_version":"0.1","packages":[]}',
        '{"packages":{"a-pack":{}}}',                                  # missing version
        '{"lockfile_version":"9.9","packages":{}}',                    # unsupported version
    ], ids=["json", "dupkey", "toplist", "pkgs-list", "no-version", "bad-version"])
    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_read_error_propagates_before_import(self, env_lock, imp, body, strict, monkeypatch):
        if strict:
            _strict(monkeypatch)
        env_lock.write_text(body, encoding="utf-8")
        before = env_lock.read_bytes()
        with pytest.warns(RuntimeWarning):
            with pytest.raises(LockfileFormatError):          # NOT disguised as "not installed"
                load_tool("a-pack")
        assert imp["imports"] == []                           # no module import
        assert env_lock.read_bytes() == before                # no mutation

    def test_missing_file_is_import_error(self, env_lock, imp):
        assert not env_lock.exists()
        with pytest.warns(RuntimeWarning):
            with pytest.raises((ImportError, LockfileFormatError)):
                load_tool("a-pack")
        assert imp["imports"] == []


# --------------------------------------------------------------------------- #
# _internal never skips the gate; module cache is not a bypass                  #
# --------------------------------------------------------------------------- #

class TestInternalAndCache:
    @pytest.mark.parametrize("case", ["structure_missing", "entry_mismatch", "json"])
    def test_internal_true_still_denies(self, env_lock, imp, monkeypatch, case):
        _strict(monkeypatch)
        if case == "structure_missing":
            _write_lock(env_lock, {"a-pack": _sealed()}, structure=False)
            exc = LockIntegrityDenied
        elif case == "entry_mismatch":
            _write_lock(env_lock, {"a-pack": _drifted()}, structure=True)
            exc = LockIntegrityDenied
        else:
            env_lock.write_text("{bad json", encoding="utf-8")
            exc = LockfileFormatError
        with pytest.raises(exc):
            load_tool("a-pack", _internal=True)               # underscore is not a boundary
        assert imp["imports"] == []

    def test_cached_module_is_not_a_bypass(self, env_lock, imp, monkeypatch):
        import sys
        sys.modules.setdefault("mod_x.tool", imp["mod"])      # pretend it was imported earlier
        _strict(monkeypatch)
        _write_lock(env_lock, {"a-pack": _drifted()}, structure=True)   # strict deny
        try:
            with pytest.warns(RuntimeWarning):
                with pytest.raises(LockIntegrityDenied):
                    load_tool("a-pack")
        finally:
            sys.modules.pop("mod_x.tool", None)
        assert imp["imports"] == []                           # denied before touching the cache


# --------------------------------------------------------------------------- #
# Single snapshot: no fail-soft reader; one strict read; entry object identity  #
# --------------------------------------------------------------------------- #

class TestSnapshotReader:
    def test_no_fallsoft_reader_one_strict_read(self, env_lock, imp, monkeypatch):
        import agentnode_sdk.installer as inst
        import agentnode_sdk.runtime_integrity as ri
        monkeypatch.setattr(inst, "read_lockfile",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("fail-soft reader called")))
        reads = {"n": 0}
        real = ri.read_lockfile_strict
        monkeypatch.setattr(ri, "read_lockfile_strict",
                            lambda *a, **k: (reads.__setitem__("n", reads["n"] + 1), real(*a, **k))[1])
        _write_lock(env_lock, {"a-pack": _sealed()}, structure=True)
        with pytest.warns(RuntimeWarning):
            load_tool("a-pack")
        assert reads["n"] == 1 and imp["imports"] == ["mod_x.tool"]

    def test_entry_object_is_from_the_snapshot(self, env_lock, monkeypatch):
        import agentnode_sdk.installer as inst
        import agentnode_sdk.runtime_integrity as ri
        entry_obj = _sealed()
        lock = {"lockfile_version": LOCKFILE_VERSION, "updated_at": "", "packages": {"a-pack": entry_obj}}
        monkeypatch.setattr(ri, "read_lockfile_strict", lambda *a, **k: lock)
        captured: dict = {}

        def _spy_load(entry, slug, tool_name):
            captured["entry"] = entry
            return lambda **k: None
        monkeypatch.setattr(inst, "_load_entrypoint_from_entry", _spy_load)
        with pytest.warns(RuntimeWarning):
            load_tool("a-pack")
        assert captured["entry"] is entry_obj                 # same object, single snapshot

    def test_read_only_no_write(self, env_lock, imp, monkeypatch):
        import agentnode_sdk._fileutil as fu
        writes = {"n": 0}
        monkeypatch.setattr(fu, "atomic_write_json", lambda *a, **k: writes.__setitem__("n", writes["n"] + 1))
        _write_lock(env_lock, {"a-pack": _drifted()}, structure=True)    # non-verified, normal allow
        before = env_lock.read_bytes()
        with pytest.warns(RuntimeWarning):
            load_tool("a-pack")
        assert writes["n"] == 0 and env_lock.read_bytes() == before


# --------------------------------------------------------------------------- #
# Audit failure never changes the load_tool decision                            #
# --------------------------------------------------------------------------- #

class TestAuditFailure:
    @staticmethod
    def _raise_audit(monkeypatch):
        import agentnode_sdk.policy as policy

        def _a(decision, event, slug, **k):
            if event == "lock_integrity_check":
                raise RuntimeError("audit backend down")
        monkeypatch.setattr(policy, "audit_decision", _a)

    def test_normal_allow_survives(self, env_lock, imp, monkeypatch):
        self._raise_audit(monkeypatch)
        _write_lock(env_lock, {"a-pack": _sealed()}, structure=False)    # not verified
        with pytest.warns(RuntimeWarning):
            func = load_tool("a-pack")
        assert func is imp["mod"].run and imp["imports"] == ["mod_x.tool"]

    def test_strict_deny_survives(self, env_lock, imp, monkeypatch):
        _strict(monkeypatch)
        self._raise_audit(monkeypatch)
        _write_lock(env_lock, {"a-pack": _sealed()}, structure=False)
        with pytest.warns(RuntimeWarning):
            with pytest.raises(LockIntegrityDenied):
                load_tool("a-pack")
        assert imp["imports"] == []


# --------------------------------------------------------------------------- #
# Direct mode — uses the gated entry via the private loader, no second read     #
# --------------------------------------------------------------------------- #

class TestDirectMode:
    def test_uses_gated_entry_no_read_no_load_tool(self, monkeypatch):
        import agentnode_sdk.runtimes.python_runner as pr
        entry = _sealed()
        captured: dict = {}

        def _spy_load(e, slug, tool_name):
            captured["entry"] = e
            return lambda **k: {"ok": True}
        # patch the name bound in python_runner (what _run_direct actually calls)
        monkeypatch.setattr(pr, "_load_entrypoint_from_entry", _spy_load)
        # any lockfile read from the direct path must NOT happen
        monkeypatch.setattr(pr, "read_lockfile",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("direct read_lockfile")))
        result = pr._run_direct(entry, "a-pack", None, {"x": 1})
        assert result == {"ok": True}
        assert captured["entry"] is entry                     # exact gated object, no re-resolve

    @pytest.mark.parametrize("tool_name,tools,ep,expect_mod,expect_cands", [
        (None, [], "mod_a.tool", "mod_a.tool", ["run"]),                      # package entrypoint
        (None, [{"name": "only", "entrypoint": "mod_b.x:go"}], None, "mod_b.x", ["go"]),  # single-tool auto
        ("go", [{"name": "go", "entrypoint": "mod_c.y:go"}], None, "mod_c.y", ["go"]),    # tool-specific
        ("alt", [], "mod_d.tool", "mod_d.tool", ["alt", "run"]),             # v0.1 candidate order
    ])
    def test_entrypoint_resolution_variants(self, tool_name, tools, ep, expect_mod, expect_cands):
        from agentnode_sdk.installer import _resolve_entrypoint_from_entry
        entry = {"tools": tools}
        if ep:
            entry["entrypoint"] = ep
        module, cands = _resolve_entrypoint_from_entry(entry, "a-pack", tool_name)
        assert module == expect_mod and cands == expect_cands


# --------------------------------------------------------------------------- #
# Subprocess mode — string-only handoff, no reader in child, TOCTOU-safe        #
# --------------------------------------------------------------------------- #

class TestSubprocessMode:
    def _popen_spy(self, monkeypatch):
        import agentnode_sdk.runtimes.python_runner as pr
        captured: dict = {}

        class _FakeProc:
            returncode = 0

            def communicate(self, input=None, timeout=None):
                captured["stdin"] = input
                return json.dumps({"ok": True, "result": {"done": True}}), ""

        monkeypatch.setattr(pr.subprocess, "Popen", lambda *a, **k: _FakeProc())
        return captured

    def test_child_wrapper_has_no_reader(self):
        import agentnode_sdk.runtimes.python_runner as pr
        w = pr._SUBPROCESS_WRAPPER
        assert "load_tool" not in w
        assert "read_lockfile" not in w
        assert "importlib" in w                               # child only imports

    def test_payload_is_module_functions_only(self, monkeypatch):
        import agentnode_sdk.runtimes.python_runner as pr
        captured = self._popen_spy(monkeypatch)
        entry = _sealed(entrypoint="mod_p.tool")
        result, error, timed_out = pr._run_subprocess(entry, "a-pack", None, {"x": 1}, 10.0)
        payload = json.loads(captured["stdin"])
        assert payload["module"] == "mod_p.tool" and payload["functions"] == ["run"]
        assert payload["kwargs"] == {"x": 1}
        # the full entry / integrity / signatures / lockfile path never cross the boundary
        assert "_integrity" not in captured["stdin"]
        assert "structure_digest" not in captured["stdin"]
        assert "_signatures" not in captured["stdin"]
        assert "agentnode.lock" not in captured["stdin"]
        assert result == {"done": True} and error is None

    def test_toctou_payload_stays_with_gated_entry(self, env_lock, monkeypatch):
        import agentnode_sdk.runtimes.python_runner as pr
        captured = self._popen_spy(monkeypatch)
        entry_a = _sealed(entrypoint="mod_from_A.tool")       # the gated entry (A)
        # After the gate, the on-disk lockfile is changed to a DIFFERENT entry (B).
        _write_lock(env_lock, {"a-pack": _sealed(entrypoint="mod_from_B.tool")}, structure=True)
        pr._run_subprocess(entry_a, "a-pack", None, {}, 10.0)
        payload = json.loads(captured["stdin"])
        assert payload["module"] == "mod_from_A.tool"         # from A, never re-read as B

    @pytest.mark.parametrize("tool_name,tools,ep", [
        ("missing", [{"name": "other", "entrypoint": "m:o"}], None),
        ("x", [], None),                                      # no entrypoint at all
    ])
    def test_unresolvable_returns_error_no_spawn(self, monkeypatch, tool_name, tools, ep):
        import agentnode_sdk.runtimes.python_runner as pr
        spawned = {"n": 0}
        monkeypatch.setattr(pr.subprocess, "Popen",
                            lambda *a, **k: spawned.__setitem__("n", spawned["n"] + 1))
        entry = {"tools": tools}
        if ep:
            entry["entrypoint"] = ep
        result, error, timed_out = pr._run_subprocess(entry, "a-pack", tool_name, {}, 10.0)
        assert result is None and error and "ImportError" in error
        assert spawned["n"] == 0                              # no process started on unresolvable

    def test_lockfile_path_not_in_child_env_or_stdin(self, monkeypatch):
        import agentnode_sdk.runtimes.python_runner as pr
        monkeypatch.setenv("AGENTNODE_LOCKFILE", "/sensitive/path/agentnode.lock")
        captured: dict = {}

        class _P:
            returncode = 0

            def communicate(self, input=None, timeout=None):
                captured["stdin"] = input
                return json.dumps({"ok": True, "result": {}}), ""

        def _popen(cmd, **k):
            captured["env"] = k.get("env")
            return _P()
        monkeypatch.setattr(pr.subprocess, "Popen", _popen)
        pr._run_subprocess(_sealed(entrypoint="mod_s.tool"), "a-pack", None, {}, 10.0)
        assert "AGENTNODE_LOCKFILE" not in captured["env"]        # never crosses the boundary
        assert "/sensitive/path" not in captured["stdin"]
        assert "agentnode.lock" not in captured["stdin"]
        assert "AGENTNODE_LOCKFILE" not in pr._SUBPROCESS_WRAPPER  # not in the child wrapper


# --------------------------------------------------------------------------- #
# run_python refuses a non-gated entry — no fail-soft read, no side effect       #
# --------------------------------------------------------------------------- #

class TestRunPythonRequiresGatedEntry:
    @pytest.mark.parametrize("mode", ["direct", "subprocess", "auto"])
    @pytest.mark.parametrize("bad", [None, "not-a-dict", 42], ids=["none", "str", "int"])
    def test_missing_or_nonobject_entry_refused(self, monkeypatch, mode, bad):
        import agentnode_sdk.installer as inst
        import agentnode_sdk.runtime_integrity as ri
        import agentnode_sdk.runtimes.python_runner as pr
        counters = {"load": 0, "popen": 0, "container": 0}
        monkeypatch.setattr(inst, "read_lockfile",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("read_lockfile")))
        monkeypatch.setattr(ri, "read_lockfile_strict",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("read_lockfile_strict")))
        monkeypatch.setattr(pr, "_load_entrypoint_from_entry",
                            lambda *a, **k: counters.__setitem__("load", counters["load"] + 1))
        monkeypatch.setattr(pr.subprocess, "Popen",
                            lambda *a, **k: counters.__setitem__("popen", counters["popen"] + 1))
        monkeypatch.setattr(pr, "_run_container",
                            lambda *a, **k: (counters.__setitem__("container", counters["container"] + 1),
                                             (None, None, False))[1])
        res = pr.run_python("a-pack", None, mode=mode, entry=bad, _host_policy_decision=_dec(None))
        assert res.success is False and res.mode_used == "no_entry"
        assert counters == {"load": 0, "popen": 0, "container": 0}   # no reader / import / spawn / container


# --------------------------------------------------------------------------- #
# Gated python paths never read the lockfile (reader on raise)                  #
# --------------------------------------------------------------------------- #

class TestGatedPathsNoReader:
    @staticmethod
    def _raise_readers(monkeypatch):
        import agentnode_sdk.installer as inst
        import agentnode_sdk.runtime_integrity as ri
        monkeypatch.setattr(inst, "read_lockfile",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("read_lockfile")))
        monkeypatch.setattr(ri, "read_lockfile_strict",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("read_lockfile_strict")))

    @pytest.mark.parametrize("mode", ["direct", "subprocess", "auto"])
    def test_host_paths_no_read(self, monkeypatch, mode):
        import agentnode_sdk.runtimes.python_runner as pr
        self._raise_readers(monkeypatch)
        monkeypatch.setattr(pr, "_load_entrypoint_from_entry", lambda e, s, t: (lambda **k: {"ok": True}))

        class _P:
            returncode = 0

            def communicate(self, input=None, timeout=None):
                return json.dumps({"ok": True, "result": {"r": 1}}), ""
        monkeypatch.setattr(pr.subprocess, "Popen", lambda *a, **k: _P())
        res = pr.run_python("a-pack", None, mode=mode, entry=_sealed(), _host_policy_decision=_dec("trusted"))   # trusted → host
        assert res.success is True

    def test_container_dispatch_no_read(self, monkeypatch):
        import agentnode_sdk.runtimes.python_runner as pr
        self._raise_readers(monkeypatch)
        got: dict = {}

        def _spy_container(slug, tool_name, kwargs, timeout, entry, consent_callback=None):
            got["entry"] = entry
            return {"ok": True}, None, False
        monkeypatch.setattr(pr, "_run_container", _spy_container)
        entry = _sealed(trust_level="unverified")             # community → sandbox/container dispatch
        res = pr.run_python("a-pack", None, mode="auto", entry=entry, _host_policy_decision=_dec("unverified"))
        assert res.success is True and got["entry"] is entry  # container got the gated entry, no read


# --------------------------------------------------------------------------- #
# Gate precedes EVERY entrypoint variant (strict deny → 0 import)               #
# --------------------------------------------------------------------------- #

class TestGateBeforeEntrypointVariants:
    @pytest.mark.parametrize("tools,ep,tool_name", [
        ([{"name": "go", "entrypoint": "m:go"}], None, "go"),         # per-tool
        ([], "mod.pkg", "alt"),                                       # v0.1 fallback + candidate order
        ([], "mod.pkg", None),                                        # no tool_name
        ([{"name": "only", "entrypoint": "m:o"}], None, None),        # single-tool auto
        ([{"name": "x", "entrypoint": "m:x"}], None, "unknown"),      # unknown tool
    ], ids=["per-tool", "v01", "no-name", "single", "unknown"])
    def test_strict_denies_before_variant_resolution(self, env_lock, imp, monkeypatch, tools, ep, tool_name):
        _strict(monkeypatch)
        e = _entry(tools=tools)
        if ep:
            e["entrypoint"] = ep
        sealed = seal_entry(e)
        _write_lock(env_lock, {"a-pack": sealed}, structure=False)   # verified entry, structure missing → strict deny
        with pytest.warns(RuntimeWarning):
            with pytest.raises(LockIntegrityDenied):
                load_tool("a-pack", tool_name)
        assert imp["imports"] == []                                   # no import for ANY variant
