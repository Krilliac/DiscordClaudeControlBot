from __future__ import annotations

from pathlib import Path

import pytest

from discord_claude_control.tools._helpers import (
    OUTPUT_TRUNCATE_AT,
    check_path_allowed,
    error_result,
    text_result,
    truncate_output,
)


def test_truncate_output_below_limit() -> None:
    text = "x" * 100
    assert truncate_output(text) == text


def test_truncate_output_above_limit() -> None:
    text = "y" * (OUTPUT_TRUNCATE_AT + 50)
    out = truncate_output(text)
    assert out.startswith("y" * OUTPUT_TRUNCATE_AT)
    assert "truncated" in out
    assert "50 more chars" in out


def test_truncate_custom_limit() -> None:
    assert truncate_output("abcdef", limit=3) == "abc\n[... truncated, 3 more chars ...]"


def test_text_result_shape() -> None:
    r = text_result("hello")
    assert r == {"content": [{"type": "text", "text": "hello"}]}


def test_error_result_shape() -> None:
    r = error_result("oops")
    assert r["is_error"] is True
    assert r["content"][0]["type"] == "text"
    assert "oops" in r["content"][0]["text"]
    assert r["content"][0]["text"].startswith("ERROR:")


def test_check_path_allowed_no_restrict(tmp_path: Path) -> None:
    # When restrict=False, anything is allowed.
    check_path_allowed(Path("/etc/passwd"), restrict=False, allow_roots=[])
    check_path_allowed(tmp_path / "x", restrict=False, allow_roots=[])


def test_check_path_allowed_with_root(tmp_path: Path) -> None:
    inside = tmp_path / "a" / "b" / "file.txt"
    inside.parent.mkdir(parents=True)
    inside.touch()
    check_path_allowed(inside, restrict=True, allow_roots=[str(tmp_path)])


def test_check_path_allowed_outside_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "elsewhere" / "file.txt"
    with pytest.raises(PermissionError, match="outside allow_roots"):
        check_path_allowed(outside, restrict=True, allow_roots=[str(tmp_path)])


def test_check_path_allowed_multiple_roots(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    target = root_b / "x.txt"
    target.touch()
    check_path_allowed(target, restrict=True, allow_roots=[str(root_a), str(root_b)])
