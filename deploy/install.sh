#!/usr/bin/env bash
# ПЕРВАЯ установка на Ubuntu 24.04. Запускать на сервере: sudo ./deploy/install.sh
# Идемпотентен: повторный запуск ничего не ломает и не перезаписывает /etc/detail-lab/detail-lab.env.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Запустите через sudo." >&2; exit 1; }

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER=detail-lab
STATE_DIR=/var/lib/detail-lab
BACKUP_DIR=/var/backups/detail-lab
ETC_DIR=/etc/detail-lab
ENV_FILE="$ETC_DIR/detail-lab.env"

echo "==> Пакеты (python3, venv, sqlite3, rsync)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv sqlite3 rsync ca-certificates >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Нужен Python 3.11+ (на Ubuntu 24.04 он 3.12)." >&2; exit 1; }

echo "==> Системный пользователь $APP_USER (без входа и без sudo)"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi

echo "==> Каталоги"
install -d -m 0755 /opt/detail-lab
install -d -m 0750 -o "$APP_USER" -g "$APP_USER" "$STATE_DIR"
install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$BACKUP_DIR"
install -d -m 0750 -o root -g "$APP_USER" "$ETC_DIR"

echo "==> Файл с секретами $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
  umask 077
  cat > "$ENV_FILE" <<ENVEOF
# Секреты и настройки бота. Права: root:detail-lab 0640. НЕ копируйте этот файл в git/чаты.
BOT_TOKEN=
ADMIN_IDS=
DATABASE_PATH=$STATE_DIR/detail_lab.db
LOG_LEVEL=INFO
# MANAGER_USERNAME=
# STUDIO_PHONE=
# STUDIO_ADDRESS=
# MAP_URL=
# WEBSITE_URL=
# DEMO_MODE=true
ENVEOF
  echo "    создан шаблон — заполните BOT_TOKEN и ADMIN_IDS"
else
  echo "    уже существует — не трогаю"
fi
chown root:"$APP_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

echo "==> Код, зависимости, systemd"
exec "$SRC/deploy/update.sh" --first-install
