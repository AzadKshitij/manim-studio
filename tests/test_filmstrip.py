"""Unit tests for the bounded filmstrip cache."""

from __future__ import annotations

import pytest

from manim_studio.filmstrip import Filmstrip


def blob(size: int = 8) -> bytes:
    return b"x" * size


class TestFilmstrip:
    def test_put_get_round_trip(self) -> None:
        strip = Filmstrip()
        assert strip.put(5, blob()) is True
        assert strip.get(5) == blob()
        assert strip.get(6) is None
        assert strip.frames == 1
        assert strip.byte_size == 8

    def test_put_negative_index_rejected(self) -> None:
        with pytest.raises(ValueError):
            Filmstrip().put(-1, blob())

    def test_overwrite_replaces_and_reaccounts_bytes(self) -> None:
        strip = Filmstrip()
        strip.put(3, blob(10))
        strip.put(3, blob(4))
        assert strip.frames == 1
        assert strip.byte_size == 4

    def test_clear(self) -> None:
        strip = Filmstrip()
        strip.put(0, blob())
        strip.clear()
        assert strip.is_empty and strip.byte_size == 0

    def test_nearest_probes_around_index(self) -> None:
        strip = Filmstrip()
        strip.put(10, b"ten")
        strip.put(12, b"twelve")
        assert strip.nearest(11, max_distance=1) == b"ten"  # ties prefer earlier
        assert strip.nearest(11, max_distance=2) == b"ten"
        assert strip.nearest(13, max_distance=0) is None
        assert strip.nearest(13, max_distance=1) == b"twelve"
        assert strip.nearest(0, max_distance=1) is None  # negative candidates skipped

    def test_nearest_exact_hit(self) -> None:
        strip = Filmstrip()
        strip.put(4, b"four")
        assert strip.nearest(4) == b"four"

    def test_eviction_by_frame_budget(self) -> None:
        strip = Filmstrip(max_frames=2)
        strip.put(1, b"one")
        strip.put(2, b"two")
        strip.put(3, b"three")
        assert strip.get(1) is None  # least recently added evicted
        assert strip.get(2) == b"two"
        assert strip.get(3) == b"three"

    def test_eviction_by_byte_budget(self) -> None:
        strip = Filmstrip(max_bytes=20)
        assert strip.put(1, blob(8)) is True
        assert strip.put(2, blob(8)) is True
        assert strip.put(3, blob(8)) is True
        assert strip.byte_size <= 20
        assert strip.get(1) is None  # oldest evicted to make room

    def test_oversized_single_frame_is_skipped(self) -> None:
        strip = Filmstrip(max_bytes=4)
        assert strip.put(0, blob(5)) is False
        assert strip.is_empty

    def test_get_refreshes_lru_order(self) -> None:
        strip = Filmstrip(max_frames=2)
        strip.put(1, b"one")
        strip.put(2, b"two")
        assert strip.get(1) == b"one"  # 1 is now most recently used
        strip.put(3, b"three")  # evicts 2
        assert strip.get(2) is None
        assert strip.get(1) == b"one"

    def test_invalid_budgets_rejected(self) -> None:
        with pytest.raises(ValueError):
            Filmstrip(max_frames=0)
        with pytest.raises(ValueError):
            Filmstrip(max_bytes=0)
