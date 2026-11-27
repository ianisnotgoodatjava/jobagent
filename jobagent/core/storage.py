"""Every file this project keeps state in: what kind it is, and how it is written.

Before this, eighteen JSON files and a SQLite database lived in logs/, each
named by the module that used it, and each written the same way: open the
target for writing -- which truncates it -- then dump. A run killed in the
middle (a laptop lid, a launchd timeout, Ctrl-C) left half a file, and
twelve of the readers caught the parse error and returned empty state. For
auto_drafted.json that chain ends in re-researching every open application:
the model bill again, and a second cold email to everyone already drafted.

So the files are declared here, once, by kind, and the kind decides what
happens when one is lost:

  SOURCE  something only a person decided -- a status they overruled, a
          company name they corrected. Cannot be rebuilt from anything.
  LEDGER  a record that something was done: a row researched (money spent),
          a reminder sent, which email closed which row. Losing it repeats
          the action.
  CACHE   derived from Gmail or the web. Losing it costs time.

SOURCE and LEDGER files live in data/; CACHE files and run output in logs/,
which can now be deleted outright. A file still at its old logs/ path is
moved the first time it is asked for.

The rules, all enforced here rather than in nineteen copies:

  * Writes are atomic: a temp file in the same folder, fsynced, then
    os.replace. A reader sees the old file or the new one, never half.
  * update() and merge() hold an exclusive lock across read-modify-write, so
    the scheduled run, the MCP server and an interactive command cannot
    erase each other's additions.
  * An unreadable CACHE is moved aside and the caller gets the default. An
    unreadable SOURCE or LEDGER raises and is left where it is: stopping is
    better than silently starting over.

This is the one module in core that touches the disk. It never touches the
network.
"""

import datetime
import fcntl
import json
import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass

from jobagent.core import home

SOURCE, LEDGER, CACHE = "source", "ledger", "cache"

# Roots, in the user's home directory (core/home.py): the repository root
# for a checkout, ~/.jobagent for an installed copy. They are absolute, so a
# process that never changed into the home directory still finds its state.
# Each can be overridden on its own; a relative override is taken as
# relative to the home directory.
DATA_DIR = os.path.join(home.home(),
                        os.path.expanduser(os.environ.get("JOBAGENT_DATA_DIR", "data")))
CACHE_DIR = os.path.join(home.home(),
                         os.path.expanduser(os.environ.get("JOBAGENT_CACHE_DIR", "logs")))
# Where state lived before data/ existed: logs/ in the checkout. It stays
# tied to the checkout, so a checkout given a new JOBAGENT_HOME still moves
# its old files over on first use.
LEGACY_DIR = os.path.join(home.checkout() or home.home(), "logs")
BACKUP_KEEP = 14


@dataclass(frozen=True)
class Entry:
    name: str
    file: str
    kind: str
    why: str

    @property
    def root(self) -> str:
        return CACHE_DIR if self.kind == CACHE else DATA_DIR


FILES = {e.name: e for e in (
    # SOURCE -----------------------------------------------------------------
    Entry("corrections", "corrections.json", SOURCE,
          "statuses and fields you overruled; scanners must not re-derive them"),
    Entry("company_aliases", "company_aliases.json", SOURCE,
          "names you corrected a company away from, so its mail still matches"),
    # LEDGER -----------------------------------------------------------------
    Entry("auto_drafted", "auto_drafted.json", LEDGER,
          "applications already researched; losing it re-bills and re-drafts"),
    Entry("drafts_created", "drafts_created.json", LEDGER,
          "drafts already written, so none is written twice"),
    Entry("notified", "notified.json", LEDGER,
          "reach-outs already reported, so no reminder repeats"),
    Entry("rejection_claims", "rejection_claims.json", LEDGER,
          "which email closed which row, so one email closes only one"),
    Entry("common_ground", "common_ground.json", LEDGER,
          "common-ground searches already paid for, one per application and "
          "trait set"),
    Entry("company_formats", "company_formats.json", LEDGER,
          "address conventions with where each came from; hit_rate reads the "
          "provenance"),
    # CACHE ------------------------------------------------------------------
    Entry("draft_headers", "draft_headers.json", CACHE, "Gmail draft headers"),
    Entry("address_patterns", "address_patterns.json", CACHE,
          "address formats learned from delivered mail"),
    Entry("outreach_outcomes", "outreach_outcomes.json", CACHE,
          "reply and bounce per outreach thread, re-read from Gmail"),
    Entry("outreach_replies", "outreach_replies.json", CACHE,
          "which corpus emails got a human reply"),
    Entry("followup_labels", "followup_labels.json", CACHE,
          "follow-up training labels from Gmail thread state"),
    Entry("contacts_harvest", "contacts_harvest.json", CACHE,
          "people seen in your mail"),
    Entry("contacts_found", "contacts_found.json", CACHE,
          "contacts from the batch research pass"),
    Entry("site_people", "site_people.json", CACHE,
          "people read off firms' own websites, 14 days"),
    Entry("enriched", "enriched.json", CACHE,
          "posting links already fetched, so dead ones are not re-fetched"),
)}

# SQLite databases. people.db is research -- rebuildable, but at the cost of
# re-reading every firm's site and FINRA -- so it is backed up with the rest.
DATABASES = {"people": os.path.join(CACHE_DIR, "people.db")}

# Folders of run output: drafts as text, briefs, raw research.
DIRS = {
    "drafts_auto": os.path.join(CACHE_DIR, "drafts", "auto"),
    "drafts_batch": os.path.join(CACHE_DIR, "drafts", "batch"),
    "drafts_ready": os.path.join(CACHE_DIR, "drafts", "ready"),
    "cover_letters": os.path.join(CACHE_DIR, "drafts", "cover_letters"),
    "recommendations": os.path.join(CACHE_DIR, "recommendations"),
    "common_ground_raw": os.path.join(CACHE_DIR, "common_ground"),
    "briefs": os.path.join(CACHE_DIR, "briefs"),
    "agent": os.path.join(CACHE_DIR, "agent"),
    "backups": os.path.join(DATA_DIR, "backups"),
}


class Corrupt(RuntimeError):
    """A SOURCE or LEDGER file cannot be read. Nothing has been changed."""


# --- where things are --------------------------------------------------------

def location(name: str) -> str:
    """Where a registered file lives. No side effects, so it is safe in a
    module-level constant; the move from logs/ happens on first use."""
    entry = FILES[name]
    return os.path.join(entry.root, entry.file)


def path(name: str) -> str:
    """The path of a registered file, moving it from logs/ if it is still there."""
    entry = FILES[name]
    target = location(name)
    legacy = os.path.join(LEGACY_DIR, entry.file)
    if (os.path.abspath(target) != os.path.abspath(legacy)
            and not os.path.exists(target) and os.path.exists(legacy)):
        os.makedirs(entry.root, exist_ok=True)
        os.replace(legacy, target)          # same disk, so atomic
        print(f"state: moved {legacy} -> {target} ({entry.kind})",
              file=sys.stderr)
    return target


def kind_of(path_or_name: str) -> str:
    if path_or_name in FILES:
        return FILES[path_or_name].kind
    base = os.path.basename(path_or_name)
    for entry in FILES.values():
        if entry.file == base:
            return entry.kind
    return CACHE


def directory(name: str) -> str:
    return DIRS[name]


def database(name: str) -> str:
    return DATABASES[name]


def _resolve(path_or_name: str) -> str:
    """A name, or a path that is some entry's location, becomes that entry's
    path (moving it from logs/ if needed). Any other path -- a test's temp
    file -- is used as given."""
    if path_or_name in FILES:
        return path(path_or_name)
    wanted = os.path.abspath(path_or_name)
    for entry in FILES.values():
        if os.path.abspath(location(entry.name)) == wanted:
            return path(entry.name)
    return path_or_name


# --- reading and writing -----------------------------------------------------

def read(path_or_name: str, default=None, kind: str | None = None):
    """The parsed JSON, or `default` if the file does not exist.

    A file that exists and does not parse is handled by its kind: a CACHE is
    moved aside to <file>.corrupt-<time> and `default` returned; a SOURCE or
    LEDGER raises Corrupt and is left exactly where it is.
    """
    target = _resolve(path_or_name)
    kind = kind or kind_of(path_or_name)
    try:
        with open(target, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (ValueError, UnicodeDecodeError) as exc:
        if kind == CACHE:
            aside = f"{target}.corrupt-{datetime.datetime.now():%Y%m%d-%H%M%S}"
            os.replace(target, aside)
            print(f"state: {target} was unreadable ({exc}); moved to {aside} "
                  f"and starting it over", file=sys.stderr)
            return default
        raise Corrupt(
            f"{target} is unreadable ({exc}). It is a {kind} file -- "
            f"{FILES[path_or_name].why if path_or_name in FILES else 'not rebuildable'}"
            f" -- so nothing was changed. Restore it from "
            f"{DIRS['backups']}/<latest>/ or repair the JSON, then run again."
        ) from None


def _write_unlocked(target: str, value, dump: dict) -> None:
    folder = os.path.dirname(target) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".{os.path.basename(target)}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f, **dump)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@contextmanager
def _lock(target: str):
    folder = os.path.dirname(target) or "."
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f".{os.path.basename(target)}.lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def write(path_or_name: str, value, **dump) -> None:
    """Replaces the file with `value`, atomically. For data computed whole."""
    target = _resolve(path_or_name)
    with _lock(target):
        _write_unlocked(target, value, dump)


def update(path_or_name: str, change, default=None, kind: str | None = None,
           **dump):
    """Read, change, write, with no other process in between.

    `change(current) -> new` is applied to what is on disk now, not to a
    copy read at the start of a long run. Returns the new value.
    """
    target = _resolve(path_or_name)
    with _lock(target):
        current = read(target, default, kind or kind_of(path_or_name))
        new = change(current)
        _write_unlocked(target, new, dump)
    return new


def merge(path_or_name: str, entries: dict, **dump) -> dict:
    """Adds `entries` to a JSON object on disk, keeping every other key.

    What the stores that only ever add should call instead of writing back
    the whole object they loaded an hour ago.
    """
    return update(path_or_name, lambda cur: {**(cur or {}), **entries}, {},
                  **dump)


def add(path_or_name: str, items, keep_last: int | None = None, **dump) -> list:
    """Adds `items` to a JSON list used as a set, keeping what is there."""
    def change(cur):
        merged = sorted({_hashable(x) for x in (cur or [])} |
                        {_hashable(x) for x in items}, key=str)
        if keep_last:
            merged = merged[-keep_last:]
        return [list(x) if isinstance(x, tuple) else x for x in merged]
    return update(path_or_name, change, [], **dump)


def _hashable(x):
    return tuple(x) if isinstance(x, list) else x


# --- databases ---------------------------------------------------------------

def connect_db(db_path: str) -> sqlite3.Connection:
    """A SQLite connection set up for more than one process.

    WAL lets readers proceed while one process writes, and the busy timeout
    makes a second writer wait its turn instead of failing at once with
    "database is locked" -- which the default rollback journal did as soon
    as the scheduled run and the MCP server wrote at the same moment.
    """
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = sqlite3.connect(db_path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 30000")
    if db_path != ":memory:":
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = NORMAL")
    db.execute("PRAGMA foreign_keys = ON")
    return db


# --- care ----------------------------------------------------------------------

def backup(today: datetime.date | None = None, keep: int = BACKUP_KEEP) -> str:
    """Copies every SOURCE and LEDGER file and every database to
    data/backups/<date>/, and keeps the last `keep` days. Returns the folder.

    Databases are copied with SQLite's own backup API, which is consistent
    even while another process is writing.
    """
    today = today or datetime.date.today()
    dest = os.path.join(DIRS["backups"], f"{today:%Y-%m-%d}")
    os.makedirs(dest, exist_ok=True)
    for entry in FILES.values():
        if entry.kind == CACHE:
            continue
        src = path(entry.name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, entry.file))
    for name, db_path in DATABASES.items():
        if os.path.exists(db_path):
            src = sqlite3.connect(db_path, timeout=30)
            out = sqlite3.connect(os.path.join(dest, os.path.basename(db_path)))
            try:
                src.backup(out)
            finally:
                out.close()
                src.close()
    days = sorted(d for d in os.listdir(DIRS["backups"])
                  if os.path.isdir(os.path.join(DIRS["backups"], d)))
    for old in days[:-keep] if keep else []:
        shutil.rmtree(os.path.join(DIRS["backups"], old), ignore_errors=True)
    return dest


def health() -> list[dict]:
    """One line per registered file and database: is it there, does it read."""
    out = []
    for entry in FILES.values():
        target = path(entry.name)
        row = {"name": entry.name, "kind": entry.kind, "path": target,
               "exists": os.path.exists(target), "ok": True, "records": None,
               "bytes": 0, "modified": ""}
        if row["exists"]:
            stat = os.stat(target)
            row["bytes"] = stat.st_size
            row["modified"] = f"{datetime.datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M}"
            try:
                with open(target, encoding="utf-8") as f:
                    data = json.load(f)
                row["records"] = len(data) if hasattr(data, "__len__") else None
            except (ValueError, UnicodeDecodeError) as exc:
                row["ok"], row["error"] = False, str(exc)[:80]
        out.append(row)
    for name, db_path in DATABASES.items():
        row = {"name": name, "kind": "database", "path": db_path,
               "exists": os.path.exists(db_path), "ok": True, "records": None,
               "bytes": 0, "modified": ""}
        if row["exists"]:
            stat = os.stat(db_path)
            row["bytes"] = stat.st_size
            row["modified"] = f"{datetime.datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M}"
            db = sqlite3.connect(db_path, timeout=30)
            try:
                verdict = db.execute("PRAGMA quick_check").fetchone()[0]
                row["ok"] = verdict == "ok"
                row["journal"] = db.execute("PRAGMA journal_mode").fetchone()[0]
                tables = [r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")]
                row["records"] = {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                                  for t in tables}
            finally:
                db.close()
        out.append(row)
    return out
