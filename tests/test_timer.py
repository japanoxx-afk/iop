import unittest

from iop_timer import (MATCH_RUNNING, MatchClock, active_player_rows,
                       format_elapsed, format_resource_board, overlay_position,
                       _Windows, TEAM_MANAGER_POINTER, SLOT_TYPE_BASE,
                       SLOT_NAME_BASE, SLOT_NAME_SIZE, LOCAL_SLOT)


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
        self.assertEqual(clock.update(3, 20), (None, False))  # client/setup loop
        self.assertEqual(clock.update(MATCH_RUNNING, 30), (0.0, True))
        self.assertEqual(clock.update(MATCH_RUNNING, 35), (5, False))
        self.assertEqual(clock.update(9, 40), (None, False))  # result screen
        self.assertEqual(clock.update(MATCH_RUNNING, 50), (0.0, True))

    def test_spectator_board_excludes_observer_and_empty_slots(self):
        slots=[
            {'type':4,'race':1,'name':'Alpha','adamas':1200,'platinum':300},
            {'type':4,'race':5,'name':'Watcher','adamas':0,'platinum':0},
            {'type':1,'race':2,'name':'COMPUTER[2]','adamas':900,'platinum':70},
            {'type':0,'race':3,'name':'','adamas':0,'platinum':0},
        ]
        rows=active_player_rows(slots)
        self.assertEqual(rows,[('Alpha',1200,300),('COMPUTER[2]',900,70)])
        board=format_resource_board(rows)
        self.assertIn('Alpha  |  1,200 / 300',board)
        self.assertNotIn('Watcher',board)

    def test_memory_layout_detects_local_observer_and_reads_players(self):
        class FakeWindows(_Windows):
            def __init__(self,memory): self.memory=memory
            def _read(self,_handle,address,size): return self.memory.get((address,size))
        manager=0x100000; player=0x200000; observer=0x300000
        u32=lambda value:value.to_bytes(4,'little')
        memory={
            (TEAM_MANAGER_POINTER,4):u32(manager),(manager+5,4):u32(2),
            (LOCAL_SLOT,1):b'\x01',(SLOT_TYPE_BASE,1):b'\x04',
            (SLOT_TYPE_BASE+1,1):b'\x04',(manager+9,4):u32(player),
            (manager+13,4):u32(observer),(player+2,1):b'\x02',
            (observer+2,1):b'\x05',(player+0x1a,8):u32(2500)+u32(750),
            (SLOT_NAME_BASE,SLOT_NAME_SIZE):b'PlayerOne\0'.ljust(SLOT_NAME_SIZE,b'\0'),
            (SLOT_NAME_BASE+SLOT_NAME_SIZE,SLOT_NAME_SIZE):b'Watcher\0'.ljust(SLOT_NAME_SIZE,b'\0'),
        }
        self.assertEqual(FakeWindows(memory).spectator_rows(1),[('PlayerOne',2500,750)])
        memory[(LOCAL_SLOT,1)]=b'\x00'
        self.assertIsNone(FakeWindows(memory).spectator_rows(1))


if __name__ == "__main__":
    unittest.main()
