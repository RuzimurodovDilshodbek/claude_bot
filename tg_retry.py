"""Barqaror bo'lmagan tarmoq uchun `HTTPXRequest` ustidan retry qatlami.

O'zbekistondan Telegram API ga to'g'ridan-to'g'ri ulanish qariyb yarmi
handshake timeout yoki `WinError 10054` (host reset) beradi. Bot polling
paytida bu xatolarni telegram kutubxonasi o'zi hazm qiladi, lekin
`sendMessage` / `editMessageText` da bir xato = xabar yo'qoladi.

Bu klass har bir HTTP so'rovni tarmoq xatosi uchun bir necha marta,
kechikish oshirib qayta yuboradi.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from telegram.request import HTTPXRequest

log = logging.getLogger("claude-tg.tg_retry")

# Polling tirikligining yagona ishonchli o'lchovi: oxirgi marta getUpdates
# so'rovi qachon muvaffaqiyatli tugagani. `Updater.running` yaramaydi — ichki
# vazifa o'lib qolsa ham u True bo'lib qolaveradi.
_last_poll_ok = time.monotonic()


def seconds_since_last_poll() -> float:
    return time.monotonic() - _last_poll_ok


def mark_poll_ok() -> None:
    global _last_poll_ok
    _last_poll_ok = time.monotonic()

# Aynan ushbu xatolarda qayta urinishga arziydi — tarmoq muammosi, server emas.
_RETRY_EXC: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
)


class RetryingHTTPXRequest(HTTPXRequest):
    """Ulanish xatosini 5 marta, eksponensial kechikish bilan qayta yuboradi."""

    def __init__(self, *args: Any, retries: int = 5, base_delay: float = 0.5,
                 max_delay: float = 8.0, track_polling: bool = False,
                 **kwargs: Any) -> None:
        # getUpdates uchun mo'ljallangan nusxa yurak urishini yozib boradi.
        self._track_polling = track_polling
        # Transport darajasidagi qayta urinish TCP/TLS bosqichidagi uzilishni
        # kechikishsiz darhol qaytadan sinaydi. O'lchovda o'rtacha javob
        # vaqtini 2.85s dan 0.69s ga tushirdi; pastdagi asyncio retry esa
        # faqat shu ham yordam bermaganda ishga tushadi.
        httpx_kwargs = dict(kwargs.pop("httpx_kwargs", None) or {})
        httpx_kwargs.setdefault("transport", httpx.AsyncHTTPTransport(retries=3))
        kwargs["httpx_kwargs"] = httpx_kwargs
        super().__init__(*args, **kwargs)
        self._retries = retries
        self._base_delay = base_delay
        self._max_delay = max_delay

    async def do_request(self, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        last: BaseException | None = None
        delay = self._base_delay
        for attempt in range(1, self._retries + 1):
            try:
                result = await super().do_request(*args, **kwargs)
                # 409 Conflict ham HTTP darajasida "muvaffaqiyat" — lekin bu
                # boshqa nusxa oqimni o'g'irlayotganini bildiradi, ya'ni bu
                # nusxa aslida hech narsa qabul qilmayapti. Yurak urishi deb
                # hisoblamaymiz, aks holda qorovul yolg'on tinchlik ko'radi.
                if self._track_polling and result[0] != 409:
                    mark_poll_ok()
                return result
            except _RETRY_EXC as exc:
                last = exc
                if attempt == self._retries:
                    break
                log.warning(
                    "Telegram ulanish xatosi (urinish %d/%d): %s — %.1fs dan keyin qayta",
                    attempt, self._retries, type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_delay)
        assert last is not None
        raise last
