#!/usr/bin/env bash
# Обновление кода БЕЗ потери базы. Запускать на сервере: sudo ./deploy/update.sh
# (также вызывается из install.sh). База лежит в /var/lib/detail-lab и этим скриптом не затрагивается.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Запустите через sudo." >&2; exit 1; }

FIRST=0
[ "${1:-}" = "--first-install" ] && FIRST=1

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER=detail-lab
APP_DIR=/opt/detail-lab/app
VENV=/opt/detail-lab/venv
ENV_FILE=/etc/detail-lab/detail-lab.env
UNITS=(detail-lab.service detail-lab-backup.service detail-lab-backup.timer)

[ -f "$ENV_FILE" ] || { echo "Нет $ENV_FILE — сначала выполните install.sh." >&2; exit 1; }

db_path="$(grep -E '^DATABASE_PATH=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
db_path="${db_path:-/var/lib/detail-lab/detail_lab.db}"

# 0) страховка: свежая копия базы перед обновлением (если база уже есть)
if [ -f "$db_path" ] && systemctl cat detail-lab-backup.service >/dev/null 2>&1; then
  echo "==> Резервная копия перед обновлением"
  systemctl start detail-lab-backup.service \
    || { echo "Не удалось сделать резервную копию — обновление остановлено." >&2; exit 1; }
fi

# 1) код: зеркалим исходники, не трогая секреты, базу и виртуальное окружение
install -d -m 0750 -o root -g "$APP_USER" "$APP_DIR"
if [ "$SRC" = "$APP_DIR" ]; then
  echo "==> Код уже в $APP_DIR (скрипт запущен оттуда) — копирование пропускаю"
else
  echo "==> Копирую код в $APP_DIR"
  rsync -a --delete \
    --chown=root:"$APP_USER" --chmod=D750,F640 \
    --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
    --exclude '.env' --exclude '.env.*' --exclude 'data/' --exclude '*.db' --exclude '*.db-*' \
    --exclude '.DS_Store' \
    "$SRC"/ "$APP_DIR"/
fi

# 2) зависимости (версии зафиксированы в constraints.txt)
echo "==> Зависимости Python"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt" -c "$APP_DIR/constraints.txt"

# 3) systemd-юниты
echo "==> systemd"
changed=0
for u in "${UNITS[@]}"; do
  if ! cmp -s "$APP_DIR/deploy/$u" "/etc/systemd/system/$u"; then
    install -m 0644 "$APP_DIR/deploy/$u" "/etc/systemd/system/$u"; changed=1
  fi
done
[ "$changed" -eq 1 ] && systemctl daemon-reload
systemctl enable detail-lab.service detail-lab-backup.timer >/dev/null 2>&1
systemctl start detail-lab-backup.timer

# 4) токен заполнен? иначе стартовать нечего
token="$(grep -E '^BOT_TOKEN=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
if [ -z "$token" ]; then
  echo
  echo "Установка завершена, но BOT_TOKEN в $ENV_FILE пока пуст."
  echo "  1) sudo nano $ENV_FILE        (BOT_TOKEN и ADMIN_IDS)"
  echo "  2) sudo systemctl start detail-lab"
  echo "  3) sudo journalctl -u detail-lab -f"
  exit 0
fi

# 5) проверка конфигурации до перезапуска: сломанный релиз не должен убить рабочий бот
echo "==> Проверка конфигурации и импортов"
# systemd-run читает EnvironmentFile ровно так же, как сам сервис (пробелы в значениях, без source).
systemd-run --quiet --wait --pipe --collect \
  -p User="$APP_USER" -p Group="$APP_USER" -p EnvironmentFile="$ENV_FILE" \
  -p WorkingDirectory="$APP_DIR" -p StateDirectory=detail-lab \
  -E PYTHONDONTWRITEBYTECODE=1 \
  "$VENV/bin/python" bot.py --check \
  || { echo "Проверка не прошла — сервис НЕ перезапущен (работает прежняя версия)." >&2; exit 1; }

echo "==> Перезапуск сервиса"
systemctl restart detail-lab.service
sleep 3
systemctl --no-pager --lines=5 status detail-lab.service || true
echo
echo "Логи: sudo journalctl -u detail-lab -f"
