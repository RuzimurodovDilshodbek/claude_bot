"""Telegramdan kelgan biriktirmalar (rasm, fayl, albom) uchun kutish ro'yxati.

Telegram albomni alohida xabarlar qilib yuboradi (caption faqat bittasida),
foydalanuvchi esa ko'pincha avval rasmni, keyin alohida xabarda matnni
yuboradi. Shuning uchun biriktirma darhol vazifaga aylanmaydi — scope
(chat + mavzu) bo'yicha shu yerda yig'ilib turadi; keyingi matn, ovoz yoki
caption ularni olib vazifa boshlaydi (bot.py `_start_or_queue`).

Faqat xotirada. Bot qayta yonsa yo'qoladi — foydalanuvchi qayta yuboradi.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from protocol import safe_name  # noqa: F401  — bot.py shu orqali ishlatadi

Scope = tuple[int, int]

# Bitta vazifa uchun jami. Telegram Bot API ham 20 MB dan katta faylni bermaydi.
MAX_TOTAL_BYTES = 20 * 1024 * 1024
# Oxirgi fayldan keyin shuncha vaqt matn kelmasa — eskiradi.
TTL_SEC = 15 * 60
# Caption kelgach albomning qolgan bo'laklari yetib kelishini kutamiz.
DEBOUNCE_SEC = 1.5


class TooLarge(ValueError):
    """Jami hajm chegaradan oshdi — fayl qabul qilinmadi."""


@dataclass
class Attachment:
    name: str
    mime: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def is_image(self) -> bool:
        return self.mime.startswith("image/")


@dataclass
class PendingBox:
    """Bitta scope'da kutayotgan biriktirmalar."""

    items: list[Attachment] = field(default_factory=list)
    caption: str = ""
    updated_at: float = field(default_factory=time.time)
    # "📎 kutmoqda" xabari (telegram.Message) — bot to'ldiradi va tahrirlaydi.
    notice: object = None
    # Debounce / eskirish vazifasi — bot boshqaradi.
    timer: asyncio.Task | None = None

    @property
    def total(self) -> int:
        return sum(a.size for a in self.items)

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) - self.updated_at > TTL_SEC

    def summary(self) -> str:
        return summary(self.items)


_boxes: dict[Scope, PendingBox] = {}


def summary(items: list[Attachment]) -> str:
    """"2 ta rasm, 1 ta fayl" ko'rinishida."""
    images = sum(1 for a in items if a.is_image)
    files = len(items) - images
    parts = []
    if images:
        parts.append(f"{images} ta rasm")
    if files:
        parts.append(f"{files} ta fayl")
    return ", ".join(parts) or "0 ta fayl"


def add(scope: Scope, att: Attachment, caption: str = "") -> PendingBox:
    """Biriktirmani ro'yxatga qo'shadi. Chegara oshsa TooLarge, ro'yxat o'zgarmaydi."""
    box = _boxes.get(scope)
    if box is None or box.expired():
        box = PendingBox()
        _boxes[scope] = box
    if box.total + att.size > MAX_TOTAL_BYTES:
        raise TooLarge(
            f"Jami hajm {MAX_TOTAL_BYTES // (1024 * 1024)} MB dan oshadi — "
            f"{att.name} qabul qilinmadi."
        )
    box.items.append(att)
    if caption.strip():
        box.caption = caption.strip()
    box.updated_at = time.time()
    return box


def peek(scope: Scope) -> PendingBox | None:
    """Ro'yxatni ko'rsatadi (olmaydi). Eskirgan bo'lsa o'chirib None."""
    box = _boxes.get(scope)
    if box is not None and box.expired():
        _boxes.pop(scope, None)
        return None
    return box


def take(scope: Scope) -> PendingBox | None:
    """Ro'yxatni oladi va bo'shatadi. Yo'q yoki eskirgan bo'lsa None."""
    box = peek(scope)
    if box is not None:
        _boxes.pop(scope, None)
    return box


def drop(scope: Scope, box: PendingBox) -> bool:
    """Aynan shu ro'yxatni o'chiradi — boshqasi turgan bo'lsa tegmaydi.

    Taymerlar uchun: eskirish vaqti kelganda scope'da allaqachon yangi ro'yxat
    bo'lishi mumkin, uni tasodifan o'chirib yubormaslik kerak.
    """
    if _boxes.get(scope) is box:
        del _boxes[scope]
        return True
    return False


def clear(scope: Scope) -> PendingBox | None:
    return _boxes.pop(scope, None)
