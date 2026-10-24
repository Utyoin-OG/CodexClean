#!/usr/bin/env python3
"""Review and quarantine projectless Codex workspace directories on Windows.

Requires Python 3.14+ for standard-library Zstandard session-log reading.
No command in this program permanently deletes files.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid

try:
    from compression import zstd
except ImportError:
    zstd = None


DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def canonical(value: str | Path) -> str:
    text = str(value)
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.abspath(os.path.normpath(text)))


def overlaps(left: str, right: str) -> bool:
    left = left.rstrip("\\/")
    right = right.rstrip("\\/")
    return left == right or left.startswith(right + os.sep) or right.startswith(left + os.sep)


def within(path: Path, root: Path) -> bool:
    p, r = canonical(path), canonical(root)
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def same_volume(left: Path, right: Path) -> bool:
    return os.path.splitdrive(canonical(left))[0] == os.path.splitdrive(canonical(right))[0]


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def metadata_from_log(path: Path) -> tuple[str, str] | None:
    if path.suffix == ".zst" and zstd is None:
        raise RuntimeError("Python 3.14 or newer is required for .zst session logs")
    opener = zstd.open if path.suffix == ".zst" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        first = stream.readline()
    if not first:
        return None
    data = json.loads(first)
    if data.get("type") != "session_meta":
        return None
    payload = data.get("payload") or {}
    thread_id = payload.get("id") or payload.get("session_id")
    cwd = payload.get("cwd")
    if isinstance(thread_id, str) and isinstance(cwd, str) and cwd:
        return thread_id, canonical(cwd)
    return None


def load_sessions(codex_home: Path) -> dict[str, tuple[str, str]]:
    """Return thread ID -> (canonical cwd, active/archived). Active wins."""
    database = codex_home / "state_5.sqlite"
    if not database.is_file():
        raise RuntimeError(f"Codex session database is missing: {database}")
    sessions: dict[str, tuple[str, str]] = {}
    uri = database.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        for thread_id, cwd, archived in connection.execute(
            "SELECT id, cwd, archived FROM threads WHERE cwd IS NOT NULL"
        ):
            if thread_id and cwd:
                sessions[str(thread_id)] = (canonical(cwd), "archived" if archived else "active")

    for folder, status in (("archived_sessions", "archived"), ("sessions", "active")):
        base = codex_home / folder
        if not base.is_dir():
            continue
        for path in base.rglob("*.jsonl*"):
            if path.suffix not in (".jsonl", ".zst"):
                continue
            try:
                metadata = metadata_from_log(path)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError(f"Cannot read session metadata: {path}: {error}") from error
            if metadata:
                thread_id, cwd = metadata
                old = sessions.get(thread_id)
                if not old or (status == "active" and old[1] != "active"):
                    sessions[thread_id] = (cwd, status)
    return sessions


def scan(workspace_root: Path, sessions: dict[str, tuple[str, str]]) -> list[dict]:
    root = workspace_root.resolve(strict=True)
    result: list[dict] = []
    for day in sorted(root.iterdir()):
        if not day.is_dir() or is_link(day) or not DATE_RE.fullmatch(day.name):
            continue
        for candidate in sorted(day.iterdir()):
            if not candidate.is_dir() or is_link(candidate):
                continue
            resolved = candidate.resolve(strict=True)
            if not within(resolved, root):
                continue
            related = [(thread_id, status) for thread_id, (cwd, status) in sessions.items()
                       if overlaps(canonical(resolved), cwd)]
            active = [thread_id for thread_id, status in related if status == "active"]
            archived = [thread_id for thread_id, status in related if status == "archived"]
            markers = (candidate / "work").is_dir() and (candidate / "outputs").is_dir()
            if active:
                state = "protected-active"
            elif archived:
                state = "archived"
            elif markers:
                state = "unlinked"
            else:
                state = "ignored-unrecognized"
            result.append({"id": f"{day.name}/{candidate.name}", "path": str(resolved),
                           "state": state, "active_sessions": active,
                           "archived_sessions": archived})
    return result


def validate_roots(workspace_root: Path, codex_home: Path, quarantine: Path) -> None:
    if not workspace_root.is_dir() or is_link(workspace_root):
        raise RuntimeError(f"Workspace root must be a real directory: {workspace_root}")
    if not codex_home.is_dir():
        raise RuntimeError(f"Codex home is missing: {codex_home}")
    target = quarantine.resolve(strict=False)
    if within(target, workspace_root) or within(target, codex_home):
        raise RuntimeError("Review folder must be outside workspace root and Codex home")
    if not same_volume(workspace_root, target):
        raise RuntimeError("Review folder must be on the same drive as workspace root for safe moves")
    if target.exists() and (not target.is_dir() or is_link(target)):
        raise RuntimeError("Review folder must be a real directory")


def write_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def remove_empty_date_dir(day: Path, root: Path) -> bool:
    """Remove only a direct, empty date directory under the workspace root."""
    if (not DATE_RE.fullmatch(day.name) or not day.is_dir() or is_link(day)
            or day.parent.resolve(strict=True) != root.resolve(strict=True)):
        return False
    try:
        day.rmdir()
    except OSError as error:
        if error.errno in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
            return False
        raise
    return True


def prune_empty_days(root: Path, yes: bool) -> int:
    days = [day for day in sorted(root.iterdir())
            if DATE_RE.fullmatch(day.name) and day.is_dir() and not is_link(day)
            and not any(day.iterdir())]
    if not days:
        print("No empty date directories.")
        return 0
    for day in days:
        print(f"Empty date directory: {day}")
    if not yes:
        print("Preview only. Add --yes to remove these empty directories.")
        return 0
    for day in days:
        if remove_empty_date_dir(day, root):
            print(f"Removed empty date directory: {day}")
    return 0


def move_candidates(args: argparse.Namespace, entries: list[dict]) -> int:
    selected = [item for item in entries if item["state"] == "archived"]
    if args.include_unlinked:
        selected += [item for item in entries if item["state"] == "unlinked"]
    if args.ids:
        wanted = set(args.ids)
        found = {item["id"] for item in selected}
        missing = wanted - found
        if missing:
            raise RuntimeError("Unknown or protected IDs: " + ", ".join(sorted(missing)))
        selected = [item for item in selected if item["id"] in wanted]
    elif not args.all:
        raise RuntimeError("Specify --ids DATE/NAME ... or --all")
    if not selected:
        print("No eligible directories.")
        return 0

    print("Directories selected for review:")
    for item in selected:
        print(f"  {item['state']:9} {item['path']}")
    if not args.yes:
        print("Preview only. Add --yes to move these directories.")
        return 0

    root = args.workspace_root.resolve(strict=True)
    quarantine = args.quarantine.resolve(strict=False)
    validate_roots(root, args.codex_home, quarantine)
    # Re-read live session state immediately before changing any filesystem path.
    fresh = {item["id"]: item for item in scan(root, load_sessions(args.codex_home))}
    for item in selected:
        actual = fresh.get(item["id"])
        if not actual or actual["state"] != item["state"] or actual["path"] != item["path"]:
            raise RuntimeError(f"Directory status changed; rescan: {item['id']}")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run = quarantine / run_id
    run.mkdir(parents=True, exist_ok=False)
    manifest_path = run / "manifest.json"
    manifest = {"format": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                "workspace_root": str(root), "items": []}
    for item in selected:
        manifest["items"].append({"id": item["id"], "source": item["path"],
                                  "destination": str(run / "items" / Path(item["id"])),
                                  "state": "planned", "classification": item["state"],
                                  "archived_sessions": item["archived_sessions"]})
    write_manifest(manifest_path, manifest)
    for item in manifest["items"]:
        source = Path(item["source"])
        destination = Path(item["destination"])
        if not within(source.resolve(strict=True), root) or is_link(source):
            raise RuntimeError(f"Unsafe source; stop and inspect manifest: {source}")
        if not within(destination, run / "items") or destination.exists():
            raise RuntimeError(f"Unsafe destination; stop and inspect manifest: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        item["state"] = "moving"
        write_manifest(manifest_path, manifest)
        source.rename(destination)
        item["state"] = "moved"
        write_manifest(manifest_path, manifest)
        print(f"Moved: {item['id']}")
        try:
            if remove_empty_date_dir(source.parent, root):
                print(f"Removed empty date directory: {source.parent}")
        except OSError as error:
            print(f"Could not remove empty date directory {source.parent}: {error}", file=sys.stderr)
    print(f"Review folder: {run}")
    print(f"Manifest: {manifest_path}")
    return 0


def restore(args: argparse.Namespace) -> int:
    run = args.run.resolve(strict=True)
    manifest_path = run / "manifest.json"
    if not manifest_path.is_file() or is_link(run):
        raise RuntimeError("Run folder has no valid manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != 1:
        raise RuntimeError("Unsupported manifest format")
    root = Path(manifest["workspace_root"]).resolve(strict=True)
    validate_roots(root, args.codex_home, run)
    selected = [item for item in manifest["items"]
                if item["state"] in ("moved", "moving") and Path(item["destination"]).is_dir()]
    if args.ids:
        wanted = set(args.ids)
        selected = [item for item in selected if item["id"] in wanted]
        if wanted != {item["id"] for item in selected}:
            raise RuntimeError("Some IDs are missing or not moved")
    elif not args.all:
        raise RuntimeError("Specify --ids DATE/NAME ... or --all")
    for item in selected:
        source = Path(item["source"])
        destination = Path(item["destination"])
        if not within(source, root) or not within(destination, run / "items"):
            raise RuntimeError("Manifest contains an unsafe path")
        if (not DATE_RE.fullmatch(source.parent.name)
                or source.parent.parent.resolve(strict=True) != root
                or is_link(source.parent)
                or (source.parent.exists() and not source.parent.is_dir())
                or not destination.is_dir()
                or is_link(destination)
                or not within(destination.resolve(strict=True), run / "items")
                or source.exists()):
            raise RuntimeError(f"Cannot safely restore: {item['id']}")
    if not selected:
        print("Nothing to restore.")
        return 0
    for item in selected:
        print(f"Restore: {item['destination']} -> {item['source']}")
    if not args.yes:
        print("Preview only. Add --yes to restore.")
        return 0
    for item in selected:
        source = Path(item["source"])
        destination = Path(item["destination"])
        source.parent.mkdir(parents=True, exist_ok=True)
        destination.rename(source)
        item["state"] = "restored"
        write_manifest(manifest_path, manifest)
        print(f"Restored: {item['id']}")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=Path.home() / "Documents" / "Codex")
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--quarantine", type=Path, default=Path.home() / "Documents" / "Codex_Review")
    commands = parser.add_subparsers(dest="command", required=True)
    scan_command = commands.add_parser("scan", help="Show candidate and protected directories")
    scan_command.add_argument("--json", action="store_true")
    move = commands.add_parser("move", help="Preview or move selected directories")
    move.add_argument("--ids", nargs="+")
    move.add_argument("--all", action="store_true")
    move.add_argument("--include-unlinked", action="store_true",
                      help="Include unlinked directories; these cannot be proven to belong to deleted chats")
    move.add_argument("--yes", action="store_true")
    prune = commands.add_parser("prune-empty-days", help="Preview or remove empty date directories")
    prune.add_argument("--yes", action="store_true")
    recovery = commands.add_parser("restore", help="Preview or restore moved directories")
    recovery.add_argument("--run", type=Path, required=True)
    recovery.add_argument("--ids", nargs="+")
    recovery.add_argument("--all", action="store_true")
    recovery.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 14):
        print("Python 3.14 or newer is required.", file=sys.stderr)
        return 2
    args = make_parser().parse_args(argv)
    try:
        if args.command == "restore":
            return restore(args)
        validate_roots(args.workspace_root, args.codex_home, args.quarantine)
        if args.command == "prune-empty-days":
            return prune_empty_days(args.workspace_root.resolve(strict=True), args.yes)
        entries = scan(args.workspace_root, load_sessions(args.codex_home))
        if args.command == "scan":
            if args.json:
                print(json.dumps(entries, ensure_ascii=False, indent=2))
            else:
                for item in entries:
                    print(f"{item['state']:20} {item['id']}")
                totals = {state: sum(item["state"] == state for item in entries)
                          for state in ("archived", "unlinked", "protected-active", "ignored-unrecognized")}
                print("Totals:", ", ".join(f"{key}={value}" for key, value in totals.items()))
            return 0
        return move_candidates(args, entries)
    except (OSError, sqlite3.Error, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
