import asyncio
import json

import runner


def _feed(lines):
    reader = asyncio.StreamReader()
    for obj in lines:
        reader.feed_data((json.dumps(obj) + "\n").encode("utf-8"))
    reader.feed_eof()
    return reader


class FakeProc:
    def __init__(self, lines):
        self.stdout = _feed(lines)


def _consume(lines, interval=0.0):
    run = runner.ClaudeRun(prompt="x", cwd=".")
    result = runner.RunResult()
    events = []

    async def emit(kind, text):
        events.append((kind, text))

    async def go():
        # StreamReader hodisa halqasiga bog'liq — shu yerda yaratiladi.
        await run._consume(FakeProc(lines), result, [], emit)

    runner_interval = runner.STREAM_INTERVAL
    runner.STREAM_INTERVAL = interval
    try:
        asyncio.run(go())
    finally:
        runner.STREAM_INTERVAL = runner_interval
    return run, result, events


def _se(event):
    return {"type": "stream_event", "event": event}


TOOL_ID = "toolu_1"
FULL_TOOL = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": TOOL_ID, "name": "Read",
     "input": {"file_path": "D:/x/pytest.ini"}}]}}
TOOL_OK = {"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": TOOL_ID, "content": "ok"}]}}
TOOL_ERR = {"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": TOOL_ID, "is_error": True, "content": "yo'q"}]}}
RESULT = {"type": "result", "subtype": "success", "result": "tayyor", "session_id": "s",
          "duration_ms": 5, "total_cost_usd": 0.0, "num_turns": 1}


def test_tool_lifecycle_events_in_order():
    _, result, events = _consume([
        _se({"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": TOOL_ID, "name": "Read", "input": {}}}),
        _se({"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": "{\"file_path\""}}),
        FULL_TOOL,
        TOOL_OK,
        RESULT,
    ])
    assert events == [("tool_start", "Read"), ("tool", "Read pytest.ini"),
                      ("tool_done", "Read pytest.ini")]
    assert result.ok and result.text == "tayyor"


def test_tool_error_still_reported():
    _, _, events = _consume([FULL_TOOL, TOOL_ERR, RESULT])
    assert events == [("tool", "Read pytest.ini"), ("tool_error", "Read pytest.ini — xato")]


def test_text_deltas_become_typing_events_then_text():
    _, _, events = _consume([
        _se({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        _se({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ab"}}),
        _se({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "cd"}}),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "abcd"}]}},
        RESULT,
    ])
    assert events[0] == ("typing", json.dumps({"chars": 2, "tail": "ab"}, ensure_ascii=False))
    assert events[1] == ("typing", json.dumps({"chars": 4, "tail": "abcd"}, ensure_ascii=False))
    assert events[2] == ("text", "abcd")


def test_typing_is_throttled_but_first_delta_is_immediate():
    _, _, events = _consume([
        _se({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        _se({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "a"}}),
        _se({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "b"}}),
        RESULT,
    ], interval=100.0)
    assert [k for k, _ in events] == ["typing"]


def test_thinking_deltas_become_thinking_events():
    _, _, events = _consume([
        _se({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
        _se({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmmm"}}),
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hmmm"}]}},
        RESULT,
    ])
    assert events == [("thinking", "4")]


def test_tail_keeps_last_80_chars():
    long = "x" * 100
    _, _, events = _consume([
        _se({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        _se({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": long}}),
        RESULT,
    ])
    payload = json.loads(events[0][1])
    assert payload == {"chars": 100, "tail": "x" * 80}


def test_result_sets_saw_result_flag():
    run, result, _ = _consume([RESULT])
    assert run._saw_result is True and result.ok


def test_argv_includes_partial_messages():
    assert "--include-partial-messages" in runner.ClaudeRun(prompt="x", cwd=".")._argv()
