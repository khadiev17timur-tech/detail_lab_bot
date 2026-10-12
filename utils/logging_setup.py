"""Настройка логирования: токен и другие секреты не попадают в журнал."""
from __future__ import annotations

import logging
import re


class RedactFilter(logging.Filter):
    """Заменяет секреты в сообщениях на ***, включая вид токена в URL Telegram API."""

    _TOKEN_LIKE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b")

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def _clean(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return self._TOKEN_LIKE.sub("***", text)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self._clean(record.getMessage())
            record.args = ()
            if record.exc_info:
                # форматируем трейс заранее, чтобы очистить и его
                fmt = logging.Formatter()
                record.exc_text = self._clean(fmt.formatException(record.exc_info))
                record.exc_info = None
        except Exception:  # фильтр не должен ронять логирование
            pass
        return True


def setup_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(RedactFilter(secrets or []))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING if level != "DEBUG" else logging.DEBUG)
