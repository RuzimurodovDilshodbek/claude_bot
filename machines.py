"""Bot uchun kompyuterlar bilan ishlash qatlami.

Bot ilgari `sessions` va `runner` modullarini to'g'ridan-to'g'ri chaqirardi —
ya'ni faqat o'zi turgan kompyuterda ishlardi. Endi shu chaqiruvlar hub orqali
tanlangan kompyuterga uzatiladi.

Qaytariladigan obyektlar `sessions.SessionInfo` / `ProjectInfo` bilan bir xil
maydon nomlariga ega, shuning uchun botning formatlash kodi o'zgarmaydi.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import protocol

_hub = None  # bot ishga tushganda o'rnatiladi


def bind(hub_instance) -> None:
    global _hub
    _hub = hub_instance


def hub():
    if _hub is None:
        raise RuntimeError("Hub hali ishga tushmagan.")
    return _hub


class NoMachine(RuntimeError):
    """Kerakli kompyuter ulanmagan."""


# --------------------------------------------------------------------------
# Masofadagi ma'lumot uchun adapterlar
# --------------------------------------------------------------------------
@dataclass
class RemoteSession:
    session_id: str
    label: str
    cwd: str
    project_name: str
    project_key: str
    user_turns: int
    mtime: float
    agent_id: str = ""
    is_open: bool = False   # kompyuterda Claude Code ilovasida ochiqmi

    @classmethod
    def from_dict(cls, data: dict, agent_id: str) -> "RemoteSession":
        return cls(
            session_id=data.get("session_id", ""),
            label=data.get("label") or "(nomsiz sessiya)",
            cwd=data.get("cwd", ""),
            project_name=data.get("project_name", ""),
            project_key=data.get("project_key", ""),
            user_turns=int(data.get("user_turns") or 0),
            mtime=float(data.get("mtime") or 0.0),
            agent_id=agent_id,
            is_open=bool(data.get("is_open")),
        )


@dataclass
class RemoteProject:
    key: str
    name: str
    cwd: str
    session_count: int
    mtime: float
    agent_id: str = ""
    sessions: list[RemoteSession] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, agent_id: str) -> "RemoteProject":
        return cls(
            key=data.get("key", ""),
            name=data.get("name", ""),
            cwd=data.get("cwd", ""),
            session_count=int(data.get("sessions") or 0),
            mtime=float(data.get("mtime") or 0.0),
            agent_id=agent_id,
        )


# --------------------------------------------------------------------------
# Kompyuter tanlash
# --------------------------------------------------------------------------
def connected() -> list:
    return hub().machines()

def count() -> int:
    return len(hub().machines())


def resolve(agent_id: str | None) -> str:
    """Ishlatiladigan kompyuterni aniqlaydi.

    Tanlangani ulangan bo'lsa — o'sha. Aks holda, bitta kompyuter ulangan
    bo'lsa avtomatik o'sha tanlanadi. Hech biri bo'lmasa — xato.
    """
    h = hub()
    if agent_id:
        machine = h.get(agent_id)
        if machine is not None:
            return machine.agent_id

    single = h.only_one()
    if single is not None:
        return single.agent_id

    if not h.machines():
        raise NoMachine("Hech qaysi kompyuter ulanmagan.")
    raise NoMachine("Kompyuter tanlanmagan — /pc")


def display_name(agent_id: str) -> str:
    if not agent_id:
        return "tanlanmagan"
    name = hub().known_name(agent_id)
    if not name:
        return "(noma'lum)"
    return name if hub().get(agent_id) else f"{name} (ulanmagan)"


def is_online(agent_id: str) -> bool:
    return bool(agent_id) and hub().get(agent_id) is not None


async def wait_for(agent_id: str, timeout: float = 600.0,
                   on_wait: Callable[[float], Awaitable[None]] | None = None) -> bool:
    """Kompyuter qayta ulanishini kutadi.

    Tarmoq bu yerda tez-tez uziladi (DNS ham, TLS ham). Vazifani darhol rad
    etish o'rniga biroz kutish foydalanuvchi uchun ancha yaxshi — agent
    odatda bir necha daqiqada o'zi qaytadi.
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if is_online(agent_id):
            return True
        if on_wait is not None:
            try:
                await on_wait(deadline - _time.monotonic())
            except Exception:
                pass
        await asyncio.sleep(4)
    return is_online(agent_id)


# --------------------------------------------------------------------------
# So'rovlar
# --------------------------------------------------------------------------
async def list_projects(agent_id: str, limit: int = 12) -> list[RemoteProject]:
    rows = await hub().request(agent_id, protocol.OP_LIST_PROJECTS)
    return [RemoteProject.from_dict(r, agent_id) for r in (rows or [])][:limit]


async def list_sessions(agent_id: str, project: str | None = None,
                        limit: int = 10) -> list[RemoteSession]:
    rows = await hub().request(
        agent_id, protocol.OP_LIST_SESSIONS,
        {"project": project, "limit": limit},
    )
    return [RemoteSession.from_dict(r, agent_id) for r in (rows or [])]


async def find_project(agent_id: str, needle: str) -> RemoteProject | None:
    low = (needle or "").strip().lower().replace("/", "\\").rstrip("\\")
    for proj in await list_projects(agent_id, limit=100):
        if low in (proj.key.lower(), proj.name.lower(),
                   proj.cwd.lower().replace("/", "\\").rstrip("\\")):
            return proj
    return None


async def find_session(agent_id: str, session_id: str) -> RemoteSession | None:
    for s in await list_sessions(agent_id, limit=200):
        if s.session_id == session_id:
            return s
    return None


async def session_tail(agent_id: str, session_id: str,
                       turns: int = 2) -> list[tuple[str, str]]:
    rows = await hub().request(
        agent_id, protocol.OP_SESSION_TAIL,
        {"session_id": session_id, "turns": turns},
    )
    return [(r[0], r[1]) for r in (rows or []) if isinstance(r, (list, tuple)) and len(r) >= 2]


@dataclass
class RunOutcome:
    """Masofadagi vazifa natijasi — `runner.RunResult` bilan bir xil maydonlar,
    shuning uchun botning hisobot kodi o'zgarmaydi."""

    ok: bool = False
    session_id: str = ""
    text: str = ""
    error: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    model: str = ""
    cancelled: bool = False
    tools_used: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "RunOutcome":
        data = data or {}
        return cls(
            ok=bool(data.get("ok")),
            session_id=data.get("session_id") or "",
            text=data.get("text") or "",
            error=data.get("error") or "",
            duration_ms=int(data.get("duration_ms") or 0),
            cost_usd=float(data.get("cost_usd") or 0.0),
            num_turns=int(data.get("num_turns") or 0),
            model=data.get("model") or "",
            cancelled=bool(data.get("cancelled")),
            tools_used=list(data.get("tools_used") or []),
        )


def new_task_id() -> str:
    return protocol.new_id()


async def run(agent_id: str, prompt: str, cwd: str, session_id: str | None,
              model: str | None, task_id: str | None = None,
              on_progress: Callable[[str, str], Awaitable[None]] | None = None) -> dict:
    return await hub().run(agent_id, {
        "prompt": prompt,
        "cwd": cwd,
        "session_id": session_id,
        "model": model,
    }, on_progress, task_id=task_id)


async def session_state(agent_id: str, session_id: str) -> dict:
    """Sessiya o'sha kompyuterda ochiqmi."""
    try:
        return await hub().request(
            agent_id, protocol.OP_SESSION_STATE,
            {"session_id": session_id}, timeout=20) or {}
    except Exception:
        return {}  # bilolmasak — ogohlantirmaymiz, vazifani to'xtatmaymiz


async def noisy_sessions(agent_id: str) -> list[dict]:
    return await hub().request(agent_id, protocol.OP_NOISY) or []


async def cleanup(agent_id: str) -> dict:
    return await hub().request(agent_id, protocol.OP_CLEANUP, timeout=120) or {}


async def cancel(agent_id: str, task_id: str) -> None:
    machine = hub().get(agent_id)
    if machine is not None:
        await machine.cancel(task_id)
