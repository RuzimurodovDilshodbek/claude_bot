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
