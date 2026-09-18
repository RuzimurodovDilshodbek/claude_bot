import asyncio
import json

import pytest

import bot


class FakeMessage:
    def __init__(self):
        self.edits: list[str] = []

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(bot, "EDIT_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(bot, "TICK_SEC", 0.01)


def _progress():
    msg = FakeMessage()
    return bot.Progress(msg, "vazifa"), msg


def test_tool_lifecycle_now_line():
    p, msg = _progress()

    async def go():
        await p("tool_start", "Read")
        assert "▶ Read …" in msg.edits[-1]
        await p("tool", "Read pytest.ini")
        assert "🔧 Read pytest.ini" in msg.edits[-1] and "▶ Read pytest.ini" in msg.edits[-1]
        await p("tool_done", "Read pytest.ini")
        assert "✅ Read pytest.ini" in msg.edits[-1] and "▶" not in msg.edits[-1]
        assert p.tool_count == 1

    asyncio.run(go())


def test_tool_without_tool_start_still_shows_now_line():
    p, msg = _progress()

    async def go():
        await p("tool", "Bash: npm test")
        assert "▶ Bash: npm test" in msg.edits[-1]
        await p("text", "tayyor")
        assert "▶" not in msg.edits[-1] and "💬 tayyor" in msg.edits[-1]

    asyncio.run(go())


def test_typing_and_thinking_lines():
    p, msg = _progress()

    async def go():
        await p("thinking", "120")
        assert "🤔 O'ylayapti…" in msg.edits[-1]
        await p("typing", json.dumps({"chars": 850, "tail": "…oxiri"}))
        assert "✍️ Javob yozilmoqda… 850 belgi" in msg.edits[-1]
        assert "🤔" not in msg.edits[-1]
        await p("text", "to'liq javob")
        assert "✍️" not in msg.edits[-1]

    asyncio.run(go())


def test_tool_error_clears_now_line():
    p, msg = _progress()

    async def go():
        await p("tool_start", "Bash")
        await p("tool_error", "Bash: ls — xato")
        assert "⚠️ Bash: ls — xato" in msg.edits[-1] and "▶" not in msg.edits[-1]

    asyncio.run(go())


def test_ticker_calls_flush_periodically():
    # Testda matn har tikda bir xil (0 s) bo'lgani uchun tahrir soni emas,
    # flush chaqiruvlari sanaladi — ishda vaqt qatori har tikda o'zgaradi.
    p, msg = _progress()
    calls = []

    async def counting_flush(force=False):
        calls.append(1)

    p.flush = counting_flush

    async def go():
        p.start_ticker()
        await asyncio.sleep(0.06)
        await p.stop()

    asyncio.run(go())
    assert len(calls) >= 2
    assert p._ticker is None


def test_edit_throttle(monkeypatch):
    monkeypatch.setattr(bot, "EDIT_MIN_INTERVAL", 100.0)
    p, msg = _progress()

    async def go():
        await p("tool", "a")
        await p("tool", "b")

    asyncio.run(go())
    assert len(msg.edits) == 1
