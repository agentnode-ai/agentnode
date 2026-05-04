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
    sub.add_parser("doctor", help="Diagnose your setup")

    # reset
    sub.add_parser("reset", help="Reset configuration")

    # config
    config_parser = sub.add_parser("config", help="View or modify settings")
    config_sub = config_parser.add_subparsers(dest="config_action")
    get_parser = config_sub.add_parser("get", help="Get a config value")
    get_parser.add_argument("key")
    set_parser = config_sub.add_parser("set", help="Set a config value")
    set_parser.add_argument("key")
    set_parser.add_argument("value")

    # search
    search_parser = sub.add_parser("search", help="Search for capabilities")
    search_parser.add_argument("query", nargs="+")

    # install
    install_parser = sub.add_parser("install", help="Install a capability")
    install_parser.add_argument("capability")
    install_parser.add_argument("--version", dest="pkg_version", default=None)
    install_parser.add_argument("--yes", "-y", action="store_true")

    # run
    run_parser = sub.add_parser("run", help="Run a capability")
    run_parser.add_argument("capability")
    run_parser.add_argument("--input", dest="input_data", default=None)
    run_parser.add_argument("--file", dest="file_path", default=None)
    run_parser.add_argument("--raw", action="store_true")

    # remove
    remove_parser = sub.add_parser("remove", help="Remove a capability")
    remove_parser.add_argument("capability")
    remove_parser.add_argument("--yes", "-y", action="store_true")

    # validate
    validate_parser = sub.add_parser("validate", help="Validate package before publishing")
    validate_parser.add_argument("path", nargs="?", default=".", help="Package directory (default: current)")

    # capabilities
    cap_parser = sub.add_parser("capabilities", help="List installed capabilities")
    cap_sub = cap_parser.add_subparsers(dest="cap_action")
    show_parser = cap_sub.add_parser("show", help="Show capability details")
    show_parser.add_argument("name")

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
            return commands.cmd_doctor()
        if args.command == "reset":
            return commands.cmd_reset()
        if args.command == "config":
            if args.config_action == "get":
                return commands.cmd_config_get(args.key)
            if args.config_action == "set":
                return commands.cmd_config_set(args.key, args.value)
            return commands.cmd_config()
        if args.command == "search":
            return commands.cmd_search(" ".join(args.query))
        if args.command == "install":
            return commands.cmd_install(
                args.capability, version=args.pkg_version, yes=args.yes
            )
        if args.command == "run":
            return commands.cmd_run(
                args.capability,
                input_data=args.input_data,
                file_path=args.file_path,
                raw=args.raw,
            )
        if args.command == "remove":
            return commands.cmd_remove(args.capability, yes=args.yes)
        if args.command == "validate":
            return commands.cmd_validate(args.path)
        if args.command == "capabilities":
            if args.cap_action == "show":
                return commands.cmd_capabilities_show(args.name)
            return commands.cmd_capabilities()
        parser.print_help()
        return 1
    except KeyboardInterrupt:
        print()
        return 130


def cli() -> None:
    """Entry point for console_scripts."""
    sys.exit(main())
