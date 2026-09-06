"""Agentlarni qabul qiladigan WebSocket hub (serverda ishlaydi).

Bot bu modulga oddiy `async` chaqiruvlar qiladi, hub esa ularni tarmoq
orqali kerakli kompyuterga uzatadi:

    await hub.request(agent_id, OP_LIST_SESSIONS, {...})   -> ma'lumot
    await hub.run(agent_id, {...}, on_progress)            -> vazifa natijasi

Agentlar hub'ga o'zi ulanadi, shuning uchun kompyuterlarda hech qanday
port ochilmaydi.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
from typing import Awaitable, Callable

import websockets
from websockets.asyncio.server import ServerConnection, serve

import protocol

log = logging.getLogger("claude-tg.hub")

ProgressCb = Callable[[str, str], Awaitable[None]]

REQUEST_TIMEOUT = 45
RUN_TIMEOUT = 3600


class Machine:
    """Ulangan bitta kompyuter."""

    def __init__(self, ws: ServerConnection, info: protocol.MachineInfo) -> None:
        self.ws = ws
        self.info = info
        self._pending: dict[str, asyncio.Future] = {}
        self._progress: dict[str, ProgressCb] = {}
        self._done: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    @property
    def agent_id(self) -> str:
        return self.info.agent_id

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def busy_tasks(self) -> int:
        return len(self._done)

    async def _send(self, message: dict) -> None:
        async with self._lock:
            await self.ws.send(protocol.dumps(message))

    # -- so'rov-javob -----------------------------------------------------
    async def request(self, op: str, args: dict | None = None,
                      timeout: float = REQUEST_TIMEOUT):
        message = protocol.req(op, args)
        rid = message["id"]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        try:
            await self._send(message)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    # -- vazifa (oqim bilan) ----------------------------------------------
    async def run(self, args: dict, on_progress: ProgressCb | None = None,
                  timeout: float = RUN_TIMEOUT, task_id: str | None = None) -> dict:
        # `task_id` ni tashqaridan berish mumkin — bot uni bekor qilish uchun
        # vazifa boshlanishidan oldin bilishi kerak.
        message = protocol.req(protocol.OP_RUN, args, rid=task_id)
        rid = message["id"]
        loop = asyncio.get_running_loop()
        done_future: asyncio.Future = loop.create_future()
        self._done[rid] = done_future
        if on_progress is not None:
            self._progress[rid] = on_progress
        try:
            await self._send(message)
            return await asyncio.wait_for(done_future, timeout=timeout)
        except asyncio.TimeoutError:
            await self.cancel(rid)
            raise
        finally:
            self._done.pop(rid, None)
            self._progress.pop(rid, None)

    async def cancel(self, task_id: str) -> None:
        try:
            await self._send(protocol.req(protocol.OP_CANCEL, {"task": task_id}))
        except Exception as exc:
            log.debug("Cancel yuborilmadi: %s", exc)

    # -- kiruvchi xabarlar ------------------------------------------------
    async def on_message(self, msg: dict) -> None:
        kind = msg.get("t")
        rid = msg.get("id") or ""

        if kind == protocol.RES:
            future = self._pending.get(rid)
            if future and not future.done():
                if msg.get("ok"):
                    future.set_result(msg.get("data"))
                else:
                    future.set_exception(
                        RuntimeError(msg.get("error") or "Agent xatosi")
                    )

        elif kind == protocol.EV:
            callback = self._progress.get(rid)
            if callback is not None:
                try:
                    await callback(msg.get("kind") or "", msg.get("text") or "")
                except Exception:
                    pass

        elif kind == protocol.DONE:
            future = self._done.get(rid)
            if future and not future.done():
                future.set_result(msg.get("result") or {})

        elif kind == protocol.PONG:
            self.info.last_seen = time.time()

    def abort_all(self, reason: str) -> None:
        """Aloqa uzilganda kutayotgan hammasini xato bilan yopamiz."""
        exc = ConnectionError(reason)
        for store in (self._pending, self._done):
            for future in list(store.values()):
                if not future.done():
                    future.set_exception(exc)
            store.clear()
        self._progress.clear()


class Hub:
    def __init__(self, token: str, name: str = "claude-hub") -> None:
        self.token = token
        self.name = name
        self._machines: dict[str, Machine] = {}
        # Uzilgan kompyuterning nomini eslab qolamiz — "ishxona qayta
        # ulanishini kutyapmiz" deyish uchun ID emas, nom kerak.
        self._known_names: dict[str, str] = {}
        self._server = None

    def known_name(self, agent_id: str) -> str:
        machine = self._machines.get(agent_id)
        if machine is not None:
            return machine.name
        return self._known_names.get(agent_id, "")

    # -- botga ko'rinadigan API -------------------------------------------
    def machines(self) -> list[Machine]:
        return sorted(self._machines.values(), key=lambda m: m.name.lower())

    def get(self, agent_id: str) -> Machine | None:
        return self._machines.get(agent_id)

    def find(self, needle: str) -> Machine | None:
        """ID yoki nom bo'yicha topadi (nom katta-kichik harfga bog'liq emas)."""
        if needle in self._machines:
            return self._machines[needle]
        low = (needle or "").strip().lower()
        for machine in self._machines.values():
            if machine.name.lower() == low or machine.agent_id.startswith(low):
                return machine
        return None

    def only_one(self) -> Machine | None:
        """Bitta kompyuter ulangan bo'lsa — o'shani qaytaradi."""
        items = list(self._machines.values())
        return items[0] if len(items) == 1 else None

    async def request(self, agent_id: str, op: str, args: dict | None = None,
                      timeout: float = REQUEST_TIMEOUT):
        machine = self.get(agent_id)
        if machine is None:
            raise ConnectionError("Kompyuter ulanmagan.")
        return await machine.request(op, args, timeout)

    async def run(self, agent_id: str, args: dict,
                  on_progress: ProgressCb | None = None,
                  task_id: str | None = None) -> dict:
        machine = self.get(agent_id)
        if machine is None:
            raise ConnectionError("Kompyuter ulanmagan.")
        return await machine.run(args, on_progress, task_id=task_id)

    # -- ulanishlarni boshqarish ------------------------------------------
    async def _handle(self, ws: ServerConnection) -> None:
        peer = getattr(ws, "remote_address", None)
        machine: Machine | None = None
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            hello = protocol.loads(raw)
        except Exception:
            await ws.close()
            return

        if hello.get("t") != protocol.HELLO:
            await ws.send(protocol.dumps(protocol.error("hello kutilgan edi")))
            await ws.close()
            return

        if not hmac.compare_digest(str(hello.get("token") or ""), self.token):
            log.warning("Noto'g'ri token bilan urinish: %s", peer)
            await ws.send(protocol.dumps(protocol.error("Token noto'g'ri")))
            await ws.close()
            return

        info = protocol.MachineInfo.from_dict({
            **(hello.get("info") or {}),
            "agent_id": hello.get("agent_id") or "",
            "name": hello.get("name") or "?",
        })
        if not info.agent_id:
            await ws.send(protocol.dumps(protocol.error("agent_id bo'sh")))
            await ws.close()
            return

        # Eski ulanish qolgan bo'lsa (masalan tarmoq uzilib qayta ulangan) —
        # uni yopamiz, aks holda ikkita yozuv qoladi.
        old = self._machines.get(info.agent_id)
        if old is not None:
            old.abort_all("Kompyuter qayta ulandi.")
            try:
                await old.ws.close()
            except Exception:
                pass

        machine = Machine(ws, info)
        self._machines[info.agent_id] = machine
        self._known_names[info.agent_id] = info.name
        await ws.send(protocol.dumps(protocol.welcome(self.name)))
        log.info("Kompyuter ulandi: %s (%s, %s) — %d loyiha, %d sessiya",
                 info.name, info.agent_id[:8], info.platform,
                 info.project_count, info.session_count)

        try:
            async for raw in ws:
                try:
                    msg = protocol.loads(raw)
                except Exception:
                    continue
                machine.info.last_seen = time.time()
                await machine.on_message(msg)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            log.warning("Ulanish xatosi (%s): %s", info.name, exc)
        finally:
            machine.abort_all("Kompyuter bilan aloqa uzildi.")
            if self._machines.get(info.agent_id) is machine:
                del self._machines[info.agent_id]
            log.info("Kompyuter uzildi: %s", info.name)

    async def start(self, host: str, port: int) -> None:
        self._server = await serve(
            self._handle, host, port,
            ping_interval=25, ping_timeout=60,
            max_size=32 * 1024 * 1024,
        )
        log.info("Hub tinglayapti: %s:%d", host, port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
