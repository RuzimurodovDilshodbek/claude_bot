import pytest

import protocol


@pytest.mark.parametrize("raw,expected", [
    ("error.log", "error.log"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\me\\Desktop\\shot.png", "shot.png"),
    ('we?ird:na*me<>.txt', "we_ird_na_me_.txt"),
    ("  .hidden ", "hidden"),
    ("", "file_7"),
    ("a" * 100 + ".png", "a" * 76 + ".png"),
])
def test_safe_name(raw, expected):
    assert protocol.safe_name(raw, "file_7") == expected


def test_pack_unpack_roundtrip():
    packed = protocol.pack_file("photo_1.jpg", "image/jpeg", b"\xff\xd8\x00abc")
    assert set(packed) == {"name", "mime", "data_b64"}
    assert protocol.unpack_file(packed) == ("photo_1.jpg", "image/jpeg", b"\xff\xd8\x00abc")


def test_pack_defaults_mime_and_unpack_tolerates_missing_fields():
    assert protocol.pack_file("a", "", b"")["mime"] == "application/octet-stream"
    assert protocol.unpack_file({}) == ("", "application/octet-stream", b"")
