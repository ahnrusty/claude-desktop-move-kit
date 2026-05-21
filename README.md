# Claude Desktop Move Kit

Safe migration helper for Claude Desktop local data on macOS and Windows.

This project exports the local Claude Desktop files that are useful when moving to a new laptop, then restores them into the correct app-data folder on the new machine. It is intentionally conservative: it migrates portable configuration and local session files, while skipping caches, auth/session state, Electron runtime data, and large regenerable bundles.

## Disclaimer

This project is an unofficial personal tool. It is not affiliated with, endorsed by, or supported by Anthropic.

It was vibe coded using Cursor with AI assistance. The code has tests and safety checks, but you should still review what it plans to export before running it on real data. Use it at your own risk, especially if your `claude_desktop_config.json` contains MCP tokens, local paths, or other sensitive configuration.

## Why This Exists

Claude Desktop stores a mix of local and server-side data:

- Regular Claude account chat history is normally server-side and should come back after signing in.
- Claude Desktop MCP configuration is local.
- Code tab sessions can be local-only.
- Cowork/local Desktop settings can be local-only.
- Electron/Chromium cache and auth state should not be copied between machines.

Manual folder copying is easy to get wrong. This tool creates a small migration archive with a manifest, checksums, safe-path validation, and dry-run restore behavior.

## What It Migrates

The safe payload currently includes:

- `claude_desktop_config.json`
- `claude-code-sessions/`
- `local-agent-mode-sessions/`
- `configLibrary/`
- `cowork_*.json`, including `cowork_account_settings.json`

## What It Skips

The tool deliberately excludes:

- Browser database storage
- `Local Storage/`
- `Session Storage/`
- `Cache/`
- `GPUCache/`
- `Code Cache/`
- `DawnCache/`
- `blob_storage/`
- `vm_bundles/`
- `sessiondata.img`
- `Cookies`
- Symlinked files

Those skipped items are usually cache, auth/session state, regenerable runtime state, or risky to copy between machines.

## Security Model

The export archive should be treated as sensitive.

Safety features:

- Restore defaults to dry-run unless `--yes` is passed.
- Existing target files are backed up before overwrite.
- Archive entries include SHA-256 checksums.
- Restore validates the full manifest before writing any file.
- Restore validates checksums before and during write.
- Archive paths reject absolute paths, `..`, Windows drive syntax, and backslashes.
- Symlinked source files are skipped.
- Restore refuses symlink destinations.
- Restore writes through temporary files and uses atomic replace.
- Export archives and restored files are set to owner-only permissions on POSIX systems.
- Archives are capped at 100,000 entries and 5 GiB uncompressed payload.

Important limitation:

- The archive is not encrypted. If your MCP config contains secrets, store and transfer the zip carefully.

## Requirements

- Python 3.10 or newer
- macOS or Windows
- Claude Desktop installed on the source and target machines

No third-party Python dependencies are required.

## Install

Clone the repo:

```bash
git clone https://github.com/ahnrusty/claude-desktop-move-kit.git
cd claude-desktop-move-kit
```

Run directly from source:

```bash
PYTHONPATH=src python3 -m claude_desktop_move --help
```

Optional editable install:

```bash
python3 -m pip install -e .
claude-desktop-move --help
```

## macOS Migration

Close Claude Desktop before exporting or restoring.

On the old Mac:

```bash
PYTHONPATH=src python3 -m claude_desktop_move detect
PYTHONPATH=src python3 -m claude_desktop_move plan
PYTHONPATH=src python3 -m claude_desktop_move export --output ~/Desktop/claude-desktop-move.zip
```

Move `~/Desktop/claude-desktop-move.zip` to the new Mac.

On the new Mac:

```bash
PYTHONPATH=src python3 -m claude_desktop_move restore --archive ~/Desktop/claude-desktop-move.zip --dry-run
PYTHONPATH=src python3 -m claude_desktop_move restore --archive ~/Desktop/claude-desktop-move.zip --yes
PYTHONPATH=src python3 -m claude_desktop_move validate
```

Reopen Claude Desktop and sign in again. Re-authentication is expected because auth tokens are machine-bound.

## Windows Migration

Close Claude Desktop before exporting or restoring.

Run in PowerShell with Python installed.

On the old Windows laptop:

```powershell
$env:PYTHONPATH = "src"
python -m claude_desktop_move detect --os win32
python -m claude_desktop_move plan --os win32
python -m claude_desktop_move export --os win32 --output "$env:USERPROFILE\Desktop\claude-desktop-move.zip"
```

Move `claude-desktop-move.zip` to the new Windows laptop.

On the new Windows laptop:

```powershell
$env:PYTHONPATH = "src"
python -m claude_desktop_move restore --archive "$env:USERPROFILE\Desktop\claude-desktop-move.zip" --dry-run
python -m claude_desktop_move restore --archive "$env:USERPROFILE\Desktop\claude-desktop-move.zip" --yes
python -m claude_desktop_move validate
```

Windows path detection checks both Roaming and Local Claude config locations because Claude Desktop behavior has varied across versions and installer types.

## Command Reference

Show detected Claude Desktop paths:

```bash
PYTHONPATH=src python3 -m claude_desktop_move detect
```

Preview export payload:

```bash
PYTHONPATH=src python3 -m claude_desktop_move plan
```

Emit a machine-readable export plan:

```bash
PYTHONPATH=src python3 -m claude_desktop_move plan --json
```

Create an archive:

```bash
PYTHONPATH=src python3 -m claude_desktop_move export --output ~/Desktop/claude-desktop-move.zip
```

Dry-run restore:

```bash
PYTHONPATH=src python3 -m claude_desktop_move restore --archive ~/Desktop/claude-desktop-move.zip --dry-run
```

Restore for real:

```bash
PYTHONPATH=src python3 -m claude_desktop_move restore --archive ~/Desktop/claude-desktop-move.zip --yes
```

Validate MCP config:

```bash
PYTHONPATH=src python3 -m claude_desktop_move validate
```

Each subcommand has its own help:

```bash
PYTHONPATH=src python3 -m claude_desktop_move detect --help
PYTHONPATH=src python3 -m claude_desktop_move plan --help
PYTHONPATH=src python3 -m claude_desktop_move export --help
PYTHONPATH=src python3 -m claude_desktop_move restore --help
PYTHONPATH=src python3 -m claude_desktop_move validate --help
```

## Default Paths

macOS:

```text
~/Library/Application Support/Claude/
~/Library/Application Support/Claude/claude_desktop_config.json
~/Library/Logs/Claude/
```

Windows:

```text
%LOCALAPPDATA%\Claude\
%LOCALAPPDATA%\Claude\claude_desktop_config.json
%APPDATA%\Claude\claude_desktop_config.json
```

## Testing

Run the test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Compile check:

```bash
python3 -m compileall -q src tests
```

## Project Status

This is an early personal migration helper. It has been smoke-tested on macOS using synthetic Claude Desktop data and validates the local macOS Claude Desktop config path. Windows support is path-aware but still needs real Windows machine validation before broad use.

## License

No license has been selected yet. Until a license is added, all rights are reserved by the repository owner.
