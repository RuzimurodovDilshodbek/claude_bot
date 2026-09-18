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
