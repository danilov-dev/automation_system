from app.protocol import encode, decode


def test_roundtrip():
    msg = {"type": "task", "task_id": "abc123",
           "command": "ping", "params": {"target": "8.8.8.8"}}
    assert decode(encode(msg)) == msg


def test_cyrillic_and_special_chars():
    msg = {"type": "report", "result": "всё ок, символ «#» и $"}
    assert decode(encode(msg)) == msg


def test_empty_line():
    assert decode(b"") is None
    assert decode(b"\n") is None
    assert decode(b"   \n") is None


def test_garbage_does_not_crash():
    out = decode(b"\x00\xff not json at all\n")
    assert out == {"type": "raw", "text": "\x00\\xff not json at all"}


def test_roundtrip_handles_newline_inside_string():
    # JSON экранирует \n внутри строк — decode не должен резать по нему
    msg = {"type": "report", "result": "line1\nline2"}
    encoded = encode(msg)
    assert encoded.count(b"\n") == 1
    assert decode(encoded) == msg