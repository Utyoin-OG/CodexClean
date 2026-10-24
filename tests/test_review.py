import contextlib
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from compression import zstd
import json


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_residue_review as tool  # noqa: E402


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "Codex"
        self.home = base / ".codex"
        self.quarantine = base / "Review"
        self.root.mkdir()
        self.home.mkdir()
        con = sqlite3.connect(self.home / "state_5.sqlite")
        con.execute("CREATE TABLE threads (id TEXT, cwd TEXT, archived INTEGER)")
        self.archived = self.make_workspace("2026-01-01", "archived")
        self.active = self.make_workspace("2026-01-01", "active")
        self.unlinked = self.make_workspace("2026-01-02", "unlinked")
        self.unknown = self.root / "2026-01-02" / "not-a-workspace"
        self.unknown.mkdir()
        con.execute("INSERT INTO threads VALUES (?, ?, ?)", ("a", str(self.archived), 1))
        con.execute("INSERT INTO threads VALUES (?, ?, ?)", ("b", str(self.active), 0))
        con.commit()
        con.close()

    def make_workspace(self, day, name):
        path = self.root / day / name
        (path / "work").mkdir(parents=True)
        (path / "outputs").mkdir()
        (path / "outputs" / "keep.txt").write_text(name, encoding="utf-8")
        return path

    def run_main(self, *commands):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = tool.main(["--workspace-root", str(self.root), "--codex-home", str(self.home),
                                "--quarantine", str(self.quarantine), *commands])
        return status, output.getvalue()

    def test_classification_and_protected_directory(self):
        status, output = self.run_main("scan")
        self.assertEqual(status, 0, output)
        self.assertIn("archived=1, unlinked=1, protected-active=1, ignored-unrecognized=1", output)
        status, output = self.run_main("move", "--ids", "2026-01-01/active", "--yes")
        self.assertEqual(status, 1)
        self.assertTrue(self.active.exists())

    def test_move_preview_move_and_restore(self):
        status, output = self.run_main("move", "--all", "--include-unlinked")
        self.assertEqual(status, 0, output)
        self.assertTrue(self.archived.exists())
        self.assertTrue(self.unlinked.exists())
        status, output = self.run_main("move", "--all", "--include-unlinked", "--yes")
        self.assertEqual(status, 0, output)
        self.assertFalse(self.archived.exists())
        self.assertFalse(self.unlinked.exists())
        self.assertTrue(self.active.exists())
        run = next(self.quarantine.iterdir())
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([x["state"] for x in manifest["items"]], ["moved", "moved"])
        status, output = self.run_main("restore", "--run", str(run), "--all", "--yes")
        self.assertEqual(status, 0, output)
        self.assertEqual((self.archived / "outputs" / "keep.txt").read_text(), "archived")
        self.assertEqual((self.unlinked / "outputs" / "keep.txt").read_text(), "unlinked")

    def test_active_log_protects_even_if_database_says_archived(self):
        session_dir = self.home / "sessions"
        session_dir.mkdir()
        payload = {"type": "session_meta", "payload": {"id": "a", "cwd": str(self.archived)}}
        with zstd.open(session_dir / "rollout-a.jsonl.zst", "wt", encoding="utf-8") as stream:
            stream.write(json.dumps(payload) + "\n")
        status, output = self.run_main("scan")
        self.assertEqual(status, 0, output)
        self.assertIn("archived=0, unlinked=1, protected-active=2", output)

    def test_review_folder_inside_workspace_is_rejected(self):
        self.quarantine = self.root / "Review"
        status, output = self.run_main("scan")
        self.assertEqual(status, 1)
        self.assertIn("outside workspace root", output)

    def test_restore_will_not_replace_new_content_at_original_path(self):
        status, output = self.run_main("move", "--all", "--yes")
        self.assertEqual(status, 0, output)
        run = next(self.quarantine.iterdir())
        self.archived.mkdir()
        (self.archived / "new.txt").write_text("new", encoding="utf-8")
        status, output = self.run_main("restore", "--run", str(run), "--all", "--yes")
        self.assertEqual(status, 1)
        self.assertIn("Cannot safely restore", output)
        self.assertEqual((self.archived / "new.txt").read_text(), "new")
        self.assertTrue((run / "items" / "2026-01-01" / "archived").exists())


if __name__ == "__main__":
    unittest.main()
