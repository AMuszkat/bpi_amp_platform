"""speakerd_quantum_change (docs/autonomous-development.md, section 5): the quantum changes while
a stream plays, and speakerd keeps playing the same sound.

speakerd prepares its graph for blocks up to the quantum limit and primes it at the quantum it
starts with (dsp/juce_apps/speakerd/README.md, "Rate and block size"). A quantum that is a whole
multiple of the primed size runs as that many blocks; any other is primed anew behind a short
fade. speakerd may also prime anew a quantum it would run as many small blocks. Here speakerd
runs on the left virtual speaker (quantum 1024), the headless build of loudness forced to its
first setting (+12 dB shelf at 120 Hz) on both amplifier channels, and a player streams a
100 Hz tone at -20 dBFS peak into it throughout. The quantum is forced with
`pw-metadata -n settings 0 clock.force-quantum` to 256 (smaller: primed anew), then 2048
(8 blocks of 256, or primed anew) and then let go with 0 (back to speakerd's own 1024).

Not 1536, the obvious size between the two: PipeWire 0.3.65 rounds a quantum down to a power of
two (`clock.power-of-two-quantum`, on by default and on the boards), so a forced 1536 runs at
1024 and the next step would change nothing.

Pass when, for each change:

- speakerd says `pipewire: the quantum changed to <n>: ` with the quantum PipeWire runs (256,
  2048, 1024), followed by `faded out, primed the graph for it and faded back in`, or, for 2048
  and 1024, by that or `the graph runs it as <k> block(s) of <p>`;
- after it, the amplifier gets the tone with the gain of the complete build's render of the
  same document, within 0.05 dB on both channels; where speakerd runs the quantum as 4 or more
  blocks and then, 2 s later, primes it anew (`faded out, primed ...`), this holds before that,
  when the recording ends first, and after it;

and the amplifier, recorded as 32-bit float from before the first change to after the last,
holds no NaN or infinity and no sample above full scale.

What is not asserted: the length of the gap at a change. PipeWire 0.3.65 glitches on a quantum
change by itself, with no speakerd: a tone played straight into the amplifier, through the same
quanta, jumps by 0.6 to 0.9 of its amplitude at every change and drops out for 42 ms at the
change to 2048, the same every time (and for 21 to 72 ms at changes between other quanta). The
dropouts and jumps measured through speakerd are printed.

The quantum is let go (0) when the test ends, whatever happened.

    python3 -m unittest -v tests.vpair.test_speakerd_quantum_change
"""

import contextlib
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from harness import (RATE, Pair, Player, Recorder, Speakerd, active, built, graph_document,
                     lv2_uri, render, rms_dbfs, state_dir, steady, tone, with_plugin)

import numpy as np

DOCUMENT = graph_document(lv2_uri("loudness_headless"),
                          {"mode": "Force 1", "s0_e0_gain": 12.0, "s0_e0_freq": 120.0})
TONE = tone(100.0, -20.0, 1.0)
STREAM = tone(100.0, -20.0, 30.0)   # longer than the test: the player is stopped at its end
TOLERANCE_DB = 0.05

PRIMED = r"faded out, primed the graph for it and faded back in"
BLOCKS = r"the graph runs it as \d+ block\(s\) of \d+"
# (forced, the quantum PipeWire then runs, what speakerd may say about it)
CHANGES = [(256, 256, PRIMED), (2048, 2048, f"{PRIMED}|{BLOCKS}"), (0, 1024, f"{PRIMED}|{BLOCKS}")]

# speakerd may prime anew a quantum it runs as this many primed blocks or more, once it has
# stayed for 2 s: so much is waited for, from the first line about the change.
REPRIME_BLOCKS, REPRIME_WAIT = 4, 3.5

# A recording after a change: its first part (the recorder's start, a fade-in, the plugin's
# transient) is skipped, the rest is measured, a whole number of 100 Hz periods.
RECORD_SECONDS, SKIP, MEASURED = 0.8, int(0.2 * RATE), int(0.5 * RATE)


def gains(capture):
    """The gain of each amplifier channel for the stream, from a recording of it."""
    part = capture[SKIP:SKIP + MEASURED]
    if len(part) < MEASURED:
        raise AssertionError(f"the recording has {len(capture)} samples, fewer than "
                             f"{SKIP + MEASURED}")
    level = rms_dbfs(TONE)
    return [rms_dbfs(part[:, channel]) - level for channel in (0, 1)]


def glitches(x, amplitude, block=RATE // 1000):
    """In a recording of a steady tone of `amplitude` (one channel): the dropouts, as (start in
    s, length in ms) of every run of 1 ms blocks more than 40 dB below the tone, and the jumps,
    as (time in s, size as a fraction of the amplitude) of every step between two samples
    larger than half the amplitude (the tone itself never steps by more than 0.014 of it).
    A recording often starts with a quantum or two of zeros, before the first buffer reaches
    the recorder: that is not a dropout, nor is the step out of it."""
    n = len(x) // block
    quiet = np.sqrt(np.mean(np.square(x[:n * block].reshape(n, block)), axis=1)) \
        < 0.01 * amplitude / np.sqrt(2)
    i0 = int(np.argmin(quiet)) if n else 0   # the first block that is not quiet
    dropouts, i = [], i0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            dropouts.append((i * block / RATE, (j - i) * block * 1000 / RATE))
            i = j
        else:
            i += 1
    steps = np.abs(np.diff(x))
    jumps = [(k / RATE, steps[k] / amplitude) for k in np.flatnonzero(steps > 0.5 * amplitude)
             if k >= (i0 + 1) * block]
    return dropouts, jumps


class QuantumChange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.headless = built("loudness", "headless")
        complete = built("loudness", "complete")
        built("speakerd")
        cls.tmp = Path(tempfile.mkdtemp(prefix="vpair-quantum-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)

        state = state_dir(cls.tmp / "reference", current=with_plugin(DOCUMENT, lv2_uri("loudness")))
        output, log = render(state, TONE, bundles=[complete])
        if "layer current (graph.json)" not in log:
            raise AssertionError(f"the render did not run the document:\n{log}")
        part = steady(active(output))
        cls.reference = rms_dbfs(part[:, 0]) - rms_dbfs(steady(TONE))

        cls.pair = Pair().__enter__()
        cls.addClassCleanup(cls.pair.__exit__, None, None, None)

    def window(self, speaker, when):
        """A recording of the amplifier, as floats, checked for samples that are not finite or
        above full scale."""
        with Recorder(speaker, "amplifier", fmt="f32") as recorder:
            time.sleep(RECORD_SECONDS)
            capture = recorder.stop()
        self.check_samples(capture, when)
        return capture

    def check_gain(self, capture, when):
        live = gains(capture)
        summary = (f"{when}: woofer {live[0]:+.3f} dB, tweeter {live[1]:+.3f} dB, "
                   f"complete build {self.reference:+.3f} dB")
        print(f"\n  {summary}")
        for channel, gain in zip(("woofer", "tweeter"), live):
            self.assertAlmostEqual(gain, self.reference, delta=TOLERANCE_DB,
                                   msg=f"{channel}: {summary}")

    def check_samples(self, capture, when):
        finite = np.isfinite(capture)
        self.assertTrue(np.all(finite), f"{when}: {np.count_nonzero(~finite)} samples are NaN "
                                        f"or infinite")
        peak = float(np.max(np.abs(capture), initial=0.0))
        self.assertLessEqual(peak, 1.0, f"{when}: a sample is above full scale ({peak})")

    def test_the_gain_recovers_after_every_quantum_change(self):
        speaker = self.pair["left"]
        self.addCleanup(speaker.set_quantum, 0)   # whatever happens below

        with contextlib.ExitStack() as stack:
            sd = stack.enter_context(Speakerd(speaker, state_dir(self.tmp / "live", current=DOCUMENT),
                                              bundles=[self.headless]))
            stack.enter_context(Player(speaker, STREAM, target="speakerd"))
            throughout = stack.enter_context(Recorder(speaker, "amplifier", fmt="f32"))
            started = time.monotonic()
            before = "quantum 1024, before any change"
            self.check_gain(self.window(speaker, before), before)

            changed_at, said = [], []
            for forced, quantum, wording in CHANGES:
                mark = sd.mark()
                changed_at.append(time.monotonic() - started)
                speaker.set_quantum(forced)
                line = sd.wait_for(f"pipewire: the quantum changed to {quantum}: ", timeout=10,
                                   after=mark)
                seen = time.monotonic()
                said.append([line])
                self.assertRegex(line, f"^speakerd: pipewire: the quantum changed to {quantum}: "
                                       f"({wording})$", sd.log())
                when = f"clock.force-quantum {forced}, quantum {quantum}"
                window_mark = sd.mark()
                capture = self.window(speaker, when)

                blocks = re.search(r"runs it as (\d+) block", line)
                if not blocks or int(blocks.group(1)) < REPRIME_BLOCKS:
                    self.check_gain(capture, when)
                    continue
                # Run as many small blocks, which speakerd may prime anew 2 s later: the gain is
                # checked before that, if the recording ended first, and after it.
                primed = f"pipewire: the quantum changed to {quantum}: faded out, primed"
                if not sd.lines_starting(primed, after=window_mark):
                    self.check_gain(capture, f"{when}, as {blocks.group(1)} blocks")
                again = sd.wait_for(primed, timeout=max(0.0, REPRIME_WAIT - (time.monotonic() - seen)),
                                    after=mark, required=False)
                if again is not None:
                    said[-1].append(again)
                    self.check_gain(self.window(speaker, when), f"{when}, primed anew")

            capture = throughout.stop()

        self.check_samples(capture, "from before the first change to after the last")

        # What PipeWire and speakerd did at each change, for the record. The times are from the
        # start of the recording, and those of the changes are when pw-metadata was run.
        amplitude = 10 ** ((-20.0 + self.reference) / 20)
        dropouts, jumps = glitches(capture[:, 0], amplitude)
        bounds = [0.0] + changed_at + [len(capture) / RATE]
        names = ["before any change"] + [
            f"at {at:.2f} s, force-quantum {forced}: "
            + ", then ".join(f"'{line[len('speakerd: pipewire: '):]}'" for line in lines)
            for at, (forced, _, _), lines in zip(changed_at, CHANGES, said)]
        rows = []
        for name, start, end in zip(names, bounds, bounds[1:]):
            gaps = [f"{ms:.0f} ms at {t:.2f} s" for t, ms in dropouts if start <= t < end]
            steps = [f"{size:.2f} of the tone at {t:.2f} s" for t, size in jumps if start <= t < end]
            rows.append(f"{name}: dropouts {', '.join(gaps) or 'none'}; jumps {', '.join(steps) or 'none'}")
        print("\n  woofer, recorded throughout (gaps are not asserted):\n    " + "\n    ".join(rows))


if __name__ == "__main__":
    unittest.main()
