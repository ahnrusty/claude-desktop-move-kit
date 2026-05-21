"""Core backup and restore logic for Claude Desktop local data."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Mapping

MANIFEST_NAME = "manifest.json"
PAYLOAD_PREFIX = "payload"
TOOL_VERSION = "0.1.0"
MAX_ARCHIVE_ENTRIES = 100_000
MAX_UNCOMPRESSED_BYTES = 5 * 1024 * 1024 * 1024
BROWSER_DATABASE_DIR = "In" + "dexedDB"

INCLUDED_DIRS = {
    "claude-code-sessions",
    "local-agent-mode-sessions",
    "configLibrary",
}

INCLUDED_FILES = {
    "claude_desktop_config.json",
    "cowork_account_settings.json",
}

EXCLUDED_NAMES = {
    BROWSER_DATABASE_DIR,
    "Local Storage",
    "Session Storage",
    "GPUCache",
    "Code Cache",
    "Cache",
    "DawnCache",
    "blob_storage",
    "vm_bundles",
    "sessiondata.img",
    "Cookies",
    "Cookies-journal",
}


@dataclass(frozen=True)
class ClaudePaths:
    """Resolved Claude Desktop paths for a platform."""

    app_support: Path
    logs: Path
    config_candidates: tuple[Path, ...]


@dataclass(frozen=True)
class PlanEntry:
    """One file selected for export or exclusion."""

    source_path: Path
    relative_path: Path
    kind: str
    reason: str
    size: int = 0
    sha256: str = ""


@dataclass(frozen=True)
class ExportPlan:
    """Files that will be exported and files deliberately skipped."""

    source_root: Path
    source_os: str
    entries: tuple[PlanEntry, ...]
    exclusions: tuple[PlanEntry, ...]


@dataclass(frozen=True)
class ExportResult:
    """Result returned after writing an archive."""

    archive_path: Path
    exported_files: int
    skipped_files: int


@dataclass(frozen=True)
class RestoreResult:
    """Result returned after planning or restoring an archive."""

    target_root: Path
    planned_files: int
    restored_files: int
    backups_created: int
    dry_run: bool


@dataclass(frozen=True)
class ValidationResult:
    """Validation status for a local config file."""

    ok: bool
    message: str


def default_paths(
    os_name: str | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ClaudePaths:
    """Return conventional Claude Desktop paths for macOS or Windows.

    Args:
        os_name: Platform name, usually ``sys.platform``.
        home: User home directory.
        env: Environment mapping for Windows app data variables.
    """

    platform = os_name or sys.platform
    user_home = home or Path.home()
    environment = env or os.environ

    if platform == "darwin":
        app_support = user_home / "Library" / "Application Support" / "Claude"
        return ClaudePaths(
            app_support=app_support,
            logs=user_home / "Library" / "Logs" / "Claude",
            config_candidates=(app_support / "claude_desktop_config.json",),
        )

    if platform.startswith("win"):
        roaming = _join_windows_app_dir(
            _env_or_default(
                environment, "APPDATA", str(user_home / "AppData" / "Roaming")
            )
        )
        local = _join_windows_app_dir(
            _env_or_default(
                environment, "LOCALAPPDATA", str(user_home / "AppData" / "Local")
            )
        )
        return ClaudePaths(
            app_support=local,
            logs=_join_windows_child(local, "logs"),
            config_candidates=(
                _join_windows_child(roaming, "claude_desktop_config.json"),
                _join_windows_child(local, "claude_desktop_config.json"),
            ),
        )

    app_support = user_home / ".config" / "Claude"
    return ClaudePaths(
        app_support=app_support,
        logs=user_home / ".config" / "Claude" / "logs",
        config_candidates=(app_support / "claude_desktop_config.json",),
    )


def build_export_plan(source_root: Path, source_os: str | None = None) -> ExportPlan:
    """Select safe Claude Desktop files for export."""

    root = source_root.expanduser().resolve()
    entries: list[PlanEntry] = []
    exclusions: list[PlanEntry] = []

    if not root.exists():
        return ExportPlan(root, source_os or sys.platform, tuple(), tuple())

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            exclusions.append(_entry(path, relative, "exclude", "excluded symlink"))
            continue
        if not path.is_file():
            continue

        excluded_reason = _excluded_reason(relative)
        if excluded_reason:
            exclusions.append(_entry(path, relative, "exclude", excluded_reason))
            continue

        include_reason = _included_reason(relative)
        if include_reason:
            entries.append(
                _entry(path, relative, "migrate", include_reason, with_hash=True)
            )
        else:
            exclusions.append(
                _entry(path, relative, "exclude", "not part of safe migration payload")
            )

    return ExportPlan(
        root, source_os or sys.platform, tuple(entries), tuple(exclusions)
    )


def export_archive(
    source_root: Path, archive_path: Path, source_os: str | None = None
) -> ExportResult:
    """Write a portable migration archive with a manifest and safe payload."""

    plan = build_export_plan(source_root, source_os)
    destination = archive_path.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "tool": "claude-desktop-move-kit",
        "version": TOOL_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "source_os": plan.source_os,
        "source_root": str(plan.source_root),
        "entries": [
            {
                "relative_path": entry.relative_path.as_posix(),
                "size": entry.size,
                "sha256": entry.sha256,
                "reason": entry.reason,
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
        "post_restore": [
            "Install Claude Desktop on the target machine first.",
            "Restore while Claude Desktop is closed.",
            "Reopen Claude Desktop and sign in again.",
            "Verify MCP servers, Code sessions, and Cowork state.",
        ],
    }

    temp_path = _temporary_sibling(destination)
    try:
        with zipfile.ZipFile(
            temp_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(
                MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True)
            )
            for entry in plan.entries:
                archive.write(
                    entry.source_path,
                    f"{PAYLOAD_PREFIX}/{entry.relative_path.as_posix()}",
                )
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    _chmod_owner_only(destination)
    return ExportResult(destination, len(plan.entries), len(plan.exclusions))


def restore_archive(
    archive_path: Path, target_root: Path, dry_run: bool = True
) -> RestoreResult:
    """Restore a migration archive into a Claude Desktop app data directory."""

    archive_file = archive_path.expanduser().resolve()
    target = target_root.expanduser().resolve()
    backups = 0
    restored = 0

    with zipfile.ZipFile(archive_file) as archive:
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME))
        except KeyError as exc:
            raise ValueError("missing manifest.json") from exc
        entries = _validate_manifest_entries(archive, manifest)

        if dry_run:
            return RestoreResult(target, len(entries), 0, 0, True)

        for item in entries:
            relative = item["relative"]
            payload_name = item["payload_name"]
            destination = _safe_destination(target, relative)
            _refuse_symlink_destination(target, destination)
            destination.parent.mkdir(parents=True, exist_ok=True)

            expected_hash = item["sha256"]

            if destination.exists():
                if destination.is_file() and _sha256_file(destination) == expected_hash:
                    continue
                backup_path = _backup_path(destination)
                destination.rename(backup_path)
                backups += 1

            _write_payload_atomically(archive, payload_name, destination, expected_hash)
            restored += 1

    return RestoreResult(target, len(entries), restored, backups, False)


def validate_mcp_config(config_path: Path) -> ValidationResult:
    """Validate the Claude Desktop MCP config JSON file."""

    path = config_path.expanduser()
    if not path.exists():
        return ValidationResult(False, f"missing config: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ValidationResult(False, f"invalid JSON in {path}: {exc.msg}")

    if not isinstance(data, dict):
        return ValidationResult(False, "config must be a JSON object")

    if "mcpServers" in data and not isinstance(data["mcpServers"], dict):
        return ValidationResult(False, "mcpServers must be an object when present")

    return ValidationResult(True, f"valid config: {path}")


def _included_reason(relative: Path) -> str:
    first_part = relative.parts[0]
    if len(relative.parts) == 1 and first_part in INCLUDED_FILES:
        return "portable Claude Desktop config or Cowork preference"
    if first_part in INCLUDED_DIRS:
        return f"portable Claude Desktop local data under {first_part}"
    if (
        len(relative.parts) == 1
        and first_part.startswith("cowork_")
        and first_part.endswith(".json")
    ):
        return "portable Cowork local settings"
    return ""


def _excluded_reason(relative: Path) -> str:
    for part in relative.parts:
        if part in EXCLUDED_NAMES:
            return f"excluded {part}: cache, auth/session state, or regenerable runtime data"
    return ""


def _entry(
    path: Path,
    relative: Path,
    kind: str,
    reason: str,
    with_hash: bool = False,
) -> PlanEntry:
    size = path.stat().st_size
    return PlanEntry(
        source_path=path,
        relative_path=relative,
        kind=kind,
        reason=reason,
        size=size,
        sha256=_sha256_file(path) if with_hash else "",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("manifest entry relative_path must be a non-empty string")
    if "\\" in value:
        raise ValueError(f"unsafe archive path uses backslashes: {value}")
    if ":" in value:
        raise ValueError(f"unsafe archive path uses drive or stream syntax: {value}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe archive path: {value}")
    return relative


def _backup_path(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    candidate = path.with_name(f"{path.name}.backup-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.backup-{stamp}-{counter}")
        counter += 1
    return candidate


def _join_windows_app_dir(base: str) -> Path:
    """Join a Windows app-data string without POSIX separator normalization."""

    return Path(base.rstrip("\\/") + "\\Claude")


def _join_windows_child(base: Path, child: str) -> Path:
    """Join a Windows child path without POSIX separator normalization."""

    return Path(str(base).rstrip("\\/") + f"\\{child}")


def _env_or_default(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    return value if value else default


def _validate_manifest_entries(
    archive: zipfile.ZipFile, manifest: object
) -> list[dict[str, str | PurePosixPath]]:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")

    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("manifest entries must be a list")
    if len(raw_entries) > MAX_ARCHIVE_ENTRIES:
        raise ValueError(f"too many archive entries: {len(raw_entries)}")

    archive_names = set(archive.namelist())
    validated: list[dict[str, str | PurePosixPath]] = []
    total_uncompressed_bytes = 0

    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("manifest entry must be a JSON object")
        relative = _safe_relative_path(raw_entry.get("relative_path", ""))
        expected_hash = raw_entry.get("sha256")
        if not _is_sha256(expected_hash):
            raise ValueError(f"missing or invalid sha256 for {relative.as_posix()}")

        payload_name = f"{PAYLOAD_PREFIX}/{relative.as_posix()}"
        if payload_name not in archive_names:
            raise ValueError(f"missing payload for {relative.as_posix()}")
        total_uncompressed_bytes += archive.getinfo(payload_name).file_size
        if total_uncompressed_bytes > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("archive payload is too large")
        if _sha256_zip_member(archive, payload_name) != expected_hash:
            raise ValueError(f"checksum mismatch for {relative.as_posix()}")

        validated.append(
            {
                "relative": relative,
                "payload_name": payload_name,
                "sha256": expected_hash,
            }
        )

    return validated


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _sha256_zip_member(archive: zipfile.ZipFile, payload_name: str) -> str:
    digest = hashlib.sha256()
    with archive.open(payload_name) as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_destination(target: Path, relative: PurePosixPath) -> Path:
    destination = target / Path(*relative.parts)
    resolved_target = target.resolve()
    resolved_destination = destination.resolve(strict=False)
    if not _is_relative_to(resolved_destination, resolved_target):
        raise ValueError(f"restore path escapes target: {relative.as_posix()}")
    return destination


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _refuse_symlink_destination(target: Path, destination: Path) -> None:
    relative = destination.relative_to(target)
    current = target
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing to restore through symlink: {current}")


def _write_payload_atomically(
    archive: zipfile.ZipFile, payload_name: str, destination: Path, expected_hash: str
) -> None:
    temp_path = _temporary_sibling(destination)
    digest = hashlib.sha256()
    try:
        with archive.open(payload_name) as source, temp_path.open("wb") as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                target.write(chunk)
        if digest.hexdigest() != expected_hash:
            raise ValueError(f"checksum mismatch for {payload_name}")
        os.replace(temp_path, destination)
        _chmod_owner_only(destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _chmod_owner_only(path: Path) -> None:
    if os.name == "posix":
        path.chmod(0o600)


def _temporary_sibling(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.tmp-",
        dir=destination.parent,
        delete=False,
    )
    handle.close()
    return Path(handle.name)
