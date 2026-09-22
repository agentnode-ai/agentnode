"""Counter-checks for mtls-revocation-time-r1 (profile R14): decision stage 5, one mutation each.

For every check: the control is green first -- and it RAN: a test that was skipped is not a green
control, it is no control, and the check is then reported NOT RUN, never passed; the mutation
LANDS -- the file's digest moves and the new text is present; a NAMED test fails, non-zero, and
its output contains the reason the mutation predicts; the named siblings stay green; and the file
is restored byte for byte, verified by digest.

Two checks exist only where their property exists: (j1) needs root on Linux, to make a root-owned
floor and act against it as another account. Elsewhere its control is skipped and the harness says
NOT RUN -- "no test ran" -- rather than counting a skip as anything.

Run from `sdk/`:  python scripts/mtls_stage5_counter_checks.py [--only ID ...] [--out DIR]
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

RV = "tests/test_mtls_revocation.py::"
FL = "tests/test_mtls_floor.py::"
PB = "tests/test_mtls_publication.py::"
RN = "tests/test_mtls_renewal.py::"

EACH = RV + "TestEachFailureOnItsOwn::"
CLOCK = RV + "TestAClockSetBackCannotUndoTime::"
OPEN = RV + "TestOpenConnectionsAreReEvaluated::"
ARITH = FL + "TestTheArithmetic::"
AGE = FL + "TestTheAgeAndTheBoot::"

D_WORKER = OPEN + "test_d_the_worker_cuts_a_gateway_revoked_under_a_running_job"
D_GATEWAY = OPEN + "test_d_the_gateway_cuts_a_worker_revoked_under_a_running_job"
P1 = AGE + ("test_p1_a_floor_nobody_keeps_up_stops_the_service_and_setting_the_clock_back_does_"
            "not_help")
P2 = AGE + "test_p2_after_a_restart_nothing_is_served_until_the_root_run_writes_in_this_boot"
LIST_R = PB + "TestTheListUnderTheFaultModel::test_r_a_crash_between_promotion_and_its_fsync_loses_no_seen_serial"
FLOOR_R = PB + ("TestTheFloorUnderTheFaultModel::test_r_a_crash_between_promotion_and_its_fsync_"
                "leaves_no_floor_behind_the_seen")

ONE_STAGE = [
    ("        files.rename(tmp, stage)\n"
     "        files.point(label + \":stage-renamed\")\n"
     "        files.fsync_dir(directory)\n",
     "        files.rename(tmp, target)\n"
     "        files.point(label + \":stage-renamed\")\n"),
    ("        files.rename(stage, target)\n"
     "        files.point(label + \":promoted\")\n"
     "        files.fsync_dir(directory)\n"
     "    except OSError as exc:\n"
     "        raise NotPromoted(",
     "        files.point(label + \":promoted\")\n"
     "        files.fsync_dir(directory)\n"
     "    except OSError as exc:\n"
     "        raise NotPromoted("),
]

CHECKS = [
    # ------------------------------------------------------------ the list and the checks
    dict(id="certificate-expiry", area="(h) the certificate expiry check removed",
         file="agentnode_sdk/pki/identity.py",
         edits=[("        if effective_time > not_after:\n", "        if False:\n")],
         test=EACH + "test_h_an_expired_certificate_is_refused_with_a_fresh_list_and_a_right_clock",
         expect="an expired certificate got through",
         green=[EACH + "test_e_an_expired_list_is_refused_while_the_certificate_is_valid"]),
    dict(id="list-freshness", area="(e) the list freshness check removed",
         file="agentnode_sdk/pki/revocation.py",
         edits=[("    if effective_time > next_update:\n", "    if False:\n")],
         test=EACH + "test_e_an_expired_list_is_refused_while_the_certificate_is_valid",
         expect="an expired list let it through",
         green=[EACH + "test_g_a_list_with_the_wrong_signature_is_refused"]),
    dict(id="list-signature", area="(g) the list signature check removed",
         file="agentnode_sdk/pki/revocation.py",
         edits=[("    if crl.issuer != anchor_cert.subject or not crl.is_signature_valid("
                 "anchor_cert.public_key()):\n", "    if False:\n")],
         test=EACH + "test_g_a_list_with_the_wrong_signature_is_refused",
         expect="a forged list was believed",
         green=[EACH + "test_e_an_expired_list_is_refused_while_the_certificate_is_valid"]),
    dict(id="unreadable-list-refused", area="(f) refusal on an unreadable list reversed",
         file="agentnode_sdk/pki/revocation.py",
         edits=[("    if not data:\n"
                 "        raise ListUnusable(UNREADABLE, \"there is no revocation list to read\")\n",
                 "    if not data:\n"
                 "        return RevocationList(number=0, this_update=0.0,\n"
                 "                              next_update=float(\"inf\"), serials=frozenset())\n")],
         test=EACH + "test_f_no_readable_list_is_refused_never_taken_as_nothing_revoked[missing]",
         expect="no list was taken as an empty one",
         green=[EACH + "test_g_a_list_with_the_wrong_signature_is_refused"]),
    dict(id="revocation-check", area="(c) the revocation check itself removed (beyond the list)",
         file="agentnode_sdk/pki/identity.py",
         edits=[("    if format(certificate.serial_number, \"x\") in serials:\n", "    if False:\n")],
         test=EACH + "test_c_a_revoked_certificate_is_refused_while_the_list_is_fresh",
         expect="a revoked gateway got through",
         green=[EACH + "test_b_a_valid_unrevoked_certificate_of_another_instance_is_refused"]),
    dict(id="instance", area="(b) the instance check removed",
         file="agentnode_sdk/pki/identity.py",
         edits=[("    if identity.instance not in set(accept_instances or ()):\n",
                 "    if False:\n")],
         test=EACH + "test_b_a_valid_unrevoked_certificate_of_another_instance_is_refused",
         expect="it got through to the worker",
         green=[EACH + "test_c_a_revoked_certificate_is_refused_while_the_list_is_fresh"]),
    # ------------------------------------------------------------ (d) each side on its own
    dict(id="re-evaluation-worker-side", area="(d) the worker's re-evaluation removed",
         file="agentnode_sdk/worker/tls.py",
         edits=[("        handle = self.watch.add(connection, connection.gateway_der, "
                 "connection.gateway, on_cut)\n", "        handle = 0\n")],
         test=D_WORKER, expect="the worker did not cut the connection of a revoked gateway",
         green=[D_GATEWAY]),
    dict(id="re-evaluation-gateway-side", area="(d) the gateway's re-evaluation removed",
         file="agentnode_sdk/worker/remote.py",
         edits=[("        self.watch.add(connection, der, who)\n", "        pass\n")],
         test=D_GATEWAY, expect="the gateway did not cut the connection to a revoked worker",
         green=[D_WORKER]),
    # ------------------------------------------------------------ the effective time
    dict(id="effective-time-i1", area="(i1) the effective time set back to the system clock alone",
         file="agentnode_sdk/pki/floor.py",
         edits=[("    return max(_system_now(), float(floor_value))\n",
                 "    return _system_now()\n")],
         test=CLOCK + "test_i1_an_expired_certificate_stays_refused_because_the_floor_stays_ahead",
         expect="an expired certificate was taken as valid again",
         green=[EACH + "test_h_an_expired_certificate_is_refused_with_a_fresh_list_and_a_right_clock"]),
    dict(id="effective-time-i2", area="(i2) the same mutation, the other acceptance",
         file="agentnode_sdk/pki/floor.py",
         edits=[("    return max(_system_now(), float(floor_value))\n",
                 "    return _system_now()\n")],
         test=CLOCK + "test_i2_an_expired_list_stays_refused_because_the_floor_stays_ahead",
         expect="an expired list was taken as current again",
         green=[EACH + "test_e_an_expired_list_is_refused_while_the_certificate_is_valid"]),
    # ------------------------------------------------------------ the floor: who writes it
    dict(id="floor-given-to-a-service", area="(j1) the floor file given to the service account",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("            _files.durable_publish(self.files, path, new.to_bytes(), mode=0o644,\n"
                 "                                   label=\"floor-\" + state.role)\n",
                 "            _files.durable_publish(self.files, path, new.to_bytes(), mode=0o644,\n"
                 "                                   label=\"floor-\" + state.role)\n"
                 "            import pwd as _pwd\n"
                 "            os.chown(path, _pwd.getpwnam(os.environ.get(\n"
                 "                \"AGENTNODE_FLOOR_TEST_ACCOUNT\", \"nobody\")).pw_uid, -1)\n")],
         test=FL + "TestTheFloorIsNotAServicesToChange::test_j1_deleting_overwriting_and_truncating_fail_for_a_service_account",
         expect="SUCCEEDED", green=[P2], platform="root on Linux"),
    dict(id="floor-silently-restarted", area="(j2) a missing or unreadable floor set to the initial value",
         file="agentnode_sdk/pki/floor.py",
         edits=[("    if data is None:\n"
                 "        raise FloorUnusable(MISSING, \"there is no floor file\")\n"
                 "    state = parse(data)\n",
                 "    if data is None:\n"
                 "        return 0.0\n"
                 "    try:\n"
                 "        state = parse(data)\n"
                 "    except FloorUnusable:\n"
                 "        return 0.0\n")],
         test=AGE + "test_j2_a_missing_or_unreadable_floor_is_never_a_fresh_start[missing]",
         expect="DID NOT RAISE", green=[P1]),
    dict(id="service-writes-the-floor", area="(k) the service writing the floor itself",
         file="agentnode_sdk/pki/trust.py",
         edits=[("        floor_bytes, floor_problem = _bytes(floor)\n",
                 "        floor_bytes, floor_problem = _bytes(floor)\n"
                 "        try:\n"
                 "            _state = _floor.parse(floor_bytes)\n"
                 "            Path(floor).write_bytes(_floor.advance(\n"
                 "                _state, system_now=_floor._system_now(),\n"
                 "                monotonic_now=_floor._monotonic(), boot=_floor._boot(),\n"
                 "                list_this_update=None).to_bytes())\n"
                 "        except Exception:\n"
                 "            pass\n")],
         test=FL + "TestTheServiceDoesNotTryToWriteTheFloor::test_k_a_complete_run_opens_the_floor_for_reading_only",
         expect="a service tried to write the floor", green=[P1]),
    dict(id="service-tries-to-write", area="(k) a write attempt built into the service, its error swallowed",
         file="agentnode_sdk/pki/trust.py",
         edits=[("        floor_bytes, floor_problem = _bytes(floor)\n",
                 "        floor_bytes, floor_problem = _bytes(floor)\n"
                 "        try:\n"
                 "            open(floor, \"ab\").close()\n"
                 "        except OSError:\n"
                 "            pass\n")],
         test=FL + "TestTheServiceDoesNotTryToWriteTheFloor::test_k_a_complete_run_opens_the_floor_for_reading_only",
         expect="a service tried to write the floor", green=[P1]),
    # ------------------------------------------------------------ the floor: its arithmetic
    dict(id="granted-not-carried", area="(l) granted_total not carried, a tolerance per update",
         file="agentnode_sdk/pki/floor.py",
         edits=[("    headroom = max(0.0, elapsed_new + state.tolerance_s - state.granted_total)\n",
                 "    headroom = max(0.0, elapsed_new + state.tolerance_s)\n"),
                ("    granted_new = state.granted_total + clock_allowed\n",
                 "    granted_new = 0.0\n")],
         test=ARITH + "test_l_many_quick_updates_with_a_clock_far_ahead",
         expect="of decision 5.7", green=[P1]),
    dict(id="anchor-every-write", area="(m) the anchor reset on every write",
         file="agentnode_sdk/pki/floor.py",
         edits=[("    if state.boot_id != boot:\n        state = replace(",
                 "    if True:\n        state = replace(")],
         test=ARITH + "test_m_restarting_the_process_resets_neither_the_anchor_nor_the_tolerance",
         expect="the anchor moved within one boot", green=[P1]),
    dict(id="tolerance-per-boot", area="(n) the tolerance granted per boot",
         file="agentnode_sdk/pki/floor.py",
         edits=[("                        elapsed_at_boot_start=state.elapsed_total)\n",
                 "                        elapsed_at_boot_start=state.elapsed_total,\n"
                 "                        granted_total=0.0)\n")],
         test=ARITH + "test_n_quick_machine_restarts_move_the_floor_no_further_than_one",
         expect="restarting the machine gave the clock more than running it did",
         green=[ARITH + "test_m_restarting_the_process_resets_neither_the_anchor_nor_the_tolerance"]),
    dict(id="clock-contribution-off", area="(o) the system clock's contribution switched off",
         file="agentnode_sdk/pki/floor.py",
         edits=[("    clock_allowed = min(clock_offer, headroom)\n", "    clock_allowed = 0.0\n")],
         test=ARITH + "test_o_a_correct_clock_on_a_running_machine_moves_the_floor",
         expect="the floor stood still", green=[P2]),
    dict(id="recovery-resets-counters", area="(q) the lifetime counters reset by a recovery",
         file="agentnode_sdk/pki/floor.py",
         edits=[("    return replace(state, generation=state.generation + 1, floor=float(target_floor))\n",
                 "    return replace(state, generation=state.generation + 1, floor=float(target_floor),\n"
                 "                   elapsed_total=0.0, granted_total=0.0, elapsed_at_boot_start=0.0)\n")],
         test=ARITH + "test_q_a_recovery_moves_the_floor_and_leaves_the_counters",
         expect="a recovery reset the lifetime counters",
         green=[ARITH + "test_o_a_correct_clock_on_a_running_machine_moves_the_floor"]),
    # ------------------------------------------------------------ the floor: age and boot
    dict(id="max-age-not-checked", area="(p1) the maximum age not checked",
         file="agentnode_sdk/pki/floor.py",
         edits=[("    if age > state.max_age_s:\n", "    if False:\n")],
         test=P1, expect="DID NOT RAISE", green=[P2]),
    dict(id="age-by-system-clock", area="(p1) the age measured on the system clock",
         file="agentnode_sdk/pki/floor.py",
         edits=[("    age = float(monotonic_now) - state.written_monotonic\n",
                 "    age = _system_now() - (state.written_at or 0.0)\n")],
         test=P1, expect="DID NOT RAISE",
         green=[ARITH + "test_o_a_correct_clock_on_a_running_machine_moves_the_floor"]),
    dict(id="foreign-boot-accepted", area="(p2) a foreign boot identity taken as within age",
         file="agentnode_sdk/pki/floor.py",
         edits=[("    if state.boot_id != boot:\n        raise FloorUnusable(OTHER_BOOT,",
                 "    if False:\n        raise FloorUnusable(OTHER_BOOT,")],
         test=P2, expect="DID NOT RAISE", green=[P1]),
    # ------------------------------------------------------------ publication and crashes
    dict(id="one-stage-publication-list", area="(r) one-stage instead of two-stage publication: the list, in LOST",
         file="agentnode_sdk/pki/files.py", edits=ONE_STAGE,
         test=LIST_R + "[lost]", expect="no durable list carries every serial a service saw",
         green=[P2]),
    dict(id="one-stage-publication-floor", area="(r) the same mutation: the floor, in LOST",
         file="agentnode_sdk/pki/files.py", edits=ONE_STAGE,
         test=FLOOR_R + "[lost]", expect="the durable floor is behind the one a service saw",
         green=[P2]),
    dict(id="floor-before-promotion", area="(r') after a restart, the floor written before the stage is promoted",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("            # 1. What a crash left. The list first, then the floors.\n",
                 "            if floor_dir is not None:\n"
                 "                _early, _why = self._published(ca_cert)\n"
                 "                for _role in _floor.ROLES:\n"
                 "                    _path = _floor.path_for(floor_dir, _role)\n"
                 "                    if self.files.exists(_path):\n"
                 "                        self._advance_floor(_path, _early)\n"
                 "            # 1. What a crash left. The list first, then the floors.\n")],
         test=PB + "TestAfterARestart::test_r_prime_nothing_is_served_before_the_stage_is_promoted_and_the_floor_written",
         expect="the stage was not promoted first",
         green=[PB + "TestAfterARestart::test_an_older_stage_is_dropped_and_a_newer_one_promoted"]),
    dict(id="effective-at-the-rename", area="(s) a revocation reported effective at the rename",
         file="agentnode_sdk/pki/files.py",
         edits=[("        files.rename(tmp, stage)\n"
                 "        files.point(label + \":stage-renamed\")\n"
                 "        files.fsync_dir(directory)\n",
                 "        files.rename(tmp, stage)\n"
                 "        files.point(label + \":stage-renamed\")\n"
                 "        files.rename(stage, target)\n"
                 "        return\n")],
         test=RV + "TestARevocationIsEffectiveOnlyOncePublished::test_a_revocation_whose_list_cannot_be_made_durable_is_not_reported_effective",
         expect="a revocation was called effective before it was",
         green=[RV + "TestARevocationIsEffectiveOnlyOncePublished::test_the_root_run_reconciles_a_list_that_lost_a_serial"]),
    dict(id="floor-despite-pending", area="(s) a floor promoted despite a pending revocation",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("                if report[\"pending_revocation\"]:\n", "                if False:\n")],
         test=RV + "TestTheWindowIsBounded::test_s_a_list_that_cannot_be_published_holds_back_the_floor_and_the_services_stop",
         expect="a floor was promoted while a revocation was pending",
         green=[EACH + "test_c_a_revoked_certificate_is_refused_while_the_list_is_fresh"]),
    # ------------------------------------------------------------ R1, beyond the decision's list
    dict(id="overlap-bound-ends", area="R1: the overlap never ends (the bound removed)",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("                if until <= now:\n", "                if False:\n")],
         test=RN + "TestTheOverlapIsBounded::test_when_the_overlap_runs_out_the_previous_certificate_is_refused",
         expect="a certificate outside its overlap was still accepted",
         green=[RN + "TestTheOverlapIsBounded::test_a_second_renewal_takes_the_overlapping_certificate_out_of_use"]),
    dict(id="overlap-bound-superseded", area="R1: a superseded certificate left unrevoked",
         file="agentnode_sdk/pki/issuer.py",
         edits=[("                    if other.get(\"revoked_at\") is None:\n"
                 "                        other[\"revoked_at\"] = round(now, 3)\n",
                 "                    if False:\n"
                 "                        other[\"revoked_at\"] = round(now, 3)\n")],
         test=RN + "TestTheOverlapIsBounded::test_a_second_renewal_takes_the_overlapping_certificate_out_of_use",
         expect="a superseded certificate was still accepted",
         green=[RN + "TestTheOverlapIsBounded::test_when_the_overlap_runs_out_the_previous_certificate_is_refused"]),
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]+-----.*?-----END [A-Z0-9 ]+-----", re.S)


def pytest(nodeids: list[str]) -> tuple[int, str, dict]:
    """Run exactly these tests. Returns the exit code, the output with any PEM block removed (the
    output is evidence, and evidence carries no certificate or list body), and the counts."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
                           "-o", "addopts=", *nodeids], cwd=HERE, env=env,
                          capture_output=True, text=True, timeout=1800)
    said = _PEM.sub("[a PEM block was removed here]", done.stdout + done.stderr)
    said = re.sub(r"-----BEGIN [A-Z0-9 ]+-----(?:\\\\n|\\n|[A-Za-z0-9+/=])*?-----END [A-Z0-9 ]+-----",
                  "[a PEM block was removed here]", said)
    counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|error|errors)", said)}
    return done.returncode, said, counts


def run_one(check: dict) -> dict:
    path = HERE / check["file"]
    result = {"id": check["id"], "area": check["area"], "file": check["file"],
              "test": check["test"], "expect": check["expect"], "green": check["green"],
              "platform": check.get("platform", "any")}
    original = path.read_bytes()
    result["before_sha256"] = sha(original)

    wanted = 1 + len(check["green"])
    code, out, counts = pytest([check["test"], *check["green"]])
    result["control_exit"] = code
    result["control_counts"] = counts
    if counts.get("skipped"):
        result.update(verdict="NOT RUN", why="no test ran: the control was SKIPPED here (%s) -- "
                      "this check has to run where its property exists (%s)"
                      % (counts, result["platform"]), control_tail=out[-1500:])
        return result
    if code != 0 or counts.get("passed", 0) != wanted:
        result.update(verdict="NOT RUN", why="the control was not green (%s)" % counts,
                      control_tail=out[-1500:])
        return result

    text = original.decode("utf-8")
    for old, new in check["edits"]:
        if text.count(old) != 1:
            result.update(verdict="NOT RUN",
                          why="the mutation's anchor occurs %d times" % text.count(old),
                          anchor=old[:200])
            return result
        text = text.replace(old, new)
    try:
        path.write_bytes(text.encode("utf-8"))
        mutated = path.read_bytes()
        result["mutated_sha256"] = sha(mutated)
        result["landed"] = (result["mutated_sha256"] != result["before_sha256"]
                            and all(new in mutated.decode("utf-8") for _, new in check["edits"]))
        code, out, counts = pytest([check["test"]])
        result["mutated_exit"] = code
        result["mutated_counts"] = counts
        result["mutated_tail"] = out[-3000:]
        result["failed_for_the_predicted_reason"] = (code != 0 and counts.get("failed", 0) == 1
                                                     and check["expect"] in out)
        green_code, green_out, green_counts = pytest(check["green"])
        result["others_stayed_green"] = (green_code == 0 and not green_counts.get("skipped")
                                         and green_counts.get("passed", 0) == len(check["green"]))
        if not result["others_stayed_green"]:
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
    parser.add_argument("--out", default=str(HERE / "counter-check-results-stage5"))
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
        print("%-30s %s" % (r["id"], r["verdict"]), flush=True)
    (out / "counter-checks.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    lines = []
    for r in results:
        lines.append("%s  [%s]  %s" % (r["verdict"], r["id"], r["area"]))
        lines.append("    file %s  before %s" % (r["file"], r.get("before_sha256", "")[:16]))
        lines.append("    named test %s" % r["test"])
        if "mutated_sha256" in r:
            lines.append("    control green, counts %s" % r.get("control_counts"))
            lines.append("    mutated %s  landed=%s" % (r["mutated_sha256"][:16], r.get("landed")))
            lines.append("    named test exit=%s counts=%s  predicted reason %r present=%s"
                         % (r.get("mutated_exit"), r.get("mutated_counts"), r["expect"],
                            r.get("failed_for_the_predicted_reason")))
            lines.append("    siblings %s stayed green=%s" % (r["green"],
                                                              r.get("others_stayed_green")))
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
