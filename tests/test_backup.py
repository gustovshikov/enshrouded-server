"""Exercise the real backup script with isolated save directories and ZIP tools."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
import zipfile


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "default"


class BackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for command in ("bash", "jq", "zip", "zipnote"):
            if not shutil.which(command):
                raise RuntimeError(f"Missing test prerequisite: {command}")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="enshrouded-backup-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.install = self.root / "server"
        self.saves = self.install / "savegame"
        self.backups = self.install / "backups"
        self.scripts = self.root / "scripts"
        for directory in (self.saves, self.backups, self.scripts):
            directory.mkdir(parents=True, exist_ok=True)
        for name in ("enshrouded-backup", "common", "defaults"):
            shutil.copy2(SOURCE / name, self.scripts / name)
        common = (self.scripts / "common").read_text()
        common = common.replace(
            "install_path=/opt/enshrouded/server",
            "install_path=" + shlex.quote(str(self.install)),
        ).replace(
            "enshrouded_backup_pidfile=/var/run/enshrouded/enshrouded-backup.pid",
            "enshrouded_backup_pidfile=" + shlex.quote(str(self.root / "backup.pid")),
        )
        (self.scripts / "common").write_text(common)
        (self.install / "enshrouded_server.json").write_text(
            json.dumps({"saveDirectory": "./savegame"})
        )
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("SERVER_", "BACKUP_"))}

    def run_backup(self, **environment):
        result = subprocess.run(
            ["bash", str(self.scripts / "enshrouded-backup")],
            env={**self.env, **environment}, capture_output=True, text=True, timeout=15,
        )
        return result

    def test_current_world_is_archived_without_legacy_index(self):
        snapshot = b"fixture: current world snapshot\n"
        (self.saves / "world0.ksd").write_bytes(snapshot)
        (self.saves / "world0.ksd.backup-0").write_bytes(b"fixture: older checkpoint")
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archives = list(self.backups.glob("*.zip"))
        self.assertEqual(len(archives), 1, result.stdout + result.stderr)
        with zipfile.ZipFile(archives[0]) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.namelist(), ["world0.ksd"])
            self.assertEqual(archive.read("world0.ksd"), snapshot)


    def test_zip_failure_is_reported_without_post_hook_or_stale_lock(self):
        (self.saves / "world0.ksd").write_bytes(b"fixture: world")
        marker = self.root / "post-hook-ran"
        result = self.run_backup(
            BACKUP_DIR=str(self.root / "missing" / "backups"),
            BACKUP_POST_HOOK="touch " + shlex.quote(str(marker)),
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("enshrouded-backup complete", result.stdout)
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / "backup.pid").exists())


    def test_failed_pre_hook_aborts_backup(self):
        (self.saves / "world0.ksd").write_bytes(b"fixture: world")
        result = self.run_backup(BACKUP_PRE_HOOK="false")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.backups.glob("*.zip")), [])
        self.assertFalse((self.root / "backup.pid").exists())


    def test_missing_save_is_not_reported_as_success(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                if legacy:
                    (self.saves / "3ad85aea-index").write_text(
                        json.dumps({"latest": 7, "time": 1700000000})
                    )
                result = self.run_backup()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(list(self.backups.glob("*.zip")), [])

    def test_retention_handles_spaces_and_preserves_non_archives(self):
        destination = self.root / "backup folder"
        destination.mkdir()
        marker = destination / "README.txt"
        marker.write_text("not an archive")
        os.utime(marker, (1, 1))
        for index in range(3):
            archive = destination / f"old-{index}.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("world0.ksd", b"fixture: old snapshot")
            os.utime(archive, (1700000000 + index, 1700000000 + index))
        (self.saves / "world0.ksd").write_bytes(b"fixture: current snapshot")
        result = self.run_backup(BACKUP_DIR=str(destination), BACKUP_MAX_COUNT="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(list(destination.glob("*.zip"))), 2, result.stdout)
        self.assertTrue((destination / "old-2.zip").exists())
        self.assertTrue(marker.exists())


    def make_legacy_save(self, index):
        name = "3ad85aea" if index == 0 else f"3ad85aea-{index}"
        snapshot = b"fixture: legacy snapshot"
        (self.saves / name).write_bytes(snapshot)
        (self.saves / "3ad85aea-index").write_text(
            json.dumps({"latest": index, "time": 1700000000, "deleted": False})
        )
        return snapshot

    def check_legacy_archive(self, index):
        snapshot = self.make_legacy_save(index)
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archives = list(self.backups.glob("*.zip"))
        self.assertEqual(len(archives), 1)
        with zipfile.ZipFile(archives[0]) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"3ad85aea", "3ad85aea-index"})
            self.assertEqual(archive.read("3ad85aea"), snapshot)
            self.assertEqual(json.loads(archive.read("3ad85aea-index")),
                             {"latest": 0, "time": 1700000000, "deleted": False})
        original = json.loads((self.saves / "3ad85aea-index").read_text())
        self.assertEqual(original["latest"], index)

    def test_legacy_unrotated_save_remains_restorable(self):
        self.check_legacy_archive(0)

    def test_legacy_rotated_save_remains_restorable(self):
        self.check_legacy_archive(7)

    def test_current_worlds_take_precedence_after_migration(self):
        self.make_legacy_save(7)
        for name in ("world0.ksd", "world1.ksd"):
            (self.saves / name).write_bytes(name.encode())
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(next(self.backups.glob("*.zip"))) as archive:
            self.assertEqual(set(archive.namelist()), {"world0.ksd", "world1.ksd"})
            for name in archive.namelist():
                self.assertEqual(archive.read(name), name.encode())

    def test_custom_save_directory_and_hooks(self):
        saves = self.root / "custom saves"
        saves.mkdir()
        (saves / "world0.ksd").write_bytes(b"fixture: before hook")
        destination = self.install / "custom backups"
        destination.mkdir()
        marker = self.root / "post-hook-ran"
        result = self.run_backup(
            SERVER_SAVE_DIR=str(saves), BACKUP_DIR="custom backups",
            BACKUP_PRE_HOOK="printf snapshot > " + shlex.quote(str(saves / "world0.ksd")),
            BACKUP_POST_HOOK="touch " + shlex.quote(str(marker)),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(next(destination.glob("*.zip"))) as archive:
            self.assertEqual(archive.read("world0.ksd"), b"snapshot")
        self.assertTrue(marker.exists())
        self.assertFalse((self.root / "backup.pid").exists())

    def test_post_hook_failure_is_reported_and_lock_released(self):
        (self.saves / "world0.ksd").write_bytes(b"fixture: world")
        result = self.run_backup(BACKUP_POST_HOOK="false")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(list(self.backups.glob("*.zip"))), 1)
        self.assertFalse((self.root / "backup.pid").exists())

    def test_zero_retention_limit_preserves_existing_archives(self):
        (self.backups / "old.zip").write_bytes(b"fixture: retained old file")
        (self.saves / "world0.ksd").write_bytes(b"fixture: world")
        result = self.run_backup(BACKUP_MAX_COUNT="0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(list(self.backups.glob("*.zip"))), 2)
        self.assertEqual((self.backups / "old.zip").read_bytes(), b"fixture: retained old file")


if __name__ == "__main__":
    unittest.main()
