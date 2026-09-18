import runner


def test_add_dir_flags_are_passed_in_order():
    run = runner.ClaudeRun(prompt="x", cwd=".", add_dirs=[r"D:\inbox\ab12", "/tmp/x"])
    argv = run._argv()
    first = argv.index("--add-dir")
    assert argv[first + 1] == r"D:\inbox\ab12"
    assert argv[first + 2:first + 4] == ["--add-dir", "/tmp/x"]


def test_no_add_dir_by_default():
    assert "--add-dir" not in runner.ClaudeRun(prompt="x", cwd=".")._argv()


def test_session_flags_unchanged():
    argv = runner.ClaudeRun(prompt="x", cwd=".", session_id="abc")._argv()
    assert argv[argv.index("--resume") + 1] == "abc"
    argv = runner.ClaudeRun(prompt="x", cwd=".", persist=False)._argv()
    assert "--no-session-persistence" in argv and "--resume" not in argv
