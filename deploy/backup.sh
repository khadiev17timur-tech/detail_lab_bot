#!/usr/bin/env bash
# Резервная копия SQLite штатным механизмом (.backup), а не простым cp:
# копия консистентна, даже если бот в этот момент пишет в базу.
#
# Переменные (необязательно):
#   DATABASE_PATH  путь к базе            (по умолчанию /var/lib/detail-lab/detail_lab.db)
#   BACKUP_DIR     куда класть копии      (по умолчанию /var/backups/detail-lab)
#   BACKUP_KEEP    сколько копий хранить  (по умолчанию 7)
#
# Одна копия в сутки: detail_lab-ГГГГ-ММ-ДД.db.gz. Повторный запуск в тот же день перезаписывает
# копию этого дня (атомарно). Удаляются только файлы detail_lab-*.db.gz старше последних KEEP.
set -euo pipefail
umask 077

DB_PATH="${DATABASE_PATH:-/var/lib/detail-lab/detail_lab.db}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/detail-lab}"
KEEP="${BACKUP_KEEP:-7}"

fail() { echo "backup: ОШИБКА: $*" >&2; exit 1; }

command -v sqlite3 >/dev/null 2>&1 || fail "не найден sqlite3 (sudo apt install sqlite3)"
command -v gzip >/dev/null 2>&1 || fail "не найден gzip"
[[ "$KEEP" =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 1 ] || fail "BACKUP_KEEP должно быть числом >= 1"
[ -f "$DB_PATH" ] || fail "база не найдена: $DB_PATH"
case "$DB_PATH$BACKUP_DIR" in *"'"*) fail "пути с одинарной кавычкой не поддерживаются" ;; esac

mkdir -p "$BACKUP_DIR"
day="$(date +%Y-%m-%d)"
tmp="$BACKUP_DIR/.detail_lab-$day.db.tmp"
part="$BACKUP_DIR/.detail_lab-$day.db.gz.part"
final="$BACKUP_DIR/detail_lab-$day.db.gz"
cleanup() { rm -f -- "$tmp" "$tmp-wal" "$tmp-shm" "$tmp-journal" "$part"; }
trap cleanup EXIT
cleanup

# 1) консистентная копия средствами SQLite (ждём блокировку до 10 с)
sqlite3 "$DB_PATH" ".timeout 10000" ".backup '$tmp'"

# 2) копия наследует режим WAL от боевой базы; переводим её в обычный режим, чтобы архив был
#    одним самодостаточным файлом (без -wal/-shm), и проверяем её целостность
sqlite3 "$tmp" 'PRAGMA journal_mode=DELETE;' >/dev/null
result="$(sqlite3 "$tmp" 'PRAGMA integrity_check;')"
[ "$result" = "ok" ] || fail "копия не прошла integrity_check: $result"

# 3) сжимаем и кладём на место атомарно
gzip -c "$tmp" > "$part"
mv -f -- "$part" "$final"

# 4) ротация: оставляем KEEP последних (имена с датой сортируются по времени)
count=0
while IFS= read -r old; do
  count=$((count + 1))
  if [ "$count" -gt "$KEEP" ]; then rm -f -- "$old"; fi
done < <(ls -1 "$BACKUP_DIR"/detail_lab-*.db.gz 2>/dev/null | sort -r)

echo "backup: готово $final ($(du -h "$final" | cut -f1)), хранится копий: $(ls -1 "$BACKUP_DIR"/detail_lab-*.db.gz | wc -l | tr -d ' ')"
