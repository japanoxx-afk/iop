import unittest

from iop_timer import MATCH_RUNNING, MatchClock, format_elapsed, overlay_position


class TimerTests(unittest.TestCase):
    def test_formats_minutes_and_hours(self):
        self.assertEqual(format_elapsed(0), "00:00")
        self.assertEqual(format_elapsed(65.9), "01:05")
        self.assertEqual(format_elapsed(3661), "01:01:01")

    def test_negative_elapsed_is_clamped(self):
        self.assertEqual(format_elapsed(-3), "00:00")

    def test_overlay_is_inside_top_right_of_client(self):
        self.assertEqual(overlay_position((100, 200, 900, 700), 120), (766, 214))

    def test_clock_waits_for_battle_loop_and_resets_for_next_match(self):
        clock = MatchClock()
        self.assertEqual(clock.update(None, 10), (None, False))
        self.assertEqual(clock.update(0, 20), (None, False))  # map/unit loading
        self.assertEqual(clock.update(MATCH_RUNNING, 30), (0.0, True))
        self.assertEqual(clock.update(MATCH_RUNNING, 35), (5, False))
        self.assertEqual(clock.update(9, 40), (None, False))  # result screen
        self.assertEqual(clock.update(MATCH_RUNNING, 50), (0.0, True))


if __name__ == "__main__":
    unittest.main()
