"""Index files must survive a failed write.

Rebuilding an index means re-probing every file in the library, which over a
network share can take hours. A crash, a full disk, or a dropped share part-way
through a write must therefore leave the previous index intact rather than a
truncated file.
"""

import json

import pytest

from media_agent.index import write_json_atomic

PAYLOAD = {"movies": [{"name": "The Matrix (1999).mkv", "resolution_class": "1080p"}]}


def test_writes_the_data(tmp_path):
    target = tmp_path / "movies.json"
    write_json_atomic(target, PAYLOAD)
    assert json.loads(target.read_text(encoding='utf-8')) == PAYLOAD


def test_overwrites_cleanly(tmp_path):
    target = tmp_path / "movies.json"
    write_json_atomic(target, {"movies": [{"name": "old"}]})
    write_json_atomic(target, PAYLOAD)
    assert json.loads(target.read_text(encoding='utf-8')) == PAYLOAD


def test_failed_write_leaves_previous_index_intact(tmp_path, monkeypatch):
    """The whole point: a mid-write failure must not destroy the old index."""
    target = tmp_path / "movies.json"
    write_json_atomic(target, PAYLOAD)
    original = target.read_text(encoding='utf-8')

    def explode(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(json, 'dump', explode)

    with pytest.raises(OSError):
        write_json_atomic(target, {"movies": [{"name": "replacement"}]})

    assert target.read_text(encoding='utf-8') == original, (
        "the previous index was damaged by a failed write"
    )


def test_failed_write_leaves_no_temp_file(tmp_path, monkeypatch):
    """A stray .tmp file next to the index would just confuse people."""
    target = tmp_path / "movies.json"
    write_json_atomic(target, PAYLOAD)

    def explode(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(json, 'dump', explode)
    with pytest.raises(OSError):
        write_json_atomic(target, PAYLOAD)

    assert list(tmp_path.glob("*.tmp")) == []


def test_unicode_survives_round_trip(tmp_path):
    """Filenames with accents and CJK must not be mangled into escapes."""
    payload = {"movies": [{"name": "Amélie (2001).mkv"}, {"name": "千と千尋.mkv"}]}
    target = tmp_path / "movies.json"
    write_json_atomic(target, payload)
    assert json.loads(target.read_text(encoding='utf-8')) == payload
    assert "Amélie" in target.read_text(encoding='utf-8')
