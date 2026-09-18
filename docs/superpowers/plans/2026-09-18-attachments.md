# Biriktirmalar (rasm, fayl, albom) — bajarish rejasi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Telegramdan kelgan rasm/fayl/albom Claude'ga yetib borsin — caption bilan darhol, caption'siz keyingi matn/ovoz bilan birga.

**Architecture:** Bot faylni Telegramdan yuklab `attachments.py` kutish ro'yxatiga qo'yadi; vazifa boshlanganda fayllar `OP_RUN` so'rovi ichida base64 bilan agentga ketadi; agent `inbox.py` orqali `inbox/<task_id>/` ga yozadi, prompt boshiga ro'yxat qo'shadi va `claude` ga `--add-dir` beradi — Claude fayllarni `Read` bilan o'zi ko'radi.

**Tech Stack:** Python 3.12, python-telegram-bot 21, websockets, pytest (faqat dev). Spec: `docs/superpowers/specs/2026-09-18-attachments-and-progress-design.md` (1-qism).

**Ishga tushirish:** hamma komanda loyiha ildizidan (`D:\work\OSPanelV6\home\personal\claude_bot`), `.venv\Scripts\python.exe` bilan. Testlar: `.venv\Scripts\python.exe -m pytest tests -q`.

**Commit qoidasi:** har commit xabari oxirida bo'sh qatordan keyin `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` bo'lsin. Xabarni faylga yozib `git commit -F` bilan bering (apostrofli o'zbekcha matn `-m "..."` ichida bash'ni sindiradi).

---

## Fayllar

| Fayl | Vazifasi |
|---|---|
| `requirements-dev.txt`, `pytest.ini`, `tests/conftest.py` | **Yangi.** Test infratuzilmasi (loyihada test yo'q edi) |
| `protocol.py` | `safe_name`, `pack_file`, `unpack_file` — bot va agent ikkalasi ishlatadi |
| `attachments.py` | **Yangi.** Bot tomonida kutish ro'yxati (`PendingBox`), chegara, eskirish |
| `inbox.py` | **Yangi.** Agent tomonida fayllarni saqlash, prompt bloki, eski papkalarni tozalash |
| `runner.py` | `ClaudeRun(add_dirs=...)` → `--add-dir` |
| `machines.py` | `run(..., attachments=...)` — base64 qadoqlash |
| `agent.py` | `do_run` da fayllarni yozish + prompt; ishga tushganda `inbox.cleanup()` |
| `bot.py` | `on_attachment`, `_download_attachment`, `_box_timer`, `_start_or_queue`; `FollowUp`; `_launch`/`_execute` fayl uzatadi; `/new` tozalaydi; HELP |
| `.gitignore`, `README.md` | `inbox/`; foydalanish bo'limi |
| `tests/test_protocol.py`, `tests/test_attachments.py`, `tests/test_inbox.py`, `tests/test_runner_argv.py`, `tests/test_agent_run.py`, `tests/test_machines_run.py`, `tests/test_bot_attachments.py` | **Yangi.** |

---

### Task 1: Test infratuzilmasi

**Files:**
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Dev talablari va pytest sozlamasi**

`requirements-dev.txt`:
```
pytest>=8.0
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
```

`tests/conftest.py`:
```python
"""Loyiha ildizini import yo'liga qo'shadi — testlar `import bot` qila olsin."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

- [ ] **Step 2: O'rnatish**

Run: `.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt`
Expected: xatosiz tugaydi.

- [ ] **Step 3: Tutun testi**

`tests/test_smoke.py`:
```python
def test_imports():
    import protocol, tgfmt  # noqa: F401
```

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: `1 passed`

- [ ] **Step 4: Commit**

```bash
git add requirements-dev.txt pytest.ini tests/conftest.py tests/test_smoke.py
git commit -F <xabar fayli>   # "Test infratuzilmasi: pytest"
```

---

### Task 2: `protocol.py` — fayl nomi va base64 qadoqlash

**Files:**
- Modify: `protocol.py` (import bo'limi; `error()` dan keyin yangi bo'lim)
- Test: `tests/test_protocol.py`

- [ ] **Step 1: Testlar**

`tests/test_protocol.py`:
```python
import pytest

import protocol


@pytest.mark.parametrize("raw,expected", [
    ("error.log", "error.log"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\me\\Desktop\\shot.png", "shot.png"),
    ('we?ird:na*me<>.txt', "we_ird_na_me_.txt"),
    ("  .hidden ", "hidden"),
    ("", "file_7"),
    ("a" * 100 + ".png", "a" * 76 + ".png"),
])
def test_safe_name(raw, expected):
    assert protocol.safe_name(raw, "file_7") == expected


def test_pack_unpack_roundtrip():
    packed = protocol.pack_file("photo_1.jpg", "image/jpeg", b"\xff\xd8\x00abc")
    assert set(packed) == {"name", "mime", "data_b64"}
    assert protocol.unpack_file(packed) == ("photo_1.jpg", "image/jpeg", b"\xff\xd8\x00abc")


def test_pack_defaults_mime_and_unpack_tolerates_missing_fields():
    assert protocol.pack_file("a", "", b"")["mime"] == "application/octet-stream"
    assert protocol.unpack_file({}) == ("", "application/octet-stream", b"")
```

- [ ] **Step 2: Yiqilishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_protocol.py -q`
Expected: FAIL — `AttributeError: module 'protocol' has no attribute 'safe_name'`

- [ ] **Step 3: Kod**

`protocol.py` — importlarga `base64` va `re` qo'shing:
```python
import base64
import json
import re
import time
import uuid
```

Modul docstring'ining oxiriga (`Shu tufayli bitta agentda...` qatoridan keyin) qo'shing:
```
`run` so'rovi argumentlari: prompt, cwd, session_id, model, permission_mode va
ixtiyoriy `attachments` — `pack_file` bilan qadoqlangan fayllar ro'yxati.
```

`error()` funksiyasidan keyin, `# --- ma'lumot tuzilmalari` dan oldin:
```python
# --- fayllar ---------------------------------------------------------------
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
NAME_LIMIT = 80


def safe_name(raw: str, fallback: str) -> str:
    """Fayl nomini diskka yozishga yaroqli qiladi.

    Faqat basename olinadi (`../` bilan papkadan chiqib bo'lmaydi), Windows'da
    taqiqlangan belgilar `_` ga almashadi, uzunlik chegaralanadi. Bo'sh qolsa
    `fallback`.
    """
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _BAD_NAME_CHARS.sub("_", name).strip(" ._")
    if len(name) > NAME_LIMIT:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            name = stem[: NAME_LIMIT - len(ext) - 1].rstrip(" ._") + "." + ext
        else:
            name = name[:NAME_LIMIT]
    return name or fallback


def pack_file(name: str, mime: str, data: bytes) -> dict:
    """Biriktirmani JSON ichida yuborish uchun base64 ga o'raydi."""
    return {
        "name": name,
        "mime": mime or "application/octet-stream",
        "data_b64": base64.b64encode(data).decode("ascii"),
    }


def unpack_file(item: dict) -> tuple[str, str, bytes]:
    """`pack_file` ning teskarisi. Yetishmagan maydonlar bo'sh qiymat oladi."""
    return (
        str(item.get("name") or ""),
        str(item.get("mime") or "application/octet-stream"),
        base64.b64decode(item.get("data_b64") or ""),
    )
```

- [ ] **Step 4: O'tishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_protocol.py -q`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add protocol.py tests/test_protocol.py
git commit -F <xabar fayli>   # "protocol: fayl nomi tozalash va base64 qadoqlash"
```

---

### Task 3: `attachments.py` — kutish ro'yxati

**Files:**
- Create: `attachments.py`
- Test: `tests/test_attachments.py`

- [ ] **Step 1: Testlar**

`tests/test_attachments.py`:
```python
import time

import pytest

import attachments as att_mod
from attachments import Attachment, TooLarge

SCOPE = (1, 0)


@pytest.fixture(autouse=True)
def clean():
    att_mod._boxes.clear()
    yield
    att_mod._boxes.clear()


def _img(n: int = 100, name: str = "photo_1.jpg") -> Attachment:
    return Attachment(name=name, mime="image/jpeg", data=b"x" * n)


def test_add_then_take_returns_items_in_order_with_caption():
    att_mod.add(SCOPE, _img(), caption="xatoni tuzat")
    att_mod.add(SCOPE, _img(name="photo_2.jpg"))
    box = att_mod.take(SCOPE)
    assert [a.name for a in box.items] == ["photo_1.jpg", "photo_2.jpg"]
    assert box.caption == "xatoni tuzat"
    assert att_mod.take(SCOPE) is None


def test_caption_from_later_album_item_is_kept():
    att_mod.add(SCOPE, _img())
    att_mod.add(SCOPE, _img(name="photo_2.jpg"), caption="  mana bu  ")
    assert att_mod.peek(SCOPE).caption == "mana bu"


def test_take_without_anything_is_none():
    assert att_mod.take(SCOPE) is None
    assert att_mod.peek(SCOPE) is None


def test_expired_box_is_dropped():
    box = att_mod.add(SCOPE, _img())
    box.updated_at = time.time() - att_mod.TTL_SEC - 1
    assert att_mod.take(SCOPE) is None


def test_new_attachment_after_expiry_starts_fresh_box():
    old = att_mod.add(SCOPE, _img(), caption="eski")
    old.updated_at = time.time() - att_mod.TTL_SEC - 1
    new = att_mod.add(SCOPE, _img(name="photo_9.jpg"))
    assert new is not old
    assert new.caption == ""
    assert [a.name for a in new.items] == ["photo_9.jpg"]


def test_total_size_limit_rejects_without_touching_box(monkeypatch):
    monkeypatch.setattr(att_mod, "MAX_TOTAL_BYTES", 250)
    att_mod.add(SCOPE, _img(200))
    with pytest.raises(TooLarge) as exc:
        att_mod.add(SCOPE, _img(100, name="big.jpg"))
    assert "big.jpg" in str(exc.value)
    assert len(att_mod.peek(SCOPE).items) == 1


def test_drop_only_removes_the_given_box():
    first = att_mod.add(SCOPE, _img())
    att_mod._boxes[SCOPE] = second = att_mod.PendingBox()
    assert att_mod.drop(SCOPE, first) is False
    assert att_mod.peek(SCOPE) is second
    assert att_mod.drop(SCOPE, second) is True
    assert att_mod.peek(SCOPE) is None


def test_clear_returns_box():
    box = att_mod.add(SCOPE, _img())
    assert att_mod.clear(SCOPE) is box
    assert att_mod.clear(SCOPE) is None


def test_summary_counts_images_and_files():
    items = [_img(), _img(), Attachment("a.log", "text/plain", b"x")]
    assert att_mod.summary(items) == "2 ta rasm, 1 ta fayl"
    assert att_mod.summary([_img()]) == "1 ta rasm"
    assert att_mod.summary([]) == "0 ta fayl"


def test_attachment_properties():
    a = Attachment("a.png", "image/png", b"12345")
    assert a.size == 5 and a.is_image
    assert not Attachment("a.log", "text/plain", b"").is_image
```

- [ ] **Step 2: Yiqilishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_attachments.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'attachments'`

- [ ] **Step 3: Kod**

`attachments.py`:
```python
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
```

- [ ] **Step 4: O'tishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_attachments.py -q`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add attachments.py tests/test_attachments.py
git commit -F <xabar fayli>   # "attachments: biriktirmalar uchun kutish ro'yxati"
```

---

### Task 4: `runner.py` — `--add-dir`

**Files:**
- Modify: `runner.py` (`ClaudeRun.__init__`, `_argv`)
- Test: `tests/test_runner_argv.py`

- [ ] **Step 1: Testlar**

`tests/test_runner_argv.py`:
```python
import runner


def test_add_dir_flags_are_passed_in_order():
    run = runner.ClaudeRun(prompt="x", cwd=".", add_dirs=[r"D:\inbox\ab12", "/tmp/x"])
    argv = run._argv()
    first = argv.index("--add-dir")
    assert argv[first + 1] == r"D:\inbox\ab12"
    assert argv[first + 2:first + 4] == ["--add-dir", "/tmp/x"]


def test_no_add_dir_by_default():
    assert "--add-dir" not in runner.ClaudeRun(prompt="x", cwd=".")._argv()


def test_session_flags_unchanged():
    argv = runner.ClaudeRun(prompt="x", cwd=".", session_id="abc")._argv()
    assert argv[argv.index("--resume") + 1] == "abc"
    argv = runner.ClaudeRun(prompt="x", cwd=".", persist=False)._argv()
    assert "--no-session-persistence" in argv and "--resume" not in argv
```

- [ ] **Step 2: Yiqilishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runner_argv.py -q`
Expected: FAIL — `TypeError: ClaudeRun.__init__() got an unexpected keyword argument 'add_dirs'`

- [ ] **Step 3: Kod**

`runner.py` `ClaudeRun.__init__` imzosi va tanasi:
```python
    def __init__(
        self,
        prompt: str,
        cwd: str,
        session_id: str | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
        persist: bool = True,
        add_dirs: list[str] | None = None,
    ) -> None:
        self.prompt = prompt
        self.cwd = cwd
        self.resume_id = session_id
        self.session_id = session_id or str(uuid.uuid4())
        self.model = model or config.DEFAULT_MODEL
        self.permission_mode = permission_mode or config.PERMISSION_MODE
        # Sinov yurishlari sessiya ro'yxatini ifloslantirmasligi uchun.
        self.persist = persist
        # cwd dan tashqaridagi ruxsatli papkalar (masalan biriktirmalar inbox'i) —
        # acceptEdits rejimida ham Claude ularni so'ramasdan o'qiy oladi.
        self.add_dirs = list(add_dirs or [])
        self.started_at = time.time()
        self._proc: asyncio.subprocess.Process | None = None
        self._cancelled = False
```

`_argv` ichida, `--session-id` bloki tugagandan keyin, `exe = config.CLAUDE_BIN` dan oldin:
```python
        for folder in self.add_dirs:
            args += ["--add-dir", folder]
```

- [ ] **Step 4: O'tishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runner_argv.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add runner.py tests/test_runner_argv.py
git commit -F <xabar fayli>   # "runner: --add-dir orqali qo'shimcha papkalar"
```

---

### Task 5: `inbox.py` — agent tomonida saqlash

**Files:**
- Create: `inbox.py`
- Modify: `.gitignore`
- Test: `tests/test_inbox.py`

- [ ] **Step 1: Testlar**

`tests/test_inbox.py`:
```python
import os
import time

import inbox
import protocol


def test_save_writes_files_in_order_with_safe_names(tmp_path):
    items = [
        protocol.pack_file("photo_1.jpg", "image/jpeg", b"\xff\xd8abc"),
        protocol.pack_file("../error.log", "text/plain", b"line\n"),
        protocol.pack_file("", "", b"?"),
    ]
    saved = inbox.save("ab12cd", items, root=tmp_path)
    assert saved.folder == tmp_path / "ab12cd"
    assert [f.path.name for f in saved.files] == ["01_photo_1.jpg", "02_error.log", "03_file_3"]
    assert saved.files[0].path.read_bytes() == b"\xff\xd8abc"
    assert saved.files[1].size == 5 and saved.files[1].mime == "text/plain"
    assert saved.files[2].mime == "application/octet-stream"


def test_save_sanitizes_task_id(tmp_path):
    saved = inbox.save("../evil", [protocol.pack_file("a", "text/plain", b"1")], root=tmp_path)
    assert saved.folder == tmp_path / "evil"


def test_with_attachments_lists_paths_and_sizes(tmp_path):
    saved = inbox.save("t1", [protocol.pack_file("a.png", "image/png", b"x" * 2048)], root=tmp_path)
    text = inbox.with_attachments("xatoni tuzat", saved)
    assert text.startswith("[Foydalanuvchi Telegram orqali 1 ta fayl biriktirdi.")
    assert f"1. {saved.files[0].path}  (image/png, 2 KB)]" in text
    assert text.endswith("\n\nxatoni tuzat")


def test_empty_prompt_gets_default_text(tmp_path):
    saved = inbox.save("t2", [protocol.pack_file("a.png", "image/png", b"x")], root=tmp_path)
    assert inbox.with_attachments("   ", saved).endswith(
        "Biriktirilgan fayllarni ko'rib chiq va nima kerakligini ayt."
    )


def test_human_size():
    assert inbox.human_size(512) == "512 B"
    assert inbox.human_size(2048) == "2 KB"
    assert inbox.human_size(3 * 1024 * 1024 + 200 * 1024) == "3.2 MB"


def test_cleanup_removes_only_old_folders(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "f").write_bytes(b"1")
    new = tmp_path / "new"
    new.mkdir()
    past = time.time() - inbox.MAX_AGE_SEC - 60
    os.utime(old, (past, past))
    assert inbox.cleanup(root=tmp_path) == 1
    assert not old.exists() and new.exists()


def test_cleanup_without_inbox_dir(tmp_path):
    assert inbox.cleanup(root=tmp_path / "yoq") == 0
```

- [ ] **Step 2: Yiqilishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_inbox.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'inbox'`

- [ ] **Step 3: Kod**

`inbox.py`:
```python
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
```

`.gitignore` — `# === Har bir mashinaga xos ===` bo'limi oxiriga (`.restarted` dan keyin):
```
# Telegramdan kelgan biriktirmalar (rasm, fayl) — vazifa bo'yicha papkalar
inbox/
```

- [ ] **Step 4: O'tishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_inbox.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add inbox.py .gitignore tests/test_inbox.py
git commit -F <xabar fayli>   # "inbox: agentda biriktirmalarni saqlash va prompt bloki"
```

---

### Task 6: `agent.py` — `do_run` fayllarni yozadi, ishga tushganda tozalaydi

**Files:**
- Modify: `agent.py` (importlar, `do_run` boshi, `main`)
- Test: `tests/test_agent_run.py`

- [ ] **Step 1: Testlar**

`tests/test_agent_run.py`:
```python
import asyncio

import pytest

import agent as agent_mod
import inbox
import protocol
import runner


class FakeResult:
    ok = True
    session_id = "s1"
    text = "TAYYOR"
    error = ""
    duration_ms = 1
    cost_usd = 0.0
    num_turns = 1
    model = "haiku"
    cancelled = False
    tools_used = []


class FakeRun:
    created = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeRun.created.append(self)

    async def run(self, on_progress=None):
        return FakeResult()

    def cancel(self):
        pass


@pytest.fixture
def ag(monkeypatch, tmp_path):
    FakeRun.created.clear()
    monkeypatch.setattr(runner, "ClaudeRun", FakeRun)
    monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
    a = agent_mod.Agent("ws://x", "tok", "test")
    a.sent = []

    async def send(message):
        a.sent.append(message)

    a.send = send
    return a


def test_run_with_attachments_saves_files_and_prefixes_prompt(ag, tmp_path):
    args = {
        "cwd": str(tmp_path), "prompt": "rasmga qara",
        "attachments": [protocol.pack_file("photo_1.jpg", "image/jpeg", b"jpg")],
    }
    asyncio.run(ag.do_run("rid1", args))
    run = FakeRun.created[0]
    folder = tmp_path / "inbox" / "rid1"
    assert (folder / "01_photo_1.jpg").read_bytes() == b"jpg"
    assert run.kwargs["add_dirs"] == [str(folder)]
    assert run.kwargs["prompt"].startswith("[Foydalanuvchi Telegram orqali 1 ta fayl biriktirdi.")
    assert run.kwargs["prompt"].endswith("\n\nrasmga qara")
    assert ag.sent[-1]["t"] == "done" and ag.sent[-1]["result"]["ok"] is True


def test_run_without_attachments_is_unchanged(ag, tmp_path):
    asyncio.run(ag.do_run("rid2", {"cwd": str(tmp_path), "prompt": "salom"}))
    run = FakeRun.created[0]
    assert run.kwargs["prompt"] == "salom" and run.kwargs["add_dirs"] == []
    assert not (tmp_path / "inbox").exists()


def test_bad_base64_reports_error_without_running(ag, tmp_path):
    args = {"cwd": str(tmp_path), "prompt": "x",
            "attachments": [{"name": "a", "mime": "text/plain", "data_b64": "A"}]}  # noto'g'ri padding
    asyncio.run(ag.do_run("rid3", args))
    assert FakeRun.created == []
    result = ag.sent[-1]["result"]
    assert result["ok"] is False and "saqlab bo'lmadi" in result["error"]
```

- [ ] **Step 2: Yiqilishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_agent_run.py -q`
Expected: birinchi test FAIL — `KeyError: 'add_dirs'` (fayl ham yozilmaydi); ikkinchisi FAIL — `KeyError: 'add_dirs'`; uchinchisi FAIL — `FakeRun.created` bo'sh emas.

- [ ] **Step 3: Kod**

`agent.py` importlariga (`import config` dan keyin):
```python
import inbox
```

`do_run` boshini shunday o'zgartiring (`run = runner.ClaudeRun(` gacha bo'lgan qism):
```python
    async def do_run(self, rid: str, args: dict) -> None:
        cwd = args.get("cwd") or ""
        if not cwd or not Path(cwd).exists():
            await self.send(protocol.done(rid, {
                "ok": False, "error": f"Papka topilmadi: {cwd}",
            }))
            return

        prompt = args.get("prompt") or ""
        add_dirs: list[str] = []
        items = args.get("attachments") or []
        if items:
            # Rasm/fayllar inbox/<task_id>/ ga yoziladi; Claude ularni Read
            # bilan ko'radi. Papka --add-dir bilan ruxsatga qo'shiladi.
            try:
                saved = inbox.save(rid, items)
            except (OSError, ValueError) as exc:
                await self.send(protocol.done(rid, {
                    "ok": False, "error": f"Biriktirmani saqlab bo'lmadi: {exc}",
                }))
                return
            prompt = inbox.with_attachments(prompt, saved)
            add_dirs.append(str(saved.folder))
            log.info("Biriktirmalar: %d ta fayl -> %s", len(saved.files), saved.folder)

        run = runner.ClaudeRun(
            prompt=prompt,
            cwd=cwd,
            session_id=args.get("session_id") or None,
            model=args.get("model") or None,
            permission_mode=args.get("permission_mode") or None,
            add_dirs=add_dirs,
        )
        self._runs[rid] = run
```

`main()` da `agent = Agent(hub_url, token, name)` dan oldin:
```python
    removed = inbox.cleanup()
    if removed:
        log.info("Inbox tozalandi: %d eski papka", removed)
```

- [ ] **Step 4: O'tishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_agent_run.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_agent_run.py
git commit -F <xabar fayli>   # "agent: biriktirmalarni inbox'ga yozib Claude'ga ko'rsatish"
```

---

### Task 7: `machines.py` — `run(..., attachments=...)`

**Files:**
- Modify: `machines.py` (`run`)
- Test: `tests/test_machines_run.py`

- [ ] **Step 1: Testlar**

`tests/test_machines_run.py`:
```python
import asyncio

import machines
from attachments import Attachment


class FakeHub:
    def __init__(self):
        self.calls = []

    async def run(self, agent_id, args, on_progress=None, task_id=None):
        self.calls.append((agent_id, args, task_id))
        return {"ok": True}


def test_run_packs_attachments_as_base64():
    hub = FakeHub()
    machines.bind(hub)
    files = [Attachment("photo_1.jpg", "image/jpeg", b"\x00\x01")]
    asyncio.run(machines.run("a1", "p", "C:/x", None, "opus", task_id="t1", attachments=files))
    agent_id, args, task_id = hub.calls[0]
    assert (agent_id, task_id) == ("a1", "t1")
    assert args["attachments"] == [{"name": "photo_1.jpg", "mime": "image/jpeg", "data_b64": "AAE="}]


def test_run_without_attachments_sends_empty_list():
    hub = FakeHub()
    machines.bind(hub)
    asyncio.run(machines.run("a1", "p", "C:/x", "s", "opus"))
    assert hub.calls[0][1]["attachments"] == []
```

- [ ] **Step 2: Yiqilishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_machines_run.py -q`
Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'attachments'` va `KeyError: 'attachments'`

- [ ] **Step 3: Kod**

`machines.py` `run` funksiyasi:
```python
async def run(agent_id: str, prompt: str, cwd: str, session_id: str | None,
              model: str | None, task_id: str | None = None,
              on_progress: Callable[[str, str], Awaitable[None]] | None = None,
              attachments: list | None = None) -> dict:
    return await hub().run(agent_id, {
        "prompt": prompt,
        "cwd": cwd,
        "session_id": session_id,
        "model": model,
        # Rasm/fayllar (attachments.Attachment) — agent inbox/<task_id>/ ga
        # yozib Claude'ga ko'rsatadi.
        "attachments": [
            protocol.pack_file(a.name, a.mime, a.data) for a in (attachments or [])
        ],
    }, on_progress, task_id=task_id)
```

- [ ] **Step 4: O'tishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_machines_run.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add machines.py tests/test_machines_run.py
git commit -F <xabar fayli>   # "machines: vazifa bilan biriktirmalarni uzatish"
```

---

### Task 8: `bot.py` — vazifa oqimi fayllarni ko'taradi (`_start_or_queue`, `FollowUp`)

**Files:**
- Modify: `bot.py` — importlar; `RunningTask` (187-199); `_quick_action` (424-425); `start_task` (1267-1286); `_launch` imzosi va "Boshlandi" xabari (1289-1385); `_execute` (`machines.run` chaqiruvi ~1409, `finally` ~1430, follow-up halqasi ~1508-1513)
- Test: `tests/test_bot_attachments.py` (birinchi qismi)

- [ ] **Step 1: Testlar**

`tests/test_bot_attachments.py`:
```python
import asyncio

import pytest

import attachments
import bot

SCOPE = (10, 0)


class FakeMsg:
    def __init__(self):
        self.texts: list[str] = []
        self.edits: list[str] = []

    async def reply_text(self, text, **kw):
        self.texts.append(text)
        return FakeMsg()

    async def reply_html(self, text, **kw):
        self.texts.append(text)
        return FakeMsg()

    async def edit_text(self, text, **kw):
        self.edits.append(text)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    attachments._boxes.clear()
    bot._running.clear()
    monkeypatch.setattr(attachments, "DEBOUNCE_SEC", 0.01)
    yield
    attachments._boxes.clear()
    bot._running.clear()


def _att(name: str = "photo_1.jpg") -> attachments.Attachment:
    return attachments.Attachment(name, "image/jpeg", b"img")


def test_start_pulls_pending_files_and_launches(monkeypatch):
    calls = []

    async def fake_launch(ctx, scope, prompt, *, voice_input=False, reply_to=None, files=None):
        calls.append((scope, prompt, voice_input, [f.name for f in files]))

    monkeypatch.setattr(bot, "_launch", fake_launch)
    attachments.add(SCOPE, _att())
    asyncio.run(bot._start_or_queue(None, SCOPE, "shu rasmga qara",
                                    voice_input=True, reply_to=FakeMsg()))
    assert calls == [(SCOPE, "shu rasmga qara", True, ["photo_1.jpg"])]
    assert attachments.peek(SCOPE) is None


def test_start_edits_notice_when_files_are_taken(monkeypatch):
    async def fake_launch(ctx, scope, prompt, *, voice_input=False, reply_to=None, files=None):
        pass

    monkeypatch.setattr(bot, "_launch", fake_launch)
    box = attachments.add(SCOPE, _att())
    box.notice = FakeMsg()
    asyncio.run(bot._start_or_queue(None, SCOPE, "x", voice_input=False, reply_to=FakeMsg()))
    assert box.notice.edits == ["📎 1 ta rasm vazifaga qo'shildi."]


def test_running_task_queues_follow_up_with_files():
    async def run():
        bot._running[SCOPE] = bot.RunningTask(agent_id="a", task_id="t", cwd="c", prompt="p")
        reply = FakeMsg()
        attachments.add(SCOPE, _att())
        await bot._start_or_queue(None, SCOPE, "keyin buni", voice_input=False, reply_to=reply)
        follow = bot._running[SCOPE].follow_ups
        assert [(f.prompt, [a.name for a in f.files]) for f in follow] == \
            [("keyin buni", ["photo_1.jpg"])]
        assert "Navbatga qo'shildi" in reply.texts[0] and "1 ta rasm" in reply.texts[0]

    asyncio.run(run())


def test_follow_up_without_files_has_empty_list():
    async def run():
        bot._running[SCOPE] = bot.RunningTask(agent_id="a", task_id="t", cwd="c", prompt="p")
        await bot._start_or_queue(None, SCOPE, "yana", voice_input=False, reply_to=FakeMsg())
        assert bot._running[SCOPE].follow_ups[0].files == []

    asyncio.run(run())
```

- [ ] **Step 2: Yiqilishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_bot_attachments.py -q`
Expected: FAIL — `AttributeError: module 'bot' has no attribute '_start_or_queue'`

- [ ] **Step 3: Kod**

`bot.py` importlariga (`import ai` dan oldin, alifbo tartibida):
```python
import attachments
```

`RunningTask` ni shunday almashtiring:
```python
@dataclass
class FollowUp:
    """Vazifa ishlab turganda kelgan qo'shimcha — navbatda kutadi."""

    prompt: str
    files: list[attachments.Attachment] = field(default_factory=list)


@dataclass
class RunningTask:
    agent_id: str          # qaysi kompyuterda ketyapti
    task_id: str           # hub orqali bekor qilish uchun
    cwd: str
    prompt: str
    voice_input: bool = False  # ovoz orqali kelgan bo'lsa — javob ham ovozda
    # Rasm/fayllar — hub'ga uzatilgach bo'shatiladi (xotira).
    files: list[attachments.Attachment] = field(default_factory=list)
    task: asyncio.Task | None = None  # start_task ichida to'ldiriladi
    started_at: float = field(default_factory=time.time)
    follow_ups: list[FollowUp] = field(default_factory=list)  # ishlab turganda kelgan qo'shimchalar

    async def cancel(self) -> None:
        await machines.cancel(self.agent_id, self.task_id)
```

`_quick_action` ichida:
```python
    if scope in _running:
        _running[scope].follow_ups.append(FollowUp(prompt))
        return await query.answer("Navbatga qo'shildi")
```

`start_task` ni shunday almashtiring:
```python
async def start_task(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    *,
    voice_input: bool = False,
) -> None:
    await _start_or_queue(ctx, scope_of(update), prompt, voice_input=voice_input,
                          reply_to=update.effective_message)


async def _start_or_queue(
    ctx: ContextTypes.DEFAULT_TYPE,
    scope: Scope,
    prompt: str,
    *,
    voice_input: bool,
    reply_to,
    box: attachments.PendingBox | None = None,
) -> None:
    """Vazifani boshlaydi yoki (ishlab turgan bo'lsa) navbatga qo'yadi.

    Kutayotgan biriktirmalar (rasm, fayl) shu yerda vazifaga qo'shiladi —
    matn, ovoz va caption yo'llari hammasi shu orqali o'tadi. `box` caption
    yo'lidan keladi (allaqachon olingan); qolganlar uchun o'zimiz olamiz.
    """
    if box is None:
        box = attachments.take(scope)
    files = list(box.items) if box is not None else []
    if box is not None:
        if box.timer is not None:
            box.timer.cancel()
            box.timer = None
        if box.notice is not None:
            try:
                await box.notice.edit_text(f"📎 {box.summary()} vazifaga qo'shildi.")
            except Exception:
                pass
    clip = f"\n📎 {attachments.summary(files)}" if files else ""

    active = _running.get(scope)
    if active is not None:
        # Ishlab turgan vazifaga qo'shimcha ko'rsatma sifatida navbatga qo'yamiz.
        active.follow_ups.append(FollowUp(prompt, files))
        elapsed = tgfmt.human_duration(int((time.time() - active.started_at) * 1000))
        return await reply_to.reply_html(
            f"📝 <b>Navbatga qo'shildi</b> (avvalgi ish {elapsed} dan beri ishlayapti)\n"
            f"<i>{html.escape(tgfmt.trim(prompt, 200))}</i>{clip}\n\n"
            "Avvalgi tugagach o'zi boshlanadi. Zudlik bilan: /stop"
        )
    await _launch(ctx, scope, prompt, voice_input=voice_input, reply_to=reply_to, files=files)
```

`_launch` imzosi:
```python
async def _launch(
    ctx: ContextTypes.DEFAULT_TYPE,
    scope: Scope,
    prompt: str,
    *,
    voice_input: bool = False,
    reply_to=None,
    files: list[attachments.Attachment] | None = None,
) -> None:
    """Vazifani boshlaydi. `_start_or_queue` va follow-up halqasi shundan foydalanadi."""
    st = state_for(scope)
    files = list(files or [])
```

`_launch` da "Boshlandi" xabari va `RunningTask`:
```python
    clip = f" · 📎 {attachments.summary(files)}" if files else ""
    status = await send(
        f"⏳ <b>Boshlandi</b> · 💻 {html.escape(machines.display_name(agent_id))}{clip}\n"
        f"<i>{html.escape(tgfmt.trim(prompt, 150))}</i>"
    )
    progress = Progress(status, prompt)

    running = RunningTask(
        agent_id=agent_id, task_id=machines.new_task_id(),
        cwd=st.cwd, prompt=prompt, voice_input=voice_input, files=files,
    )
```

`_execute` da `machines.run` chaqiruvi:
```python
        payload = await machines.run(
            running.agent_id, running.prompt, running.cwd,
            st.session_id or None, st.model,
            task_id=running.task_id, on_progress=progress,
            attachments=running.files,
        )
```

`_execute` `finally` bloki:
```python
    finally:
        _running.pop(scope, None)
        running.files = []  # baytlar hub'ga ketdi — xotirani bo'shatamiz
```

`_execute` oxiridagi follow-up halqasi:
```python
    # Ishlab turganda kelgan qo'shimchalarni ketma-ket bajaramiz.
    for follow in running.follow_ups:
        clip = f"\n📎 {attachments.summary(follow.files)}" if follow.files else ""
        await send_to(
            ctx.bot, scope,
            f"▶️ Navbatdagi vazifa:\n<i>{html.escape(tgfmt.trim(follow.prompt, 300))}</i>{clip}",
        )
        await _launch(ctx, scope, follow.prompt, voice_input=running.voice_input,
                      files=follow.files)
```

- [ ] **Step 4: O'tishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: hammasi `passed` (bot testlari 4 ta).

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_attachments.py
git commit -F <xabar fayli>   # "bot: vazifa oqimi biriktirmalarni ko'taradi"
```

---

### Task 9: `bot.py` — rasm/fayl qabul qilish (`on_attachment`, taymer)

**Files:**
- Modify: `bot.py` — `on_voice` dan keyin yangi funksiyalar; `main()` handlerlar (1775-1776 atrofi)
- Test: `tests/test_bot_attachments.py` (davomi)

- [ ] **Step 1: Testlar** — `tests/test_bot_attachments.py` oxiriga:

```python
class FakeFile:
    def __init__(self, data: bytes):
        self._data = data

    async def download_as_bytearray(self):
        return bytearray(self._data)


class FakePhoto:
    def __init__(self, width: int, data: bytes = b"jpegdata"):
        self.width = width
        self.file_size = len(data)
        self._data = data

    async def get_file(self):
        return FakeFile(self._data)


class FakeDoc:
    def __init__(self, file_name, mime_type, data: bytes = b"log", file_size=None):
        self.file_name = file_name
        self.mime_type = mime_type
        self.file_size = len(data) if file_size is None else file_size
        self._data = data

    async def get_file(self):
        if self.file_size > attachments.MAX_TOTAL_BYTES:
            raise AssertionError("katta faylni yuklamasligi kerak")
        return FakeFile(self._data)


class FakeMediaMsg(FakeMsg):
    def __init__(self, message_id=1, photo=(), document=None, caption=None):
        super().__init__()
        self.message_id = message_id
        self.photo = list(photo)
        self.document = document
        self.caption = caption


def test_download_photo_picks_largest_and_names_by_message_id():
    msg = FakeMediaMsg(message_id=512, photo=[FakePhoto(90, b"small"), FakePhoto(800)])
    att = asyncio.run(bot._download_attachment(msg))
    assert (att.name, att.mime, att.data) == ("photo_512.jpg", "image/jpeg", b"jpegdata")


def test_download_document_sanitizes_name_and_keeps_mime():
    msg = FakeMediaMsg(message_id=7, document=FakeDoc("../x/err:or.log", "text/plain"))
    att = asyncio.run(bot._download_attachment(msg))
    assert (att.name, att.mime, att.data) == ("err_or.log", "text/plain", b"log")


def test_download_document_without_name_or_mime():
    msg = FakeMediaMsg(message_id=9, document=FakeDoc(None, None))
    att = asyncio.run(bot._download_attachment(msg))
    assert (att.name, att.mime) == ("file_9", "application/octet-stream")


def test_download_rejects_too_big_before_downloading():
    doc = FakeDoc("big.zip", "application/zip", file_size=attachments.MAX_TOTAL_BYTES + 1)
    msg = FakeMediaMsg(document=doc)
    assert asyncio.run(bot._download_attachment(msg)) is None
    assert msg.texts and "20 MB" in msg.texts[0]


def test_download_failure_tells_user():
    class Broken(FakeDoc):
        async def get_file(self):
            raise RuntimeError("tarmoq")

    msg = FakeMediaMsg(document=Broken("a.txt", "text/plain"))
    assert asyncio.run(bot._download_attachment(msg)) is None
    assert msg.texts == ["📎 Faylni yuklab bo'lmadi, qayta yuboring."]


def test_message_without_media_is_ignored():
    assert asyncio.run(bot._download_attachment(FakeMediaMsg())) is None


def test_caption_launches_after_debounce(monkeypatch):
    calls = []

    async def fake_launch(ctx, scope, prompt, *, voice_input=False, reply_to=None, files=None):
        calls.append((scope, prompt, [f.name for f in files]))

    monkeypatch.setattr(bot, "_launch", fake_launch)

    async def run():
        box = attachments.add(SCOPE, _att(), "xatoni tuzat")
        bot._schedule_box(None, SCOPE, box, FakeMsg())
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert calls == [(SCOPE, "xatoni tuzat", ["photo_1.jpg"])]
    assert attachments.peek(SCOPE) is None


def test_album_items_during_debounce_are_included(monkeypatch):
    calls = []

    async def fake_launch(ctx, scope, prompt, *, voice_input=False, reply_to=None, files=None):
        calls.append([f.name for f in files])

    monkeypatch.setattr(bot, "_launch", fake_launch)

    async def run():
        box = attachments.add(SCOPE, _att(), "albom")
        bot._schedule_box(None, SCOPE, box, FakeMsg())
        box = attachments.add(SCOPE, _att("photo_2.jpg"))
        bot._schedule_box(None, SCOPE, box, FakeMsg())
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert calls == [["photo_1.jpg", "photo_2.jpg"]]


def test_without_caption_posts_notice_and_updates_it():
    async def run():
        msg = FakeMsg()
        box = attachments.add(SCOPE, _att())
        bot._schedule_box(None, SCOPE, box, msg)
        await asyncio.sleep(0.02)
        assert msg.texts == ["📎 1 ta rasm kutmoqda — vazifani yozing yoki ovoz yuboring."]
        box = attachments.add(SCOPE, attachments.Attachment("a.log", "text/plain", b"1"))
        bot._schedule_box(None, SCOPE, box, msg)
        await asyncio.sleep(0.02)
        assert box.notice.edits == ["📎 1 ta rasm, 1 ta fayl kutmoqda — vazifani yozing yoki ovoz yuboring."]
        box.timer.cancel()

    asyncio.run(run())


def test_notice_expires(monkeypatch):
    monkeypatch.setattr(attachments, "TTL_SEC", 0.02)

    async def run():
        msg = FakeMsg()
        box = attachments.add(SCOPE, _att())
        bot._schedule_box(None, SCOPE, box, msg)
        await asyncio.sleep(0.08)
        assert box.notice.edits == ["⌛ Biriktirmalar eskirdi — qayta yuboring."]
        assert attachments.peek(SCOPE) is None

    asyncio.run(run())
```

- [ ] **Step 2: Yiqilishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests/test_bot_attachments.py -q`
Expected: yangi testlar FAIL — `AttributeError: module 'bot' has no attribute '_download_attachment'` / `_schedule_box`

- [ ] **Step 3: Kod** — `bot.py` da `on_voice` funksiyasidan keyin (`# Vazifani bajarish` bo'limidan oldin):

```python
# --------------------------------------------------------------------------
# Rasm va fayllar
#
# Biriktirma darhol vazifa bo'lmaydi: caption bo'lsa qisqa kutib (albomning
# qolgan bo'laklari kelsin) boshlaymiz; bo'lmasa keyingi matn/ovoz xabari
# ularni oladi (attachments.py). Har scope uchun bitta taymer.
# --------------------------------------------------------------------------
async def _download_attachment(message) -> attachments.Attachment | None:
    """Xabardagi rasm/faylni yuklaydi. Bo'lmasa yoki xato bo'lsa None
    (foydalanuvchiga sabab yozib)."""
    if message.photo:
        media = message.photo[-1]  # eng katta o'lcham
        name, mime = f"photo_{message.message_id}.jpg", "image/jpeg"
    elif message.document:
        media = message.document
        name = attachments.safe_name(media.file_name or "", f"file_{message.message_id}")
        mime = media.mime_type or "application/octet-stream"
    else:
        return None

    limit_mb = attachments.MAX_TOTAL_BYTES // (1024 * 1024)
    if (media.file_size or 0) > attachments.MAX_TOTAL_BYTES:
        await message.reply_text(
            f"📎 {name} juda katta — {limit_mb} MB gacha qabul qilinadi."
        )
        return None
    try:
        tg_file = await media.get_file()
        data = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:
        log.warning("Biriktirma yuklanmadi: %s", exc)
        await message.reply_text("📎 Faylni yuklab bo'lmadi, qayta yuboring.")
        return None
    return attachments.Attachment(name=name, mime=mime, data=data)


async def _box_timer(ctx: ContextTypes.DEFAULT_TYPE, scope: Scope,
                     box: attachments.PendingBox, reply_to) -> None:
    """Caption bo'lsa — debounce'dan keyin vazifa; bo'lmasa ogohlantirish va eskirish."""
    try:
        if box.caption:
            await asyncio.sleep(attachments.DEBOUNCE_SEC)
            if not attachments.drop(scope, box):
                return  # allaqachon olingan (masalan matn kelib qoldi)
            box.timer = None  # o'z vazifamizni bekor qilib qo'ymaslik uchun
            await _start_or_queue(ctx, scope, box.caption, voice_input=False,
                                  reply_to=reply_to, box=box)
            return

        text = f"📎 {box.summary()} kutmoqda — vazifani yozing yoki ovoz yuboring."
        if box.notice is None:
            box.notice = await reply_to.reply_text(text)
        else:
            try:
                await box.notice.edit_text(text)
            except BadRequest:
                pass
        await asyncio.sleep(attachments.TTL_SEC)
        if attachments.drop(scope, box) and box.notice is not None:
            try:
                await box.notice.edit_text("⌛ Biriktirmalar eskirdi — qayta yuboring.")
            except BadRequest:
                pass
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Biriktirma taymeri xatosi")


def _schedule_box(ctx: ContextTypes.DEFAULT_TYPE, scope: Scope,
                  box: attachments.PendingBox, reply_to) -> None:
    """Har yangi biriktirmada taymer qaytadan boshlanadi."""
    if box.timer is not None:
        box.timer.cancel()
    box.timer = asyncio.create_task(_box_timer(ctx, scope, box, reply_to))


async def on_attachment(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    message = update.effective_message
    scope = scope_of(update)
    att = await _download_attachment(message)
    if att is None:
        return
    try:
        box = attachments.add(scope, att, message.caption or "")
    except attachments.TooLarge as exc:
        return await message.reply_text(f"📎 {exc}")
    _schedule_box(ctx, scope, box, message)
```

`main()` da handlerlar — `on_voice` qatoridan oldin:
```python
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, on_attachment))
```

- [ ] **Step 4: O'tishini tekshirish**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: hammasi `passed`.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_attachments.py
git commit -F <xabar fayli>   # "bot: rasm va fayllarni qabul qilish (caption, albom, kutish)"
```

---

### Task 10: `/new` tozalaydi, HELP va README

**Files:**
- Modify: `bot.py` — `cmd_new`; `HELP` matni
- Modify: `README.md` — "Foydalanish" jadvali va "Fayllar" jadvali

- [ ] **Step 1: `cmd_new`**

```python
async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    scope = scope_of(update)
    st = state_for(scope)
    if not st.cwd:
        return await update.effective_message.reply_text(
            "Avval loyihani tanlang: /projects"
        )
    st.session_id = ""
    save_states()
    dropped = attachments.clear(scope)
    if dropped is not None and dropped.timer is not None:
        dropped.timer.cancel()
    extra = ""
    if dropped is not None and dropped.items:
        extra = f"\n📎 Kutayotgan biriktirmalar ({dropped.summary()}) tashlab yuborildi."
    await update.effective_message.reply_html(
        "🆕 Yangi sessiya. Keyingi xabaringiz yangi suhbatni boshlaydi."
        f"{extra}\n\n" + _where_text(st)
    )
```

- [ ] **Step 2: HELP** — `Ovozda so'rasangiz, javob ham avtomatik ovozda keladi.` qatoridan keyin:
```
<b>Rasm yoki fayl</b> ham yuborsa bo'ladi: caption bilan — darhol vazifa;
caption'siz — keyingi matn yoki ovoz bilan birga ketadi (15 daqiqa kutadi).
Albom ham bo'ladi. Claude fayllarni o'zi ko'radi.
```

- [ ] **Step 3: README** — "Foydalanish" jadvalida `*(matn yoki ovoz)*` qatoridan keyin:
```
| *(rasm yoki fayl)* | Caption bilan — darhol vazifa; caption'siz — keyingi matn/ovoz bilan birga. Albom bo'ladi. Claude faylni o'zi ko'radi (`inbox/` papkasi) |
```
"Fayllar" jadvaliga `machines.py` dan keyin:
```
| `attachments.py` | Rasm/fayllar uchun kutish ro'yxati (bot tomonida) |
| `inbox.py` | Agentda biriktirmalarni `inbox/<vazifa>/` ga yozish |
```

- [ ] **Step 4: Testlar o'tadimi**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: hammasi `passed`.

- [ ] **Step 5: Commit**

```bash
git add bot.py README.md
git commit -F <xabar fayli>   # "/new biriktirmalarni tozalaydi; hujjatlar"
```

---

### Task 11: Jonli tekshiruv (deploy)

Bot **serverda** ishlaydi — `bot.py` o'zgarishlari faqat u yerda kuchga kiradi. Agent — shu kompyuterda (`ishxona`) va serverda.

- [ ] **Step 1: Push**

```bash
git push origin main
```

- [ ] **Step 2: Serverni yangilash** (root ssh; xizmatlar `claudebot` foydalanuvchisi ostida)

```bash
ssh root@161.97.88.95 'cd /home/claudebot/claude-tg && sudo -u claudebot git pull --ff-only && systemctl restart claude-tg-bot claude-tg-agent && sleep 6 && systemctl is-active claude-tg-bot claude-tg-agent && journalctl -u claude-tg-bot -n 5 -o cat --no-pager'
```
Expected: `active` ×2, jurnalda `Bot ishga tushdi` va `Kompyuter ulandi: server`.

- [ ] **Step 3: Lokal agentni qayta yoqish** (WMI orqali — sandbox o'ldirmasin)

PowerShell:
```powershell
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" | Where-Object { $_.CommandLine -like '*claude_bot\agent.bat*' } | ForEach-Object { taskkill /T /F /PID $_.ProcessId }
$dir = "D:\work\OSPanelV6\home\personal\claude_bot"; $s = ([wmiclass]"Win32_ProcessStartup").CreateInstance(); $s.ShowWindow = 7
Invoke-WmiMethod -Class Win32_Process -Name Create -ArgumentList @("cmd.exe /c `"$dir\agent.bat`"", $dir, $s)
Start-Sleep 8; Get-Content "$dir\agent.log" -Tail 3
```
Expected: `Ulandi. Hub: claude-tg, agent nomi: ishxona`.

- [ ] **Step 4: Telegramdan sinov** (foydalanuvchi qiladi, natijani aytadi)

1. Rasm + caption "Bu rasmda nima yozilgan?" → "⏳ Boshlandi · 💻 ishxona · 📎 1 ta rasm", javobda rasm mazmuni.
2. Rasm caption'siz → "📎 1 ta rasm kutmoqda…", keyin ovoz → "📎 1 ta rasm vazifaga qo'shildi." va vazifa.
3. Albom (3 rasm, caption) → "📎 3 ta rasm".
4. `.log` fayl + caption → Claude fayl mazmunini o'qiydi.

Agent logida: `Biriktirmalar: N ta fayl -> ...\inbox\<id>`.

- [ ] **Step 5: Notebook** — u yerda ham `git pull` kerak (eski agent `attachments` ni e'tiborsiz qoldiradi: vazifa ishlaydi, lekin fayl Claude'ga yetmaydi). Foydalanuvchiga eslatib qo'ying.
