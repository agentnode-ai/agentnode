"""The page a person uses, served by the gateway that already answers their client's calls.

Two things about this are deliberate and are the reason it is a separate module rather than a few
lines in the request handler.

**It decides nothing.** It reads one file off disk and writes it back. It does not look at a token,
does not touch `GatewayState`, and cannot reach the worker or the runtime. Everything the page goes
on to do, it does by calling the same `/v1/op/` addresses any other client calls, with the same
credential, through the same dispatcher -- so the browser is not a privileged client, it is one
more caller of a contract that was already there. If this module could be deleted without changing
what anyone is allowed to do, it is the right shape; it can.

**It is one file with nothing fetched.** No bundler, no package manifest, no content delivery
network. The gateway this serves from is meant to be reachable by one person on one host with the
port closed to everything else, and a page that pulls a script from the internet would be a page
that stops working exactly there -- or, worse, one that works until the day the script changes
under it. It also means the automated browser test needs no build step before it can open it.
"""
from __future__ import annotations

import os

#: Where the page lives. Not under `/v1/`, because it is not part of the protocol and versioning it
#: alongside the contract would suggest a client could depend on its shape. Nothing should.
PATH = "/console"

HERE = os.path.dirname(os.path.abspath(__file__))

#: What may be served, and the only thing that may be. Not a directory listing and not a path
#: joined to anything a request supplied -- an explicit table, because "serve files from here" is
#: how a static route becomes a way to read the state directory.
FILES = {
    "/console": ("index.html", "text/html; charset=utf-8"),
    "/console/": ("index.html", "text/html; charset=utf-8"),
    # Separate files rather than inline blocks, and that is a security decision rather than
    # tidiness. A content security policy that has to allow inline code allows ANY inline code,
    # which is most of what an injection wants; with these in files the policy can say
    # `script-src 'self'` and mean it.
    "/console/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/console/app.css": ("app.css", "text/css; charset=utf-8"),
}


def ours(path: str) -> bool:
    return path.split("?", 1)[0] in FILES


def handle(path: str):
    """Return `(status, content_type, body)` for one of ours. No request, no headers, no decision."""
    name, content_type = FILES[path.split("?", 1)[0]]
    with open(os.path.join(HERE, name), "rb") as fh:
        return 200, content_type, fh.read()
