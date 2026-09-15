import unittest

from iop_timer import format_elapsed, overlay_position


class TimerTests(unittest.TestCase):
    def test_formats_minutes_and_hours(self):
        self.assertEqual(format_elapsed(0), "00:00")
        self.assertEqual(format_elapsed(65.9), "01:05")
        self.assertEqual(format_elapsed(3661), "01:01:01")

    def test_negative_elapsed_is_clamped(self):
        self.assertEqual(format_elapsed(-3), "00:00")

    def test_overlay_is_inside_top_right_of_client(self):
        self.assertEqual(overlay_position((100, 200, 900, 700), 120), (766, 214))


if __name__ == "__main__":
    unittest.main()
