import os
import time

import inbox
import protocol


def test_save_writes_files_in_order_with_safe_names(tmp_path):
    items = [
        protocol.pack_file("photo_1.jpg", "image/jpeg", b"\xff\xd8abc"),
        protocol.pack_file("../error.log", "text/plain", b"line\n"),
        protocol.pack_file("", "", b"?"),
    ]
    saved = inbox.save("ab12cd", items, root=tmp_path)
    assert saved.folder == tmp_path / "ab12cd"
    assert [f.path.name for f in saved.files] == ["01_photo_1.jpg", "02_error.log", "03_file_3"]
    assert saved.files[0].path.read_bytes() == b"\xff\xd8abc"
    assert saved.files[1].size == 5 and saved.files[1].mime == "text/plain"
    assert saved.files[2].mime == "application/octet-stream"


def test_save_sanitizes_task_id(tmp_path):
    saved = inbox.save("../evil", [protocol.pack_file("a", "text/plain", b"1")], root=tmp_path)
    assert saved.folder == tmp_path / "evil"


def test_with_attachments_lists_paths_and_sizes(tmp_path):
    saved = inbox.save("t1", [protocol.pack_file("a.png", "image/png", b"x" * 2048)], root=tmp_path)
    text = inbox.with_attachments("xatoni tuzat", saved)
    assert text.startswith("[Foydalanuvchi Telegram orqali 1 ta fayl biriktirdi.")
    assert f"1. {saved.files[0].path}  (image/png, 2 KB)]" in text
    assert text.endswith("\n\nxatoni tuzat")


def test_empty_prompt_gets_default_text(tmp_path):
    saved = inbox.save("t2", [protocol.pack_file("a.png", "image/png", b"x")], root=tmp_path)
    assert inbox.with_attachments("   ", saved).endswith(
        "Biriktirilgan fayllarni ko'rib chiq va nima kerakligini ayt."
    )


def test_human_size():
    assert inbox.human_size(512) == "512 B"
    assert inbox.human_size(2048) == "2 KB"
    assert inbox.human_size(3 * 1024 * 1024 + 200 * 1024) == "3.2 MB"


def test_cleanup_removes_only_old_folders(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "f").write_bytes(b"1")
    new = tmp_path / "new"
    new.mkdir()
    past = time.time() - inbox.MAX_AGE_SEC - 60
    os.utime(old, (past, past))
    assert inbox.cleanup(root=tmp_path) == 1
    assert not old.exists() and new.exists()


def test_cleanup_without_inbox_dir(tmp_path):
    assert inbox.cleanup(root=tmp_path / "yoq") == 0
