import asyncio

import machines
from attachments import Attachment


class FakeHub:
    def __init__(self):
        self.calls = []

    async def run(self, agent_id, args, on_progress=None, task_id=None):
        self.calls.append((agent_id, args, task_id))
        return {"ok": True}


def test_run_packs_attachments_as_base64():
    hub = FakeHub()
    machines.bind(hub)
    files = [Attachment("photo_1.jpg", "image/jpeg", b"\x00\x01")]
    asyncio.run(machines.run("a1", "p", "C:/x", None, "opus", task_id="t1", attachments=files))
    agent_id, args, task_id = hub.calls[0]
    assert (agent_id, task_id) == ("a1", "t1")
    assert args["attachments"] == [{"name": "photo_1.jpg", "mime": "image/jpeg", "data_b64": "AAE="}]


def test_run_without_attachments_sends_empty_list():
    hub = FakeHub()
    machines.bind(hub)
    asyncio.run(machines.run("a1", "p", "C:/x", "s", "opus"))
    assert hub.calls[0][1]["attachments"] == []
