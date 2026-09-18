"""Claude Code CLI ni subprocess sifatida ishga tushirish va oqimni o'qish.

`claude -p --output-format stream-json` har bir hodisani alohida JSON qator
qilib chiqaradi. Shu qatorlarni o'qib, jarayonni Telegramga uzatamiz.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import config

ProgressCb = Callable[[str, str], Awaitable[None]]

# Bitta JSON qator juda uzun bo'lishi mumkin (katta tool natijalari).
STREAM_LIMIT = 32 * 1024 * 1024


@dataclass
class RunResult:
    ok: bool = False
    session_id: str = ""
    text: str = ""
    error: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    model: str = ""
    cancelled: bool = False
    tools_used: list[str] = field(default_factory=list)


def _short(value: object, limit: int = 70) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_tool(name: str, tool_input: dict) -> str:
    """Tool chaqiruvini bitta qatorlik tushunarli matnga aylantiradi."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        path = str(ti.get("file_path") or ti.get("notebook_path") or "")
        return f"{name} {Path(path).name}" if path else name
    if name in ("Bash", "PowerShell"):
        return f"{name}: {_short(ti.get('command'))}"
    if name == "Grep":
        where = ti.get("path") or ti.get("glob") or ""
        return f"Grep {_short(ti.get('pattern'), 40)}" + (f" ({Path(str(where)).name})" if where else "")
    if name == "Glob":
        return f"Glob {_short(ti.get('pattern'), 40)}"
    if name in ("Task", "Agent"):
        return f"Agent: {_short(ti.get('description') or ti.get('prompt'), 50)}"
    if name in ("WebFetch", "WebSearch"):
        return f"{name}: {_short(ti.get('url') or ti.get('query'), 50)}"
    if name == "TodoWrite":
        todos = ti.get("todos") or []
        return f"Reja yangilandi ({len(todos)} punkt)"
    return name


def _blocks_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            out.append(block.get("text", ""))
    return "\n".join(out)


class ClaudeRun:
    """Bitta vazifa yurishi. `run()` tugaguncha `cancel()` chaqirsa bo'ladi."""

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

    # -- jarayonni qurish -------------------------------------------------
    def _argv(self) -> list[str]:
        args = [
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", self.permission_mode,
            "--model", self.model,
        ]
        if not self.persist:
            args += ["--no-session-persistence"]
        elif self.resume_id:
            args += ["--resume", self.resume_id]
        else:
            args += ["--session-id", self.session_id]
        for folder in self.add_dirs:
            args += ["--add-dir", folder]

        exe = config.CLAUDE_BIN
        if exe.lower().endswith((".cmd", ".bat")):
            # .cmd shim ni to'g'ridan-to'g'ri CreateProcess ishga tushira olmaydi.
            return [os.environ.get("COMSPEC", "cmd.exe"), "/c", exe, *args]
        return [exe, *args]

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        env["FORCE_COLOR"] = "0"
        env["CLAUDE_CODE_ENTRYPOINT"] = "telegram-bot"
        if config.CLAUDE_CODE_OAUTH_TOKEN:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = config.CLAUDE_CODE_OAUTH_TOKEN
        if config.ANTHROPIC_API_KEY:
            env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY
        return env

    # -- boshqaruv --------------------------------------------------------
    def cancel(self) -> None:
        self._cancelled = True
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        if os.name == "nt":
            # cmd.exe ni o'ldirish node bolasini o'ldirmaydi — butun daraxtni yopamiz.
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            proc.terminate()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # -- asosiy oqim ------------------------------------------------------
    async def run(self, on_progress: ProgressCb | None = None) -> RunResult:
        result = RunResult(session_id=self.session_id, model=self.model)

        async def emit(kind: str, text: str) -> None:
            if on_progress is not None:
                try:
                    await on_progress(kind, text)
                except Exception:  # progress xatosi vazifani to'xtatmasin
                    pass

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv(),
                cwd=self.cwd,
                env=self._env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=STREAM_LIMIT,
            )
        except FileNotFoundError:
            result.error = f"Claude CLI topilmadi: {config.CLAUDE_BIN}"
            return result
        except NotADirectoryError:
            result.error = f"Papka mavjud emas: {self.cwd}"
            return result

        proc = self._proc
        assert proc.stdin and proc.stdout and proc.stderr

        # Promptni argv orqali emas, stdin orqali beramiz: uzunlik chegarasi
        # va qo'shtirnoq muammolari bo'lmaydi.
        try:
            proc.stdin.write(self.prompt.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

        stderr_chunks: list[bytes] = []

        async def drain_stderr() -> None:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                stderr_chunks.append(chunk)

        stderr_task = asyncio.create_task(drain_stderr())
        assistant_text: list[str] = []

        try:
            await asyncio.wait_for(
                self._consume(proc, result, assistant_text, emit),
                timeout=config.TASK_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            self.cancel()
            result.error = f"Vaqt tugadi ({config.TASK_TIMEOUT_SEC} sekund)."
        except asyncio.CancelledError:
            self.cancel()
            result.cancelled = True
            raise
        finally:
            stderr_task.cancel()
            try:
                await proc.wait()
            except Exception:
                pass

        if self._cancelled:
            result.cancelled = True
            result.error = result.error or "Vazifa to'xtatildi."

        if not result.text:
            result.text = "\n".join(t for t in assistant_text if t.strip()).strip()

        if not result.ok and not result.error:
            stderr_text = b"".join(stderr_chunks).decode("utf-8", "replace").strip()
            if stderr_text:
                result.error = _short(stderr_text.splitlines()[-1], 300)
            elif proc.returncode:
                result.error = f"Claude {proc.returncode} kodi bilan tugadi."

        result.duration_ms = result.duration_ms or int((time.time() - self.started_at) * 1000)
        return result

    async def _consume(
        self,
        proc: asyncio.subprocess.Process,
        result: RunResult,
        assistant_text: list[str],
        emit: ProgressCb,
    ) -> None:
        assert proc.stdout
        pending_tools: dict[str, str] = {}

        while True:
            try:
                raw = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                # Juda uzun qator — o'tkazib yuboramiz.
                continue
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            subtype = event.get("subtype")

            if etype == "system" and subtype == "init":
                result.session_id = event.get("session_id") or result.session_id
                self.session_id = result.session_id
                result.model = event.get("model") or result.model
                await emit("start", f"Sessiya {result.session_id[:8]} · {result.model}")
                continue

            if etype == "system" and subtype == "api_retry":
                await emit(
                    "retry",
                    f"API qayta urinish ({event.get('error_status')}) "
                    f"{event.get('attempt')}/{event.get('max_retries')}",
                )
                continue

            if etype == "assistant":
                message = event.get("message") or {}
                for block in message.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text = (block.get("text") or "").strip()
                        if text:
                            assistant_text.append(text)
                            await emit("text", text)
                    elif btype == "tool_use":
                        name = block.get("name") or "Tool"
                        desc = describe_tool(name, block.get("input") or {})
                        pending_tools[block.get("id") or ""] = desc
                        result.tools_used.append(name)
                        await emit("tool", desc)
                continue

            if etype == "user":
                message = event.get("message") or {}
                for block in message.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result" and block.get("is_error"):
                        desc = pending_tools.get(block.get("tool_use_id") or "", "Tool")
                        await emit("tool_error", f"{desc} — xato")
                continue

            if etype == "result":
                result.duration_ms = int(event.get("duration_ms") or 0)
                result.cost_usd = float(event.get("total_cost_usd") or 0.0)
                result.num_turns = int(event.get("num_turns") or 0)
                result.session_id = event.get("session_id") or result.session_id
                text = (event.get("result") or "").strip()
                if event.get("is_error"):
                    result.ok = False
                    result.error = text or "Noma'lum xato."
                else:
                    result.ok = True
                    result.text = text
                break


def build_prompt(user_text: str) -> str:
    """Telegramdan kelgan matnni promptga aylantiradi."""
    return user_text.strip()
