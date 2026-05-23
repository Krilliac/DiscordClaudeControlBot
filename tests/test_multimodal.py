"""Tests for the multimodal user-turn streaming path."""

from __future__ import annotations

import base64

import pytest

from discord_claude_control.agent import ImageBlob, UserTurn, _stream_multimodal


def test_user_turn_from_str() -> None:
    t = UserTurn.from_any("hello")
    assert t.text == "hello"
    assert t.images == ()


def test_user_turn_from_self() -> None:
    img = ImageBlob(mime_type="image/png", data=b"\x89PNG")
    src = UserTurn(text="hi", images=(img,))
    t = UserTurn.from_any(src)
    assert t is src


@pytest.mark.asyncio
async def test_stream_multimodal_with_image() -> None:
    img = ImageBlob(mime_type="image/png", data=b"\x89PNG\r\n\x1a\n")
    turn = UserTurn(text="describe this", images=(img,))

    messages = [m async for m in _stream_multimodal(turn)]
    assert len(messages) == 1
    msg = messages[0]
    assert msg["type"] == "user"
    assert msg["parent_tool_use_id"] is None
    content = msg["message"]["content"]
    assert len(content) == 2
    # image first, text after
    assert content[0]["type"] == "image"
    assert content[0]["source"]["type"] == "base64"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[0]["source"]["data"] == base64.b64encode(img.data).decode("ascii")
    assert content[1] == {"type": "text", "text": "describe this"}


@pytest.mark.asyncio
async def test_stream_multimodal_text_only_falls_through() -> None:
    # text-only would normally use the string path; if someone constructs
    # a UserTurn with no images we still produce a valid message dict.
    turn = UserTurn(text="hi")
    messages = [m async for m in _stream_multimodal(turn)]
    assert messages[0]["message"]["content"] == [{"type": "text", "text": "hi"}]


@pytest.mark.asyncio
async def test_stream_multimodal_empty_turn_has_placeholder() -> None:
    # Defensive: never emit an empty content list.
    turn = UserTurn(text="", images=())
    messages = [m async for m in _stream_multimodal(turn)]
    assert messages[0]["message"]["content"] == [{"type": "text", "text": "(empty turn)"}]


@pytest.mark.asyncio
async def test_stream_multimodal_multiple_images() -> None:
    imgs = (
        ImageBlob(mime_type="image/jpeg", data=b"a"),
        ImageBlob(mime_type="image/png", data=b"b"),
    )
    turn = UserTurn(text="compare", images=imgs)
    messages = [m async for m in _stream_multimodal(turn)]
    content = messages[0]["message"]["content"]
    # both images, in order, then text
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[1]["source"]["media_type"] == "image/png"
    assert content[2]["type"] == "text"
