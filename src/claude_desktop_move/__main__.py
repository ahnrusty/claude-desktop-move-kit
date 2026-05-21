"""Command line interface for Claude Desktop Move Kit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from claude_desktop_move.core import (
    build_export_plan,
    default_paths,
    export_archive,
    restore_archive,
    validate_mcp_config,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-desktop-move",
        description="Safely export, restore, and validate portable Claude Desktop local data.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    detect = subcommands.add_parser(
        "detect", help="Show conventional Claude Desktop paths."
    )
    detect.add_argument(
        "--os",
        default=sys.platform,
        help="Platform to resolve: darwin, win32, or linux.",
    )
    detect.add_argument(
        "--home", type=Path, default=Path.home(), help="Home directory to resolve from."
    )
    detect.set_defaults(func=_cmd_detect)

    plan = subcommands.add_parser(
        "plan", help="Show what would be exported from a Claude Desktop data directory."
    )
    plan.add_argument(
        "--source", type=Path, default=None, help="Claude Desktop data directory."
    )
    plan.add_argument(
        "--os", default=sys.platform, help="Source platform label for the manifest."
    )
    plan.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    plan.set_defaults(func=_cmd_plan)

    export = subcommands.add_parser("export", help="Create a safe migration archive.")
    export.add_argument(
        "--source", type=Path, default=None, help="Claude Desktop data directory."
    )
    export.add_argument(
        "--output", type=Path, required=True, help="Archive path to write."
    )
    export.add_argument(
        "--os", default=sys.platform, help="Source platform label for the manifest."
    )
    export.set_defaults(func=_cmd_export)

    restore = subcommands.add_parser("restore", help="Restore a migration archive.")
    restore.add_argument(
        "--archive",
        type=Path,
        required=True,
        help="Archive created by the export command.",
    )
    restore.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Target Claude Desktop data directory.",
    )
    restore.add_argument(
        "--dry-run", action="store_true", help="Plan restore without writing files."
    )
    restore.add_argument(
        "--yes",
        action="store_true",
        help="Confirm restore writes without an interactive prompt.",
    )
    restore.set_defaults(func=_cmd_restore)

    validate = subcommands.add_parser(
        "validate", help="Validate a Claude Desktop MCP config file."
    )
    validate.add_argument(
        "--config", type=Path, default=None, help="Path to claude_desktop_config.json."
    )
    validate.set_defaults(func=_cmd_validate)

    return parser


def _cmd_detect(args: argparse.Namespace) -> int:
    paths = default_paths(args.os, args.home)
    print(f"app_support: {paths.app_support}")
    print(f"logs: {paths.logs}")
    for config in paths.config_candidates:
        status = "exists" if config.exists() else "missing"
        print(f"config_candidate: {config} ({status})")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    source = args.source or default_paths(args.os).app_support
    plan = build_export_plan(source, args.os)

    if args.json:
        print(
            json.dumps(
                {
                    "source_root": str(plan.source_root),
                    "source_os": plan.source_os,
                    "entries": [
                        {
                            "relative_path": entry.relative_path.as_posix(),
                            "reason": entry.reason,
                            "size": entry.size,
                            "sha256": entry.sha256,
                        }
                        for entry in plan.entries
                    ],
                    "exclusions": [
                        {
                            "relative_path": entry.relative_path.as_posix(),
                            "reason": entry.reason,
                        }
                        for entry in plan.exclusions
                    ],
                },
                indent=2,
            )
        )
        return 0

    print(f"source: {plan.source_root}")
    print(f"will_export: {len(plan.entries)} file(s)")
    for entry in plan.entries:
        print(f"  + {entry.relative_path.as_posix()} ({entry.reason})")
    print(f"will_skip: {len(plan.exclusions)} file(s)")
    for entry in plan.exclusions[:20]:
        print(f"  - {entry.relative_path.as_posix()} ({entry.reason})")
    if len(plan.exclusions) > 20:
        print(f"  ... {len(plan.exclusions) - 20} more skipped file(s)")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    source = args.source or default_paths(args.os).app_support
    result = export_archive(source, args.output, args.os)
    print(f"archive: {result.archive_path}")
    print(f"exported_files: {result.exported_files}")
    print(f"skipped_files: {result.skipped_files}")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    target = args.target or default_paths().app_support
    dry_run = args.dry_run or not args.yes

    if dry_run and not args.dry_run:
        print("dry_run: true")
        print("pass --yes to restore files")

    result = restore_archive(args.archive, target, dry_run=dry_run)
    print(f"target: {result.target_root}")
    print(f"planned_files: {result.planned_files}")
    print(f"restored_files: {result.restored_files}")
    print(f"backups_created: {result.backups_created}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    config = args.config
    if config is None:
        candidates = default_paths().config_candidates
        config = next(
            (candidate for candidate in candidates if candidate.exists()), candidates[0]
        )

    result = validate_mcp_config(config)
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
