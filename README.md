# CodexClean

[简体中文说明](README.zh-CN.md)

Archiving or deleting a Codex chat does not always remove its local workspace folders, generated files, or build artifacts. Over time, these leftovers can consume disk space, while the connection between a chat and its files becomes difficult to trace by hand. CodexClean helps you identify and review likely leftovers before moving them to a folder you choose.

CodexClean is an independent Windows command-line tool. It reads local session metadata, protects folders used by active chats, and records selected moves in a manifest so they can be restored. It never permanently deletes files.

## What it identifies

| Status | Meaning | Eligible for moving? |
| --- | --- | --- |
| `archived` | A local archived session points to this folder, and no active session does | Yes |
| `unlinked` | The folder has the usual `work` and `outputs` subfolders, but no local session points to it | Only with `--include-unlinked` |
| `protected-active` | An active local session points to this folder | No |
| `ignored-unrecognized` | No session points to the folder and it lacks the expected markers | No |

**Unlinked does not prove deleted.** Missing session metadata, sync differences, or work done outside local Codex can also leave a folder unlinked. Review the exact paths before moving any of them.

CodexClean deliberately scans only date-organized, projectless folders under `Documents/Codex` by default. It does not claim ownership of arbitrary repositories, download folders, cloud attachments, or every file Codex may have edited elsewhere.

## Requirements

- Windows and Python 3.14 or newer. The script uses only the Python standard library.
- Read access to your own `~/.codex/state_5.sqlite` and session logs.
- The review directory must be on the same drive as the workspace root, outside both the workspace root and Codex home. This allows a directory rename instead of a cross-drive copy-and-delete operation.

Defaults:

- Workspace root: `%USERPROFILE%\Documents\Codex`
- Codex home: `%CODEX_HOME%`, or `%USERPROFILE%\.codex` if unset
- Review directory: `%USERPROFILE%\Documents\Codex_Review`

## Quick start

Run these commands in PowerShell from the repository folder:

```powershell
py -3 .\codex_residue_review.py scan
py -3 .\codex_residue_review.py move --all
```

The second command is a **preview**. It selects confirmed archived folders only. After checking every path, add `--yes` to move them:

```powershell
py -3 .\codex_residue_review.py move --all --yes
```

To include unlinked candidates, preview first, then run the same command with `--yes` after reviewing the list:

```powershell
py -3 .\codex_residue_review.py move --all --include-unlinked
py -3 .\codex_residue_review.py move --all --include-unlinked --yes
```

You can select individual folder IDs from the scan result:

```powershell
py -3 .\codex_residue_review.py move --ids 2026-01-01/example --include-unlinked
```

Set a different review directory by placing the global option before the subcommand:

```powershell
py -3 .\codex_residue_review.py --quarantine "C:\Codex_Review" move --all
```

Each move creates a timestamped run folder in the review directory. `items/` preserves the original date/folder layout, and `manifest.json` records the source, destination, classification, and move state. Open the run folder to inspect its contents. Delete unwanted files manually after review.
