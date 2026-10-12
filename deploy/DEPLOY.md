# Развёртывание DETAIL LAB на VPS (Ubuntu 24.04, systemd)

Бот работает как системный сервис `detail-lab`: стартует при загрузке сервера, перезапускается при сбое,
запущен от отдельного пользователя `detail-lab` (не root). Заявки хранятся в SQLite вне кода —
`/var/lib/detail-lab/detail_lab.db`, и обновление кода их не трогает. Каждую ночь делается резервная копия.
Docker не нужен. Входящие порты не нужны: бот сам опрашивает Telegram (long polling).

> Эти файлы подготовлены и проверены локально (синтаксис скриптов, резервное копирование, зависимости).
> Сам VPS я не настраивал: шаги ниже вы выполняете на своём сервере.

## Что где лежит на сервере

| Что                  | Путь                                  | Кто владелец                   |
|----------------------|----------------------------------------|--------------------------------|
| Код                  | `/opt/detail-lab/app`                  | root:detail-lab, только чтение |
| Python-окружение     | `/opt/detail-lab/venv`                 | root                           |
| Секреты и настройки  | `/etc/detail-lab/detail-lab.env`       | root:detail-lab, права `0640`  |
| **База данных**      | `/var/lib/detail-lab/detail_lab.db`    | detail-lab, `0750`             |
| Резервные копии      | `/var/backups/detail-lab/`             | detail-lab, `0700`             |
| Юниты systemd        | `/etc/systemd/system/detail-lab*.{service,timer}` | root              |

Файлы проекта: `deploy/install.sh`, `deploy/update.sh`, `deploy/upload.sh`, `deploy/backup.sh`,
`deploy/detail-lab.service`, `deploy/detail-lab-backup.service`, `deploy/detail-lab-backup.timer`,
а также `constraints.txt` (точные версии библиотек).

---

## 1. Сервер

Подойдёт VPS: Ubuntu Server 24.04 LTS, 1 vCPU, 1 ГБ RAM, 20 ГБ SSD. Расположение — Европа
(серверу нужен доступ к `api.telegram.org`; из России он может быть нестабилен). Бот занимает ~50 МБ RAM.

## 2. Первичная настройка Ubuntu (один раз)

Подключитесь (адрес и пароль/ключ дал хостер; `IP_СЕРВЕРА` подставьте свой):

```bash
ssh root@IP_СЕРВЕРА
```

**2.1. Обычный пользователь с sudo** (работать под root постоянно не нужно):

```bash
adduser deploy                 # задайте пароль
usermod -aG sudo deploy
```

**2.2. Вход по SSH-ключу.** На вашем компьютере (если ключа ещё нет: `ssh-keygen -t ed25519`):

```bash
ssh-copy-id deploy@IP_СЕРВЕРА
ssh deploy@IP_СЕРВЕРА          # убедитесь, что заходит БЕЗ пароля, и дальше работайте под deploy
```

> Отключать вход по паролю (`PasswordAuthentication no` в `/etc/ssh/sshd_config`) стоит только после того,
> как вход по ключу проверен, и не закрывая текущую сессию — иначе можно потерять доступ.
> Скрипты проекта настройки SSH не меняют.

**2.3. Обновления и файрвол.** Бот не принимает входящих соединений, поэтому разрешаем только SSH:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ufw unattended-upgrades fail2ban
sudo ufw allow OpenSSH         # СНАЧАЛА разрешить SSH
sudo ufw enable
sudo ufw status
sudo dpkg-reconfigure -plow unattended-upgrades   # автоматические обновления безопасности (ответьте «Да»)
```

**2.4. (Рекомендуется для 1 ГБ RAM) своп-файл 1 ГБ:**

```bash
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**2.5. Постоянные логи** (иначе после перезагрузки журнал может пропасть):

```bash
sudo mkdir -p /var/log/journal
sudo sed -i 's/^#\?SystemMaxUse=.*/SystemMaxUse=200M/' /etc/systemd/journald.conf
sudo systemctl restart systemd-journald
```

## 3. Передача проекта на сервер

**Вариант A (проще всего): с вашего компьютера через rsync.** Из папки проекта:

```bash
./deploy/upload.sh deploy@IP_СЕРВЕРА
```

Скрипт отправит код в `~/detail_lab_bot_src` на сервере, **без** `.env`, базы данных, `.venv` и `.git`.
Свои фото портфолио из `assets/portfolio/` он отправит.

**Вариант B: через git.** Репозиторий приватный, поэтому нужен deploy-ключ: на сервере
`ssh-keygen -t ed25519 -f ~/.ssh/detail_lab_deploy`, публичную часть добавьте в GitHub → репозиторий →
Settings → Deploy keys (только чтение), затем `git clone git@github.com:<аккаунт>/detail_lab_bot.git ~/detail_lab_bot_src`
(для нестандартного имени ключа настройте `~/.ssh/config`).
Фото портфолио в git не попадают — их надо отправить отдельно.

## 4. Установка (один раз)

На сервере:

```bash
sudo bash ~/detail_lab_bot_src/deploy/install.sh
```

Что сделает скрипт: поставит `python3`, `python3-venv`, `sqlite3`, `rsync`; создаст системного пользователя
`detail-lab` (без пароля, без входа, без sudo); создаст каталоги из таблицы выше; создаст **шаблон** файла
секретов (существующий не перезапишет); скопирует код, создаст venv и установит зависимости строго по
`constraints.txt`; установит юниты и включит автозапуск и ночные бэкапы.

## 5. Токен и запуск

Токен и ID администратора вы вписываете сами. Откройте файл секретов:

```bash
sudo nano /etc/detail-lab/detail-lab.env
```

```
BOT_TOKEN=123456789:AA...      # токен от @BotFather
ADMIN_IDS=111111111            # ваш Telegram ID (бот @userinfobot); несколько — через запятую
DATABASE_PATH=/var/lib/detail-lab/detail_lab.db
LOG_LEVEL=INFO
```

Остальные настройки (`MANAGER_USERNAME`, `STUDIO_PHONE`, `DEMO_MODE=false`…) — как в `.env.example`.
Значения пишутся без кавычек. Права файла проверьте командой `ls -l /etc/detail-lab/detail-lab.env`
(должно быть `-rw-r----- root detail-lab`). Затем:

```bash
sudo bash ~/detail_lab_bot_src/deploy/update.sh      # проверит настройки и запустит сервис
```

> **Перед запуском на сервере остановите бота на своём Mac** (`pkill -f bot.py`) — на один токен
> допускается только один polling-процесс (см. раздел 11).

## 6. Проверка, что всё работает

```bash
sudo systemctl status detail-lab            # должно быть: active (running)
sudo journalctl -u detail-lab -n 50 --no-pager
systemctl is-enabled detail-lab             # enabled — стартует при загрузке сервера
systemctl list-timers detail-lab-backup.timer
ls -l /var/lib/detail-lab/                  # detail_lab.db принадлежит detail-lab
```

В логе должна быть строка `Бот @... запущен`. Затем откройте бота в Telegram: `/start`, оформите
тестовую заявку, проверьте `/admin`. Проверка автозапуска: `sudo reboot`, через минуту — `systemctl status detail-lab`.

## 7. Остановка, запуск, перезапуск, логи

```bash
sudo systemctl stop detail-lab
sudo systemctl start detail-lab
sudo systemctl restart detail-lab
sudo systemctl status detail-lab
sudo journalctl -u detail-lab -f                  # логи в реальном времени (Ctrl+C — выйти)
sudo journalctl -u detail-lab --since "1 hour ago"
sudo journalctl -u detail-lab -p err              # только ошибки
sudo systemctl disable --now detail-lab           # выключить и убрать из автозапуска
```

## 8. Обновление кода без потери базы

1. На вашем компьютере: `./deploy/upload.sh deploy@IP_СЕРВЕРА`  (или `git pull` в `~/detail_lab_bot_src` при варианте B).
2. На сервере: `sudo bash ~/detail_lab_bot_src/deploy/update.sh`.

Что делает `update.sh`: сначала делает **резервную копию базы** (если копия не удалась — обновление
останавливается), затем копирует код в `/opt/detail-lab/app`, обновляет зависимости, обновляет юниты,
**проверяет настройки и импорты новой версии** (`bot.py --check`) и только после этого перезапускает сервис.
Если проверка не прошла, прежняя версия продолжает работать. База `/var/lib/detail-lab/` скриптом не затрагивается.
Секреты в `/etc/detail-lab/` тоже не меняются.

Правка настроек без смены кода: `sudo nano /etc/detail-lab/detail-lab.env`, затем `sudo systemctl restart detail-lab`.

## 9. Резервные копии и восстановление

**Как устроено.** Таймер `detail-lab-backup.timer` каждую ночь в ~03:30 (время сервера, обычно UTC) запускает
`deploy/backup.sh`. Он делает консистентную копию штатной командой SQLite `.backup`, проверяет её
`PRAGMA integrity_check`, сжимает в `/var/backups/detail-lab/detail_lab-ГГГГ-ММ-ДД.db.gz` и оставляет **7 последних**
копий (больше — удаляет, остальные файлы в каталоге не трогает). Копия безопасна при работающем боте.

```bash
sudo systemctl start detail-lab-backup.service     # сделать копию прямо сейчас
sudo journalctl -u detail-lab-backup -n 20 --no-pager
sudo ls -lh /var/backups/detail-lab/
systemctl list-timers detail-lab-backup.timer
```

> Копии лежат на том же сервере: если сервер пропадёт, пропадут и они. Периодически забирайте свежую копию к себе.
> На сервере: `sudo cp /var/backups/detail-lab/detail_lab-ГГГГ-ММ-ДД.db.gz ~ && sudo chown deploy ~/detail_lab-ГГГГ-ММ-ДД.db.gz`,
> на вашем компьютере: `scp deploy@IP_СЕРВЕРА:detail_lab-ГГГГ-ММ-ДД.db.gz .`

**Восстановление** (например, база повреждена или нужно вернуться к вчерашнему состоянию):

```bash
sudo systemctl stop detail-lab
sudo mv /var/lib/detail-lab/detail_lab.db /var/lib/detail-lab/detail_lab.db.broken-$(date +%F-%H%M)   # не удаляем, на всякий случай
sudo rm -f /var/lib/detail-lab/detail_lab.db-wal /var/lib/detail-lab/detail_lab.db-shm
sudo gunzip -c /var/backups/detail-lab/detail_lab-ГГГГ-ММ-ДД.db.gz | sudo tee /var/lib/detail-lab/detail_lab.db >/dev/null
sudo chown detail-lab:detail-lab /var/lib/detail-lab/detail_lab.db
sudo chmod 0600 /var/lib/detail-lab/detail_lab.db
sudo -u detail-lab sqlite3 /var/lib/detail-lab/detail_lab.db 'PRAGMA integrity_check; SELECT COUNT(*) FROM bookings;'
sudo systemctl start detail-lab
```

Файл `detail_lab.db.broken-*` можно удалить вручную, когда убедитесь, что всё в порядке.

**Перенос существующей базы с Mac** (необязательно — в ней обычно только тестовые данные). На Mac:
`sqlite3 data/detail_lab.db ".backup '/tmp/detail_lab_export.db'"` и `scp /tmp/detail_lab_export.db deploy@IP_СЕРВЕРА:/tmp/`,
на сервере — шаги восстановления выше, подставив `/tmp/detail_lab_export.db` вместо распаковки архива (без `gunzip`).

## 10. Удаление сервиса (если понадобится)

```bash
sudo systemctl disable --now detail-lab detail-lab-backup.timer
sudo rm /etc/systemd/system/detail-lab.service /etc/systemd/system/detail-lab-backup.{service,timer}
sudo systemctl daemon-reload
```

Данные (`/var/lib/detail-lab`, `/var/backups/detail-lab`) и секреты (`/etc/detail-lab`) эти команды **не удаляют**.

---

## 11. Диагностика типичных проблем

Начинать всегда с: `sudo systemctl status detail-lab` и `sudo journalctl -u detail-lab -n 100 --no-pager`.

### Неправильный токен
*Признаки:* в логе `Telegram отклонил токен (Unauthorized)`; сервис остановился (`inactive/failed`, код выхода 3 —
автоперезапуск намеренно отключён). Если токен отозвали, пока бот работал, в логе повторяется
`Failed to fetch updates - TelegramUnauthorizedError`.
*Что делать:* проверить `BOT_TOKEN` в `/etc/detail-lab/detail-lab.env` (без кавычек и пробелов, формат `цифры:буквы`),
при необходимости получить новый токен в @BotFather (`/token`), затем `sudo systemctl restart detail-lab`.
Быстрая проверка токена вручную (токен в историю команд попадёт — лучше брать из файла):
`curl -s "https://api.telegram.org/bot$(sudo grep ^BOT_TOKEN= /etc/detail-lab/detail-lab.env | cut -d= -f2-)/getMe"`.

### Ошибка настроек
*Признаки:* `Ошибка конфигурации: ...` (например, `BOT_TOKEN не задан`, `ADMIN_IDS: ...`), код выхода 2.
*Что делать:* исправить значение в файле `detail-lab.env` по тексту ошибки, перезапустить сервис.

### Ошибки импорта (`ModuleNotFoundError`, `ImportError`)
*Причины:* зависимости не установлены или код загружен не полностью.
*Что делать:*
```bash
sudo /opt/detail-lab/venv/bin/python -m pip install -r /opt/detail-lab/app/requirements.txt -c /opt/detail-lab/app/constraints.txt
sudo bash ~/detail_lab_bot_src/deploy/update.sh      # полная пересборка: код + зависимости + проверка
```
Если после этого видна `No module named 'handlers'` и подобное — каталог `/opt/detail-lab/app` пуст или неполон: повторите загрузку кода (раздел 3).

### Нет прав на базу данных (`sqlite3.OperationalError: unable to open database file`, `attempt to write a readonly database`)
*Причины:* каталог базы принадлежит не `detail-lab` (например, файл положили руками от root).
*Что делать:*
```bash
ls -ld /var/lib/detail-lab; ls -l /var/lib/detail-lab
sudo chown -R detail-lab:detail-lab /var/lib/detail-lab
sudo chmod 0750 /var/lib/detail-lab && sudo chmod 0600 /var/lib/detail-lab/detail_lab.db*
sudo systemctl restart detail-lab
```
Также проверьте, что в `detail-lab.env` указан `DATABASE_PATH=/var/lib/detail-lab/detail_lab.db`: песочница сервиса
разрешает запись только в этот каталог. Если база лежит в другом месте, сервис не сможет в неё писать.

### Нехватка памяти
*Признаки:* сервис внезапно перезапускается; в `sudo journalctl -k | grep -i -E "oom|killed process"` есть записи;
в логе юнита `Failed with result 'oom-kill'`.
*Что делать:* `free -h` и `systemctl show detail-lab -p MemoryCurrent -p MemoryPeak` — бот в норме занимает ~50–100 МБ.
Если память занята другими процессами — найдите их (`ps aux --sort=-%mem | head`). Добавьте своп (п. 2.4).
Лимит сервиса `MemoryMax=512M` задан в `detail-lab.service`; менять по необходимости и выполнить `sudo systemctl daemon-reload && sudo systemctl restart detail-lab`.

### Потеря соединения с Telegram API
*Признаки:* в логе `Failed to fetch updates - TelegramNetworkError ... Request timeout / Cannot connect to host api.telegram.org`
и `Sleep for N seconds and try again`. Это **нормально** при кратких сбоях: бот сам повторяет запросы с нарастающей паузой
и пишет `Connection established`, когда связь вернулась. Перезапуск не нужен.
*Если длится долго:*
```bash
curl -sS -m 10 -o /dev/null -w "%{http_code}\n" https://api.telegram.org/     # ждём 200/302/404, а не таймаут
getent hosts api.telegram.org
curl -sS -m 10 -o /dev/null -w "%{http_code}\n" https://www.google.com/    # есть ли интернет вообще
```
Если с сервера Telegram недоступен (блокировки у хостера/в стране сервера) — выберите сервер в другой стране.
При старте сервера без сети бот ждёт сети и сам запускается, когда она появится.

### Конфликт двух polling-процессов
*Признаки:* в логе повторяется `TelegramConflictError: ... Conflict: terminated by other getUpdates request; make sure that only one bot instance is running`;
бот отвечает редко или с задержкой.
*Причина:* тот же токен используется ещё где-то: бот на вашем Mac, второй экземпляр на сервере, старая копия.
*Что делать:*
```bash
pgrep -af "bot.py"                         # на этом сервере должен быть ровно один процесс (запущенный systemd)
sudo systemctl status detail-lab           # проверить, что сервис один
# на Mac:  pkill -f bot.py
```
Если не можете найти, где ещё запущен бот, — отзовите токен в @BotFather (`/revoke`), впишите новый в `detail-lab.env`
и перезапустите сервис: старые экземпляры перестанут работать. Если ранее для бота был установлен webhook, при старте он снимается автоматически.

### Сервис не стартует: «start request repeated too quickly»
Сработал предохранитель systemd (20 неудачных стартов за 10 минут). Найдите причину в журнале, исправьте,
затем `sudo systemctl reset-failed detail-lab && sudo systemctl start detail-lab`.

### Бот не отвечает, хотя сервис `active (running)`
1. `sudo journalctl -u detail-lab -n 100` — есть ли `TelegramConflictError` или сетевые ошибки (см. выше).
2. Правильный ли бот открыт (токен из `detail-lab.env` соответствует username бота: `getMe`).
3. Не заблокирован ли бот у пользователя (для заявок админу: администратор должен нажать **Start** у бота).
4. `sudo systemctl restart detail-lab` и повторная проверка.
