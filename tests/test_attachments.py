import time

import pytest

import attachments as att_mod
from attachments import Attachment, TooLarge

SCOPE = (1, 0)


@pytest.fixture(autouse=True)
def clean():
    att_mod._boxes.clear()
    yield
    att_mod._boxes.clear()


def _img(n: int = 100, name: str = "photo_1.jpg") -> Attachment:
    return Attachment(name=name, mime="image/jpeg", data=b"x" * n)


def test_add_then_take_returns_items_in_order_with_caption():
    att_mod.add(SCOPE, _img(), caption="xatoni tuzat")
    att_mod.add(SCOPE, _img(name="photo_2.jpg"))
    box = att_mod.take(SCOPE)
    assert [a.name for a in box.items] == ["photo_1.jpg", "photo_2.jpg"]
    assert box.caption == "xatoni tuzat"
    assert att_mod.take(SCOPE) is None


def test_caption_from_later_album_item_is_kept():
    att_mod.add(SCOPE, _img())
    att_mod.add(SCOPE, _img(name="photo_2.jpg"), caption="  mana bu  ")
    assert att_mod.peek(SCOPE).caption == "mana bu"


def test_take_without_anything_is_none():
    assert att_mod.take(SCOPE) is None
    assert att_mod.peek(SCOPE) is None


def test_expired_box_is_dropped():
    box = att_mod.add(SCOPE, _img())
    box.updated_at = time.time() - att_mod.TTL_SEC - 1
    assert att_mod.take(SCOPE) is None


def test_new_attachment_after_expiry_starts_fresh_box():
    old = att_mod.add(SCOPE, _img(), caption="eski")
    old.updated_at = time.time() - att_mod.TTL_SEC - 1
    new = att_mod.add(SCOPE, _img(name="photo_9.jpg"))
    assert new is not old
    assert new.caption == ""
    assert [a.name for a in new.items] == ["photo_9.jpg"]


def test_total_size_limit_rejects_without_touching_box(monkeypatch):
    monkeypatch.setattr(att_mod, "MAX_TOTAL_BYTES", 250)
    att_mod.add(SCOPE, _img(200))
    with pytest.raises(TooLarge) as exc:
        att_mod.add(SCOPE, _img(100, name="big.jpg"))
    assert "big.jpg" in str(exc.value)
    assert len(att_mod.peek(SCOPE).items) == 1


def test_drop_only_removes_the_given_box():
    first = att_mod.add(SCOPE, _img())
    att_mod._boxes[SCOPE] = second = att_mod.PendingBox()
    assert att_mod.drop(SCOPE, first) is False
    assert att_mod.peek(SCOPE) is second
    assert att_mod.drop(SCOPE, second) is True
    assert att_mod.peek(SCOPE) is None


def test_clear_returns_box():
    box = att_mod.add(SCOPE, _img())
    assert att_mod.clear(SCOPE) is box
    assert att_mod.clear(SCOPE) is None


def test_summary_counts_images_and_files():
    items = [_img(), _img(), Attachment("a.log", "text/plain", b"x")]
    assert att_mod.summary(items) == "2 ta rasm, 1 ta fayl"
    assert att_mod.summary([_img()]) == "1 ta rasm"
    assert att_mod.summary([]) == "0 ta fayl"


def test_attachment_properties():
    a = Attachment("a.png", "image/png", b"12345")
    assert a.size == 5 and a.is_image
    assert not Attachment("a.log", "text/plain", b"").is_image
