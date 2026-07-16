"""Runtime enforcement of the two-stage lockfile integrity — 0.2A-2c.

``run_tool`` gates BOTH per-entry integrity and the global structure digest from
ONE fail-closed snapshot, before any side effect (trust refresh, sandbox, policy,
dispatch). The decision is the merged core helper's (``evaluate_lock_integrity``),
NOT a second policy:

- verified / verified              → allow, silent (both modes)
- any other readable status        → normal: allow + exactly one warn + one allow
  (except entry_status=absent)         audit; strict: deny + one deny audit
- entry_status=absent              → deny both modes
- hard read / base-model error     → lockfile_error surface (both modes), no
                                     invented integrity report

Strictly read-only: no seal / write / updated_at change / audit-mutation. The
downstream policy/guard pipeline is mocked to ALLOW so that a (non-)dispatch
isolates the integrity gate's decision.
"""
import json

import pytest

from agentnode_sdk.lock_integrity import (
    evaluate_lock_integrity,
    seal_entry,
    seal_structure,
)


def _entry(**over) -> dict:
    e = {
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": "python",
        "entrypoint": "m.tool",
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
    e["version"] = e["version"] + "-drifted"   # content drift; _integrity unchanged
    return e


def _write(lf, packages, *, structure, updated_at="2026-01-01T00:00:00+00:00"):
    data = {"lockfile_version": "0.1", "updated_at": updated_at, "packages": packages}
    if structure:
        data = seal_structure(data)
    lf.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    """Harness: mock the runtime dispatchers, a no-op trust refresh, an allow-all
    downstream pipeline, plus write + audit spies. Returns a state dict."""
    import agentnode_sdk._fileutil as fu
    import agentnode_sdk.guard as guard
    import agentnode_sdk.input_guard as input_guard
    import agentnode_sdk.runner as runner
    import agentnode_sdk.runtimes.mcp_runner as mcpr
    import agentnode_sdk.runtimes.python_runner as pyr
    import agentnode_sdk.runtimes.remote_runner as remr
    import agentnode_sdk.sandbox as sandbox
    from agentnode_sdk.guard import GuardDecision
    from agentnode_sdk.models import RunToolResult
    from agentnode_sdk.policy import PolicyResult

    state = {"dispatch": [], "refresh": 0, "writes": 0, "audits": [], "lf": tmp_path / "agentnode.lock"}

    def _mk(name):
        def _d(slug, tool_name=None, **k):
            state["dispatch"].append(name)
            return RunToolResult(success=True, mode_used=name)
        return _d

    monkeypatch.setattr(pyr, "run_python", _mk("python"))
    monkeypatch.setattr(mcpr, "run_mcp", _mk("mcp"))
    monkeypatch.setattr(remr, "run_remote", _mk("remote"))

    def _refresh(slug, entry, path):
        state["refresh"] += 1
        return entry
    monkeypatch.setattr(runner, "_maybe_refresh_trust", _refresh)

    # Allow-all downstream pipeline (isolate the integrity gate).
    monkeypatch.setattr(runner, "check_run",
                        lambda *a, **k: PolicyResult(action="allow", reason="t", source="t"))
    monkeypatch.setattr(runner, "check_risk_policies", lambda *a, **k: None)
    monkeypatch.setattr(guard, "check_action",
                        lambda *a, **k: GuardDecision(action="allow", reason="t", source="t"))
    monkeypatch.setattr(guard, "check_rate_limit",
                        lambda *a, **k: PolicyResult(action="allow", reason="t", source="t"))
    monkeypatch.setattr(input_guard, "validate_tool_input", lambda *a, **k: [])
    monkeypatch.setattr(sandbox, "enforce_sandbox_policy", lambda *a, **k: None)

    # Audit spy (records every audit_decision call).
    def _audit(decision, event, slug, **k):
        state["audits"].append({"event": event, "action": decision.action,
                                "reason": decision.reason, "source": decision.source,
                                "slug": slug, "extra": k.get("extra")})
    monkeypatch.setattr(runner, "audit_decision", _audit)

    # Write spy.
    real_write = fu.atomic_write_json
    monkeypatch.setattr(fu, "atomic_write_json",
                        lambda *a, **k: (state.__setitem__("writes", state["writes"] + 1),
                                         real_write(*a, **k))[1])
    return state


def _run(rt, slug="a-pack"):
    from agentnode_sdk.runner import run_tool
    return run_tool(slug, lockfile_path=rt["lf"])


def _strict(monkeypatch):
    monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")


def _integrity_audits(rt):
    return [a for a in rt["audits"] if a["event"] == "lock_integrity_check"]


# --------------------------------------------------------------------------- #
# verified / verified                                                          #
# --------------------------------------------------------------------------- #

class TestVerified:
    @pytest.mark.parametrize("runtime_name", ["python", "mcp", "remote"])
    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_dispatches_once_silent(self, rt, monkeypatch, caplog, runtime_name, strict):
        if strict:
            _strict(monkeypatch)
        _write(rt["lf"], {"a-pack": _sealed(runtime=runtime_name)}, structure=True)
        before = rt["lf"].read_bytes()
        res = _run(rt)
        assert res.success is True
        assert rt["dispatch"] == [runtime_name]                 # dispatcher exactly once
        assert rt["writes"] == 0 and rt["lf"].read_bytes() == before
        assert "Lockfile integrity for" not in caplog.text      # no migration warning
        assert _integrity_audits(rt) == []                      # no migration audit


# --------------------------------------------------------------------------- #
# missing structure digest (entry verified)                                    #
# --------------------------------------------------------------------------- #

class TestStructureMissing:
    def test_normal_allows_warns_dispatches(self, rt, caplog):
        _write(rt["lf"], {"a-pack": _sealed()}, structure=False)   # sealed entry, NO digest
        assert evaluate_lock_integrity("a-pack", _lock(rt), strict=False).structure_status == "missing"
        res = _run(rt)
        assert res.success is True and rt["dispatch"] == ["python"]
        assert "structure=missing" in caplog.text
        aud = _integrity_audits(rt)
        assert len(aud) == 1 and aud[0]["action"] == "allow" and aud[0]["source"] == "lock_integrity"

    def test_strict_denies_no_dispatch(self, rt, monkeypatch):
        _strict(monkeypatch)
        _write(rt["lf"], {"a-pack": _sealed()}, structure=False)
        before = rt["lf"].read_bytes()
        res = _run(rt)
        assert res.mode_used == "integrity_denied" and rt["dispatch"] == []
        assert rt["refresh"] == 0 and rt["writes"] == 0 and rt["lf"].read_bytes() == before
        aud = _integrity_audits(rt)
        assert len(aud) == 1 and aud[0]["action"] == "deny"


def _lock(rt) -> dict:
    return json.loads(rt["lf"].read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# missing per-entry integrity (unsealed entry)                                 #
# --------------------------------------------------------------------------- #

class TestEntryMissing:
    def test_normal_allows_with_warning(self, rt, caplog):
        _write(rt["lf"], {"a-pack": _entry()}, structure=False)   # unsealed
        rep = evaluate_lock_integrity("a-pack", _lock(rt), strict=False)
        assert rep.entry_status == "missing"
        res = _run(rt)
        assert res.success is True and rt["dispatch"] == ["python"]
        assert "entry=missing" in caplog.text
        assert rt["writes"] == 0                                   # no auto-seal

    def test_strict_denies(self, rt, monkeypatch):
        _strict(monkeypatch)
        _write(rt["lf"], {"a-pack": _entry()}, structure=False)
        assert _run(rt).mode_used == "integrity_denied" and rt["dispatch"] == []


# --------------------------------------------------------------------------- #
# entry content drift (mismatch) with structure still verified                 #
# --------------------------------------------------------------------------- #

class TestEntryDrift:
    def test_normal_allows_pinned_status(self, rt):
        _write(rt["lf"], {"a-pack": _drifted()}, structure=True)   # digest over unchanged _integrity
        rep = evaluate_lock_integrity("a-pack", _lock(rt), strict=False)
        assert rep.entry_status == "mismatch" and rep.structure_status == "verified"
        assert _run(rt).success is True and rt["dispatch"] == ["python"]
        assert rt["writes"] == 0                                   # no healing / mutation

    def test_strict_denies(self, rt, monkeypatch):
        _strict(monkeypatch)
        _write(rt["lf"], {"a-pack": _drifted()}, structure=True)
        res = _run(rt)
        assert res.mode_used == "integrity_denied" and rt["dispatch"] == []
        assert res.policy["integrity"]["entry_status"] == "mismatch"
        assert res.policy["integrity"]["structure_status"] == "verified"


# --------------------------------------------------------------------------- #
# structure drift (entry verified): mismatch / invalid / unsupported           #
# --------------------------------------------------------------------------- #

class TestStructureDrift:
    def _corrupt(self, rt, how):
        _write(rt["lf"], {"a-pack": _sealed()}, structure=True)
        d = json.loads(rt["lf"].read_text(encoding="utf-8"))
        if how == "mismatch":
            d["structure_digest"]["hash"] = "0" * 64
        elif how == "unsupported":
            d["structure_digest"]["canonicalization_version"] = 99
        elif how == "invalid":
            d["structure_digest"] = "nope"
        rt["lf"].write_text(json.dumps(d, indent=2), encoding="utf-8")
        return d

    @pytest.mark.parametrize("how", ["mismatch", "invalid", "unsupported"])
    def test_normal_allows_dispatches(self, rt, how):
        self._corrupt(rt, how)
        rep = evaluate_lock_integrity("a-pack", _lock(rt), strict=False)
        assert rep.structure_status == how
        assert _run(rt).success is True and rt["dispatch"] == ["python"]

    @pytest.mark.parametrize("how", ["mismatch", "invalid", "unsupported"])
    def test_strict_denies(self, rt, monkeypatch, how):
        self._corrupt(rt, how)
        _strict(monkeypatch)
        res = _run(rt)
        assert res.mode_used == "integrity_denied" and rt["dispatch"] == []
        assert res.policy["integrity"]["structure_status"] == how


# --------------------------------------------------------------------------- #
# malformed _integrity metadata — exact core statuses, no reclassification     #
# --------------------------------------------------------------------------- #

class TestMalformedMetadata:
    CASES = {
        "null": (None, "missing"),
        "algo": ({"algorithm": "md5", "canonical_version": 1, "hash": "a" * 64}, "mismatch"),
        "cver": ({"algorithm": "sha256", "canonical_version": 99, "hash": "a" * 64}, "mismatch"),
    }

    @pytest.mark.parametrize("key", list(CASES))
    def test_normal_allows_pinned(self, rt, key):
        integ, exp_entry = self.CASES[key]
        e = _entry()
        e["_integrity"] = integ
        _write(rt["lf"], {"a-pack": e}, structure=False)
        rep = evaluate_lock_integrity("a-pack", _lock(rt), strict=False)
        assert rep.entry_status == exp_entry            # exact core status, unchanged
        assert rep.structure_status == "invalid"
        assert _run(rt).success is True and rt["dispatch"] == ["python"]   # normal allow

    @pytest.mark.parametrize("key", list(CASES))
    def test_strict_denies(self, rt, monkeypatch, key):
        integ, _exp = self.CASES[key]
        e = _entry()
        e["_integrity"] = integ
        _write(rt["lf"], {"a-pack": e}, structure=False)
        _strict(monkeypatch)
        assert _run(rt).mode_used == "integrity_denied" and rt["dispatch"] == []


# --------------------------------------------------------------------------- #
# invalid slug (requested) + corrupt OTHER entry → structure invalid           #
# --------------------------------------------------------------------------- #

class TestStructureInvalidCauses:
    def test_requested_invalid_slug(self, rt, monkeypatch):
        _write(rt["lf"], {"Bad_Slug": _sealed()}, structure=False)   # verify_structure → invalid
        rep = evaluate_lock_integrity("Bad_Slug", _lock(rt), strict=False)
        assert rep.entry_status == "verified" and rep.structure_status == "invalid"
        assert _run(rt, "Bad_Slug").success is True and rt["dispatch"] == ["python"]  # normal allow
        _strict(monkeypatch)
        rt["dispatch"].clear()
        assert _run(rt, "Bad_Slug").mode_used == "integrity_denied" and rt["dispatch"] == []

    def test_corrupt_other_entry_requested_verified(self, rt, monkeypatch):
        _write(rt["lf"], {"a-pack": _sealed(), "b-pack": _entry()}, structure=False)  # b unsealed
        rep = evaluate_lock_integrity("a-pack", _lock(rt), strict=False)
        assert rep.entry_status == "verified" and rep.structure_status == "invalid"
        assert _run(rt, "a-pack").success is True and rt["dispatch"] == ["python"]
        _strict(monkeypatch)
        rt["dispatch"].clear()
        assert _run(rt, "a-pack").mode_used == "integrity_denied" and rt["dispatch"] == []


# --------------------------------------------------------------------------- #
# entry_status = absent → deny both modes                                       #
# --------------------------------------------------------------------------- #

class TestAbsent:
    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_missing_package(self, rt, monkeypatch, strict):
        if strict:
            _strict(monkeypatch)
        _write(rt["lf"], {"other-pack": _sealed()}, structure=True)
        res = _run(rt, "a-pack")
        assert res.mode_used == "integrity_denied" and rt["dispatch"] == []
        assert res.policy["integrity"]["entry_status"] == "absent"
        assert rt["refresh"] == 0 and rt["writes"] == 0

    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_non_object_entry(self, rt, monkeypatch, strict):
        if strict:
            _strict(monkeypatch)
        rt["lf"].write_text(
            '{"lockfile_version":"0.1","updated_at":"z","packages":{"a-pack":42}}',
            encoding="utf-8")
        res = _run(rt, "a-pack")
        assert res.mode_used == "integrity_denied" and rt["dispatch"] == []
        assert res.policy["integrity"]["entry_status"] == "absent"


# --------------------------------------------------------------------------- #
# hard read / base-model errors → lockfile_error, no side effect               #
# --------------------------------------------------------------------------- #

class TestHardReadErrors:
    BODIES = {
        "invalid_json": "{not valid json",
        "dup_key": '{"lockfile_version":"0.1","packages":{},"packages":{}}',
        "top_list": '["x"]',
        "packages_non_dict": '{"lockfile_version":"0.1","packages":[]}',
        "missing_version": '{"packages":{"a-pack":{}}}',
        "unsupported_version": '{"lockfile_version":"9.9","packages":{}}',
    }

    @pytest.mark.parametrize("key", list(BODIES))
    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_body_fails_closed(self, rt, monkeypatch, key, strict):
        if strict:
            _strict(monkeypatch)
        rt["lf"].write_text(self.BODIES[key], encoding="utf-8")
        before = rt["lf"].read_bytes()
        res = _run(rt, "a-pack")
        assert res.mode_used == "lockfile_error"          # NOT integrity_denied
        assert res.policy is None                          # no invented integrity report
        assert rt["dispatch"] == [] and rt["refresh"] == 0 and rt["writes"] == 0
        assert rt["lf"].read_bytes() == before

    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_missing_file(self, rt, monkeypatch, strict):
        if strict:
            _strict(monkeypatch)
        assert not rt["lf"].exists()
        res = _run(rt, "a-pack")
        assert res.mode_used == "lockfile_error"
        assert rt["dispatch"] == [] and rt["refresh"] == 0 and rt["writes"] == 0

    def test_oserror(self, rt, monkeypatch):
        from pathlib import Path
        _write(rt["lf"], {"a-pack": _sealed()}, structure=True)
        monkeypatch.setattr(Path, "read_text",
                            lambda self, *a, **k: (_ for _ in ()).throw(OSError("boom")))
        res = _run(rt, "a-pack")
        assert res.mode_used == "lockfile_error"
        assert "boom" not in (res.error or "")
        assert rt["dispatch"] == [] and rt["writes"] == 0


# --------------------------------------------------------------------------- #
# one read per run_tool; refresh gated on deny; audit content                   #
# --------------------------------------------------------------------------- #

class TestSnapshotAndSideEffects:
    def test_exactly_one_read(self, rt, monkeypatch):
        import agentnode_sdk.runtime_integrity as ri
        reads = {"n": 0}
        real = ri.read_lockfile_strict
        monkeypatch.setattr(ri, "read_lockfile_strict",
                            lambda *a, **k: (reads.__setitem__("n", reads["n"] + 1), real(*a, **k))[1])
        _write(rt["lf"], {"a-pack": _sealed()}, structure=True)
        _run(rt)
        assert reads["n"] == 1                              # one snapshot per top-level run_tool

    def test_refresh_due_but_gate_denies(self, rt, monkeypatch):
        # Refresh would be due (old installed_at), but a strict deny happens first.
        _strict(monkeypatch)
        _write(rt["lf"], {"a-pack": _sealed(installed_at="2020-01-01T00:00:00+00:00")}, structure=False)
        before = rt["lf"].read_bytes()
        res = _run(rt)
        assert res.mode_used == "integrity_denied"
        assert rt["refresh"] == 0                           # _maybe_refresh_trust never called
        assert rt["writes"] == 0 and rt["lf"].read_bytes() == before

    def test_deny_audit_has_no_sensitive_values(self, rt, monkeypatch):
        _strict(monkeypatch)
        e = _drifted(entrypoint="evil.secret.module")
        _write(rt["lf"], {"a-pack": e}, structure=True)
        _run(rt)
        aud = _integrity_audits(rt)
        assert len(aud) == 1 and aud[0]["action"] == "deny"
        blob = json.dumps(aud[0])
        assert "evil.secret.module" not in blob and "sha256" not in blob.replace("sha256:x", "")
        assert aud[0]["extra"]["strict"] is True
        assert aud[0]["extra"]["entry_status"] == "mismatch"


# --------------------------------------------------------------------------- #
# read-only across every state                                                  #
# --------------------------------------------------------------------------- #

class TestReadOnlyAllStates:
    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    @pytest.mark.parametrize("state", ["verified", "structure_missing", "entry_missing",
                                       "entry_drift", "structure_invalid"])
    def test_no_write_byte_identical(self, rt, monkeypatch, strict, state):
        if strict:
            _strict(monkeypatch)
        if state == "verified":
            _write(rt["lf"], {"a-pack": _sealed()}, structure=True)
        elif state == "structure_missing":
            _write(rt["lf"], {"a-pack": _sealed()}, structure=False)
        elif state == "entry_missing":
            _write(rt["lf"], {"a-pack": _entry()}, structure=False)
        elif state == "entry_drift":
            _write(rt["lf"], {"a-pack": _drifted()}, structure=True)
        elif state == "structure_invalid":
            _write(rt["lf"], {"a-pack": _sealed(), "b-pack": _entry()}, structure=False)
        before = rt["lf"].read_bytes()
        before_updated = json.loads(before)["updated_at"]
        _run(rt)
        assert rt["writes"] == 0                            # integrity gate never writes
        assert rt["lf"].read_bytes() == before
        assert json.loads(rt["lf"].read_bytes())["updated_at"] == before_updated
