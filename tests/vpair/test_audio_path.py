"""Rung 2 smoke tests: the virtual pair itself.

These prove the virtual speakers behave like the real ones' audio path before anything runs
on them: a signal reaches the amplifier with its frequency and level intact, both amplifier
channels are seen, and the two speakers are independent.

    python3 -m unittest discover -s tests/vpair -v
"""

import threading
import unittest

from harness import Pair, active, peak_frequency, rms_dbfs, tone

# A sine's RMS is 3.01 dB below its peak.
SINE_RMS_OFFSET_DB = -3.01


class AudioPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pair = Pair().__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.pair.__exit__(None, None, None)

    def check_tone(self, speaker, freq_hz, level_dbfs):
        received = active(self.pair[speaker].play_and_record(tone(freq_hz, level_dbfs, 1.0)))
        self.assertGreater(len(received), 0.8 * 48000, f"{speaker}: the tone did not arrive")

        for channel, name in ((0, "woofer"), (1, "tweeter")):
            x = received[:, channel]
            self.assertAlmostEqual(peak_frequency(x), freq_hz, delta=2.0,
                                   msg=f"{speaker} {name}: wrong frequency")
            self.assertAlmostEqual(rms_dbfs(x), level_dbfs + SINE_RMS_OFFSET_DB, delta=0.5,
                                   msg=f"{speaker} {name}: wrong level")

    def test_left_receives_a_tone(self):
        self.check_tone("left", 440.0, -20.0)

    def test_right_receives_a_tone(self):
        self.check_tone("right", 1000.0, -12.0)

    def test_speakers_are_independent(self):
        # While the left speaker plays a loud tone, the right one's amplifier stays silent.
        result = {}
        listener = threading.Thread(
            target=lambda: result.update(right=self.pair["right"].record(2.0)))
        listener.start()
        left = active(self.pair["left"].play_and_record(tone(440.0, -6.0, 1.0)))
        listener.join()

        self.assertGreater(len(left), 0.8 * 48000, "left: the tone did not arrive")
        self.assertLess(rms_dbfs(result["right"]), -90.0, "right: picked up the left speaker")


if __name__ == "__main__":
    unittest.main()
