"""Stage 2: egress lifecycle tests with NO docker daemon (subprocess is faked)."""
from __future__ import annotations

import pytest

from agentnode_sdk.sandbox import egress
from agentnode_sdk.sandbox.types import SandboxAvailability, SandboxRequiredError


class _FakeBackend:
    def __init__(self, available: bool = True):
        self._a = available

    def check_available(self):
        return SandboxAvailability(
            available=self._a, backend="docker",
            reason="" if self._a else "no docker",
        )


class _Recorder:
    """Stand-in for egress._run; records argv, can fail on the Nth call, and ANSWERS like a runtime.

    `start_egress_proxy` now reads back what it created -- is the network internal, does it carry a
    resolver, which networks is the proxy actually on and by which id -- and refuses the arrangement when
    the answers disagree with what was asked for. A double that answered nothing made that refusal fire,
    which was the check working rather than a broken test; so this says what a runtime says, and the
    tests that WANT the refusal ask for it by constructing a double that answers differently.
    """

    def __init__(self, fail_on=None, exc=RuntimeError("boom"), internal=True, dns=False,
                 attached=None):
        self.calls = []
        self.fail_on = fail_on
        self.exc = exc
        self.n = 0
        self.internal = internal
        self.dns = dns
        self.attached = attached            # None -> exactly this handle's own two networks

    def _answer(self, argv) -> str:
        import json as _json

        if argv[1:3] == ["network", "inspect"]:
            name = argv[3]
            return _json.dumps({
                "id": "netid-" + name, "internal": bool(self.internal),
                "dns_enabled": bool(self.dns), "ipv6_enabled": False,
                "subnets": [{"subnet": "10.89.0.0/24", "gateway": "10.89.0.1"}],
                "labels": {"agentnode.component": "egress"},
            })
        if argv[1] == "inspect" and "{{json .NetworkSettings.Networks}}" in argv:
            nets = self.attached
            if nets is None:
                nets = [a for call in self.calls for a in call if a.endswith(("-int", "-ext"))]
                nets = sorted(set(nets))
            return _json.dumps({n: {"NetworkID": "netid-" + n, "IPAddress": "10.89.0.2",
                                    "GlobalIPv6Address": ""} for n in nets})
        if argv[1] == "inspect" and "{{.Id}}" in argv:
            return "containerid-" + argv[2]
        return ""

    def __call__(self, argv, timeout=30.0):
        self.calls.append(list(argv))
        self.n += 1
        if self.fail_on is not None and self.n == self.fail_on:
            raise self.exc
        answer = self._answer(list(argv))

        class _CP:
            stdout = answer
            stderr = ""
        return _CP()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # never actually poll docker for health; start clean registry each test
    monkeypatch.setattr(egress, "_wait_healthy", lambda *a, **k: None)
    egress._live.clear()
    yield
    egress._live.clear()


def test_happy_path(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(egress, "_run", rec)
    h = egress.start_egress_proxy(["example.com"], backend=_FakeBackend())

    assert rec.calls[0][:3] == ["docker", "network", "create"] and "--internal" in rec.calls[0]
    assert rec.calls[1][:3] == ["docker", "network", "create"]
    assert rec.calls[2][:3] == ["docker", "run", "-d"]
    assert egress._BASE_IMAGE in rec.calls[2]
    assert "EGRESS_ALLOWLIST=example.com" in rec.calls[2]
    assert rec.calls[3][:3] == ["docker", "network", "connect"]
    assert "--alias" in rec.calls[3] and "egress-proxy" in rec.calls[3]
    # the proxy run must NOT publish any host ports
    assert "-p" not in rec.calls[2] and "--publish" not in rec.calls[2]

    assert h.spec.network_name == h.int_net
    assert h.spec.proxy_url == "http://egress-proxy:8888"
    assert h.spec.allowed_domains == ("example.com",)
    assert h.proxy_name.startswith("agentnode-egress-") and h.proxy_name.endswith("-proxy")
    assert h in egress._live


def test_invalid_allowlist_zero_docker(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(egress, "_run", rec)
    with pytest.raises(ValueError):
        egress.start_egress_proxy([], backend=_FakeBackend())
    with pytest.raises(ValueError):
        egress.start_egress_proxy(["bad/host"], backend=_FakeBackend())
    assert rec.calls == []          # nothing touched docker
    assert not egress._live


def test_backend_unavailable_zero_docker(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(egress, "_run", rec)
    with pytest.raises(SandboxRequiredError):
        egress.start_egress_proxy(["example.com"], backend=_FakeBackend(available=False))
    assert rec.calls == []
    assert not egress._live


def test_failure_cleans_partial_resources(monkeypatch):
    # fail on the 2nd docker call (ext-net create) -> only the int net exists -> must be removed
    rec = _Recorder(fail_on=2)
    monkeypatch.setattr(egress, "_run", rec)
    with pytest.raises(RuntimeError):
        egress.start_egress_proxy(["example.com"], backend=_FakeBackend())
    rms = [c for c in rec.calls if c[1:3] == ["network", "rm"]]
    assert any(c[-1].endswith("-int") for c in rms)   # partial int net torn down
    assert not egress._live                            # no handle registered


def test_stop_is_idempotent_and_scoped(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(egress, "_run", rec)
    h = egress.start_egress_proxy(["example.com"], backend=_FakeBackend())
    egress.stop_egress_proxy(h)
    egress.stop_egress_proxy(h)  # second call must not raise
    assert h not in egress._live
    # teardown targets ONLY this handle's own names
    rm_f = [c for c in rec.calls if c[1:3] == ["rm", "-f"]]
    assert rm_f and all(c[-1] == h.proxy_name for c in rm_f)
    net_rm = [c[-1] for c in rec.calls if c[1:3] == ["network", "rm"]]
    assert set(net_rm) <= {h.int_net, h.ext_net}


def test_context_manager_tears_down(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(egress, "_run", rec)
    with egress.egress_proxy(["example.com"], backend=_FakeBackend()) as h:
        assert h in egress._live
    assert h not in egress._live
