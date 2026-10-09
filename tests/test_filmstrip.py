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


class TestRetain:
    def _strip(self) -> Filmstrip:
        strip = Filmstrip()
        for index in range(10):
            strip.put(index, bytes([index]) * 4)
        return strip

    def test_keeps_only_the_given_half_open_ranges(self) -> None:
        strip = self._strip()
        assert strip.retain([(0, 3), (7, 9)]) == 5
        assert [i for i in range(10) if strip.get(i) is not None] == [0, 1, 2, 7, 8]

    def test_byte_accounting_follows_the_kept_frames(self) -> None:
        strip = self._strip()
        strip.retain([(2, 4)])
        assert strip.byte_size == 8 and strip.frames == 2

    def test_no_ranges_empties_the_cache(self) -> None:
        strip = self._strip()
        assert strip.retain([]) == 0 and strip.is_empty

    def test_has_range(self) -> None:
        strip = self._strip()
        strip.retain([(0, 4)])
        assert strip.has_range(0, 4) and not strip.has_range(0, 5)
        assert strip.has_range(3, 3)  # empty range is trivially present


class TestRetainShifted:
    @staticmethod
    def _strip() -> Filmstrip:
        strip = Filmstrip()
        for index in range(10):
            strip.put(index, bytes([index]) * 4)
        return strip

    def test_frames_move_by_the_delta_and_the_rest_is_dropped(self) -> None:
        strip = self._strip()
        kept = strip.retain_shifted([(0, 2, 0), (4, 7, 10)])
        assert kept == 5
        assert strip.get(0) == bytes([0]) * 4 and strip.get(1) == bytes([1]) * 4
        assert strip.get(14) == bytes([4]) * 4 and strip.get(16) == bytes([6]) * 4
        assert strip.get(4) is None and strip.get(2) is None and strip.get(7) is None
        assert strip.byte_size == 5 * 4

    def test_negative_deltas_move_frames_earlier(self) -> None:
        strip = self._strip()
        strip.retain_shifted([(5, 8, -5)])
        assert [strip.get(i) for i in range(3)] == [bytes([5]) * 4, bytes([6]) * 4, bytes([7]) * 4]

    def test_frames_that_would_land_before_zero_are_dropped(self) -> None:
        strip = self._strip()
        assert strip.retain_shifted([(0, 3, -1)]) == 2
        assert strip.get(0) == bytes([1]) * 4

    def test_no_moves_empties_the_strip(self) -> None:
        strip = self._strip()
        assert strip.retain_shifted([]) == 0 and strip.is_empty

    def test_lru_order_is_preserved(self) -> None:
        strip = Filmstrip(max_frames=4)
        for index in range(4):
            strip.put(index, b"x")
        strip.retain_shifted([(0, 4, 100)])
        strip.put(1, b"y")  # over budget: the oldest kept entry (100) is evicted first
        strip.put(500, b"z")  # ... then the next oldest (101)
        assert strip.get(100) is None and strip.get(101) is None
        assert strip.get(102) == b"x" and strip.get(103) == b"x"
