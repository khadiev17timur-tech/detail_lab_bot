import pytest

from config import ConfigError, load_settings, parse_admin_ids

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def env(**kw):
    base = {"BOT_TOKEN": TOKEN, "ADMIN_IDS": "1, 2;3"}
    base.update(kw)
    return base


def test_admin_ids_parsing():
    assert parse_admin_ids("1, 2;3 4,,") == frozenset({1, 2, 3, 4})
    assert parse_admin_ids("") == frozenset() and parse_admin_ids(None) == frozenset()
    for bad in ("abc", "1,x", "-5", "0"):
        with pytest.raises(ConfigError):
            parse_admin_ids(bad)


def test_load_ok_and_defaults():
    s = load_settings(env())
    assert s.admin_ids == {1, 2, 3} and s.studio.name == "DETAIL LAB" and s.studio.demo
    assert s.db_path.name == "detail_lab.db" and s.log_level == "INFO"


@pytest.mark.parametrize("token", ["", "   ", "your_token_here", "123:short", "abc"])
def test_bad_token(token):
    with pytest.raises(ConfigError):
        load_settings(env(BOT_TOKEN=token))


def test_token_never_in_repr():
    assert TOKEN not in repr(load_settings(env())) and "***" in repr(load_settings(env()))


def test_studio_overrides_and_validation():
    s = load_settings(env(MANAGER_USERNAME="@detail_mgr", MAP_URL="https://m.example/x",
                          WEBSITE_URL="", STUDIO_PHONE="+7 900 000-00-00", DEMO_MODE="false",
                          TIMEZONE="Asia/Yekaterinburg"))
    assert s.studio.manager_url == "https://t.me/detail_mgr" and s.studio.map_url == "https://m.example/x"
    assert s.studio.website_url == "" and s.studio.phone == "+7 900 000-00-00"
    assert not s.studio.demo and s.studio.timezone == "Asia/Yekaterinburg"
    for bad in ({"MANAGER_USERNAME": "a b"}, {"MAP_URL": "javascript:alert(1)"}, {"TIMEZONE": "Mars/Base"},
                {"LOG_LEVEL": "LOUD"}):
        with pytest.raises(ConfigError):
            load_settings(env(**bad))


def test_database_path_env_and_legacy_alias(tmp_path):
    new = load_settings(env(DATABASE_PATH="/var/lib/detail-lab/detail_lab.db"))
    assert str(new.db_path) == "/var/lib/detail-lab/detail_lab.db"
    legacy = load_settings(env(DB_PATH="/srv/old.db"))
    assert str(legacy.db_path) == "/srv/old.db"
    both = load_settings(env(DATABASE_PATH="/a.db", DB_PATH="/b.db"))
    assert str(both.db_path) == "/a.db"          # новое имя главнее старого


def test_backup_settings(tmp_path):
    s = load_settings(env(DATABASE_PATH=str(tmp_path / "x" / "db.sqlite")))
    assert s.backup_enabled and s.backup_send_to_admins and s.backup_keep == 7
    assert s.backup_dir == tmp_path / "x" / "backups"
    s2 = load_settings(env(AUTO_BACKUP="false", BACKUP_SEND_TO_ADMINS="0", BACKUP_KEEP="14"))
    assert not s2.backup_enabled and not s2.backup_send_to_admins and s2.backup_keep == 14
    for bad in ("0", "abc", "-1"):
        with pytest.raises(ConfigError):
            load_settings(env(BACKUP_KEEP=bad))
