"""Bajarilgan vazifalar tarixi — xarajat va statistika uchun.

Har vazifadan keyin bitta JSON qatori qo'shiladi (append-only). Format
osongina o'chiriladi/tahrirlanadi, hech qanday DB kerak emas.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import config

HISTORY_FILE = config.BASE_DIR / "history.jsonl"


@dataclass
class Entry:
    ts: float
    chat_id: int
    project: str
    session_id: str
    model: str
    prompt: str
    ok: bool
    cancelled: bool
    duration_ms: int
    cost_usd: float
    tools: int


def record(chat_id: int, project: str, session_id: str, model: str,
           prompt: str, ok: bool, cancelled: bool, duration_ms: int,
           cost_usd: float, tools: int) -> None:
    entry = {
        "ts": time.time(),
        "chat_id": chat_id,
        "project": project,
        "session_id": session_id,
        "model": model,
        "prompt": prompt[:200],
        "ok": ok,
        "cancelled": cancelled,
        "duration_ms": duration_ms,
        "cost_usd": cost_usd,
        "tools": tools,
    }
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # tarix xatosi vazifani buzmasin


def _load() -> list[Entry]:
    if not HISTORY_FILE.exists():
        return []
    out: list[Entry] = []
    with HISTORY_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(Entry(
                    ts=d["ts"], chat_id=d["chat_id"], project=d.get("project", ""),
                    session_id=d.get("session_id", ""), model=d.get("model", ""),
                    prompt=d.get("prompt", ""), ok=bool(d.get("ok")),
                    cancelled=bool(d.get("cancelled")),
                    duration_ms=int(d.get("duration_ms", 0)),
                    cost_usd=float(d.get("cost_usd", 0.0)),
                    tools=int(d.get("tools", 0)),
                ))
            except (KeyError, ValueError, json.JSONDecodeError):
                continue
    return out


def summary(chat_id: int | None = None, days: int = 30) -> str:
    """`/xarajat` komandasi uchun matnli hisobot."""
    cutoff = time.time() - days * 86400
    entries = [e for e in _load() if e.ts >= cutoff]
    if chat_id is not None:
        entries = [e for e in entries if e.chat_id == chat_id]

    if not entries:
        return f"So'nggi {days} kunda tarix yo'q."

    day_ago = time.time() - 86400
    today = [e for e in entries if e.ts >= day_ago]

    total_cost = sum(e.cost_usd for e in entries)
    total_dur = sum(e.duration_ms for e in entries) / 1000
    ok = sum(1 for e in entries if e.ok)
    fail = sum(1 for e in entries if not e.ok and not e.cancelled)
    cancelled = sum(1 for e in entries if e.cancelled)

    by_model: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
    for e in entries:
        c, s = by_model[e.model or "?"]
        by_model[e.model or "?"] = (c + 1, s + e.cost_usd)

    by_project: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
    for e in entries:
        c, s = by_project[e.project or "?"]
        by_project[e.project or "?"] = (c + 1, s + e.cost_usd)

    lines = [
        f"<b>📊 So'nggi {days} kun</b>",
        f"Vazifalar: <b>{len(entries)}</b>  "
        f"(✅ {ok} · ❌ {fail} · 🛑 {cancelled})",
        f"Xarajat: <b>${total_cost:.2f}</b>  ({_fmt_duration(total_dur)})",
        "",
        f"<b>Bugun:</b> {len(today)} vazifa · "
        f"${sum(e.cost_usd for e in today):.2f}",
    ]

    if by_model:
        lines.append("")
        lines.append("<b>Modellar:</b>")
        for m, (c, s) in sorted(by_model.items(), key=lambda x: -x[1][1]):
            lines.append(f"  • {m}: {c} vazifa · ${s:.2f}")

    top_projects = sorted(by_project.items(), key=lambda x: -x[1][1])[:5]
    if top_projects:
        lines.append("")
        lines.append("<b>Loyihalar (top 5):</b>")
        for p, (c, s) in top_projects:
            lines.append(f"  • {p}: {c} vazifa · ${s:.2f}")

    return "\n".join(lines)


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} sek"
    if seconds < 3600:
        return f"{seconds/60:.1f} daq"
    return f"{seconds/3600:.1f} soat"
