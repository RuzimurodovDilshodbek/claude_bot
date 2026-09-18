"""Kompyuterda ishlaydigan agent — hub'ga ulanib vazifalarni bajaradi.

Agent hub'ga O'ZI ulanadi (tashqi port ochilmaydi, NAT ortida ishlayveradi).
Uzilsa qayta ulanadi. Claude sessiyalari shu kompyuterda qoladi — agent
faqat mahalliy `claude` CLI ni ishga tushiradi.

Ishlatish:  .venv\\Scripts\\python.exe agent.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import socket
import subprocess
import sys
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

import websockets

import config
import inbox
import protocol
import runner
import sessions

logging.basicConfig(
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        RotatingFileHandler(
            config.BASE_DIR / "agent.log", maxBytes=5 * 1024 * 1024,
            backupCount=2, encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("claude-agent")

ID_FILE = config.BASE_DIR / "agent_id.txt"
RECONNECT_MIN = 3
RECONNECT_MAX = 60
PING_INTERVAL = 25


def stable_agent_id() -> str:
    """Kompyuterni bir xil tanish uchun barqaror ID (faylda saqlanadi)."""
    if ID_FILE.exists():
        value = ID_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = uuid.uuid4().hex[:16]
    ID_FILE.write_text(value, encoding="utf-8")
    return value


def claude_version() -> str:
    try:
        exe = config.CLAUDE_BIN
        argv = ([os.environ.get("COMSPEC", "cmd.exe"), "/c", exe, "--version"]
                if exe.lower().endswith((".cmd", ".bat")) else [exe, "--version"])
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return (out.stdout or "").strip().splitlines()[0] if out.stdout else "?"
    except Exception:
        return "?"


def machine_info() -> dict:
    projects = sessions.list_projects()
    return {
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.release()}",
        "claude_version": claude_version(),
        "project_count": len(projects),
        "session_count": sum(len(p.sessions) for p in projects),
        "permission_mode": config.PERMISSION_MODE,
    }


class Agent:
    def __init__(self, hub_url: str, token: str, name: str) -> None:
        self.hub_url = hub_url
        self.token = token
        self.name = name
        self.agent_id = stable_agent_id()
        self._ws = None
        self._runs: dict[str, runner.ClaudeRun] = {}
        self._send_lock = asyncio.Lock()

    # -- yuborish ---------------------------------------------------------
    async def send(self, message: dict) -> None:
        ws = self._ws
        if ws is None:
            return
        async with self._send_lock:
            try:
                await ws.send(protocol.dumps(message))
            except Exception as exc:
                log.debug("Yuborilmadi: %s", exc)

    # -- so'rovlarni bajarish ---------------------------------------------
    async def handle_req(self, msg: dict) -> None:
        rid = msg.get("id") or ""
        op = msg.get("op") or ""
        args = msg.get("args") or {}

        try:
            if op == protocol.OP_INFO:
                await self.send(protocol.res(rid, True, machine_info()))

            elif op == protocol.OP_LIST_PROJECTS:
                data = [
                    {
                        "key": p.key,
                        "name": p.name,
                        "cwd": p.cwd,
                        "sessions": len(p.sessions),
                        "mtime": p.mtime,
                    }
                    for p in sessions.list_projects()
                ]
                await self.send(protocol.res(rid, True, data))

            elif op == protocol.OP_LIST_SESSIONS:
                project = args.get("project")
                limit = int(args.get("limit") or 10)
                if project:
                    proj = sessions.find_project(project)
                    items = proj.sessions[:limit] if proj else []
                else:
                    items = sessions.recent_sessions(limit)
                # Ochiq sessiyalarni bir marta hisoblaymiz (har biri uchun
                # alohida emas — PID tekshiruvi arzon emas).
                live = sessions.open_sessions()
                data = [
                    {
                        "session_id": s.session_id,
                        "label": s.label,
                        "cwd": s.cwd,
                        "project_name": s.project_name,
                        "project_key": s.project_key,
                        "user_turns": s.user_turns,
                        "mtime": s.mtime,
                        "is_open": s.session_id in live,
                    }
                    for s in items
                ]
                await self.send(protocol.res(rid, True, data))

            elif op == protocol.OP_SESSION_TAIL:
                info = sessions.find_session(args.get("session_id") or "")
                if info is None:
                    await self.send(protocol.res(rid, False, error="Sessiya topilmadi"))
                else:
                    rows = sessions.transcript_tail(info.path, int(args.get("turns") or 2))
                    await self.send(protocol.res(rid, True, rows))

            elif op == protocol.OP_NOISY:
                data = [
                    {
                        "session_id": s.session_id,
                        "label": s.label,
                        "project_name": s.project_name,
                        "size": s.size,
                        "mtime": s.mtime,
                    }
                    for s in sessions.noisy_sessions()
                ]
                await self.send(protocol.res(rid, True, data))

            elif op == protocol.OP_CLEANUP:
                # Faqat "shovqin" deb belgilangan sessiyalar o'chiriladi —
                # ro'yxatni hub emas, agentning o'zi qaytadan hisoblaydi,
                # shuning uchun tarmoq orqali begona ID kelib qololmaydi.
                deleted = freed = 0
                for info in sessions.noisy_sessions():
                    try:
                        size = info.size
                        info.path.unlink()
                        sessions._cache.pop(str(info.path), None)
                        deleted += 1
                        freed += size
                    except OSError:
                        continue
                log.info("Tozalandi: %d sessiya, %d bayt", deleted, freed)
                await self.send(protocol.res(rid, True,
                                             {"deleted": deleted, "freed": freed}))

            elif op == protocol.OP_SESSION_STATE:
                # Sessiya shu kompyuterda Claude Code ilovasida
                # ochiq turganini aniqlaydi.
                await self.send(protocol.res(
                    rid, True,
                    sessions.session_state(args.get('session_id') or '')))

            elif op == protocol.OP_RUN:
                asyncio.create_task(self.do_run(rid, args))

            elif op == protocol.OP_CANCEL:
                target = args.get("task") or ""
                run = self._runs.get(target)
                if run is not None:
                    run.cancel()
                    await self.send(protocol.res(rid, True, {"cancelled": True}))
                else:
                    await self.send(protocol.res(rid, False, error="Vazifa topilmadi"))

            else:
                await self.send(protocol.res(rid, False, error=f"Noma'lum op: {op}"))

        except Exception as exc:
            log.exception("So'rov xatosi: %s", op)
            await self.send(protocol.res(rid, False, error=str(exc)))

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

        async def on_progress(kind: str, text: str) -> None:
            await self.send(protocol.ev(rid, kind, text))

        try:
            result = await run.run(on_progress)
            payload = {
                "ok": result.ok,
                "session_id": result.session_id,
                "text": result.text,
                "error": result.error,
                "duration_ms": result.duration_ms,
                "cost_usd": result.cost_usd,
                "num_turns": result.num_turns,
                "model": result.model,
                "cancelled": result.cancelled,
                "tools_used": result.tools_used,
            }
        except Exception as exc:
            log.exception("Vazifa xatosi")
            payload = {"ok": False, "error": str(exc), "session_id": "",
                       "text": "", "duration_ms": 0, "cost_usd": 0.0,
                       "num_turns": 0, "model": "", "cancelled": False,
                       "tools_used": []}
        finally:
            self._runs.pop(rid, None)

        await self.send(protocol.done(rid, payload))

    # -- ulanish halqasi --------------------------------------------------
    async def session(self) -> None:
        log.info("Hub'ga ulanyapmiz: %s", self.hub_url)
        async with websockets.connect(
            self.hub_url,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_INTERVAL * 2,
            max_size=32 * 1024 * 1024,
            open_timeout=30,
        ) as ws:
            self._ws = ws
            await self.send(protocol.hello(
                self.token, self.agent_id, self.name, machine_info()
            ))

            first = protocol.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if first.get("t") == protocol.ERROR:
                raise RuntimeError(f"Hub rad etdi: {first.get('error')}")
            if first.get("t") != protocol.WELCOME:
                raise RuntimeError(f"Kutilmagan javob: {first.get('t')}")

            log.info("Ulandi. Hub: %s, agent nomi: %s",
                     first.get("hub"), self.name)

            async for raw in ws:
                try:
                    msg = protocol.loads(raw)
                except Exception:
                    continue
                kind = msg.get("t")
                if kind == protocol.REQ:
                    await self.handle_req(msg)
                elif kind == protocol.PING:
                    await self.send({"t": protocol.PONG})

    async def run_forever(self) -> None:
        delay = RECONNECT_MIN
        while True:
            try:
                await self.session()
                delay = RECONNECT_MIN
                log.warning("Hub bilan aloqa uzildi.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Ulanmadi (%s): %s", type(exc).__name__,
                            str(exc)[:150])
            finally:
                self._ws = None
                for run in list(self._runs.values()):
                    run.cancel()
                self._runs.clear()

            log.info("%.0f sekunddan keyin qayta urinaman…", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 1.7, RECONNECT_MAX)


def main() -> None:
    hub_url = os.getenv("AGENT_HUB_URL", "").strip()
    token = os.getenv("AGENT_TOKEN", "").strip()
    name = os.getenv("AGENT_NAME", "").strip() or socket.gethostname()

    problems = []
    if not hub_url:
        problems.append("AGENT_HUB_URL kiritilmagan (.env)")
    if not token:
        problems.append("AGENT_TOKEN kiritilmagan (.env)")
    if problems:
        for p in problems:
            log.error(p)
        raise SystemExit(1)

    removed = inbox.cleanup()
    if removed:
        log.info("Inbox tozalandi: %d eski papka", removed)

    agent = Agent(hub_url, token, name)
    log.info("Agent: %s (%s)", name, agent.agent_id)
    log.info("Claude CLI: %s", config.CLAUDE_BIN)
    log.info("Ruxsat rejimi: %s", config.PERMISSION_MODE)

    try:
        asyncio.run(agent.run_forever())
    except KeyboardInterrupt:
        log.info("To'xtatildi.")


if __name__ == "__main__":
    main()
