"""Hub (server) va agent (kompyuter) o'rtasidagi xabar formati.

Bir yo'nalishli oqim emas, so'rov-javob + hodisa oqimi aralash:

    agent -> hub   hello        ulanish va tanishuv
    hub   -> agent welcome      qabul qilindi
    hub   -> agent req          so'rov (list_projects, run, cancel, ...)
    agent -> hub   res          so'rovga javob
    agent -> hub   ev           vazifa davomidagi hodisa (oqim)
    agent -> hub   done         vazifa tugadi
    ikkalasi       ping/pong    aloqa tirikligini tekshirish

Har bir `req` da `id` bor; `res`, `ev` va `done` shu `id` ga bog'lanadi.
Shu tufayli bitta agentda bir nechta vazifa parallel ketishi mumkin.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

# --- xabar turlari ---
HELLO = "hello"
WELCOME = "welcome"
REQ = "req"
RES = "res"
EV = "ev"
DONE = "done"
PING = "ping"
PONG = "pong"
ERROR = "error"

# --- so'rov operatsiyalari ---
OP_LIST_PROJECTS = "list_projects"
OP_LIST_SESSIONS = "list_sessions"
OP_SESSION_TAIL = "session_tail"
OP_RUN = "run"
OP_CANCEL = "cancel"
OP_INFO = "info"
OP_NOISY = "noisy_sessions"
OP_CLEANUP = "cleanup"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | bytes) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("xabar obyekt bo'lishi kerak")
    return data


# --- qurish yordamchilari -------------------------------------------------
def hello(token: str, agent_id: str, name: str, info: dict) -> dict:
    return {
        "t": HELLO,
        "v": PROTOCOL_VERSION,
        "token": token,
        "agent_id": agent_id,
        "name": name,
        "info": info,
        "ts": time.time(),
    }


def welcome(hub_name: str) -> dict:
    return {"t": WELCOME, "v": PROTOCOL_VERSION, "hub": hub_name, "ts": time.time()}


def req(op: str, args: dict | None = None, rid: str | None = None) -> dict:
    return {"t": REQ, "id": rid or new_id(), "op": op, "args": args or {}}


def res(rid: str, ok: bool, data: Any = None, error: str = "") -> dict:
    return {"t": RES, "id": rid, "ok": ok, "data": data, "error": error}


def ev(rid: str, kind: str, text: str) -> dict:
    return {"t": EV, "id": rid, "kind": kind, "text": text}


def done(rid: str, result: dict) -> dict:
    return {"t": DONE, "id": rid, "result": result}


def error(message: str) -> dict:
    return {"t": ERROR, "error": message}


# --- ma'lumot tuzilmalari -------------------------------------------------
@dataclass
class MachineInfo:
    """Agent o'zi haqida hub'ga aytadigan ma'lumot."""

    agent_id: str
    name: str
    hostname: str = ""
    platform: str = ""
    claude_version: str = ""
    project_count: int = 0
    session_count: int = 0
    permission_mode: str = ""
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "hostname": self.hostname,
            "platform": self.platform,
            "claude_version": self.claude_version,
            "project_count": self.project_count,
            "session_count": self.session_count,
            "permission_mode": self.permission_mode,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MachineInfo":
        return cls(
            agent_id=data.get("agent_id", ""),
            name=data.get("name", ""),
            hostname=data.get("hostname", ""),
            platform=data.get("platform", ""),
            claude_version=data.get("claude_version", ""),
            project_count=int(data.get("project_count") or 0),
            session_count=int(data.get("session_count") or 0),
            permission_mode=data.get("permission_mode", ""),
            connected_at=float(data.get("connected_at") or time.time()),
            last_seen=float(data.get("last_seen") or time.time()),
        )
