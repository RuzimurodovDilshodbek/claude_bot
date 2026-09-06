"""Claude javobini Telegram HTML formatiga o'girish va bo'laklarga bo'lish."""
from __future__ import annotations

import html
import re

TG_LIMIT = 3900

_FENCE = re.compile(r"```([^\n`]*)\n?(.*?)(?:```|\Z)", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s*(.+)$", re.M)
_HR = re.compile(r"^\s*[-*_]{3,}\s*$", re.M)


def _inline(text: str) -> str:
    """Qator ichidagi markdown -> HTML. Kirish allaqachon escape qilingan."""
    out = _LINK.sub(r'<a href="\2">\1</a>', text)
    out = _INLINE_CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<b>\1</b>", out)
    out = _ITALIC.sub(r"<i>\1</i>", out)
    out = _HEADING.sub(lambda m: f"<b>{m.group(2)}</b>", out)
    out = _HR.sub("──────────", out)
    return out


def to_html(text: str) -> str:
    """Markdown matnni Telegram qo'llaydigan HTML ga aylantiradi."""
    pieces: list[str] = []
    cursor = 0
    for match in _FENCE.finditer(text or ""):
        before = text[cursor:match.start()]
        pieces.append(_inline(html.escape(before)))
        lang = (match.group(1) or "").strip()
        code = html.escape(match.group(2) or "")
        cls = f' class="language-{html.escape(lang)}"' if lang else ""
        pieces.append(f"<pre><code{cls}>{code}</code></pre>")
        cursor = match.end()
    pieces.append(_inline(html.escape(text[cursor:])))
    return "".join(pieces).strip()


def split_plain(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Matnni Telegram chegarasiga sig'adigan bo'laklarga bo'ladi.

    Avval xatboshi, keyin qator, oxirida majburiy kesish bo'yicha.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    buffer = ""
    for para in text.split("\n\n"):
        candidate = f"{buffer}\n\n{para}" if buffer else para
        if len(candidate) <= limit:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
            buffer = ""
        while len(para) > limit:
            cut = para.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = limit
            chunks.append(para[:cut])
            para = para[cut:].lstrip("\n")
        buffer = para
    if buffer:
        chunks.append(buffer)
    return [c for c in chunks if c.strip()]


def html_chunks(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Bo'laklarga bo'lib, har birini alohida HTML ga o'giradi.

    Har bir bo'lak mustaqil konvertatsiya qilinadi, shuning uchun teglar
    hech qachon bo'laklar orasida ochiq qolmaydi.
    """
    return [to_html(chunk) for chunk in split_plain(text, limit)]


def trim(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def human_duration(ms: int) -> str:
    seconds = max(0, int(ms // 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def human_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "hozirgina"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} daqiqa oldin"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} soat oldin"
    days = hours // 24
    if days < 30:
        return f"{days} kun oldin"
    return f"{days // 30} oy oldin"
