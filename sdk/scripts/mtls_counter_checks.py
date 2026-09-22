"""Counter-checks for mtls-loopback-identity-r1 (profile M16).

Kept runnable after stage 5 (mtls-revocation-time-r1): three anchors moved with the code they
mutate -- the worker's context is now built by `Contexts`, `check_peer` takes the trust view, and
check 2 (validity) is judged by `check_peer` at the effective time instead of by OpenSSL, so its
mutation now removes that judgement rather than switching OpenSSL's time check off (which stage 5
does on purpose). Every mutation still takes away the same property as before.

For every check: the control is green first; the mutation LANDS -- the file's digest moves and
the new text is present; a NAMED test fails, non-zero, and its output contains the reason the
mutation predicts; the tests that must not be affected stay green; and the file is restored
byte for byte, which is verified by digest.

Run from `sdk/`:  python scripts/mtls_counter_checks.py [--only ID ...] [--out DIR]

It writes counter-checks.json and counter-checks.txt. A check that could not be carried out is
reported as NOT RUN with the reason, never as passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

T = "tests/test_mtls_transport.py::"
FC = "tests/test_mtls_fail_closed.py::"
IS = "tests/test_mtls_issuer.py::"
PA = "tests/test_mtls_parity.py::"

B_TEST = T + "TestEachDirectionOnItsOwn::test_a_gateway_without_a_certificate_reaches_nothing"
C_TEST = T + "TestEachDirectionOnItsOwn::test_an_impostor_worker_is_never_sent_the_job"

CHECKS = [
    # ------------------------------------------------------------ stage 2, per direction
    dict(id="worker-side-verification", area="stage 2 (b) / identity, worker side",
         file="agentnode_sdk/worker/tls.py",
         edits=[("            connection = self.contexts.current().wrap_socket(raw, server_side=True)\n",
                 "            _unchecked = self.contexts.current()\n"
                 "            _unchecked.verify_mode = ssl.CERT_NONE\n"
                 "            connection = _unchecked.wrap_socket(raw, server_side=True)\n"),
                ("            who = _identity.check_peer(der,\n"
                 "                                       deployment=self.settings.deployment,\n"
                 "                                       expected_role=_identity.GATEWAY,\n"
                 "                                       accept_instances=self.settings.accept,\n"
                 "                                       trust=self.settings.trust(_identity.WORKER))\n",
                 "            who = None\n")],
         test=B_TEST, expect="an application byte from it reached the worker",
         green=[C_TEST]),
    dict(id="gateway-side-verification", area="stage 2 (c) / identity, gateway side",
         file="agentnode_sdk/worker/tls.py",
         edits=[("        who = _identity.check_peer(der,\n"
                 "                                   deployment=settings.deployment,\n"
                 "                                   expected_role=_identity.WORKER,\n"
                 "                                   accept_instances=settings.accept,\n"
                 "                                   trust=settings.trust(_identity.GATEWAY))\n",
                 "        who = _identity.Identity(settings.deployment, 'worker', 'unchecked')\n")],
         test=C_TEST, expect="DID NOT RAISE", green=[B_TEST]),
    dict(id="message-layer-mac", area="stage 2 (a) / the MAC over TLS",
         file="agentnode_sdk/worker/protocol.py",
         edits=[("    if not hmac.compare_digest(mac, hmac.new(key, payload, hashlib.sha256).digest()):",
                 "    if False:")],
         test=T + "TestAJobCrossesTheNewTransport::test_the_mac_still_decides_over_tls",
         expect="and the MAC still refused it", green=[B_TEST, C_TEST]),
    dict(id="plaintext-refusal", area="stage 2 (d) / plaintext refusal",
         file="agentnode_sdk/worker/tls.py",
         # Since stage 5 an admitted connection is also registered for re-evaluation; a door
         # with no admission has no identity to register, so the registration goes with it.
         edits=[("        connection = self.admit(raw)\n", "        connection = raw\n"),
                ("        handle = self.watch.add(connection, connection.gateway_der, "
                 "connection.gateway, on_cut)\n", "        handle = 0\n")],
         test=T + "TestPlaintextIsNotAWayIn::test_plaintext_on_the_tls_port_reaches_nothing",
         expect="plaintext bytes reached the worker's message layer",
         green=[T + "TestLoopbackOnly::test_neither_end_can_be_pointed_elsewhere"]),
    # ------------------------------------------------------------ stage 3, one check each
    dict(id="trust-chain", area="check 1 / trust chain",
         file="agentnode_sdk/worker/tls.py",
         edits=[("    context.load_verify_locations(cafile=settings.anchor)\n",
                 "    context.load_verify_locations(cafile=settings.anchor)\n"
                 "    import glob as _g, os as _o\n"
                 "    for _more in _g.glob(_o.path.join(_o.path.dirname(_o.path.dirname(\n"
                 "            settings.anchor)), '*-ca.pem')):\n"
                 "        context.load_verify_locations(cafile=_more)\n")],
         test=FC + "TestEachCheckRefusesOnItsOwn::test_check_1_a_foreign_ca_with_a_perfect_name_is_refused",
         expect="it got through to the worker", green=[FC + "TestEachCheckRefusesOnItsOwn::test_check_2_an_expired_certificate_is_refused"]),
    dict(id="validity-window", area="check 2 / validity",
         file="agentnode_sdk/pki/identity.py",
         edits=[("        if effective_time > not_after:\n", "        if False:\n")],
         test=FC + "TestEachCheckRefusesOnItsOwn::test_check_2_an_expired_certificate_is_refused",
         expect="it got through to the worker", green=[FC + "TestEachCheckRefusesOnItsOwn::test_check_1_a_foreign_ca_with_a_perfect_name_is_refused"]),
    dict(id="extended-key-usage", area="check 3 / usage",
         file="agentnode_sdk/pki/identity.py",
         edits=[("    usages = _usages(certificate)\n"
                 "    if usages is None:\n"
                 "        raise PeerRefused(CHECK_USAGE, presented, \"it carries no extended key usage\")\n"
                 "    if tuple(usages) != (wanted,):\n",
                 "    usages = _usages(certificate)\n"
                 "    if False:\n")],
         test=FC + "TestEachCheckRefusesOnItsOwn::test_check_3_a_certificate_with_no_usage_is_refused",
         expect="it got through to the worker", green=[FC + "TestEachCheckRefusesOnItsOwn::test_check_4_the_right_usage_with_the_wrong_role_is_refused"]),
    dict(id="role", area="check 4 / role",
         file="agentnode_sdk/pki/identity.py",
         edits=[("    if identity.role != expected_role:\n", "    if False:\n")],
         test=FC + "TestEachCheckRefusesOnItsOwn::test_check_4_the_right_usage_with_the_wrong_role_is_refused",
         expect="it got through to the worker", green=[FC + "TestEachCheckRefusesOnItsOwn::test_check_3_a_certificate_with_no_usage_is_refused"]),
    dict(id="deployment", area="check 4 / identity: deployment",
         file="agentnode_sdk/pki/identity.py",
         edits=[("    if identity.deployment != deployment:\n", "    if False:\n")],
         test=FC + "TestEachCheckRefusesOnItsOwn::test_check_4_another_deployment_is_refused",
         expect="it got through to the worker", green=[FC + "TestEachCheckRefusesOnItsOwn::test_check_5_an_instance_this_side_does_not_accept_is_refused"]),
    dict(id="name-grammar", area="check 4 / identity: grammar, no normalisation",
         file="agentnode_sdk/pki/identity.py",
         edits=[("    if not isinstance(uri, str) or len(uri) > MAX_LENGTH:\n",
                 "    uri = uri.lower() if isinstance(uri, str) else uri\n"
                 "    if not isinstance(uri, str) or len(uri) > MAX_LENGTH:\n")],
         test=FC + "TestEachCheckRefusesOnItsOwn::test_check_4_a_name_outside_the_grammar_is_refused",
         expect="it got through to the worker", green=[FC + "TestEachCheckRefusesOnItsOwn::test_check_4_another_deployment_is_refused"]),
    dict(id="instance", area="check 5 / identity: instance",
         file="agentnode_sdk/pki/identity.py",
         edits=[("    if identity.instance not in set(accept_instances or ()):\n", "    if False:\n")],
         test=FC + "TestEachCheckRefusesOnItsOwn::test_check_5_an_instance_this_side_does_not_accept_is_refused",
         expect="it got through to the worker", green=[FC + "TestEachCheckRefusesOnItsOwn::test_check_4_another_deployment_is_refused"]),
    # ------------------------------------------------------------ stage 4
    dict(id="topology-binding", area="stage 4 / record bound to the handshake identity",
         file="agentnode_sdk/worker/remote.py",
         edits=[("        return self.transport, who.uri(), who.instance\n",
                 "        return self.transport, who.uri(), str(self._describe().get(\"instance_label\") or \"\")\n")],
         test=PA + "TestTheRecordIsBoundToWhoTheConnectionProved::test_the_signed_line_names_the_instance_the_handshake_checked",
         expect="assert 'w1' == 'w2'",
         green=[PA + "TestTheRecordIsBoundToWhoTheConnectionProved::test_and_w1_is_recorded_as_w1"]),
    dict(id="checked-before-claimed", area="stage 3 / a changed endpoint is refused before the ledger",
         file="agentnode_sdk/gateway/server.py",
         edits=[("            self.worker.confirm_reachable()\n", "            pass\n")],
         test=FC + "TestTheGatewayRefusesBeforeClaimingAnything::test_a_changed_endpoint_or_certificate_is_refused_before_anything_is_claimed[w2]",
         expect="the job was accepted before the worker was reached",
         green=[FC + "TestTheTransportLostInTheMiddleOfAJob::test_it_ends_with_exactly_one_line_that_says_so"]),
    # ------------------------------------------------------------ stage 1
    dict(id="fields-from-the-inventory", area="stage 1 (d)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("            ca_key, ca_cert = self._ca()\n"
                 "            certificate = self._build(inventory, entry, public_key, ca_key, ca_cert)\n"
                 "            pem = certificate.public_bytes(serialization.Encoding.PEM)\n"
                 "            transaction = uuid.uuid4().hex\n",
                 "            ca_key, ca_cert = self._ca()\n"
                 "            from cryptography import x509 as _x\n"
                 "            try:\n"
                 "                _asked = _x.load_pem_x509_csr(csr_pem).extensions.get_extension_for_class(\n"
                 "                    _x.SubjectAlternativeName).value.get_values_for_type(\n"
                 "                    _x.UniformResourceIdentifier)[0]\n"
                 "                entry = dict(entry, uri=_asked)\n"
                 "            except Exception:\n"
                 "                pass\n"
                 "            certificate = self._build(inventory, entry, public_key, ca_key, ca_cert)\n"
                 "            pem = certificate.public_bytes(serialization.Encoding.PEM)\n"
                 "            transaction = uuid.uuid4().hex\n")],
         test=IS + "TestTheRequestContributesOnlyItsKey::test_a_request_asking_for_more_gets_exactly_the_entry",
         expect="PeerRefused", green=[IS + "TestTheSecret::test_a_wrong_secret_no_certificate"]),
    dict(id="enrollment-secret", area="stage 1 (e)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("                       if e.get(\"secret_sha256\") and e[\"secret_sha256\"] == presented]\n",
                 "                       if e.get(\"secret_sha256\")]\n")],
         test=IS + "TestTheSecret::test_a_wrong_secret_no_certificate",
         expect="DID NOT RAISE", green=[IS + "TestRenewal::test_a_renewal_not_signed_by_the_current_key_is_refused"]),
    dict(id="renewal-signature", area="stage 1 (f)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("                current.public_key().verify(signature, csr_pem, ec.ECDSA(hashes.SHA256()))\n",
                 "                pass\n")],
         test=IS + "TestRenewal::test_a_renewal_not_signed_by_the_current_key_is_refused",
         expect="DID NOT RAISE", green=[IS + "TestTheSecret::test_a_wrong_secret_no_certificate"]),
    dict(id="exclusive-lock", area="stage 1 (g)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("        return ProcessLock(self.ca_dir / LOCK, timeout=30.0)\n",
                 "        import contextlib\n        return contextlib.nullcontext()\n")],
         test=IS + "TestTwoClaimsAtOnce::test_two_simultaneous_claims_yield_one_certificate",
         expect="the two claims interfered", green=[IS + "TestTheSecret::test_a_wrong_secret_no_certificate"]),
    dict(id="commit-by-rename", area="stage 1 (h1)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("            _files.durable_replace(self.files, self.ca_dir / INVENTORY, data,\n"
                 "                                   temporary=INVENTORY_TMP, label=label)\n",
                 "            self.files.overwrite(self.ca_dir / INVENTORY, data)\n"
                 "            self.files.point(label + \":before-rename\")\n"
                 "            self.files.fsync_file(self.ca_dir / INVENTORY)\n"
                 "            self.files.point(label + \":after-dirsync\")\n")],
         test=IS + "TestTheTransactionUnderBothOutcomes::test_h1_before_the_rename_the_entry_is_still_unclaimed",
         expect="JSONDecodeError", green=[IS + "TestTheSecret::test_a_wrong_secret_no_certificate"]),
    dict(id="deliver-after-commit", area="stage 1 (h2)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("            self._commit(inventory, \"issue\")\n            self._deliver(entry, pem)\n            return pem\n",
                 "            self._deliver(entry, pem)\n            self._commit(inventory, \"issue\")\n            return pem\n")],
         test=IS + "TestTheTransactionUnderBothOutcomes::test_after_a_crash_and_a_retry_exactly_one_committed_one_delivered[issue:after-rename-lost]",
         expect="assert", green=[IS + "TestTheSecret::test_a_wrong_secret_no_certificate"]),
    dict(id="repeatable-delivery", area="stage 1 (h3)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("                    if used.get(\"secret_sha256\") == presented:\n",
                 "                    if False:\n")],
         test=IS + "TestTheTransactionUnderBothOutcomes::test_h3_after_the_commit_delivery_is_repeated_not_reissued",
         expect="IssuanceRefused", green=[IS + "TestTheSecret::test_a_wrong_secret_no_certificate"]),
    dict(id="indeterminate-not-rollback", area="stage 1 (h4)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("            raise Indeterminate(\"the inventory could not be committed durably: \" + str(exc)) \\\n"
                 "                from exc\n",
                 "            return\n")],
         test=IS + "TestTheTransactionUnderBothOutcomes::test_h4_a_failed_directory_fsync_is_indeterminate_and_delivers_nothing",
         expect="DID NOT RAISE", green=[IS + "TestTheSecret::test_a_wrong_secret_no_certificate"]),
    dict(id="fsync-before-acting", area="stage 1 (h4, next call)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("        try:\n            self.files.fsync_dir(self.ca_dir)\n        except OSError as exc:\n",
                 "        try:\n            pass\n        except OSError as exc:\n")],
         test=IS + "TestTheTransactionUnderBothOutcomes::test_h4_and_the_next_call_delivers_only_after_its_own_fsync",
         expect="it delivered on a state it could not make durable",
         green=[IS + "TestTheSecret::test_a_wrong_secret_no_certificate"]),
    dict(id="leftover-removed-unread", area="stage 1 (h5)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("            # Unread. It was never committed, and a certificate inside it was never delivered.\n"
                 "            self.files.remove(leftover)\n",
                 "            pass\n")],
         test=IS + "TestTheTransactionUnderBothOutcomes::test_h5_a_leftover_temporary_inventory_is_removed_unread",
         expect="Indeterminate", green=[IS + "TestTheSecret::test_a_wrong_secret_no_certificate"]),
    dict(id="overlap-status", area="stage 1 (h6)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("                elif other[\"status\"] == CURRENT:\n                    other[\"status\"] = OVERLAPPING\n",
                 "                elif other[\"status\"] == CURRENT:\n                    other[\"status\"] = CURRENT\n")],
         test=IS + "TestTheTransactionUnderBothOutcomes::test_h6_renewal_keeps_one_current_and_at_most_one_overlapping",
         expect="assert", green=[IS + "TestTheSecret::test_a_wrong_secret_no_certificate"]),
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: A PEM block in test output -- a certificate a failing test printed, say. The output is kept as
#: evidence, and evidence may not carry a certificate body or a key, so any such block is removed
#: before the output is stored. The tests themselves avoid printing one; this is the second layer.
_PEM = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.S)


def pytest(nodeids: list[str]) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "-o", "addopts=", *nodeids], cwd=HERE, env=env,
                          capture_output=True, text=True, timeout=900)
    said = done.stdout + done.stderr
    said = _PEM.sub("[a PEM block was removed here]", said)
    # A block printed through repr() arrives with escaped newlines; take those too.
    said = re.sub(r"-----BEGIN [A-Z ]+-----(?:\\\\n|\\n|[A-Za-z0-9+/=])*?-----END [A-Z ]+-----",
                  "[a PEM block was removed here]", said)
    return done.returncode, said


def run_one(check: dict) -> dict:
    path = HERE / check["file"]
    result = {"id": check["id"], "area": check["area"], "file": check["file"],
              "test": check["test"], "expect": check["expect"], "green": check["green"]}
    original = path.read_bytes()
    result["before_sha256"] = sha(original)

    code, out = pytest([check["test"], *check["green"]])
    result["control_exit"] = code
    if code != 0:
        result.update(verdict="NOT RUN", why="the control was not green", control_tail=out[-1500:])
        return result

    text = original.decode("utf-8")
    for old, new in check["edits"]:
        if text.count(old) != 1:
            result.update(verdict="NOT RUN", why="the mutation's anchor occurs %d times" % text.count(old),
                          anchor=old[:200])
            return result
        text = text.replace(old, new)
    try:
        path.write_bytes(text.encode("utf-8"))
        mutated = path.read_bytes()
        result["mutated_sha256"] = sha(mutated)
        result["landed"] = (result["mutated_sha256"] != result["before_sha256"]
                            and all(new in mutated.decode("utf-8") for _, new in check["edits"]))
        code, out = pytest([check["test"]])
        result["mutated_exit"] = code
        result["mutated_tail"] = out[-2500:]
        result["failed_for_the_predicted_reason"] = code != 0 and check["expect"] in out
        green_code, green_out = pytest(check["green"])
        result["others_stayed_green"] = green_code == 0
        if green_code != 0:
            result["green_tail"] = green_out[-1500:]
    finally:
        path.write_bytes(original)
    result["restored_sha256"] = sha(path.read_bytes())
    result["restored_byte_exactly"] = result["restored_sha256"] == result["before_sha256"]
    ok = (result["landed"] and result["mutated_exit"] != 0
          and result["failed_for_the_predicted_reason"] and result["others_stayed_green"]
          and result["restored_byte_exactly"])
    result["verdict"] = "RED AS PREDICTED" if ok else "DID NOT DISCRIMINATE"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--out", default=str(HERE / "counter-check-results"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chosen = [c for c in CHECKS if not args.only or c["id"] in args.only]
    results = []
    for check in chosen:
        started = time.time()
        r = run_one(check)
        r["seconds"] = round(time.time() - started, 1)
        results.append(r)
        print("%-28s %s" % (r["id"], r["verdict"]), flush=True)
    (out / "counter-checks.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    lines = []
    for r in results:
        lines.append("%s  [%s]  %s" % (r["verdict"], r["id"], r["area"]))
        lines.append("    file %s  before %s" % (r["file"], r.get("before_sha256", "")[:16]))
        if "mutated_sha256" in r:
            lines.append("    mutated %s  landed=%s" % (r["mutated_sha256"][:16], r.get("landed")))
            lines.append("    named test exit=%s  predicted reason %r present=%s"
                         % (r.get("mutated_exit"), r["expect"], r.get("failed_for_the_predicted_reason")))
            lines.append("    others stayed green=%s" % r.get("others_stayed_green"))
            lines.append("    restored %s  byte-exact=%s" % (r["restored_sha256"][:16],
                                                          r.get("restored_byte_exactly")))
        else:
            lines.append("    " + r.get("why", ""))
    (out / "counter-checks.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = [r for r in results if r["verdict"] != "RED AS PREDICTED"]
    print("%d of %d red as predicted" % (len(results) - len(bad), len(results)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
