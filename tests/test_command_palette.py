"""Pure command-palette rules: scopes, fuzzy scoring, ranking and recency (no Qt)."""

from __future__ import annotations

from manim_studio.command_palette import (
    ACTIONS,
    BLOCKS,
    RECENT,
    SCENES,
    TIMELINE,
    PaletteEntry,
    fuzzy_score,
    parse_query,
    rank,
    remember,
)


def _entries() -> list[PaletteEntry]:
    return [
        PaletteEntry("a:reload", "Reload preview", ACTIONS, shortcut="F5"),
        PaletteEntry(
            "a:export",
            "Export…",
            ACTIONS,
            shortcut="Ctrl+E",
            enabled=False,
            reason="open a scene first",
        ),
        PaletteEntry("t:play", "Play / pause", TIMELINE, shortcut="Space"),
        PaletteEntry("t:zoom", "Zoom to selection", TIMELINE, keywords="fit range"),
        PaletteEntry("s:Demo", "Preview scene: Demo", SCENES, detail="current"),
        PaletteEntry("r:/x/demo.py", "demo.py", RECENT, detail="/x"),
        PaletteEntry("b:3", "#3 ShowCreation", BLOCKS, detail="0:01.00 · 1.00s"),
    ]


class TestParseQuery:
    def test_plain_text_searches_everything(self) -> None:
        assert parse_query("  play ") == parse_query("play")
        assert parse_query("play").scope == "all"
        assert parse_query("play").term == "play"

    def test_prefixes_select_a_scope_and_drop_the_prefix(self) -> None:
        assert parse_query(">undo").scope == "actions"
        assert parse_query(">undo").term == "undo"
        assert parse_query("@ shows").scope == "blocks"
        assert parse_query("@ shows").term == "shows"
        assert parse_query("?").scope == "shortcuts"
        assert parse_query("?").term == ""

    def test_empty_query_is_all_scope(self) -> None:
        assert parse_query("").scope == "all"
        assert parse_query("").term == ""


class TestFuzzyScore:
    def test_no_match_is_none(self) -> None:
        assert fuzzy_score("xq", "Play / pause") is None

    def test_empty_term_matches_with_zero(self) -> None:
        assert fuzzy_score("", "anything") == 0

    def test_word_start_and_contiguous_beat_scattered_mid_word(self) -> None:
        assert fuzzy_score("sel", "Zoom to selection") > fuzzy_score("sel", "unselected")

    def test_match_is_case_insensitive_and_ignores_spaces(self) -> None:
        assert fuzzy_score("ZOOM SEL", "Zoom to selection") is not None


class TestRank:
    def test_empty_query_keeps_given_order(self) -> None:
        titles = [entry.title for entry in rank(_entries(), "")]
        assert titles[:2] == ["Reload preview", "Export…"]

    def test_recently_used_entries_come_first(self) -> None:
        ranked = rank(_entries(), "", recent=["t:zoom", "a:reload"])
        assert [entry.key for entry in ranked[:2]] == ["t:zoom", "a:reload"]

    def test_better_match_ranks_first(self) -> None:
        ranked = rank(_entries(), "reload")
        assert ranked[0].key == "a:reload"

    def test_disabled_entries_are_still_listed(self) -> None:
        keys = [entry.key for entry in rank(_entries(), "export")]
        assert keys == ["a:export"]
        assert rank(_entries(), "export")[0].enabled is False

    def test_keywords_are_searched_but_not_required(self) -> None:
        assert [entry.key for entry in rank(_entries(), "range")] == ["t:zoom"]

    def test_actions_prefix_excludes_scenes_and_blocks(self) -> None:
        groups = {entry.group for entry in rank(_entries(), ">")}
        assert groups == {ACTIONS, TIMELINE}

    def test_blocks_prefix_searches_only_blocks(self) -> None:
        assert [entry.key for entry in rank(_entries(), "@ show")] == ["b:3"]

    def test_shortcuts_prefix_shows_only_entries_with_a_shortcut(self) -> None:
        keys = {entry.key for entry in rank(_entries(), "?")}
        assert keys == {"a:reload", "a:export", "t:play"}

    def test_limit_caps_the_result(self) -> None:
        assert len(rank(_entries(), "", limit=2)) == 2

    def test_no_match_returns_nothing(self) -> None:
        assert rank(_entries(), "qqqq") == []


class TestRemember:
    def test_moves_key_to_front_without_duplicates(self) -> None:
        assert remember(["a", "b", "c"], "b") == ["b", "a", "c"]

    def test_caps_the_history(self) -> None:
        assert remember(["a", "b", "c"], "d", limit=3) == ["d", "a", "b"]
