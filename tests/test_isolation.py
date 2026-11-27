"""Tests run against a throwaway home, never the real contacts database.

Fixture people were being recorded as real in logs/people.db, because
connect()'s default path was frozen when the module loaded and so ignored
every redirect. tests/conftest.py fails the whole run if a real state file
changes; these pin down the two mechanisms underneath it.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.core import storage  # noqa: E402
from jobagent.people import people_db  # noqa: E402


def test_this_run_writes_nowhere_near_the_real_state():
    real = (os.path.join(ROOT, "data"), os.path.join(ROOT, "logs"))
    for path in (storage.DATA_DIR, storage.CACHE_DIR, storage.LEGACY_DIR,
                 people_db.DB_PATH):
        assert not os.path.abspath(path).startswith(real), path


def test_the_default_database_follows_a_redirect(tmp_path, monkeypatch):
    target = str(tmp_path / "people.db")
    monkeypatch.setattr(people_db, "DB_PATH", target)
    people_db.connect().close()
    assert os.path.exists(target)
