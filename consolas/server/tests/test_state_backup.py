from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKUP_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_state_if_due.py"
spec = importlib.util.spec_from_file_location("backup_state_if_due", BACKUP_SCRIPT)
assert spec and spec.loader
backup_state_if_due = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup_state_if_due)


class StateBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmpdir.name)
        connection = sqlite3.connect(self.data_dir / "consolas.sqlite")
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.execute("INSERT INTO sample VALUES (42)")
        connection.commit()
        connection.close()
        self.export_succeeds = True

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def run_backup(self) -> tuple[int, dict]:
        environment = {
            "CONSOLAS_DATA_DIR": str(self.data_dir),
            "CONSOLAS_BACKUP_APP_BASE_URL": "http://backup-test.invalid",
            "CONSOLAS_BACKUP_INTERVAL_HOURS": "24",
            "CONSOLAS_BACKUP_RETENTION": "14",
        }
        output = io.StringIO()
        def export_state(_: str, destination: Path) -> bool:
            if not self.export_succeeds:
                return False
            destination.write_bytes(b'{"version":1,"user":{}}')
            return True

        with (
            patch.dict(os.environ, environment, clear=False),
            patch.object(backup_state_if_due, "export_state_json", side_effect=export_state),
            contextlib.redirect_stdout(output),
        ):
            result = backup_state_if_due.main()
        return result, json.loads(output.getvalue())

    def test_successful_backup_has_database_export_and_completion_state(self) -> None:
        result, payload = self.run_backup()

        self.assertEqual(result, 0)
        self.assertEqual(payload["status"], "ok")
        backup_dir = Path(payload["backupDir"])
        self.assertTrue((backup_dir / "consolas.sqlite").exists())
        self.assertTrue((backup_dir / "state-export.json").exists())
        self.assertTrue((self.data_dir / "backups" / ".backup_state.json").exists())

    def test_failed_export_does_not_mark_or_keep_a_partial_backup(self) -> None:
        self.export_succeeds = False
        result, payload = self.run_backup()

        self.assertEqual(result, 1)
        self.assertEqual(payload["status"], "degraded")
        backups_dir = self.data_dir / "backups"
        self.assertFalse((backups_dir / ".backup_state.json").exists())
        self.assertEqual([path for path in backups_dir.iterdir() if path.is_dir()], [])
