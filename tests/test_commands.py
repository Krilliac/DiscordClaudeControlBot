"""Tests for the bang/slash command registry."""

from __future__ import annotations

import pytest

from discord_claude_control.commands import (
    COMMANDS,
    find_by_bang,
    render_detail,
    render_overview,
    resolve,
)


@pytest.fixture
def resolved() -> tuple:
    return resolve(stop="!stop", ping="ping", status="!status", stay="!stay")


def test_resolve_applies_overrides() -> None:
    r = resolve(stop="!halt", ping="alive", status="?how", stay="?stay")
    by_name = {c.spec.name: c.bang for c in r}
    assert by_name["stop"] == "!halt"
    assert by_name["ping"] == "alive"
    assert by_name["status"] == "?how"
    assert by_name["stay"] == "?stay"
    # non-overridable commands keep their built-in bang
    assert by_name["help"] == "!help"
    assert by_name["cost"] == "!cost"


def test_resolve_screenshot_has_no_bang() -> None:
    r = resolve(stop="!stop", ping="ping", status="!status", stay="!stay")
    by_name = {c.spec.name: c.bang for c in r}
    assert by_name["screenshot"] is None


def test_find_exact_match_stop(resolved: tuple) -> None:
    match = find_by_bang("!stop", resolved)
    assert match is not None
    cmd, tail = match
    assert cmd.spec.name == "stop"
    assert tail == ""


def test_find_with_surrounding_whitespace(resolved: tuple) -> None:
    match = find_by_bang("  !stop  ", resolved)
    assert match is not None
    assert match[0].spec.name == "stop"


def test_ping_case_insensitive(resolved: tuple) -> None:
    for variant in ("ping", "PING", "Ping", "  pInG  "):
        match = find_by_bang(variant, resolved)
        assert match is not None, variant
        assert match[0].spec.name == "ping"


def test_stop_is_case_sensitive(resolved: tuple) -> None:
    # !stop matches; !STOP does not.
    assert find_by_bang("!stop", resolved) is not None
    assert find_by_bang("!STOP", resolved) is None


def test_stay_matches(resolved: tuple) -> None:
    match = find_by_bang("!stay", resolved)
    assert match is not None
    cmd, tail = match
    assert cmd.spec.name == "stay"
    assert tail == ""


def test_stay_rejects_args(resolved: tuple) -> None:
    # takes_args=False on stay -- trailing tokens should not match
    assert find_by_bang("!stay please", resolved) is None


def test_takes_args_no_args(resolved: tuple) -> None:
    match = find_by_bang("!cost", resolved)
    assert match is not None
    cmd, tail = match
    assert cmd.spec.name == "cost"
    assert tail == ""


def test_takes_args_with_args(resolved: tuple) -> None:
    match = find_by_bang("!cost week", resolved)
    assert match is not None
    cmd, tail = match
    assert cmd.spec.name == "cost"
    assert tail == "week"


def test_takes_args_multiple_words(resolved: tuple) -> None:
    match = find_by_bang("!help cost", resolved)
    assert match is not None
    cmd, tail = match
    assert cmd.spec.name == "help"
    assert tail == "cost"


def test_no_args_rejects_args(resolved: tuple) -> None:
    # !stop is takes_args=False; anything after the command should not match.
    assert find_by_bang("!stop now", resolved) is None


def test_non_command_returns_none(resolved: tuple) -> None:
    for content in ("hello", "say hi", "!st", "", "   "):
        assert find_by_bang(content, resolved) is None, content


def test_render_overview_lists_all_surfaces(resolved: tuple) -> None:
    out = render_overview(resolved)
    # bang surface members
    assert "!help" in out
    assert "!cost" in out
    assert "!stop" in out
    assert "ping" in out
    assert "!status" in out
    assert "!stay" in out
    # slash surface members
    assert "/help" in out
    assert "/stop" in out
    assert "/status" in out
    assert "/stay" in out
    assert "/cost" in out
    assert "/screenshot" in out
    # screenshot has no message form, so the bang section should not advertise it
    bang_section, _, slash_section = out.partition("Slash commands")
    assert "screenshot" not in bang_section


def test_render_detail_known_command(resolved: tuple) -> None:
    out = render_detail("cost", resolved)
    assert out is not None
    assert "cost" in out
    assert "!cost" in out
    assert "/cost" in out
    assert "today" in out


def test_render_detail_stay_explains_token_cost(resolved: tuple) -> None:
    out = render_detail("stay", resolved)
    assert out is not None
    assert "no" in out.lower() and "token" in out.lower()


def test_render_detail_unknown_returns_none(resolved: tuple) -> None:
    assert render_detail("nonexistent", resolved) is None


def test_command_names_are_unique() -> None:
    names = [c.name for c in COMMANDS]
    assert len(names) == len(set(names))
