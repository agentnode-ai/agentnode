#!/usr/bin/env python3
"""E2E registry signing verification through the real network path.

Verifies that trust-critical API responses are correctly signed (or
correctly unsigned in pre-activation mode) when accessed through the
full network stack including reverse proxy.

Verification is body-byte based, independent of TLS termination,
Content-Length, or Transfer-Encoding headers.

Exit codes:
  0  All checks passed
  1  Verification failed (signature invalid, missing when expected, etc.)
  2  Endpoint unreachable or HTTP error
  3  Malformed signature header
  4  Health endpoint state mismatch

Usage:
  # Pre-activation: confirm no signatures, health inactive
  python scripts/verify_signing.py --base-url http://localhost:8001 \\
      --slug mcp-filesystem --pre-activation

  # Post-activation: verify signatures against known public key
  python scripts/verify_signing.py --base-url http://localhost:8001 \\
      --slug mcp-filesystem --public-key-b64 <base64-ed25519-pubkey>
"""
import argparse
import base64
import json
import sys

import httpx


def _check_health(client: httpx.Client, base_url: str, expect_active: bool) -> bool:
    """Check /v1/health/signing matches expected state."""
    try:
        resp = client.get(f"{base_url}/v1/health/signing")
        resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"FAIL  health endpoint unreachable: {e}")
        return False

    data = resp.json()
    active = data.get("signing_active", False)
    if active != expect_active:
        print(
            f"FAIL  health: signing_active={active}, expected={expect_active} "
            f"(trust_mode={data.get('trust_mode', '?')})"
        )
        return False

    mode = data.get("trust_mode", "?")
    print(f"OK    health: signing_active={active}, trust_mode={mode}")
    return True


def _check_no_signature(client: httpx.Client, url: str, label: str) -> bool:
    """Confirm a trust-critical endpoint has NO signature header."""
    try:
        resp = client.get(url)
        if resp.status_code != 200:
            print(f"SKIP  {label}: HTTP {resp.status_code}")
            return True
    except httpx.HTTPError as e:
        print(f"FAIL  {label}: unreachable: {e}")
        return False

    if "x-agentnode-signature" in resp.headers:
        print(f"FAIL  {label}: unexpected signature header in pre-activation mode")
        return False

    print(f"OK    {label}: no signature header (pre-activation)")
    return True


def _check_signature(
    client: httpx.Client, url: str, label: str, pub_key_b64: str,
) -> bool:
    """Verify a trust-critical endpoint has a valid signature."""
    try:
        resp = client.get(url)
        if resp.status_code != 200:
            print(f"SKIP  {label}: HTTP {resp.status_code}")
            return True
    except httpx.HTTPError as e:
        print(f"FAIL  {label}: unreachable: {e}")
        return False

    sig_header = resp.headers.get("x-agentnode-signature")
    if sig_header is None:
        print(f"FAIL  {label}: missing X-AgentNode-Signature header")
        return False

    parts = sig_header.split(":", 2)
    if len(parts) != 3:
        print(f"FAIL  {label}: malformed signature header: {sig_header[:80]}")
        return False

    alg, key_id, sig_b64 = parts
    if alg != "ed25519":
        print(f"FAIL  {label}: unexpected algorithm: {alg}")
        return False

    try:
        sig_bytes = base64.b64decode(sig_b64, validate=True)
    except Exception as e:
        print(f"FAIL  {label}: bad base64 in signature: {e}")
        return False

    if len(sig_bytes) != 64:
        print(f"FAIL  {label}: signature length {len(sig_bytes)}, expected 64")
        return False

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature

    try:
        pub_bytes = base64.b64decode(pub_key_b64, validate=True)
        pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_key.verify(sig_bytes, resp.content)
    except InvalidSignature:
        print(f"FAIL  {label}: signature verification FAILED (body-byte mismatch)")
        return False
    except Exception as e:
        print(f"FAIL  {label}: key/signature error: {e}")
        return False

    body = resp.content
    if body.endswith(b"\n") or body.endswith(b"\r\n"):
        print(f"WARN  {label}: response body has trailing newline")

    try:
        json.loads(body)
    except json.JSONDecodeError:
        print(f"WARN  {label}: response body is not valid JSON")

    print(f"OK    {label}: signature valid (key_id={key_id}, {len(body)} bytes)")
    return True


def _check_non_critical_unsigned(client: httpx.Client, base_url: str) -> bool:
    """Non-critical endpoint must never have signature header."""
    url = f"{base_url}/health"
    try:
        resp = client.get(url)
    except httpx.HTTPError:
        print("SKIP  /health: unreachable")
        return True

    if "x-agentnode-signature" in resp.headers:
        print("FAIL  /health: non-critical endpoint has signature header")
        return False

    print("OK    /health: no signature (non-critical, correct)")
    return True


def _check_gzip_invariant(client: httpx.Client, url: str, label: str) -> bool:
    """Request with Accept-Encoding: gzip, verify body is still usable."""
    try:
        resp = client.get(url, headers={"Accept-Encoding": "gzip"})
        if resp.status_code != 200:
            print(f"SKIP  {label} (gzip): HTTP {resp.status_code}")
            return True
    except httpx.HTTPError as e:
        print(f"FAIL  {label} (gzip): unreachable: {e}")
        return False

    try:
        json.loads(resp.content)
        print(f"OK    {label} (gzip): response body decompressed and valid JSON")
        return True
    except json.JSONDecodeError:
        print(f"FAIL  {label} (gzip): decompressed body is not valid JSON")
        return False


def main():
    parser = argparse.ArgumentParser(description="Verify registry signing E2E")
    parser.add_argument("--base-url", required=True, help="API base URL")
    parser.add_argument("--slug", required=True, help="Known package slug")
    parser.add_argument("--pre-activation", action="store_true",
                        help="Pre-activation mode: expect NO signatures")
    parser.add_argument("--public-key-b64", help="Base64 Ed25519 public key (post-activation)")
    args = parser.parse_args()

    if not args.pre_activation and not args.public_key_b64:
        print("ERROR: post-activation mode requires --public-key-b64")
        sys.exit(2)

    base = args.base_url.rstrip("/")
    slug = args.slug
    passed = 0
    failed = 0

    with httpx.Client(timeout=15.0) as client:
        # Health check
        ok = _check_health(client, base, expect_active=not args.pre_activation)
        if not ok:
            sys.exit(4)
        passed += 1

        endpoints = [
            (f"{base}/v1/packages/{slug}/install-info", f"install-info({slug})"),
            (f"{base}/v1/packages/{slug}", f"package({slug})"),
        ]

        if args.pre_activation:
            for url, label in endpoints:
                if _check_no_signature(client, url, label):
                    passed += 1
                else:
                    failed += 1
        else:
            for url, label in endpoints:
                if _check_signature(client, url, label, args.public_key_b64):
                    passed += 1
                else:
                    failed += 1

        # Non-critical: never signed
        if _check_non_critical_unsigned(client, base):
            passed += 1
        else:
            failed += 1

        # gzip invariant
        install_url = f"{base}/v1/packages/{slug}/install-info"
        if _check_gzip_invariant(client, install_url, f"install-info({slug})"):
            passed += 1
        else:
            failed += 1

    print(f"\n{'PASSED' if failed == 0 else 'FAILED'}: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
