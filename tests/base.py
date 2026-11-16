"""Shared test helpers: every test works on a throwaway SQLite database."""

import os
import sys
import tempfile
import unittest

# Make the application modules and the test helpers importable
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, TESTS_DIR)

import db  # noqa: E402

INITIAL_DB = os.path.join(BASE_DIR, "initial_db.sql")
MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")


def apply_migrations(test_case=None):
    """Run every migration in order, like the application does at startup."""
    current_version = db.get_parameter("version")
    migration_files = os.listdir(MIGRATIONS_DIR)
    while True:
        migration_file = db.get_next_migration(migration_files, current_version)
        if not migration_file:
            break
        db.create_or_update_sqlite_db(os.path.join(MIGRATIONS_DIR, migration_file))
        new_version = db.get_parameter("version")
        if test_case is not None:
            test_case.assertNotEqual(
                new_version, current_version, "Migration did not bump the version"
            )
        current_version = new_version


# Several modules read the settings through a singleton created at import time
# (see pyVintedVN.requester). Bootstrap a throwaway database before they are
# imported, and never touch ./data.
_BOOTSTRAP_DIR = tempfile.TemporaryDirectory()
db.DB_PATH = os.path.join(_BOOTSTRAP_DIR.name, "bootstrap.db")
db.create_or_update_sqlite_db(INITIAL_DB)


class TempDatabaseTestCase(unittest.TestCase):
    """Base class that points db.DB_PATH at a temporary, migrated database."""

    original_db_path = None

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        db.create_or_update_sqlite_db(INITIAL_DB)
        apply_migrations(self)

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()
