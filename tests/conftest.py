"""Тестовый стенд: настоящий Dispatcher + поддельная сессия Telegram.

Бот не обращается в сеть: все вызовы Bot API перехватываются FakeTelegram,
а «пользователи» отправляют сообщения и нажимают кнопки через Harness.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import (DeleteMessage, EditMessageReplyMarkup, EditMessageText, SendDocument,
                             SendMessage, SendPhoto)
from aiogram.types import InlineKeyboardMarkup, Message, Update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import build_dispatcher  # noqa: E402
from config import Settings  # noqa: E402
from content.studio import STUDIO  # noqa: E402
from database import Database, Repository  # noqa: E402

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
ADMIN_ID = 900
CLIENT_ID = 100


@dataclass
class Out:
    chat_id: int
    message_id: int
    text: str | None
    markup: object
    kind: str
    tick: int
    deleted: bool = False
    document: bytes | None = None
    filename: str | None = None


class FakeTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.outbox: list[Out] = []
        self.calls: list = []
        self.forbidden_chats: set[int] = set()
        self.fail_next: dict[int, list] = {}      # chat_id -> очередь исключений для следующих отправок
        self._ids = 5000
        self._tick = 0

    async def close(self) -> None:
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    def _msg(self, bot, chat_id, message_id, text):
        return Message.model_validate({
            "message_id": message_id, "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": bot.id, "is_bot": True, "first_name": "bot"},
            "text": text,
        }, context={"bot": bot})

    def _find(self, chat_id, message_id):
        for o in self.outbox:
            if o.chat_id == chat_id and o.message_id == message_id:
                return o
        return None

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        self._tick += 1
        if isinstance(method, (SendMessage, SendPhoto, SendDocument)):
            queue = self.fail_next.get(method.chat_id)
            if queue:
                raise queue.pop(0)
            if method.chat_id in self.forbidden_chats:
                raise TelegramForbiddenError(method, "Forbidden: bot was blocked by the user")
            self._ids += 1
            text = getattr(method, "text", None) or getattr(method, "caption", None)
            out = Out(method.chat_id, self._ids, text, method.reply_markup, type(method).__name__, self._tick)
            if isinstance(method, SendDocument):
                doc = method.document
                out.document = getattr(doc, "data", None)
                out.filename = getattr(doc, "filename", None)
            self.outbox.append(out)
            return self._msg(bot, method.chat_id, out.message_id, text)
        if isinstance(method, EditMessageText):
            out = self._find(method.chat_id, method.message_id)
            if out is not None:
                out.text, out.markup, out.tick = method.text, method.reply_markup, self._tick
            return self._msg(bot, method.chat_id, method.message_id, method.text)
        if isinstance(method, EditMessageReplyMarkup):
            out = self._find(method.chat_id, method.message_id)
            if out is not None:
                out.markup = method.reply_markup
            return True
        if isinstance(method, DeleteMessage):
            out = self._find(method.chat_id, method.message_id)
            if out is not None:
                out.deleted = True
            return True
        return True


@dataclass
class Harness:
    bot: Bot
    dp: object
    tg: FakeTelegram
    repo: Repository
    settings: Settings
    _update_id: int = 0
    _msg_id: int = 0
    answers: list = field(default_factory=list)

    # ---- входящие
    async def _feed(self, payload: dict) -> None:
        self._update_id += 1
        update = Update.model_validate({"update_id": self._update_id, **payload}, context={"bot": self.bot})
        await self.dp.feed_update(self.bot, update)

    def _user(self, uid: int, username: str | None = None) -> dict:
        return {"id": uid, "is_bot": False, "first_name": "Иван", "last_name": "Петров",
                **({"username": username} if username else {})}

    async def send(self, uid: int, text: str, *, username: str | None = "ivan") -> None:
        self._msg_id += 1
        msg = {"message_id": self._msg_id, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
               "from": self._user(uid, username), "text": text}
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self._feed({"message": msg})

    async def send_contact(self, uid: int, phone: str, *, contact_user_id: int | None = None) -> None:
        self._msg_id += 1
        msg = {"message_id": self._msg_id, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
               "from": self._user(uid), "contact": {"phone_number": phone, "first_name": "Иван",
                                                     "user_id": contact_user_id or uid}}
        await self._feed({"message": msg})

    async def send_sticker(self, uid: int) -> None:
        self._msg_id += 1
        msg = {"message_id": self._msg_id, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
               "from": self._user(uid),
               "sticker": {"file_id": "x", "file_unique_id": "y", "type": "regular", "width": 1,
                           "height": 1, "is_animated": False, "is_video": False}}
        await self._feed({"message": msg})

    async def press(self, uid: int, out: Out, data: str, *, username: str | None = "ivan") -> None:
        await self._feed({"callback_query": {
            "id": f"cb{self._update_id + 1}", "from": self._user(uid, username), "chat_instance": "ci",
            "data": data,
            "message": {"message_id": out.message_id, "date": int(time.time()),
                        "chat": {"id": out.chat_id, "type": "private"},
                        "from": {"id": self.bot.id, "is_bot": True, "first_name": "bot"},
                        "text": out.text or "."},
        }})

    # ---- исходящие
    def visible(self, chat_id: int) -> list[Out]:
        return [o for o in self.tg.outbox if o.chat_id == chat_id and not o.deleted]

    def last(self, chat_id: int) -> Out:
        msgs = self.visible(chat_id)
        assert msgs, f"в чате {chat_id} нет сообщений"
        return max(msgs, key=lambda o: o.tick)

    def last_text(self, chat_id: int) -> str:
        return self.last(chat_id).text or ""

    def all_text(self, chat_id: int) -> str:
        return "\n---\n".join(o.text or "" for o in self.visible(chat_id))

    @staticmethod
    def buttons(out: Out) -> list:
        if isinstance(out.markup, InlineKeyboardMarkup):
            return [b for row in out.markup.inline_keyboard for b in row]
        return []

    def find_button(self, chat_id: int, part: str):
        for out in sorted(self.visible(chat_id), key=lambda o: -o.tick):
            for b in self.buttons(out):
                if part in b.text and b.callback_data:
                    return out, b
        have = [b.text for o in self.visible(chat_id) for b in self.buttons(o)]
        raise AssertionError(f"кнопка «{part}» не найдена. Есть: {have}")

    async def click(self, uid: int, part: str, *, username: str | None = "ivan") -> Out:
        out, button = self.find_button(uid, part)
        await self.press(uid, out, button.callback_data, username=username)
        return out


@pytest_asyncio.fixture
async def env(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    repo = Repository(db)
    settings = Settings(
        bot_token=TOKEN, admin_ids=frozenset({ADMIN_ID}), db_path=tmp_path / "test.db",
        portfolio_dir=tmp_path / "portfolio", log_level="INFO",
        studio=replace(STUDIO, manager_username=""),
    )
    tg = FakeTelegram()
    bot = Bot(token=TOKEN, session=tg, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(settings, repo)
    yield Harness(bot=bot, dp=dp, tg=tg, repo=repo, settings=settings)
    await db.close()
