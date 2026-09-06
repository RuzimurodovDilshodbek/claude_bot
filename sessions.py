"""~/.claude/projects ichidagi mavjud sessiyalarni o'qish.

Claude Code har bir sessiyani `<project-key>/<session-id>.jsonl` sifatida
saqlaydi. Bu modul shu fayllardan loyiha va sessiya ro'yxatini yig'adi.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import config

_RE_USER = re.compile(r'"type":\s*"user"')
_RE_ASSISTANT = re.compile(r'"type":\s*"assistant"')
# cwd qiymatida qo'shtirnoq bo'lmaydi (Windows yo'llari), shuning uchun
# oddiy shablon yetarli; qiymat json ichida escape qilingan holda keladi.
_RE_CWD = re.compile(r'"cwd":\s*"([^"]*)"')


def _unescape(raw: str) -> str:
    try:
        return json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return raw

_cache: dict[str, tuple[float, int, "SessionInfo"]] = {}

# Shu qatorlardan biriga to'g'ri kelgan sessiya "test/shovqin" hisoblanadi.
NOISE_MARKERS = (
    "faqat 'tayyor'",
    "faqat 'ok'",
    "faqat \"tayyor\"",
    "faqat \"ok\"",
    "test message",
    "sinov xabari",
)


def is_noise(info: "SessionInfo") -> bool:
    """Test/shovqin sessiyani aniqlaydi.

    Belgilar: juda kalta (2 dan kam xabar), yoki nomida test iborasi bor,
    yoki umumiy hajmi ozgina (2 KB dan kichik).
    """
    if info.user_turns < 2 and info.size < 8_000:
        return True
    if info.size < 2_000:
        return True
    label = (info.title or info.last_prompt or "").lower()
    return any(marker in label for marker in NOISE_MARKERS)


@dataclass
class SessionInfo:
    session_id: str
    path: Path
    project_key: str
    cwd: str
    title: str
    last_prompt: str
    mtime: float
    size: int
    user_turns: int
    assistant_turns: int

    @property
    def label(self) -> str:
        return self.title or self.last_prompt or "(nomsiz sessiya)"

    @property
    def project_name(self) -> str:
        cwd = self.cwd.replace("/", "\\").rstrip("\\")
        return cwd.rsplit("\\", 1)[-1] if cwd else self.project_key


@dataclass
class ProjectInfo:
    key: str
    cwd: str
    dir: Path
    sessions: list[SessionInfo]

    @property
    def name(self) -> str:
        cwd = self.cwd.replace("/", "\\").rstrip("\\")
        return cwd.rsplit("\\", 1)[-1] if cwd else self.key

    @property
    def mtime(self) -> float:
        return max((s.mtime for s in self.sessions), default=0.0)


def _text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def read_session(path: Path) -> SessionInfo | None:
    """Bitta .jsonl sessiya faylini o'qib metama'lumot qaytaradi (kesh bilan)."""
    try:
        st = path.stat()
    except OSError:
        return None

    hit = _cache.get(str(path))
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]

    title = ""
    last_prompt = ""
    first_prompt = ""
    cwd = ""
    user_turns = 0
    assistant_turns = 0

    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                # Qimmat json.loads dan oldin arzon tekshiruv.
                if '"ai-title"' in line:
                    try:
                        title = json.loads(line).get("aiTitle") or title
                    except json.JSONDecodeError:
                        pass
                    continue
                if '"last-prompt"' in line:
                    try:
                        last_prompt = json.loads(line).get("lastPrompt") or last_prompt
                    except json.JSONDecodeError:
                        pass
                    continue
                if _RE_ASSISTANT.search(line):
                    assistant_turns += 1
                    if not cwd:
                        m = _RE_CWD.search(line)
                        if m:
                            cwd = _unescape(m.group(1))
                    continue
                if _RE_USER.search(line):
                    user_turns += 1
                    if not cwd:
                        m = _RE_CWD.search(line)
                        if m:
                            cwd = _unescape(m.group(1))
                    if not first_prompt:
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("isSidechain"):
                            continue
                        first_prompt = _text_of(obj.get("message") or {}).strip()
    except OSError:
        return None

    info = SessionInfo(
        session_id=path.stem,
        path=path,
        project_key=path.parent.name,
        cwd=cwd,
        title=title.strip(),
        last_prompt=(last_prompt or first_prompt).strip(),
        mtime=st.st_mtime,
        size=st.st_size,
        user_turns=user_turns,
        assistant_turns=assistant_turns,
    )
    _cache[str(path)] = (st.st_mtime, st.st_size, info)
    return info


def list_projects(min_turns: int = 1, include_noise: bool = False) -> list[ProjectInfo]:
    """Barcha loyihalar, oxirgi faollik bo'yicha saralangan.

    `include_noise=False` bo'lsa test/qisqa sessiyalar chiqarib tashlanadi.
    """
    root = config.PROJECTS_DIR
    if not root.exists():
        return []

    projects: list[ProjectInfo] = []
    for pdir in root.iterdir():
        if not pdir.is_dir():
            continue
        found: list[SessionInfo] = []
        for f in pdir.glob("*.jsonl"):
            info = read_session(f)
            if not info or info.user_turns < min_turns:
                continue
            if not include_noise and is_noise(info):
                continue
            found.append(info)
        if not found:
            continue
        found.sort(key=lambda s: s.mtime, reverse=True)
        cwd = next((s.cwd for s in found if s.cwd), "")
        projects.append(ProjectInfo(key=pdir.name, cwd=cwd, dir=pdir, sessions=found))

    projects.sort(key=lambda p: p.mtime, reverse=True)
    return projects


def find_project(key_or_path: str) -> ProjectInfo | None:
    needle = (key_or_path or "").strip().lower().replace("/", "\\").rstrip("\\")
    # Loyihani topishda test sessiyalarga ehtiyoj yo'q, lekin izlash keng bo'lsin
    for proj in list_projects(include_noise=True):
        if proj.key.lower() == needle:
            return proj
        if proj.cwd.lower().replace("/", "\\").rstrip("\\") == needle:
            return proj
        if proj.name.lower() == needle:
            return proj
    return None


def find_session(session_id: str) -> SessionInfo | None:
    # ID bo'yicha izlash — noise bo'lsa ham topilsin, foydalanuvchi tanlagan.
    for proj in list_projects(include_noise=True):
        for s in proj.sessions:
            if s.session_id == session_id:
                return s
    return None


def recent_sessions(limit: int = 10, include_noise: bool = False) -> list[SessionInfo]:
    """Kompyuterdagi barcha loyihalar bo'yicha eng so'nggi sessiyalar."""
    out: list[SessionInfo] = []
    for proj in list_projects(include_noise=include_noise):
        out.extend(proj.sessions)
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out[:limit]


def noisy_sessions() -> list[SessionInfo]:
    """Barcha "shovqin" sessiyalarni qaytaradi — /cleanup uchun."""
    out: list[SessionInfo] = []
    root = config.PROJECTS_DIR
    if not root.exists():
        return out
    for pdir in root.iterdir():
        if not pdir.is_dir():
            continue
        for f in pdir.glob("*.jsonl"):
            info = read_session(f)
            if info and is_noise(info):
                out.append(info)
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


# --------------------------------------------------------------------------
# Sessiya hozir kompyuterda ochiqmi?
#
# Claude Code har bir ishlayotgan sessiyani `~/.claude/sessions/<pid>.json`
# da qayd qiladi: sessionId, cwd, pid, kind, entrypoint. Jarayon tugaganda
# fayl qolib ketishi mumkin, shuning uchun PID tirikligini ham tekshiramiz.
# --------------------------------------------------------------------------
SESSIONS_DIR = config.PROJECTS_DIR.parent / "sessions"


def _pid_alive(pid: int) -> bool:
    """Jarayon tirikmi.

    DIQQAT: Windows'da `os.kill(pid, 0)` ISHLATILMAYDI — u yerda os.kill
    TerminateProcess ni chaqiradi va jarayonni haqiqatan O'LDIRADI.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False

    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)  # POSIX'da xavfsiz — faqat tekshiradi
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # boshqa foydalanuvchiniki, lekin mavjud


# Botning o'z yurishlari ham shu yerga qayd qilinadi (runner.py
# CLAUDE_CODE_ENTRYPOINT=telegram-bot qo'yadi). Ular "kompyuterda ochiq"
# hisoblanmasligi kerak — aks holda bot o'zini o'zi ogohlantiradi.
BOT_ENTRYPOINT = "telegram-bot"


def open_sessions(interactive_only: bool = True) -> dict[str, dict]:
    """Hozir ochiq sessiyalar: {session_id: {pid, cwd, kind, entrypoint, ...}}.

    `interactive_only` — faqat odam ochgan sessiyalar (Claude Code ilovasi
    yoki terminal); botning o'z yurishlari hisobga olinmaydi.
    """
    out: dict[str, dict] = {}
    if not SESSIONS_DIR.exists():
        return out

    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        session_id = data.get("sessionId")
        pid = data.get("pid")
        if not session_id or not _pid_alive(pid):
            continue  # yozuv yo'q yoki eskirgan
        if interactive_only and data.get("entrypoint") == BOT_ENTRYPOINT:
            continue  # bizning o'z vazifamiz
        out[session_id] = {
            "pid": pid,
            "cwd": data.get("cwd", ""),
            "kind": data.get("kind", ""),
            "entrypoint": data.get("entrypoint", ""),
            "name": data.get("name", ""),
            "version": data.get("version", ""),
        }
    return out


def session_state(session_id: str) -> dict:
    """Bitta sessiya holati — bot ogohlantirish uchun so'raydi."""
    info = open_sessions().get(session_id)
    return {"open": info is not None, **(info or {})}


def transcript_tail(path: Path, turns: int = 6) -> list[tuple[str, str]]:
    """Sessiyaning oxirgi bir necha xabarini (rol, matn) ko'rinishida qaytaradi."""
    rows: list[tuple[str, str]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not ('"type": "user"' in line or '"type":"user"' in line
                        or '"type": "assistant"' in line or '"type":"assistant"' in line):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("isSidechain"):
                    continue
                role = obj.get("type")
                if role not in ("user", "assistant"):
                    continue
                text = _text_of(obj.get("message") or {}).strip()
                if not text:
                    continue
                rows.append((role, text))
    except OSError:
        return []
    return rows[-turns:]
