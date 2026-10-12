"""Проверки файлов развёртывания: инструкции должны соответствовать реально созданным файлам."""
import gzip
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
SCRIPTS = ["install.sh", "update.sh", "upload.sh", "backup.sh", "make_archive.sh"]
UNITS = ["detail-lab.service", "detail-lab-backup.service", "detail-lab-backup.timer"]
HAS_BASH = shutil.which("bash") is not None
HAS_SQLITE_CLI = shutil.which("sqlite3") is not None


def test_all_deploy_files_exist():
    for name in SCRIPTS + UNITS + ["DEPLOY.md", "BOTHOST.md", "make_archive.sh"]:
        assert (DEPLOY / name).is_file(), name
    assert (ROOT / "constraints.txt").is_file()


@pytest.mark.skipif(not HAS_BASH, reason="нужен bash")
@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_have_valid_syntax_and_strict_mode(script):
    path = DEPLOY / script
    assert subprocess.run(["bash", "-n", str(path)]).returncode == 0
    assert "set -euo pipefail" in path.read_text()


def test_docs_reference_only_existing_files():
    doc = (DEPLOY / "DEPLOY.md").read_text() + (ROOT / "README.md").read_text()
    for ref in set(re.findall(r"deploy/([A-Za-z0-9_.\-]+\.(?:sh|service|timer|md))", doc)):
        assert (DEPLOY / ref).is_file(), f"в документации упомянут несуществующий файл deploy/{ref}"


def test_service_unit_is_hardened_and_consistent():
    unit = (DEPLOY / "detail-lab.service").read_text()
    for needle in ("User=detail-lab", "Restart=on-failure", "RestartPreventExitStatus=2 3",
                   "EnvironmentFile=/etc/detail-lab/detail-lab.env", "StateDirectory=detail-lab",
                   "NoNewPrivileges=true", "ProtectSystem=strict", "WantedBy=multi-user.target",
                   "After=network-online.target", "TimeoutStopSec=", "ExecStart=/opt/detail-lab/venv/bin/python bot.py"):
        assert needle in unit, needle
    assert "User=root" not in unit
    # в юнитах нет секретов и адресов
    for text in (DEPLOY / u for u in UNITS):
        body = text.read_text()
        assert not re.search(r"\d{8,}:[A-Za-z0-9_-]{30,}", body)
        assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", body)


def test_exit_codes_in_unit_match_bot():
    bot_src = (ROOT / "bot.py").read_text()
    assert "EXIT_OK, EXIT_CONFIG, EXIT_TOKEN = 0, 2, 3" in bot_src


def test_backup_unit_can_write_where_script_writes():
    unit = (DEPLOY / "detail-lab-backup.service").read_text()
    script = (DEPLOY / "backup.sh").read_text()
    assert "/var/backups/detail-lab" in unit and "/var/backups/detail-lab" in script
    assert "ReadWritePaths=/var/lib/detail-lab /var/backups/detail-lab" in unit
    assert "[Timer]" in (DEPLOY / "detail-lab-backup.timer").read_text()
    assert "Persistent=true" in (DEPLOY / "detail-lab-backup.timer").read_text()


def test_paths_agree_between_scripts_env_and_unit():
    install = (DEPLOY / "install.sh").read_text()
    assert "DATABASE_PATH=$STATE_DIR/detail_lab.db" in install and "STATE_DIR=/var/lib/detail-lab" in install
    assert "0640" in install                       # права файла секретов
    assert "--shell /usr/sbin/nologin" in install  # пользователь без входа
    update = (DEPLOY / "update.sh").read_text()
    assert "/opt/detail-lab/app" in update and "/opt/detail-lab/venv" in update
    assert "-c \"$APP_DIR/constraints.txt\"" in update
    assert "--exclude 'data/'" in update and "--exclude '.env'" in update   # база и секреты не затираются


def test_secrets_not_in_git_and_no_hardcoded_hosts():
    gitignore = (ROOT / ".gitignore").read_text()
    for entry in (".env", "data/", "*.db"):
        assert entry in gitignore
    for name in SCRIPTS + ["DEPLOY.md"]:
        text = (DEPLOY / name).read_text()
        assert not re.search(r"\d{8,}:[A-Za-z0-9_-]{30,}", text)
        assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text), f"IP-адрес в {name}"


def test_constraints_pin_runtime_dependencies():
    pins = (ROOT / "constraints.txt").read_text()
    for pkg in ("aiogram==", "aiosqlite==", "python-dotenv=="):
        assert pkg in pins
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name = re.split(r"[<>=!~]", line)[0].lower().replace("_", "-")
            assert name in pins.lower(), name


# ---------------------------------------------------------------- backup.sh
@pytest.mark.skipif(not (HAS_BASH and HAS_SQLITE_CLI), reason="нужны bash и sqlite3")
class TestBackupScript:
    def _run(self, tmp_path, **env_over):
        env = {**os.environ, "DATABASE_PATH": str(tmp_path / "db.sqlite"), "BACKUP_DIR": str(tmp_path / "bk"), **env_over}
        return subprocess.run(["bash", str(DEPLOY / "backup.sh")], env=env, capture_output=True, text=True)

    def _make_db(self, tmp_path):
        con = sqlite3.connect(tmp_path / "db.sqlite")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE bookings (id INTEGER PRIMARY KEY, name TEXT)")
        con.executemany("INSERT INTO bookings (name) VALUES (?)", [(f"Клиент {i}",) for i in range(20)])
        con.commit()
        return con  # соединение остаётся открытым: «бот работает»

    def test_backup_is_consistent_while_db_is_open(self, tmp_path):
        con = self._make_db(tmp_path)
        res = self._run(tmp_path)
        assert res.returncode == 0, res.stderr
        files = [p for p in (tmp_path / "bk").iterdir()]
        assert [p.name for p in files if not p.name.startswith(".")] and not [p for p in files if p.name.startswith(".")]
        archive = next(p for p in files if p.suffix == ".gz")
        assert oct(archive.stat().st_mode & 0o777) == "0o600"
        restored = tmp_path / "restored.db"
        restored.write_bytes(gzip.decompress(archive.read_bytes()))
        check = sqlite3.connect(restored)
        assert check.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 20
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        con.close()

    def test_rotation_keeps_seven_newest_and_other_files(self, tmp_path):
        self._make_db(tmp_path).close()
        bk = tmp_path / "bk"
        bk.mkdir()
        for day in range(1, 13):
            (bk / f"detail_lab-2026-01-{day:02d}.db.gz").write_bytes(gzip.compress(b"x"))
        (bk / "notes.txt").write_text("не трогать")
        assert self._run(tmp_path).returncode == 0
        archives = sorted(p.name for p in bk.glob("detail_lab-*.db.gz"))
        assert len(archives) == 7 and archives[0] == "detail_lab-2026-01-07.db.gz"
        assert (bk / "notes.txt").exists()

    def test_failures_return_nonzero_and_leave_no_junk(self, tmp_path):
        assert self._run(tmp_path).returncode != 0                      # базы нет
        (tmp_path / "db.sqlite").write_text("это не база")
        assert self._run(tmp_path).returncode != 0                      # битая база
        assert self._run(tmp_path, BACKUP_KEEP="0").returncode != 0     # неверный параметр
        leftovers = list((tmp_path / "bk").glob("*")) if (tmp_path / "bk").exists() else []
        assert leftovers == []


def test_dockerfile_is_safe_for_bothost():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert text.isascii(), "Bothost плохо читает не-ASCII в Dockerfile"
    assert "FROM python:3.12" in text and 'CMD ["python", "bot.py"]' in text
    assert "WORKDIR /usr/src/app" in text            # код вне /app: его не перекрывает bind mount
    assert "/app/data" in text and "DATABASE_PATH=/app/data/" in text
    assert "COPY . ." in text
    ignore = (ROOT / ".dockerignore").read_text()
    for entry in (".env", "data", ".venv", ".git"):
        assert entry in ignore
