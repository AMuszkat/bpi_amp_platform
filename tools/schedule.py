#!/usr/bin/env python3
"""tools/test.sh's schedule: the steps of a run with more than one job, in phases, so that what
times itself never runs beside a build (docs/autonomous-development.md, section 4, "How the checks
run").

    tools/schedule.py --plan FILE --logs DIR [--results FILE] [--gate FILE] [--heavy-slots N]
                      [--nice N] [--lock FILE --what TEXT]

FILE has one step a line (blank lines and lines beginning with # aside), split as a shell would
(shlex; tools/test.sh writes the words with printf %q):

    NAME KIND [title=TEXT] [needs=A,B] [slots=N] [gated] [guards=UNIT,...] -- COMMAND...

- KIND heavy: phase 1, at most --heavy-slots (2) at once, `slots` of them each (1; the pool's
  prebuild takes 2), started in the plan's order (one waits for the slots rather than be overtaken),
  at nice --nice (19): plugin-check with LTO, `plugin <name>`, the pool's prebuild.
- KIND light: phase 1, beside them, not niced: tools, plugin-versions, the controller's build...
- KIND test: phase 2, once every phase-1 step has ended: the pool, the controller's own tests.
- needs: steps of earlier lines it waits for; one of them not passed, it is skipped, saying why
  (the controller's own tests after its failed build). A failed step stops nothing else.
- gated: the step the gate is for (the pool, with --gate FILE). The gate, FILE, is created
  (atomically) once every phase-2 step but the gated ones has ended; a step that `guards` UNIT and
  did not pass puts "leave-out UNIT: why" in it (tests/vpair/pool.py, "the gate").

Each step runs in a session of its own, its stdin /dev/null, its stdout and stderr in
DIR/<NAME>.log (DIR is the schedule's own: emptied of files as it starts); its whole output is
printed as one block when it ends, between "=== /test TITLE: RESULT (...)" and "=== end of TITLE". While steps run, a line says when each starts, and every
60 s which still run. At the end: a table (phase, step, start, seconds, result), the container's
peak tasks and memory over the run (its cgroup's pids.current and memory.current, where they can
be read), and --results gets "NAME RESULT" a line (passed, failed, skipped, interrupted).

--lock FILE: one schedule at a time per checkout (tools/test.sh gives build/test-run.lock): a
second waits, saying which run holds it (--what) once and then every 60 s; SIGINT or SIGTERM while
it waits ends it, nothing run.

Exit status: 0 when every step passed, 1 otherwise, 2 for a plan it cannot run. SIGINT or SIGTERM
passes the signal to every running step's session, starts nothing more, waits for them (SIGKILL
after 60 s), prints their blocks and the table, and ends the schedule by that signal.
tools/tests/test_schedule.py tests it on synthetic steps.
"""

import argparse
import fcntl
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

KINDS = {"heavy": 1, "light": 1, "test": 2}
TICK = 60.0                 # seconds between the lines that say what still runs
STOP_GRACE = 60.0           # after a signal, seconds before the steps still running are killed


class PlanError(Exception):
    pass


class Step:
    def __init__(self, name, kind, command, title=None, needs=(), slots=1, gated=False, guards=()):
        self.name, self.kind, self.command = name, kind, command
        self.title = title or name
        self.needs, self.slots, self.gated, self.guards = list(needs), slots, gated, list(guards)
        self.phase = KINDS[kind]
        self.process = self.log = None
        self.start = self.end = None
        self.result = None          # passed, failed, skipped, interrupted
        self.why = ""

    @property
    def ended(self):
        return self.result is not None


def read_plan(text):
    steps, names = [], {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            words = shlex.split(line)
        except ValueError as error:
            raise PlanError(f"line {number}: {error}") from None
        if "--" not in words:
            raise PlanError(f"line {number}: no -- before the command")
        head, command = words[:words.index("--")], words[words.index("--") + 1:]
        if len(head) < 2 or not command:
            raise PlanError(f"line {number}: NAME KIND [options] -- COMMAND...")
        name, kind, options = head[0], head[1], head[2:]
        if kind not in KINDS:
            raise PlanError(f"line {number}: kind {kind!r}: heavy, light or test")
        if name in names or "/" in name or name.startswith("."):
            raise PlanError(f"line {number}: step name {name!r} is used twice or is no file name")
        args = {}
        for option in options:
            key, eq, value = option.partition("=")
            if key == "gated" and not eq:
                args["gated"] = True
            elif key == "title" and eq:
                args["title"] = value
            elif key == "needs" and eq:
                args["needs"] = [n for n in value.split(",") if n]
            elif key == "guards" and eq:
                args["guards"] = [n for n in value.split(",") if n]
            elif key == "slots" and eq and value.isdigit() and int(value) >= 1:
                args["slots"] = int(value)
            else:
                raise PlanError(f"line {number}: option {option!r} (title=, needs=, slots=, gated, guards=)")
        step = Step(name, kind, command, **args)
        for need in step.needs:
            if need not in names:
                raise PlanError(f"line {number}: {name} needs {need}, which no line before it is")
            if names[need].phase > step.phase:
                raise PlanError(f"line {number}: {name} (phase {step.phase}) needs {need}, a later phase's")
        if step.gated and step.phase != 2:
            raise PlanError(f"line {number}: {name}: only a test step is gated")
        if step.slots > 1 and kind != "heavy":
            raise PlanError(f"line {number}: {name}: slots are a heavy step's")
        names[name] = step
        steps.append(step)
    if not steps:
        raise PlanError("no step")
    return steps


def cgroup(name):
    try:
        text = Path("/sys/fs/cgroup", name).read_text().strip()
        return None if text == "max" else int(text)
    except (OSError, ValueError):
        return None


class Schedule:
    def __init__(self, steps, logs, gate=None, heavy_slots=2, nice=19, out=None):
        self.steps, self.logs, self.gate = steps, Path(logs), Path(gate) if gate else None
        self.heavy_slots, self.nice = heavy_slots, nice
        self.out = out or (lambda text: print(text, flush=True))
        self.byname = {s.name: s for s in steps}
        self.caught = []
        self.t0 = time.monotonic()
        self.gate_open = False
        self.peak = {"tasks": {1: 0, 2: 0}, "memory": 0}

    def now(self):
        return time.monotonic() - self.t0

    def say(self, text):
        self.out(f"schedule: [{self.now():6.1f} s] {text}")

    # -- starting ---------------------------------------------------------------------------------

    def heavy_in_use(self):
        return sum(min(s.slots, self.heavy_slots) for s in self.steps
                   if s.kind == "heavy" and s.process is not None and not s.ended)

    def ready(self, step):
        """Whether `step` may start (or be skipped) now."""
        if step.start is not None or step.ended:
            return False
        if any(s.phase < step.phase and not s.ended for s in self.steps):
            return False
        if any(not self.byname[n].ended for n in step.needs):
            return False
        if step.kind == "heavy" and not any(self.byname[n].result != "passed" for n in step.needs):
            earlier = self.steps[:self.steps.index(step)]
            if any(s.kind == "heavy" and s.start is None and not s.ended for s in earlier):
                return False                    # in the plan's order: none overtakes one waiting
            if self.heavy_in_use() + min(step.slots, self.heavy_slots) > self.heavy_slots:
                return False
        return True

    def start(self, step):
        failed = [n for n in step.needs if self.byname[n].result != "passed"]
        if failed:
            step.start = step.end = self.now()
            step.result = "skipped"
            step.why = ", ".join(f"{self.byname[n].title} {self.byname[n].result}" for n in failed)
            self.out(f"=== /test {step.title}: skipped: {step.why}")
            return
        step.log = self.logs / f"{step.name}.log"
        command = (["nice", "-n", str(self.nice)] if step.kind == "heavy" else []) + step.command
        how = f"phase {step.phase}, {step.kind}" + (f", {step.slots} slots" if step.slots > 1 else "") \
            + (f", nice {self.nice}" if step.kind == "heavy" else "")
        with open(step.log, "wb") as log:
            try:
                step.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                                stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as error:
                log.write(f"schedule: {command[0]} could not be run: {error}\n".encode())
                step.start = step.end = self.now()
                step.result = "failed"
                self.finish_block(step, how)
                return
        step.start = self.now()
        step.how = how
        self.say(f"start {step.title} ({how}; its output: {step.log})")

    # -- ending -----------------------------------------------------------------------------------

    def reap(self, step):
        status = step.process.poll()
        if status is None:
            return False
        step.end = self.now()
        step.status = status
        if self.caught and status != 0:
            step.result = "interrupted"
        else:
            step.result = "passed" if status == 0 else "failed"
        self.finish_block(step, step.how)
        return True

    def finish_block(self, step, how):
        try:
            text = step.log.read_text(errors="replace").rstrip()
        except OSError as error:
            text = f"schedule: its log could not be read: {error}"
        took = step.end - step.start
        status = getattr(step, "status", None)
        result = step.result if step.result == "passed" else \
            f"{step.result.upper() if step.result == 'failed' else step.result}" \
            + (f" (exit {status})" if status not in (None, 0) and status > 0 else
               f" (signal {-status})" if status is not None and status < 0 else "")
        step.shown = result
        header = f"=== /test {step.title}: {result} ({how}, {took:.1f} s)"
        self.out(f"{header}\n{text}\n=== end of {step.title}" if text else header)

    # -- the gate ---------------------------------------------------------------------------------

    def maybe_open_gate(self):
        if self.gate is None or self.gate_open:
            return
        if any(s.phase == 1 and not s.ended for s in self.steps):
            return
        if any(s.phase == 2 and not s.gated and not s.ended for s in self.steps):
            return
        lines = [f"leave-out {unit}: {s.title} did not pass ({s.result}{': ' + s.why if s.why else ''})"
                 for s in self.steps if s.result != "passed" for unit in s.guards]
        tmp = self.gate.with_name(f".{self.gate.name}.{os.getpid()}")
        tmp.write_text("".join(line + "\n" for line in lines))
        os.replace(tmp, self.gate)
        self.gate_open = True
        if any(s.gated for s in self.steps):
            self.say(f"the gate is open ({self.gate}): every other test has ended"
                     + ("; it leaves out " + ", ".join(line.split(":")[0][len("leave-out "):] for line in lines)
                        if lines else ""))

    # -- the run ----------------------------------------------------------------------------------

    def watch(self):
        tasks, memory = cgroup("pids.current"), cgroup("memory.current")
        phase = 2 if all(s.ended for s in self.steps if s.phase == 1) else 1
        if tasks is not None:
            self.peak["tasks"][phase] = max(self.peak["tasks"][phase], tasks)
        if memory is not None:
            self.peak["memory"] = max(self.peak["memory"], memory)

    def run(self):
        self.logs.mkdir(parents=True, exist_ok=True)
        for old in self.logs.iterdir():          # the folder is the schedule's: the last run's note too
            if old.is_file() or old.is_symlink():
                old.unlink(missing_ok=True)
        if self.gate is not None:
            self.gate.unlink(missing_ok=True)
        said = self.now()
        stopping_since = None
        while True:
            running = [s for s in self.steps if s.process is not None and not s.ended]
            for step in running:
                self.reap(step)
            if not self.caught:
                progress = True
                while progress:
                    progress = False
                    for step in self.steps:
                        if self.ready(step):
                            self.start(step)
                            progress = True
                    self.maybe_open_gate()
            else:
                if stopping_since is None:
                    stopping_since = time.monotonic()
                    for step in self.steps:
                        if step.start is None:
                            step.start = step.end = self.now()
                            step.result, step.why = "interrupted", "not started"
                    self.signal_all(self.caught[0])
                elif time.monotonic() - stopping_since > STOP_GRACE:
                    self.signal_all(signal.SIGKILL)
                    stopping_since = float("inf")
            running = [s for s in self.steps if s.process is not None and not s.ended]
            if all(s.ended for s in self.steps):
                break
            self.watch()
            if self.now() - said >= TICK and running:
                said = self.now()
                self.say("running: " + ", ".join(f"{s.title} ({self.now() - s.start:.0f} s)" for s in running))
            time.sleep(0.1)
        return all(s.result == "passed" for s in self.steps)

    def signal_all(self, signum):
        for step in self.steps:
            if step.process is not None and not step.ended:
                try:
                    os.killpg(step.process.pid, signum)
                except (ProcessLookupError, PermissionError):
                    pass

    def summary(self):
        width = max(len(s.title) for s in self.steps + [Step("step", "light", ["-"])])
        rows = [f"phase  {'step':{width}}  start  seconds  result"]
        for s in sorted(self.steps, key=lambda s: (s.phase, s.start if s.start is not None else 1e9)):
            took = "" if s.result in ("skipped",) or s.start is None or s.end is None or s.why == "not started" \
                else f"{s.end - s.start:.1f}"
            result = getattr(s, "shown", None) or s.result + (f": {s.why}" if s.why else "")
            rows.append(f"{s.phase:>5}  {s.title:{width}}  {s.start:5.1f}  {took:>7}  {result}")
        bad = [s.title for s in self.steps if s.result == "failed"]
        skipped = [s.title for s in self.steps if s.result == "skipped"]
        stopped = [s.title for s in self.steps if s.result == "interrupted"]
        verdict = "all passed" if not (bad or skipped or stopped) else "; ".join(
            part for part in (f"FAILED: {', '.join(bad)}" if bad else "",
                              f"skipped: {', '.join(skipped)}" if skipped else "",
                              f"interrupted: {', '.join(stopped)}" if stopped else "") if part)
        rows.append(f"schedule: {len(self.steps)} step(s) in {self.now():.1f} s: {verdict}")
        tasks, pids_max = self.peak["tasks"], cgroup("pids.max")
        if tasks[1] or tasks[2]:
            rows.append(f"schedule: the container's peak: {max(tasks.values())} tasks of pids.max {pids_max or 'max'} "
                        f"(phase 1 {tasks[1]}, phase 2 {tasks[2]}), memory {self.peak['memory'] / (1 << 30):.1f} GiB")
        return "\n".join(rows)


def hold_lock(path, what, out, interrupted):
    """The checkout's schedule lock, waiting while another run holds it; None if interrupted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o644), "r+")
    waited = said = None
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            pass
        now = time.monotonic()
        if waited is None or now - said >= TICK:
            try:
                holder = " ".join(path.read_text().split()) or "a run that has not said which yet"
            except OSError:
                holder = "a run"
            if waited is None:
                waited = now
                out(f"schedule: another run holds this checkout: {holder}; waiting for it to end (one schedule at "
                    f"a time per checkout, {path}: its builds would run beside this one's tests)")
            else:
                out(f"schedule: still waiting for {holder} ({now - waited:.0f} s)")
            said = now
        if interrupted():
            lock.close()
            return None
        time.sleep(0.2)
    if waited is not None:
        out(f"schedule: the other run ended; this one starts after waiting {time.monotonic() - waited:.1f} s")
    lock.seek(0)
    lock.truncate()
    lock.write(f"{what or 'a schedule'}, pid {os.getpid()}, since {time.strftime('%H:%M:%S %Z').strip()}\n")
    lock.flush()
    return lock


def main(argv=None):
    parser = argparse.ArgumentParser(description="tools/test.sh's schedule (see its docstring).")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--logs", required=True, help="the folder of the steps' logs (emptied of files first)")
    parser.add_argument("--results", default=None)
    parser.add_argument("--gate", default=None)
    parser.add_argument("--heavy-slots", type=int, default=2)
    parser.add_argument("--nice", type=int, default=19)
    parser.add_argument("--lock", default=None)
    parser.add_argument("--what", default="")
    args = parser.parse_args(argv)
    if args.heavy_slots < 1:
        parser.error("--heavy-slots: 1 or more")
    try:
        steps = read_plan(Path(args.plan).read_text())
    except (OSError, PlanError) as error:
        print(f"schedule: the plan {args.plan}: {error}", file=sys.stderr, flush=True)
        return 2

    caught = []

    def on_signal(signum, _frame):
        caught.append(signum)

    previous = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}
    lock = None
    if args.lock:
        lock = hold_lock(Path(args.lock), args.what, lambda text: print(text, flush=True), lambda: bool(caught))
        if lock is None:
            print(f"schedule: {signal.Signals(caught[0]).name} while waiting: nothing ran", flush=True)
            signal.signal(caught[0], signal.SIG_DFL)
            os.kill(os.getpid(), caught[0])
            return 1
    schedule = Schedule(steps, args.logs, args.gate, args.heavy_slots, args.nice)
    schedule.caught = caught
    if caught:
        print(f"schedule: {signal.Signals(caught[0]).name} before it started: nothing ran", flush=True)
    ok = schedule.run()
    print(schedule.summary(), flush=True)
    if args.results:
        Path(args.results).write_text("".join(f"{s.name} {s.result}\n" for s in steps))
    if caught:
        for s in previous:
            signal.signal(s, signal.SIG_DFL)
        os.kill(os.getpid(), caught[0])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
