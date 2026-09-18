"""Agent tomonida biriktirmalarni saqlash: inbox/<task_id>/NN_<nom>.

Bot Telegramdan olgan fayllarni hub orqali base64 qilib yuboradi; bu modul
ularni diskka yozadi va Claude uchun prompt boshiga qo'yiladigan ro'yxatni
tuzadi. Claude fayllarni o'zi `Read` bilan ko'radi — papka `--add-dir` bilan
ruxsat ro'yxatiga qo'shiladi (acceptEdits rejimida ham so'ramaydi).
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
import protocol

INBOX_DIR = config.BASE_DIR / "inbox"
# Vazifa papkalari shuncha vaqtdan keyin o'chiriladi (agent ishga tushganda).
MAX_AGE_SEC = 7 * 24 * 3600

DEFAULT_PROMPT = "Biriktirilgan fayllarni ko'rib chiq va nima kerakligini ayt."


@dataclass
class SavedFile:
    path: Path
    mime: str
    size: int


@dataclass
class Saved:
    folder: Path
    files: list[SavedFile] = field(default_factory=list)


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n // 1024} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def save(task_id: str, items: list[dict], root: Path | None = None) -> Saved:
    """Fayllarni inbox/<task_id>/NN_<nom> qilib yozadi — albom tartibi saqlanadi."""
    folder = (root or INBOX_DIR) / protocol.safe_name(task_id, "task")
    folder.mkdir(parents=True, exist_ok=True)
    saved = Saved(folder=folder)
    for n, item in enumerate(items, 1):
        name, mime, data = protocol.unpack_file(item)
        path = folder / f"{n:02d}_{protocol.safe_name(name, f'file_{n}')}"
        path.write_bytes(data)
        saved.files.append(SavedFile(path=path, mime=mime, size=len(data)))
    return saved


def prompt_block(saved: Saved) -> str:
    lines = [
        f"[Foydalanuvchi Telegram orqali {len(saved.files)} ta fayl biriktirdi. "
        "Avval har birini Read bilan ko'rib chiq:"
    ]
    for n, f in enumerate(saved.files, 1):
        lines.append(f"{n}. {f.path}  ({f.mime}, {human_size(f.size)})")
    lines[-1] += "]"
    return "\n".join(lines)


def with_attachments(prompt: str, saved: Saved) -> str:
    """Foydalanuvchi matni oldiga fayllar ro'yxatini qo'yadi."""
    text = (prompt or "").strip() or DEFAULT_PROMPT
    return prompt_block(saved) + "\n\n" + text


def cleanup(max_age_sec: float = MAX_AGE_SEC, root: Path | None = None,
            now: float | None = None) -> int:
    """Eski vazifa papkalarini o'chiradi; nechtasi o'chgani qaytadi."""
    base = root or INBOX_DIR
    if not base.exists():
        return 0
    now = now if now is not None else time.time()
    removed = 0
    for folder in base.iterdir():
        try:
            if folder.is_dir() and now - folder.stat().st_mtime > max_age_sec:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed
