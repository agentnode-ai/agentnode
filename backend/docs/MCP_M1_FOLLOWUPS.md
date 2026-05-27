# MCP M1 Follow-ups

Tracking doc for known gaps identified during M1 review. Not M1 blockers.

## 1. SDK Gap: mcp_command not yet consumed by install path

**Status:** Backend complete, SDK pending

Backend delivers `mcp_server` (incl. `command`) in install-info response.
TG-4 signing middleware signs the full response body, covering `mcp_server.command`
at the transport layer.

SDK `lock_integrity.py` already lists `mcp_command` in `CANONICAL_FIELDS` and
`SENSITIVE_FIELDS` — the lockfile integrity design is ready.

**Gap:** `client.install()` does not extract `mcp_server` from the install-info
response and does not pass `mcp_command` to `install_package()`. The lockfile
protection for `mcp_command` is therefore prepared but not active in the normal
MCP install path.

**Fix:** Next SDK release that adds MCP install support must:
1. Parse `mcp_server` from `InstallMetadata` response
2. Forward `mcp_command=meta.mcp_server.command` to `install_package()`
3. Verify lockfile entry contains `mcp_command` after install

## 2. System Publisher Guard Coverage

**Status:** API + Service guarded, future paths not yet

`is_system_publisher` publish-block is enforced at:
- `app/packages/router.py` — API endpoint (403)
- `app/packages/service.py` — `publish_package()` service layer (403)

**Not yet covered** (paths don't exist yet):
- Admin CLI publish commands
- Bulk import / batch seed tools
- Future package clone or fork APIs
- Internal background jobs that create packages

**Rule:** Any new code path that creates or modifies packages under a publisher
must check `publisher.is_system_publisher` and reject writes. The guard must
be applied at introduction time, not retrofitted.
