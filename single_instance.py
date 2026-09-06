"""Bir vaqtda faqat bitta bot nusxasi ishlashini kafolatlaydi.

Nega kerak: ikkita nusxa bir vaqtda `getUpdates` qilsa, Telegram ulardan
birini `Conflict: terminated by other getUpdates request` bilan uzadi va
xabarlar ikkisi orasida yo'qoladi. Tashqaridan bu "bot javob bermayapti"
bo'lib ko'rinadi, logda esa hech qanday tushunarli xato bo'lmaydi.

Bu holat oson yuzaga keladi: Startup papkadagi yorliq va Scheduled Task
ikkalasi ham o'rnatilgan bo'lsa, yoki qo'lda `start.bat` ochilsa.

Yechim — localhost portini band qilish. Port jarayon o'lishi bilan OS
tomonidan bo'shatiladi, shuning uchun "qotib qolgan qulf" muammosi yo'q
(fayl qulfidan farqli).
"""
from __future__ import annotations

import logging
import socket

log = logging.getLogger("claude-tg.lock")

# Boshqa dastur band qilishi ehtimoli past bo'lgan port.
LOCK_PORT = 47653
LOCK_HOST = "127.0.0.1"

_socket: socket.socket | None = None


class AlreadyRunning(RuntimeError):
    """Boshqa nusxa allaqachon ishlayapti."""


def acquire(port: int = LOCK_PORT) -> socket.socket:
    """Qulfni oladi. Band bo'lsa `AlreadyRunning` ko'taradi."""
    global _socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR ni ATAYLAB qo'ymaymiz — u aynan biz aniqlamoqchi bo'lgan
    # "port band" holatini yashirib yuboradi.
    try:
        sock.bind((LOCK_HOST, port))
        sock.listen(1)
    except OSError as exc:
        sock.close()
        raise AlreadyRunning(
            f"Bot allaqachon ishlayapti (port {port} band). "
            "Ikkinchi nusxa ishga tushmaydi."
        ) from exc

    _socket = sock
    log.info("Yagona nusxa qulfi olindi (port %d).", port)
    return sock


def release() -> None:
    global _socket
    if _socket is not None:
        try:
            _socket.close()
        except OSError:
            pass
        _socket = None


def is_running(port: int = LOCK_PORT) -> bool:
    """Boshqa nusxa ishlayaptimi — tekshiruv skriptlari uchun."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((LOCK_HOST, port))
        return False
    except OSError:
        return True
    finally:
        probe.close()
