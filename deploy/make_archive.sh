#!/usr/bin/env bash
# Собирает zip-архив проекта для загрузки на хостинг (Bothost: «Загрузка бота из архива»).
# В архив НЕ попадают: .env, база данных, .venv, .git, кэши. Фото портфолио (assets/portfolio) — попадают.
# Использование: ./deploy/make_archive.sh [путь_к_архиву]    (по умолчанию ~/Downloads/detail_lab_bot.zip)
set -euo pipefail
command -v zip >/dev/null 2>&1 || { echo "Не найдена утилита zip" >&2; exit 1; }
cd "$(dirname "${BASH_SOURCE[0]}")/.."
OUT="${1:-$HOME/Downloads/detail_lab_bot.zip}"
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
zip -qr "$OUT" . \
  -x '.git/*' '.venv/*' '*/__pycache__/*' '__pycache__/*' '.pytest_cache/*' \
     '.env' '.env.*' '!.env.example' 'data/*' '*.db' '*.db-*' '*.DS_Store' 'tests/*' 'deploy/*' \
     'requirements-dev.txt' 'constraints.txt'
# .env.example безопасен и полезен — добавляем явно
zip -q "$OUT" .env.example
echo "Готово: $OUT ($(du -h "$OUT" | cut -f1))"
