"""How state is kept on disk: atomically, under a lock, and by kind.

Each test is one of the ways the old pattern -- open the file for writing,
dump, and read any parse error as "empty" -- could cost a real action: a
half-written ledger read as nothing researched, two processes erasing each
other's additions, a corrections file deleted along with the caches.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.core import storage  # noqa: E402


class TestAtomicWrites:
    def test_a_crash_mid_write_leaves_the_old_file(self, tmp_path, monkeypatch):
        target = tmp_path / "auto_drafted.json"
        storage.write(str(target), ["row-1", "row-2"])

        def dies_halfway(value, fh, **kw):
            fh.write('["row-1", "ro')
            raise KeyboardInterrupt          # the lid closing

        monkeypatch.setattr(storage.json, "dump", dies_halfway)
        with pytest.raises(KeyboardInterrupt):
            storage.write(str(target), ["row-1", "row-2", "row-3"])
        monkeypatch.undo()
        assert json.loads(target.read_text()) == ["row-1", "row-2"]
        assert not [p for p in os.listdir(tmp_path) if ".tmp-" in p]


class TestCorruptionByKind:
    def test_a_broken_cache_is_moved_aside_and_rebuilt(self, tmp_path):
        target = tmp_path / "draft_headers.json"
        target.write_text('{"d1": {"to": "x@y.com"')
        assert storage.read(str(target), {}) == {}
        assert not target.exists()
        assert [p for p in os.listdir(tmp_path) if ".corrupt-" in p]

    def test_a_broken_ledger_stops_and_is_left_alone(self, tmp_path):
        target = tmp_path / "auto_drafted.json"
        target.write_text('["moodys|credit analyst", "fitch|')
        with pytest.raises(storage.Corrupt) as err:
            storage.read(str(target), [])
        assert "ledger" in str(err.value) and "backups" in str(err.value)
        assert target.read_text().startswith('["moodys')    # untouched

    def test_the_bug_this_exists_for(self, tmp_path, monkeypatch):
        """auto_draft read a truncated ledger as "nothing researched yet",
        which re-bills every open row and drafts to everyone a second time."""
        from jobagent.outreach import auto_draft
        ledger = tmp_path / "auto_drafted.json"
        ledger.write_text('["moodys|credit analyst", "fit')
        monkeypatch.setattr(auto_draft, "DONE_FILE", str(ledger))
        with pytest.raises(storage.Corrupt):
            auto_draft._done()

    def test_a_missing_file_is_simply_empty(self, tmp_path):
        assert storage.read(str(tmp_path / "corrections.json"), {}) == {}


class TestNoLostUpdates:
    def test_merge_keeps_keys_written_since_the_load(self, tmp_path):
        target = str(tmp_path / "rejection_claims.json")
        storage.write(target, {"msg-1": "wolfe|analyst"})
        stale = storage.read(target, {})          # loaded at start of a run
        storage.merge(target, {"msg-2": "kbra|analyst"})   # another process
        storage.merge(target, {**stale, "msg-3": "fitch|analyst"})
        assert set(storage.read(target, {})) == {"msg-1", "msg-2", "msg-3"}

    def test_four_processes_adding_at_once_lose_nothing(self, tmp_path):
        target = str(tmp_path / "notified.json")
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from jobagent.core import storage\n"
            "for i in range(25):\n"
            "    storage.add(sys.argv[2], [f'{sys.argv[3]}-{i}'])\n")
        procs = [subprocess.Popen([sys.executable, "-c", script, ROOT, target,
                                   str(n)]) for n in range(4)]
        assert all(p.wait(timeout=60) == 0 for p in procs)
        assert len(storage.read(target, [])) == 100

    def test_corrections_unlock_applies_to_the_file_not_a_stale_copy(
            self, tmp_path, monkeypatch):
        from jobagent.core import corrections
        monkeypatch.setattr(corrections, "STORE", str(tmp_path / "corrections.json"))
        monkeypatch.setattr(corrections, "_cache", None)
        corrections.record("Acme", "Analyst", "Status", "rejected", reason="test")
        corrections.record("Beta", "Associate", "Status", "applied", reason="test")
        assert corrections.unlock("Acme", "Analyst") is True
        assert corrections.unlock("Acme", "Analyst") is False
        on_disk = json.loads((tmp_path / "corrections.json").read_text())
        assert len(on_disk) == 1 and "Beta" in json.dumps(on_disk)


class TestLayout:
    @pytest.fixture
    def roots(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setattr(storage, "CACHE_DIR", str(tmp_path / "logs"))
        monkeypatch.setattr(storage, "LEGACY_DIR", str(tmp_path / "logs"))
        return tmp_path

    def test_what_cannot_be_rebuilt_is_moved_out_of_logs(self, roots):
        legacy = roots / "logs"
        legacy.mkdir()
        (legacy / "corrections.json").write_text('{"k": 1}')
        (legacy / "draft_headers.json").write_text("{}")
        moved = storage.path("corrections")
        assert moved == str(roots / "data" / "corrections.json")
        assert json.loads(open(moved).read()) == {"k": 1}
        assert not (legacy / "corrections.json").exists()
        # A cache stays where it is.
        assert storage.path("draft_headers") == str(legacy / "draft_headers.json")

    def test_every_kind_is_one_of_three(self):
        assert {e.kind for e in storage.FILES.values()} == {
            storage.SOURCE, storage.LEDGER, storage.CACHE}

    def test_no_module_names_its_own_state_path(self):
        """One registry or none: a path built elsewhere is a file this module
        cannot back up, check, or move."""
        import ast
        import architecture
        bad = []
        for layer in architecture.LAYERS:
            for name in sorted(os.listdir(os.path.join(ROOT, architecture.folder(layer)))):
                if not name.endswith(".py") or (layer, name) == ("core", "storage.py"):
                    continue
                tree = ast.parse(open(os.path.join(ROOT, architecture.folder(layer), name)).read())
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call) and node.args
                            and isinstance(node.args[0], ast.Constant)
                            and node.args[0].value in ("logs", "data")
                            and getattr(node.func, "attr", "") == "join"):
                        bad.append(f"{layer}/{name}:{node.lineno}")
        assert not bad, bad


class TestCare:
    def test_backup_copies_what_matters_and_keeps_two_weeks(
            self, tmp_path, monkeypatch):
        import datetime
        data, logs = tmp_path / "data", tmp_path / "logs"
        monkeypatch.setattr(storage, "DATA_DIR", str(data))
        monkeypatch.setattr(storage, "CACHE_DIR", str(logs))
        monkeypatch.setattr(storage, "LEGACY_DIR", str(logs))
        monkeypatch.setitem(storage.DIRS, "backups", str(data / "backups"))
        db_path = str(logs / "people.db")
        monkeypatch.setitem(storage.DATABASES, "people", db_path)

        storage.write("corrections", {"k": 1})
        storage.write("draft_headers", {"d": 1})            # a cache: skipped
        db = storage.connect_db(db_path)
        db.execute("CREATE TABLE people (name TEXT)")
        db.execute("INSERT INTO people VALUES ('Jane Roe')")
        db.commit()

        for day in range(20):
            storage.backup(today=datetime.date(2026, 11, 1) + datetime.timedelta(day),
                           keep=14)
        days = sorted(os.listdir(data / "backups"))
        assert len(days) == 14 and days[-1] == "2026-11-20"
        latest = data / "backups" / "2026-11-20"
        assert sorted(os.listdir(latest)) == ["corrections.json", "people.db"]
        copy = sqlite3.connect(latest / "people.db")
        assert copy.execute("SELECT name FROM people").fetchall() == [("Jane Roe",)]
        db.close()

    def test_databases_are_set_up_for_more_than_one_process(self, tmp_path):
        db = storage.connect_db(str(tmp_path / "x.db"))
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_health_reports_an_unreadable_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setattr(storage, "CACHE_DIR", str(tmp_path / "logs"))
        monkeypatch.setattr(storage, "LEGACY_DIR", str(tmp_path / "logs"))
        monkeypatch.setitem(storage.DATABASES, "people", str(tmp_path / "none.db"))
        os.makedirs(tmp_path / "data")
        (tmp_path / "data" / "auto_drafted.json").write_text("[1, 2")
        rows = {r["name"]: r for r in storage.health()}
        assert rows["auto_drafted"]["ok"] is False
        assert rows["corrections"]["exists"] is False


class TestLongLivedProcesses:
    def test_the_sheet_is_re_read_after_a_minute(self, monkeypatch):
        """Claude Desktop keeps the MCP server open for days; it answered
        from the sheet as it was when it started."""
        from jobagent.agent import toolbox
        from jobagent.store import sheets
        monkeypatch.setattr(sheets, "_rows_cache", [{"row_number": 2}])
        start = time.monotonic()
        monkeypatch.setattr(toolbox, "_last_refresh", start)
        toolbox._refresh_if_stale(now=start + 5)
        assert sheets._rows_cache is not None          # same pass: kept
        toolbox._refresh_if_stale(now=start + 61)
        assert sheets._rows_cache is None              # a minute on: re-read
