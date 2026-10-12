#!/usr/bin/env bash
# Запускается на ВАШЕМ компьютере: отправляет код на сервер (без .env, базы, venv и мусора).
# Использование:  ./deploy/upload.sh пользователь@адрес_сервера
# Адрес и пользователь в скрипт не зашиты.
set -euo pipefail
TARGET="${1:-}"
[ -n "$TARGET" ] || { echo "Использование: $0 пользователь@адрес_сервера" >&2; exit 1; }
cd "$(dirname "${BASH_SOURCE[0]}")/.."
rsync -az --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
  --exclude '.env' --exclude '.env.*' --exclude 'data/' --exclude '*.db' --exclude '*.db-*' \
  --exclude '.DS_Store' \
  ./ "$TARGET:detail_lab_bot_src/"
echo "Код загружен в ~/detail_lab_bot_src на $TARGET."
echo "Дальше на сервере:  sudo ~/detail_lab_bot_src/deploy/update.sh   (первая установка: install.sh)"
