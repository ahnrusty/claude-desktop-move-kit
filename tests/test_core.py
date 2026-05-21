import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_desktop_move.core import (  # noqa: E402
    BROWSER_DATABASE_DIR,
    build_export_plan,
    default_paths,
    export_archive,
    restore_archive,
    validate_mcp_config,
)


class ClaudeDesktopMoveTests(unittest.TestCase):
    def test_default_paths_for_macos(self):
        paths = default_paths("darwin", Path("/Users/fr"))

        self.assertEqual(
            paths.app_support, Path("/Users/fr/Library/Application Support/Claude")
        )
        self.assertEqual(paths.logs, Path("/Users/fr/Library/Logs/Claude"))
        self.assertIn(
            paths.app_support / "claude_desktop_config.json", paths.config_candidates
        )

    def test_default_paths_for_windows_include_roaming_and_local(self):
        env = {
            "APPDATA": r"C:\Users\fr\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\fr\AppData\Local",
        }

        paths = default_paths("win32", Path(r"C:\Users\fr"), env)

        self.assertEqual(paths.app_support, Path(r"C:\Users\fr\AppData\Local\Claude"))
        self.assertIn(
            Path(r"C:\Users\fr\AppData\Roaming\Claude\claude_desktop_config.json"),
            paths.config_candidates,
        )
        self.assertIn(
            Path(r"C:\Users\fr\AppData\Local\Claude\claude_desktop_config.json"),
            paths.config_candidates,
        )

    def test_export_plan_includes_safe_payload_and_excludes_regenerable_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_sample_claude_data(root)

            plan = build_export_plan(root, source_os="darwin")
            included = {entry.relative_path.as_posix() for entry in plan.entries}
            excluded = {entry.relative_path.as_posix() for entry in plan.exclusions}

            self.assertIn("claude_desktop_config.json", included)
            self.assertIn("claude-code-sessions/account/org/local_1.json", included)
            self.assertIn(
                "local-agent-mode-sessions/account/org/local_legacy.json", included
            )
            self.assertIn("cowork_account_settings.json", included)
            self.assertIn("configLibrary/profile.json", included)
            self.assertIn(f"{BROWSER_DATABASE_DIR}/db.sqlite", excluded)
            self.assertIn("Local Storage/leveldb/000003.log", excluded)
            self.assertIn("Session Storage/session.json", excluded)
            self.assertIn("vm_bundles/bundle.bin", excluded)
            self.assertIn("sessiondata.img", excluded)

    def test_export_archive_writes_manifest_and_safe_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Claude"
            archive_path = Path(tmp) / "claude-desktop-move.zip"
            self._write_sample_claude_data(root)

            result = export_archive(root, archive_path, source_os="darwin")

            self.assertEqual(result.archive_path, archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("manifest.json"))

            self.assertIn("payload/claude_desktop_config.json", names)
            self.assertIn(
                "payload/claude-code-sessions/account/org/local_1.json", names
            )
            self.assertNotIn(f"payload/{BROWSER_DATABASE_DIR}/db.sqlite", names)
            self.assertEqual(manifest["source_os"], "darwin")
            self.assertTrue(manifest["entries"][0]["sha256"])

    def test_restore_archive_dry_run_does_not_write_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Claude"
            archive_path = Path(tmp) / "move.zip"
            target = Path(tmp) / "NewClaude"
            self._write_sample_claude_data(root)
            export_archive(root, archive_path, source_os="darwin")

            result = restore_archive(archive_path, target, dry_run=True)

            self.assertGreater(result.planned_files, 0)
            self.assertFalse((target / "claude_desktop_config.json").exists())

    def test_restore_archive_writes_files_and_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Claude"
            archive_path = Path(tmp) / "move.zip"
            target = Path(tmp) / "NewClaude"
            self._write_sample_claude_data(root)
            export_archive(root, archive_path, source_os="darwin")
            target.mkdir()
            (target / "claude_desktop_config.json").write_text("old", encoding="utf-8")

            result = restore_archive(archive_path, target, dry_run=False)

            self.assertGreater(result.restored_files, 0)
            self.assertEqual(
                json.loads((target / "claude_desktop_config.json").read_text())[
                    "mcpServers"
                ],
                {},
            )
            backups = list(target.glob("claude_desktop_config.json.backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old")

    def test_validate_mcp_config_reports_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "claude_desktop_config.json"
            config_path.write_text("{not-json", encoding="utf-8")

            result = validate_mcp_config(config_path)

            self.assertFalse(result.ok)
            self.assertIn("invalid JSON", result.message)

    def test_export_plan_skips_symlinked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Claude"
            outside = Path(tmp) / "secret.txt"
            root.mkdir()
            outside.write_text("do-not-export", encoding="utf-8")
            (root / "claude_desktop_config.json").symlink_to(outside)

            plan = build_export_plan(root, source_os="darwin")

            self.assertEqual(plan.entries, ())
            self.assertEqual(plan.exclusions[0].reason, "excluded symlink")

    def test_restore_rejects_path_traversal_entries_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "evil.zip"
            target = Path(tmp) / "target"
            target.mkdir()
            self._write_custom_archive(archive_path, "../evil.txt", b"evil")

            with self.assertRaises(ValueError):
                restore_archive(archive_path, target, dry_run=False)

            self.assertFalse((Path(tmp) / "evil.txt").exists())

    def test_restore_rejects_windows_style_path_traversal_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "evil.zip"
            target = Path(tmp) / "target"
            target.mkdir()
            self._write_custom_archive(archive_path, r"..\evil.txt", b"evil")

            with self.assertRaises(ValueError):
                restore_archive(archive_path, target, dry_run=False)

            self.assertFalse((target / r"..\evil.txt").exists())

    def test_restore_rejects_missing_hash_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "missing-hash.zip"
            target = Path(tmp) / "target"
            target.mkdir()
            self._write_custom_archive(
                archive_path, "claude_desktop_config.json", b"{}", sha256=""
            )

            with self.assertRaises(ValueError):
                restore_archive(archive_path, target, dry_run=False)

            self.assertFalse((target / "claude_desktop_config.json").exists())

    def test_restore_validates_missing_payload_before_writing_any_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "missing-payload.zip"
            target = Path(tmp) / "target"
            target.mkdir()
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "entries": [
                                {
                                    "relative_path": "claude_desktop_config.json",
                                    "sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
                                },
                                {
                                    "relative_path": "claude-code-sessions/a/o/local_1.json",
                                    "sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
                                },
                            ]
                        }
                    ),
                )
                archive.writestr("payload/claude_desktop_config.json", b"{}")

            with self.assertRaises(ValueError):
                restore_archive(archive_path, target, dry_run=False)

            self.assertFalse((target / "claude_desktop_config.json").exists())

    def test_restore_refuses_symlink_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Claude"
            archive_path = Path(tmp) / "move.zip"
            target = Path(tmp) / "target"
            outside = Path(tmp) / "outside.json"
            self._write_sample_claude_data(root)
            export_archive(root, archive_path, source_os="darwin")
            target.mkdir()
            outside.write_text("outside", encoding="utf-8")
            (target / "claude_desktop_config.json").symlink_to(outside)

            with self.assertRaises(ValueError):
                restore_archive(archive_path, target, dry_run=False)

            self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

    def test_restore_sets_owner_only_permissions_on_posix(self):
        if os.name != "posix":
            self.skipTest("POSIX permission check")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Claude"
            archive_path = Path(tmp) / "move.zip"
            target = Path(tmp) / "target"
            self._write_sample_claude_data(root)
            export_archive(root, archive_path, source_os="darwin")

            restore_archive(archive_path, target, dry_run=False)

            mode = (target / "claude_desktop_config.json").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_restore_missing_manifest_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "missing-manifest.zip"
            target = Path(tmp) / "target"
            target.mkdir()
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("payload/claude_desktop_config.json", b"{}")

            with self.assertRaises(ValueError):
                restore_archive(archive_path, target, dry_run=False)

    def _write_sample_claude_data(self, root: Path):
        (root / "claude-code-sessions/account/org").mkdir(parents=True)
        (root / "local-agent-mode-sessions/account/org").mkdir(parents=True)
        (root / "configLibrary").mkdir(parents=True)
        (root / BROWSER_DATABASE_DIR).mkdir(parents=True)
        (root / "Local Storage/leveldb").mkdir(parents=True)
        (root / "Session Storage").mkdir(parents=True)
        (root / "vm_bundles").mkdir(parents=True)

        (root / "claude_desktop_config.json").write_text(
            json.dumps({"mcpServers": {}}), encoding="utf-8"
        )
        (root / "claude-code-sessions/account/org/local_1.json").write_text(
            "{}", encoding="utf-8"
        )
        (root / "local-agent-mode-sessions/account/org/local_legacy.json").write_text(
            "{}", encoding="utf-8"
        )
        (root / "cowork_account_settings.json").write_text("{}", encoding="utf-8")
        (root / "configLibrary/profile.json").write_text("{}", encoding="utf-8")
        (root / BROWSER_DATABASE_DIR / "db.sqlite").write_text("skip", encoding="utf-8")
        (root / "Local Storage/leveldb/000003.log").write_text("skip", encoding="utf-8")
        (root / "Session Storage/session.json").write_text("skip", encoding="utf-8")
        (root / "vm_bundles/bundle.bin").write_bytes(b"skip")
        (root / "sessiondata.img").write_bytes(b"skip")

    def _write_custom_archive(
        self,
        archive_path: Path,
        relative_path: str,
        payload: bytes,
        sha256: str | None = None,
    ):
        import hashlib

        digest = hashlib.sha256(payload).hexdigest() if sha256 is None else sha256
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "entries": [
                            {
                                "relative_path": relative_path,
                                "sha256": digest,
                            }
                        ]
                    }
                ),
            )
            archive.writestr(f"payload/{relative_path}", payload)


if __name__ == "__main__":
    unittest.main()
