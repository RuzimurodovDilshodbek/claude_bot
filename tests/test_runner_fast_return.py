import asyncio
import sys
import time

import runner

SCRIPT = r'''
import json, sys, time
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s1", "model": "m"}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "result": "tayyor", "session_id": "s1",
                  "duration_ms": 1, "total_cost_usd": 0, "num_turns": 1}), flush=True)
time.sleep(2)
'''


def test_run_returns_right_after_result_event(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(SCRIPT, encoding="utf-8")
    monkeypatch.setattr(runner.ClaudeRun, "_argv", lambda self: [sys.executable, str(script)])

    async def go():
        run = runner.ClaudeRun(prompt="x", cwd=str(tmp_path), persist=False)
        t0 = time.monotonic()
        result = await run.run()
        elapsed = time.monotonic() - t0
        assert run._reaper is not None
        await run._reaper  # fon vazifasi jarayonni yig'ib oladi
        return result, elapsed

    result, elapsed = asyncio.run(go())
    assert result.ok and result.text == "tayyor"
    assert elapsed < 1.5, f"natijadan keyin {elapsed:.1f} s kutdi"
