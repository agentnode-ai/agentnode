"""`agentnode pki` -- this deployment's issuer, and a service asking it for an identity.

Additive: nothing existing is renamed or moved.

Run as root:
    agentnode pki init    --deployment <id>
    agentnode pki add     --role worker|gateway --instance <name> --account <user> --tls-dir <dir>
    agentnode pki enroll  --request <dir>/request.json
    agentnode pki renew   --request <dir>/renewal.json
    agentnode pki show

Run as the SERVICE's account, so its private key is made where it stays:
    agentnode pki request --tls-dir <dir> [--renew]

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


def dispatch(args) -> int:
    handlers = {"init": cmd_init, "add": cmd_add, "enroll": cmd_enroll, "renew": cmd_renew,
                "request": cmd_request, "show": cmd_show}
    action = getattr(args, "pki_command", None)
    if action not in handlers:
        print(__doc__)
        return 2
    if action != "request" and hasattr(os, "geteuid") and os.geteuid() != 0:
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


__all__ = ["add_parser", "dispatch"]
