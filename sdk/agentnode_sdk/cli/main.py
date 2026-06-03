"""AgentNode CLI entry point — argparse routing."""
from __future__ import annotations

import argparse
import sys

import agentnode_sdk
from agentnode_sdk.cli.output import set_color


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentnode",
        description="AgentNode — capability infrastructure for AI agents",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"agentnode {agentnode_sdk.__version__}",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    sub = parser.add_subparsers(dest="command")

    # setup
    sub.add_parser("setup", help="Run setup wizard")

    # doctor
    doctor_parser = sub.add_parser("doctor", help="Diagnose setup and detect missing capabilities")
    doctor_parser.add_argument("--fix", action="store_true", help="Auto-install suggested packages")
    doctor_parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")

    # mcp
    mcp_parser = sub.add_parser("mcp", help="MCP server management")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command")
    mcp_doctor = mcp_sub.add_parser("doctor", help="Check MCP server readiness")
    mcp_doctor.add_argument("slug", help="MCP package slug")
    mcp_doctor.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    mcp_doctor.add_argument("--skip-start", action="store_true", help="Skip subprocess start test")
    mcp_verify_p = mcp_sub.add_parser("verify", help="Verify an agentnode.yaml manifest")
    mcp_verify_p.add_argument("path", nargs="?", default=".", help="Directory or file path")
    mcp_verify_p.add_argument("--test", action="store_true", help="Run protocol test (starts the MCP)")
    mcp_verify_p.add_argument("--json", dest="json_output", action="store_true", help="JSON report output")
    mcp_status_p = mcp_sub.add_parser("status", help="Check submission status")
    mcp_status_p.add_argument("submission_id", help="Submission ID from agentnode mcp submit")
    mcp_status_p.add_argument("--token", default=None, help="API key (default: AGENTNODE_API_KEY)")
    mcp_status_p.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    mcp_submit_p = mcp_sub.add_parser("submit", help="Submit MCP for catalog review")
    mcp_submit_p.add_argument("path", nargs="?", default=".", help="Directory containing agentnode.yaml")
    mcp_submit_p.add_argument("--test", action="store_true", help="Run protocol test before submitting")
    mcp_submit_p.add_argument("--token", default=None, help="API key (default: AGENTNODE_API_KEY)")
    mcp_submit_p.add_argument("--dry-run", action="store_true", help="Verify only, don't submit")

    # reset
    sub.add_parser("reset", help="Reset configuration")

    # auth
    auth_parser = sub.add_parser("auth", help="Manage provider credentials")
    auth_sub = auth_parser.add_subparsers(dest="auth_action")
    auth_set_p = auth_sub.add_parser("set", help="Store a credential for a provider")
    auth_set_p.add_argument("provider")
    auth_sub.add_parser("list", help="Show configured credentials")
    auth_rm_p = auth_sub.add_parser("remove", help="Remove a stored credential")
    auth_rm_p.add_argument("provider")
    auth_sub.add_parser("status", help="Check credential status for installed packages")

    # config
    config_parser = sub.add_parser("config", help="View or modify settings")
    config_sub = config_parser.add_subparsers(dest="config_action")
    config_sub.add_parser("list", help="Show all settings with descriptions and allowed values")
    get_parser = config_sub.add_parser("get", help="Get a config value")
    get_parser.add_argument("key")
    set_parser = config_sub.add_parser("set", help="Set a config value")
    set_parser.add_argument("key")
    set_parser.add_argument("value")

    # search
    search_parser = sub.add_parser("search", help="Search for capabilities")
    search_parser.add_argument("query", nargs="+")

    # discover
    discover_parser = sub.add_parser("discover", help="Browse trending, new, and categorized packages")
    discover_parser.add_argument("--category", "-c", default=None, help="Filter by category (connector, research, automation, data)")
    discover_parser.add_argument("--trending", action="store_true", help="Show top packages by installs")
    discover_parser.add_argument("--new", action="store_true", help="Show recently published packages")
    discover_parser.add_argument("--type", dest="pkg_type", choices=["toolpack", "agent"], default=None, help="Filter by package type")

    # resolve
    resolve_parser = sub.add_parser("resolve", help="Find best packages for a capability")
    resolve_parser.add_argument("capability", nargs="+", help="Capability ID(s) to resolve")
    resolve_parser.add_argument("--framework", default=None, help="Preferred framework for scoring")
    resolve_parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")

    # recommend
    recommend_parser = sub.add_parser("recommend", help="Suggest packages based on your installed setup")
    recommend_parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")

    # install
    install_parser = sub.add_parser("install", help="Install a capability")
    install_parser.add_argument("capability")
    install_parser.add_argument("--version", dest="pkg_version", default=None)
    install_parser.add_argument("--yes", "-y", action="store_true")

    # run
    run_parser = sub.add_parser("run", help="Run a capability or natural language task")
    run_parser.add_argument("capability", nargs="+", help="Package slug or natural language task")
    run_parser.add_argument("--input", dest="input_data", default=None)
    run_parser.add_argument("--file", dest="file_path", default=None)
    run_parser.add_argument("--raw", action="store_true", help="Output only result payload")
    run_parser.add_argument("--json", dest="json_output", action="store_true", help="Full structured JSON output")
    run_parser.add_argument("--explain", action="store_true", help="Show reasoning: why this capability and package")
    run_parser.add_argument("--dry-run", action="store_true", help="Show plan without executing")

    # remove
    remove_parser = sub.add_parser("remove", help="Remove a capability")
    remove_parser.add_argument("capability")
    remove_parser.add_argument("--yes", "-y", action="store_true")

    # audit
    audit_parser = sub.add_parser("audit", help="Show recent policy decisions")
    audit_parser.add_argument("-n", "--limit", type=int, default=20, help="Number of entries")
    audit_parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    audit_parser.add_argument("--event", "-e", type=str, default=None, help="Filter by event type")
    audit_parser.add_argument("--slug", "-s", type=str, default=None, help="Filter by package slug")
    audit_parser.add_argument("--action", "-a", type=str, default=None, help="Filter by action (allow/deny/prompt)")
    audit_parser.add_argument("--tool", dest="tool_name", type=str, default=None, help="Filter by tool name")

    # logs
    logs_parser = sub.add_parser("logs", help="Show agent run logs")
    logs_parser.add_argument("run_id", nargs="?", default=None, help="Show specific run")
    logs_parser.add_argument("-n", "--limit", type=int, default=10, help="Number of runs to list")
    logs_parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")

    # init
    init_parser = sub.add_parser("init", help="Create a new package from template")
    init_parser.add_argument("name", nargs="?", default=None, help="Package ID (optional, prompted if omitted)")
    init_parser.add_argument("--type", dest="template_type", choices=["local", "api", "file", "agent", "skill"], default=None)

    # validate
    validate_parser = sub.add_parser("validate", help="Validate package before publishing")
    validate_parser.add_argument("path", nargs="?", default=".", help="Package directory (default: current)")

    # verify-local
    verify_parser = sub.add_parser("verify-local", help="Run verification pipeline locally")
    verify_parser.add_argument("path", nargs="?", default=".", help="Package directory (default: current)")

    # publish
    publish_parser = sub.add_parser("publish", help="Publish package to AgentNode registry")
    publish_parser.add_argument("path", nargs="?", default=".", help="Package directory (default: current)")
    publish_parser.add_argument("--dry-run", action="store_true", help="Validate and build without uploading")
    publish_parser.add_argument("--skip-validate", action="store_true", help="Continue despite validation errors")
    publish_parser.add_argument("--token", default=None, help="API key (default: AGENTNODE_API_KEY env)")

    # record-cases
    record_parser = sub.add_parser("record-cases", help="Record VCR cassettes for API verification cases")
    record_parser.add_argument("path", nargs="?", default=".", help="Package directory (default: current)")
    record_parser.add_argument("--strict", action="store_true", help="Exit with error if cassettes contain risky fields")

    # inspect
    inspect_parser = sub.add_parser("inspect", help="Security report for an installed package")
    inspect_parser.add_argument("slug", help="Package slug to inspect")
    inspect_parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")

    # capabilities
    cap_parser = sub.add_parser("capabilities", help="List installed capabilities")
    cap_sub = cap_parser.add_subparsers(dest="cap_action")
    show_parser = cap_sub.add_parser("show", help="Show capability details")
    show_parser.add_argument("name")

    # skill
    skill_parser = sub.add_parser("skill", help="Manage installed skills")
    skill_sub = skill_parser.add_subparsers(dest="skill_action")
    skill_sub.add_parser("list", help="List installed skills")
    skill_show_parser = skill_sub.add_parser("show", help="Show skill details")
    skill_show_parser.add_argument("slug", help="Skill slug")
    skill_show_parser.add_argument("--render", nargs="*", metavar="KEY=VALUE", help="Render prompt with variables")
    skill_show_parser.add_argument("--raw", action="store_true", help="Output raw prompt text only")

    # guard
    guard_parser = sub.add_parser("guard", help="Guard policy and status")
    guard_sub = guard_parser.add_subparsers(dest="guard_action")
    guard_policy_parser = guard_sub.add_parser("policy", help="Show resolved guard policy")
    guard_policy_parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    guard_status_parser = guard_sub.add_parser("status", help="Show guard activity summary")
    guard_status_parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    guard_set_parser = guard_sub.add_parser("set", help="Set a guard action policy")
    guard_set_parser.add_argument("action_type", help="Action type (e.g. delete, execute, read)")
    guard_set_parser.add_argument("value", help="Policy value (allow, prompt, deny)")
    guard_set_parser.add_argument("--tool", dest="tool_key", default=None, help="Per-tool override (slug/tool_name)")
    guard_unset_parser = guard_sub.add_parser("unset", help="Remove a guard tool override")
    guard_unset_parser.add_argument("action_type", nargs="?", default=None, help="Action type to remove (omit to remove all)")
    guard_unset_parser.add_argument("--tool", dest="tool_key", required=True, help="Tool to unset (slug/tool_name)")
    guard_check_parser = guard_sub.add_parser("check", help="Dry-run guard check for a tool")
    guard_check_parser.add_argument("tool_key", help="slug/tool_name to check")
    guard_check_parser.add_argument("--action", default=None, help="Override action classification")
    guard_check_parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    guard_sub.add_parser("reset", help="Reset guard policies to defaults")

    # lock
    lock_parser = sub.add_parser("lock", help="Lockfile integrity management")
    lock_sub = lock_parser.add_subparsers(dest="lock_action")
    lock_seal_p = lock_sub.add_parser("seal", help="Compute integrity hashes for all entries")
    lock_seal_p.add_argument("--force", action="store_true", help="Recompute even if already sealed")
    lock_verify_p = lock_sub.add_parser("verify", help="Verify integrity of all entries")
    lock_verify_p.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    lock_verify_p.add_argument("--strict", action="store_true", help="Treat missing integrity as failure")
    lock_verify_p.add_argument("--online", action="store_true", help="Verify publisher key status against the registry")

    # serve
    sub.add_parser("serve", help="Start MCP server (stdio)")

    # Sandbox runtime image — explicit pull only (no auto-pull). Full guided
    # setup/repair UX is Sprint B.
    sandbox_parser = sub.add_parser("sandbox", help="Sandbox runtime image management")
    sandbox_sub = sandbox_parser.add_subparsers(dest="sandbox_action")
    sandbox_sub.add_parser("pull", help="Pull the pinned sandbox image (explicit, no auto-pull)")

    args = parser.parse_args(argv)

    if args.no_color:
        set_color(False)

    from agentnode_sdk.cli import commands

    try:
        if args.command is None:
            return commands.cmd_dashboard()
        if args.command == "setup":
            return commands.cmd_setup()
        if args.command == "doctor":
            return commands.cmd_doctor(fix=args.fix, json_output=args.json_output)
        if args.command == "mcp":
            if args.mcp_command == "doctor":
                from agentnode_sdk.cli.mcp_commands import cmd_mcp_doctor
                return cmd_mcp_doctor(args.slug, args.json_output, args.skip_start)
            if args.mcp_command == "verify":
                from agentnode_sdk.cli.mcp_verify import cmd_mcp_verify
                return cmd_mcp_verify(args.path, test=args.test, json_output=args.json_output)
            if args.mcp_command == "status":
                from agentnode_sdk.cli.mcp_status import cmd_mcp_status
                return cmd_mcp_status(args.submission_id, token=args.token, json_output=args.json_output)
            if args.mcp_command == "submit":
                from agentnode_sdk.cli.mcp_submit import cmd_mcp_submit
                return cmd_mcp_submit(args.path, token=args.token, test=args.test, dry_run=args.dry_run)
            mcp_parser.print_help()
            return 0
        if args.command == "sandbox":
            if args.sandbox_action == "pull":
                from agentnode_sdk.cli.sandbox_commands import cmd_sandbox_pull
                return cmd_sandbox_pull()
            sandbox_parser.print_help()
            return 0
        if args.command == "reset":
            return commands.cmd_reset()
        if args.command == "auth":
            from agentnode_sdk.cli.auth import (
                cmd_auth_set, cmd_auth_list, cmd_auth_remove, cmd_auth_status,
            )
            if args.auth_action == "set":
                return cmd_auth_set(args.provider)
            if args.auth_action == "list":
                return cmd_auth_list()
            if args.auth_action == "remove":
                return cmd_auth_remove(args.provider)
            if args.auth_action == "status":
                return cmd_auth_status()
            return cmd_auth_list()
        if args.command == "config":
            if args.config_action == "list":
                return commands.cmd_config_list()
            if args.config_action == "get":
                return commands.cmd_config_get(args.key)
            if args.config_action == "set":
                return commands.cmd_config_set(args.key, args.value)
            return commands.cmd_config()
        if args.command == "search":
            return commands.cmd_search(" ".join(args.query))
        if args.command == "discover":
            return commands.cmd_discover(
                category=args.category,
                trending=args.trending,
                new=args.new,
                package_type=args.pkg_type,
            )
        if args.command == "resolve":
            return commands.cmd_resolve(args.capability, framework=args.framework, json_output=args.json_output)
        if args.command == "recommend":
            return commands.cmd_recommend(json_output=args.json_output)
        if args.command == "install":
            return commands.cmd_install(
                args.capability, version=args.pkg_version, yes=args.yes
            )
        if args.command == "run":
            return commands.cmd_run(
                " ".join(args.capability),
                input_data=args.input_data,
                file_path=args.file_path,
                raw=args.raw,
                explain=args.explain,
                dry_run=args.dry_run,
                json_output=args.json_output,
            )
        if args.command == "remove":
            return commands.cmd_remove(args.capability, yes=args.yes)
        if args.command == "audit":
            from agentnode_sdk.cli.audit import cmd_audit
            return cmd_audit(
                limit=args.limit,
                json_output=args.json_output,
                event=args.event,
                slug=args.slug,
                action=args.action,
                tool_name=args.tool_name,
            )
        if args.command == "logs":
            return commands.cmd_logs(
                run_id=args.run_id, limit=args.limit, json_output=args.json_output,
            )
        if args.command == "init":
            return commands.cmd_init(name=args.name, template_type=args.template_type)
        if args.command == "validate":
            return commands.cmd_validate(args.path)
        if args.command == "verify-local":
            return commands.cmd_verify_local(args.path)
        if args.command == "publish":
            from agentnode_sdk.cli.publish import cmd_publish
            return cmd_publish(args.path, dry_run=args.dry_run, skip_validate=args.skip_validate, token=args.token)
        if args.command == "record-cases":
            return commands.cmd_record_cases(args.path, strict=args.strict)
        if args.command == "inspect":
            return commands.cmd_inspect(args.slug, json_output=args.json_output)
        if args.command == "capabilities":
            if args.cap_action == "show":
                return commands.cmd_capabilities_show(args.name)
            return commands.cmd_capabilities()
        if args.command == "skill":
            if args.skill_action == "show":
                render_vars = None
                if args.render:
                    render_vars = {}
                    for item in args.render:
                        if "=" not in item:
                            print(f"Invalid --render argument: {item} (expected KEY=VALUE)", file=sys.stderr)
                            return 1
                        k, v = item.split("=", 1)
                        if not k:
                            print(f"Invalid key in --render: {item}", file=sys.stderr)
                            return 1
                        render_vars[k] = v
                return commands.cmd_skill_show(args.slug, render_vars=render_vars, raw=args.raw)
            return commands.cmd_skill_list()
        if args.command == "guard":
            if args.guard_action == "policy":
                return commands.cmd_guard_policy(json_output=args.json_output)
            if args.guard_action == "status":
                return commands.cmd_guard_status(json_output=args.json_output)
            if args.guard_action == "set":
                return commands.cmd_guard_set(args.action_type, args.value, tool_key=args.tool_key)
            if args.guard_action == "unset":
                return commands.cmd_guard_unset(tool_key=args.tool_key, action_type=args.action_type)
            if args.guard_action == "check":
                return commands.cmd_guard_check(args.tool_key, action=args.action, json_output=args.json_output)
            if args.guard_action == "reset":
                return commands.cmd_guard_reset()
            guard_parser.print_help()
            return 1
        if args.command == "lock":
            if args.lock_action == "seal":
                return commands.cmd_lock_seal(force=args.force)
            if args.lock_action == "verify":
                return commands.cmd_lock_verify(
                    json_output=args.json_output,
                    strict=args.strict,
                    online=args.online,
                )
            lock_parser.print_help()
            return 1
        if args.command == "serve":
            from agentnode_sdk.cli.serve import cmd_serve
            return cmd_serve()
        parser.print_help()
        return 1
    except KeyboardInterrupt:
        print()
        return 130


def cli() -> None:
    """Entry point for console_scripts."""
    sys.exit(main())
