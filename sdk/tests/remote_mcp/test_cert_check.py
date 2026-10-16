"""The independent certificate monitor (profile R12): each alert condition fires, and a healthy
origin passes. The served-certificate path is exercised end to end against a local TLS server."""
from __future__ import annotations

import datetime as dt
import socket
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from agentnode_sdk.remote_mcp import cert_check as C

DOMAIN = "mcp.agentnode.net"
NOW = dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc)


def _name(cn, org=None):
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn)]
    if org:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org))
    return x509.Name(attrs)


def make_ca():
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(_name("drill root", "Test CA Org"))
            .issuer_name(_name("drill root", "Test CA Org")).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOW - dt.timedelta(days=1)).not_valid_after(NOW + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    return key, cert


def make_leaf(ca, *, names=(DOMAIN,), days=60, start=None):
    ca_key, ca_cert = ca
    key = ec.generate_private_key(ec.SECP256R1())
    start = start or (NOW - dt.timedelta(days=1))
    cert = (x509.CertificateBuilder().subject_name(_name(names[0])).issuer_name(ca_cert.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(start + dt.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    return key, cert


def der(cert):
    return cert.public_bytes(serialization.Encoding.DER)


def test_healthy_certificate_passes():
    ca = make_ca()
    _, leaf = make_leaf(ca, days=60)
    assert C.evaluate(der(leaf), domain=DOMAIN, min_days=21, issuer_contains="Test CA",
                      expected_fingerprint=C.fingerprint(der(leaf)), now=NOW) == []


def test_alerts_below_21_days():
    _, leaf = make_leaf(make_ca(), days=21)                 # 20 days left at NOW
    problems = C.evaluate(der(leaf), domain=DOMAIN, min_days=21, now=NOW)
    assert len(problems) == 1 and "expires in 20.0 days" in problems[0]


def test_alerts_on_wrong_san_issuer_and_fingerprint():
    ca = make_ca()
    _, leaf = make_leaf(ca, names=("other.example",))
    _, installed = make_leaf(ca)
    problems = C.evaluate(der(leaf), domain=DOMAIN, issuer_contains="Let's Encrypt",
                          expected_fingerprint=C.fingerprint(der(installed)), now=NOW)
    text = " | ".join(problems)
    assert "does not name" in text and "issuer" in text and "is not the installed one" in text


def test_installed_fingerprint_reads_the_leaf_of_a_fullchain(tmp_path):
    ca = make_ca()
    _, leaf = make_leaf(ca)
    p = tmp_path / "fullchain.pem"
    p.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + ca[1].public_bytes(serialization.Encoding.PEM))
    assert C.installed_fingerprint(str(p)) == C.fingerprint(der(leaf))


def test_timer_and_stamp_problems(tmp_path):
    stamp = tmp_path / "last-success"
    stamp.write_text("1000")
    assert C.timer_problems("t", str(stamp), 36, run=lambda a: "active", now=1000 + 3600) == []
    p = C.timer_problems("t", str(stamp), 36, run=lambda a: "inactive", now=1000 + 40 * 3600)
    assert any("not active" in x for x in p) and any("40.0 h ago" in x for x in p)
    assert any("no recorded success" in x for x in
               C.timer_problems("t", str(tmp_path / "missing"), 36, run=lambda a: "active"))


def _serve_once(cert_pem, key_pem, tmp_path):
    cf, kf = tmp_path / "srv.pem", tmp_path / "srv.key"
    cf.write_bytes(cert_pem)
    kf.write_bytes(key_pem)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cf), str(kf))
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)

    def run():
        conn, _ = lsock.accept()
        try:
            with ctx.wrap_socket(conn, server_side=True) as tls:
                tls.recv(1)
        except (OSError, ssl.SSLError):
            pass
        finally:
            lsock.close()
    threading.Thread(target=run, daemon=True).start()
    return lsock.getsockname()[1]


def test_main_checks_what_is_actually_served(tmp_path, capsys):
    ca = make_ca()
    key, leaf = make_leaf(ca, days=60, start=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
    pem = leaf.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    cafile = tmp_path / "ca.pem"
    cafile.write_bytes(ca[1].public_bytes(serialization.Encoding.PEM))
    installed = tmp_path / "fullchain.pem"
    installed.write_bytes(pem)
    port = _serve_once(pem, key_pem, tmp_path)
    rc = C.main(["--domain", DOMAIN, "--connect", "127.0.0.1", "--port", str(port), "--cafile", str(cafile),
                 "--installed", str(installed), "--min-days", "21"])
    out = capsys.readouterr().out
    assert rc == 0 and "OK %s" % DOMAIN in out
    port = _serve_once(pem, key_pem, tmp_path)
    rc = C.main(["--domain", DOMAIN, "--connect", "127.0.0.1", "--port", str(port), "--cafile", str(cafile),
                 "--min-days", "90"])
    out = capsys.readouterr().out
    assert rc == 1 and "ALERT served certificate expires" in out


def test_unreachable_origin_is_an_alert(capsys):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                              # nothing listens there now
    rc = C.main(["--domain", DOMAIN, "--connect", "127.0.0.1", "--port", str(port)])
    assert rc == 1 and "could not fetch" in capsys.readouterr().out
