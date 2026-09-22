"""`agentnode pki` -- this deployment's issuer, and a service asking it for an identity.

Additive: nothing existing is renamed or moved.

Run as root:
    agentnode pki init    --deployment <id>
    agentnode pki add     --role worker|gateway --instance <name> --account <user> --tls-dir <dir>
    agentnode pki enroll  --request <dir>/request.json
    agentnode pki renew   --request <dir>/renewal.json
    agentnode pki show
    agentnode pki revoke  --serial <hex>
    agentnode pki recover --role worker|gateway --instance <name> --account <user> --tls-dir <dir>
    agentnode pki publish
    agentnode pki tick    [--floor-dir /var/lib/agentnode-floor]      (the root run, on a timer)
    agentnode pki floor init    [--floor-dir ...] [--tolerance S] [--max-age S] [--after-loss]
    agentnode pki floor recover --role worker|gateway [--floor-dir ...]
    agentnode pki floor show    [--floor-dir ...]

Run as the SERVICE's account, so its private key is made where it stays:
    agentnode pki request --tls-dir <dir> [--renew]
    agentnode pki install --tls-dir <dir> [--trust <ca.pem>]     (after root renewed)

Nothing here prints a key, a secret or a certificate body. What is printed is names, serials and
fingerprints.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from agentnode_sdk.cli.output import bold


def _issuer(args):
    from agentnode_sdk.pki.issuer import DEFAULT_CA_DIR, DEFAULT_TRUST_DIR, Issuer

    return Issuer(getattr(args, "ca_dir", None) or DEFAULT_CA_DIR,
                  getattr(args, "trust_dir", None) or DEFAULT_TRUST_DIR)


def _account(name: str):
    import pwd

    entry = pwd.getpwnam(name)
    return entry.pw_uid, entry.pw_gid


def cmd_init(args) -> int:
    issuer = _issuer(args)
    issuer.initialise(str(args.deployment))
    print()
    print(f"  {bold('An issuer for deployment ' + args.deployment + ' exists.')}")
    print(f"  Its key is in {issuer.ca_dir} (root only); its certificate is in {issuer.trust_dir}.")
    return 0


def cmd_add(args) -> int:
    issuer = _issuer(args)
    uid, gid = _account(str(args.account))
    folder = Path(args.tls_dir)
    name = issuer.add(str(args.role), str(args.instance), secret_at=folder / "secret",
                      owner_uid=uid, owner_gid=gid, deliver_to=folder / "cert.pem")
    print()
    print(f"  Entry {name} exists. Its single-use secret is in {folder / 'secret'},")
    print(f"  readable only by {args.account}. That account runs, as itself:")
    print(f"    agentnode pki request --tls-dir {folder}")
    return 0


def _read_request(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_enroll(args) -> int:
    from agentnode_sdk.pki.issuer import Indeterminate, IssuanceRefused

    body = _read_request(args.request)
    try:
        _issuer(args).enroll(body["csr"].encode("ascii"), str(body.get("secret") or ""))
    except IssuanceRefused as refused:
        print(f"  Refused: {refused}")
        return 1
    except Indeterminate as unsure:
        print(f"  Not done, and not refused: {unsure}")
        print("  Run the same command again once the disk behaves; it reconciles, it does not")
        print("  issue twice.")
        return 3
    print("  Issued and delivered.")
    return 0


def cmd_renew(args) -> int:
    from agentnode_sdk.pki.issuer import Indeterminate, IssuanceRefused

    body = _read_request(args.request)
    try:
        _issuer(args).renew(body["csr"].encode("ascii"), body["current"].encode("ascii"),
                            bytes.fromhex(body["signature"]))
    except IssuanceRefused as refused:
        print(f"  Refused: {refused}")
        return 1
    except Indeterminate as unsure:
        print(f"  Not done, and not refused: {unsure}")
        return 3
    print("  Renewed; the new certificate is beside the current one as cert.pem.next.")
    return 0


def cmd_request(args) -> int:
    from agentnode_sdk.pki.issuer import make_request

    folder = Path(args.tls_dir)
    secret = ""
    if not args.renew:
        secret = (folder / "secret").read_text(encoding="ascii").strip()
    out = make_request(folder, secret, renew=bool(args.renew))
    print(f"  A request is at {out}. The private key stayed in {folder}.")
    return 0


def cmd_show(args) -> int:
    print(json.dumps(_issuer(args).inventory(), indent=1, sort_keys=True))
    return 0


def _floor_dir(args):
    from agentnode_sdk.pki.floor import DEFAULT_DIR

    return getattr(args, "floor_dir", None) or DEFAULT_DIR


def cmd_revoke(args) -> int:
    from agentnode_sdk.pki.issuer import Indeterminate, IssuanceRefused

    try:
        done = _issuer(args).revoke(str(args.serial))
    except (IssuanceRefused, Indeterminate) as refused:
        print(f"  Not revoked: {refused}")
        return 1
    if done["effective"]:
        print(f"  Revoked {done['serial']} ({done['entry']}). The list carrying it is published")
        print("  and durable: the revocation is in effect from now.")
        return 0
    # Recorded, and NOT in effect. Said as such, because an operator who read "revoked" here
    # would stop worrying about a key that still works.
    print(f"  Recorded {done['serial']} ({done['entry']}) as revoked, but the list could NOT be")
    print("  published durably, so the revocation is NOT in effect yet. The root run retries,")
    print("  and holds back the time floor meanwhile, so the services stop serving after the")
    print("  floor's maximum age if it keeps failing.")
    return 3


def cmd_recover(args) -> int:
    from agentnode_sdk.pki.issuer import Indeterminate, IssuanceRefused

    uid, gid = _account(str(args.account))
    folder = Path(args.tls_dir)
    try:
        done = _issuer(args).recover_entry(str(args.role), str(args.instance),
                                           secret_at=folder / "secret", owner_uid=uid,
                                           owner_gid=gid)
    except (IssuanceRefused, Indeterminate) as refused:
        print(f"  Not recovered: {refused}")
        return 1
    print(f"  {done['entry']}: renewal locked, {len(done['revoked'])} certificate(s) revoked,")
    print(f"  and a fresh single-use secret is in {folder / 'secret'} for {args.account}.")
    if not done["effective"]:
        print("  The list could NOT be published durably: the revocations are NOT in effect yet.")
        return 3
    print("  The list is published and durable. That account now makes a FRESH key:")
    print(f"    rm {folder}/key.pem && agentnode pki request --tls-dir {folder}")
    return 0


def cmd_publish(args) -> int:
    done = _issuer(args).publish()
    print("  Published list number %d." % done["number"] if done["published"] else
          "  The list could NOT be published durably.")
    return 0 if done["published"] else 3


def cmd_tick(args) -> int:
    report = _issuer(args).tick(_floor_dir(args))
    print(json.dumps(report, indent=1, sort_keys=True))
    failed = report.get("pending_revocation") or any(
        not str(v).startswith("written") for v in report.get("floors", {}).values())
    return 3 if failed else 0


def cmd_floor(args) -> int:
    from agentnode_sdk.pki import floor as _floor
    from agentnode_sdk.pki.issuer import IssuanceRefused

    action = getattr(args, "floor_command", None)
    if action == "init":
        try:
            done = _issuer(args).floor_init(_floor_dir(args), tolerance_s=float(args.tolerance),
                                            max_age_s=float(args.max_age),
                                            after_loss=bool(args.after_loss))
        except IssuanceRefused as refused:
            print(f"  Not set up: {refused}")
            return 1
        for role, path in sorted(done.items()):
            print(f"  {role}: {path} (root's, read-only to the services). It is written by")
            print("  `agentnode pki tick`; until then the services do not serve over TLS.")
        return 0
    if action == "recover":
        done = _issuer(args).floor_recover(_floor_dir(args), str(args.role))
        print(json.dumps(done, indent=1, sort_keys=True))
        return 0
    if action == "show":
        shown = {}
        for role in _floor.ROLES:
            path = _floor.path_for(_floor_dir(args), role)
            try:
                state = _floor.parse(path.read_bytes())
                shown[role] = {"floor": state.floor, "generation": state.generation,
                               "elapsed_total": state.elapsed_total,
                               "granted_total": state.granted_total,
                               "tolerance_s": state.tolerance_s, "max_age_s": state.max_age_s,
                               "boot_id": state.boot_id}
            except (OSError, _floor.FloorUnusable) as exc:
                shown[role] = "unusable: %s" % type(exc).__name__
        print(json.dumps(shown, indent=1, sort_keys=True))
        return 0
    print(__doc__)
    return 2


def cmd_install(args) -> int:
    from agentnode_sdk.pki.issuer import DEFAULT_TRUST_DIR, IssuanceRefused, install_renewal

    anchor = getattr(args, "trust", None) or str(Path(DEFAULT_TRUST_DIR) / "ca.pem")
    try:
        done = install_renewal(args.tls_dir, anchor)
    except (IssuanceRefused, OSError, ValueError) as refused:
        print(f"  Not installed: {refused}")
        return 1
    print(f"  Installed the renewed certificate {done['serial']}. The running service takes it")
    print("  up on its next connection; nothing needs a restart.")
    return 0


def dispatch(args) -> int:
    handlers = {"init": cmd_init, "add": cmd_add, "enroll": cmd_enroll, "renew": cmd_renew,
                "request": cmd_request, "show": cmd_show, "revoke": cmd_revoke,
                "recover": cmd_recover, "publish": cmd_publish, "tick": cmd_tick,
                "floor": cmd_floor, "install": cmd_install}
    action = getattr(args, "pki_command", None)
    if action not in handlers:
        print(__doc__)
        return 2
    # `request` and `install` are the service's own: its key, its directory, its account.
    if action not in ("request", "install") and hasattr(os, "geteuid") and os.geteuid() != 0:
        print("  The issuer is root's; this command is not.")
        return 1
    return handlers[action](args)


def add_parser(subparsers) -> None:
    pki = subparsers.add_parser("pki", help="This deployment's issuer for gateway and worker")
    actions = pki.add_subparsers(dest="pki_command")

    def where(p):
        p.add_argument("--ca-dir", dest="ca_dir", default=None)
        p.add_argument("--trust-dir", dest="trust_dir", default=None)

    p = actions.add_parser("init", help="Create the issuer (root)")
    p.add_argument("--deployment", required=True)
    where(p)
    p = actions.add_parser("add", help="Create an entry and its single-use secret (root)")
    p.add_argument("--role", required=True, choices=("gateway", "worker"))
    p.add_argument("--instance", required=True)
    p.add_argument("--account", required=True)
    p.add_argument("--tls-dir", dest="tls_dir", required=True)
    where(p)
    p = actions.add_parser("enroll", help="Issue against a request (root)")
    p.add_argument("--request", required=True)
    where(p)
    p = actions.add_parser("renew", help="Renew against a signed request (root)")
    p.add_argument("--request", required=True)
    where(p)
    p = actions.add_parser("request", help="Make a key and a request, as the service")
    p.add_argument("--tls-dir", dest="tls_dir", required=True)
    p.add_argument("--renew", action="store_true")
    p = actions.add_parser("show", help="The inventory, without secrets or certificate bodies")
    where(p)
    p = actions.add_parser("revoke", help="Revoke a certificate and publish the list (root)")
    p.add_argument("--serial", required=True)
    where(p)
    p = actions.add_parser("recover", help="Recover an entry from a compromised key (root)")
    p.add_argument("--role", required=True, choices=("gateway", "worker"))
    p.add_argument("--instance", required=True)
    p.add_argument("--account", required=True)
    p.add_argument("--tls-dir", dest="tls_dir", required=True)
    where(p)
    p = actions.add_parser("publish", help="Sign and publish a fresh revocation list (root)")
    where(p)
    p = actions.add_parser("tick", help="The root run: list, overlaps, floors (root, on a timer)")
    p.add_argument("--floor-dir", dest="floor_dir", default=None)
    where(p)
    p = actions.add_parser("floor", help="The time floor the services judge by (root)")
    floor_actions = p.add_subparsers(dest="floor_command")
    f = floor_actions.add_parser("init", help="Set the floor files up, once")
    f.add_argument("--floor-dir", dest="floor_dir", default=None)
    f.add_argument("--tolerance", type=float, default=600.0)
    f.add_argument("--max-age", dest="max_age", type=float, default=900.0)
    f.add_argument("--after-loss", dest="after_loss", action="store_true")
    where(f)
    f = floor_actions.add_parser("recover", help="Move a floor that stands too far ahead")
    f.add_argument("--role", required=True, choices=("gateway", "worker"))
    f.add_argument("--floor-dir", dest="floor_dir", default=None)
    where(f)
    f = floor_actions.add_parser("show", help="The floors, their counters and their age limit")
    f.add_argument("--floor-dir", dest="floor_dir", default=None)
    where(f)
    p = actions.add_parser("install", help="Put a renewed certificate in place, as the service")
    p.add_argument("--tls-dir", dest="tls_dir", required=True)
    p.add_argument("--trust", default=None)


__all__ = ["add_parser", "dispatch"]
