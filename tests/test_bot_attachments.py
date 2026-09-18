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


class FakeFile:
    def __init__(self, data: bytes):
        self._data = data

    async def download_as_bytearray(self):
        return bytearray(self._data)


class FakePhoto:
    def __init__(self, width: int, data: bytes = b"jpegdata"):
        self.width = width
        self.file_size = len(data)
        self._data = data

    async def get_file(self):
        return FakeFile(self._data)


class FakeDoc:
    def __init__(self, file_name, mime_type, data: bytes = b"log", file_size=None):
        self.file_name = file_name
        self.mime_type = mime_type
        self.file_size = len(data) if file_size is None else file_size
        self._data = data

    async def get_file(self):
        if self.file_size > attachments.MAX_TOTAL_BYTES:
            raise AssertionError("katta faylni yuklamasligi kerak")
        return FakeFile(self._data)


class FakeMediaMsg(FakeMsg):
    def __init__(self, message_id=1, photo=(), document=None, caption=None):
        super().__init__()
        self.message_id = message_id
        self.photo = list(photo)
        self.document = document
        self.caption = caption


def test_download_photo_picks_largest_and_names_by_message_id():
    msg = FakeMediaMsg(message_id=512, photo=[FakePhoto(90, b"small"), FakePhoto(800)])
    att = asyncio.run(bot._download_attachment(msg))
    assert (att.name, att.mime, att.data) == ("photo_512.jpg", "image/jpeg", b"jpegdata")


def test_download_document_sanitizes_name_and_keeps_mime():
    msg = FakeMediaMsg(message_id=7, document=FakeDoc("../x/err:or.log", "text/plain"))
    att = asyncio.run(bot._download_attachment(msg))
    assert (att.name, att.mime, att.data) == ("err_or.log", "text/plain", b"log")


def test_download_document_without_name_or_mime():
    msg = FakeMediaMsg(message_id=9, document=FakeDoc(None, None))
    att = asyncio.run(bot._download_attachment(msg))
    assert (att.name, att.mime) == ("file_9", "application/octet-stream")


def test_download_rejects_too_big_before_downloading():
    doc = FakeDoc("big.zip", "application/zip", file_size=attachments.MAX_TOTAL_BYTES + 1)
    msg = FakeMediaMsg(document=doc)
    assert asyncio.run(bot._download_attachment(msg)) is None
    assert msg.texts and "20 MB" in msg.texts[0]


def test_download_failure_tells_user():
    class Broken(FakeDoc):
        async def get_file(self):
            raise RuntimeError("tarmoq")

    msg = FakeMediaMsg(document=Broken("a.txt", "text/plain"))
    assert asyncio.run(bot._download_attachment(msg)) is None
    assert msg.texts == ["📎 Faylni yuklab bo'lmadi, qayta yuboring."]


def test_message_without_media_is_ignored():
    assert asyncio.run(bot._download_attachment(FakeMediaMsg())) is None


def test_caption_launches_after_debounce(monkeypatch):
    calls = []

    async def fake_launch(ctx, scope, prompt, *, voice_input=False, reply_to=None, files=None):
        calls.append((scope, prompt, [f.name for f in files]))

    monkeypatch.setattr(bot, "_launch", fake_launch)

    async def run():
        box = attachments.add(SCOPE, _att(), "xatoni tuzat")
        bot._schedule_box(None, SCOPE, box, FakeMsg())
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert calls == [(SCOPE, "xatoni tuzat", ["photo_1.jpg"])]
    assert attachments.peek(SCOPE) is None


def test_album_items_during_debounce_are_included(monkeypatch):
    calls = []

    async def fake_launch(ctx, scope, prompt, *, voice_input=False, reply_to=None, files=None):
        calls.append([f.name for f in files])

    monkeypatch.setattr(bot, "_launch", fake_launch)

    async def run():
        box = attachments.add(SCOPE, _att(), "albom")
        bot._schedule_box(None, SCOPE, box, FakeMsg())
        box = attachments.add(SCOPE, _att("photo_2.jpg"))
        bot._schedule_box(None, SCOPE, box, FakeMsg())
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert calls == [["photo_1.jpg", "photo_2.jpg"]]


def test_without_caption_posts_notice_and_updates_it():
    async def run():
        msg = FakeMsg()
        box = attachments.add(SCOPE, _att())
        bot._schedule_box(None, SCOPE, box, msg)
        await asyncio.sleep(0.02)
        assert msg.texts == ["📎 1 ta rasm kutmoqda — vazifani yozing yoki ovoz yuboring."]
        box = attachments.add(SCOPE, attachments.Attachment("a.log", "text/plain", b"1"))
        bot._schedule_box(None, SCOPE, box, msg)
        await asyncio.sleep(0.02)
        assert box.notice.edits == ["📎 1 ta rasm, 1 ta fayl kutmoqda — vazifani yozing yoki ovoz yuboring."]
        box.timer.cancel()

    asyncio.run(run())


def test_notice_expires(monkeypatch):
    monkeypatch.setattr(attachments, "TTL_SEC", 0.02)

    async def run():
        msg = FakeMsg()
        box = attachments.add(SCOPE, _att())
        bot._schedule_box(None, SCOPE, box, msg)
        await asyncio.sleep(0.08)
        assert box.notice.edits == ["⌛ Biriktirmalar eskirdi — qayta yuboring."]
        assert attachments.peek(SCOPE) is None

    asyncio.run(run())
