"""Live batch feedback that stays outside captured structure diagnostics."""
from __future__ import annotations

import logging
import shutil
import sys
import textwrap
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import TextIO


class BatchProgress:
    """Refresh a terminal display, or emit readable events when output is redirected.

    Keep the original stream: structure_log temporarily redirects sys.stdout.
    A heartbeat runs independently of blocking validation/upload calls.
    """

    def __init__(
        self, names: Sequence[str], *, label: str, show_lists: bool = False,
        success_label: str = "Succeeded", stream: TextIO | None = None,
        refresh_interval: float = 1.0, heartbeat_interval: float = 30.0,
    ):
        self.states = dict.fromkeys(names, "pending")
        self.label = label
        self.show_lists = show_lists
        self.success_label = success_label
        self.stream = stream if stream is not None else sys.stdout
        self.tty = (bool(getattr(self.stream, "isatty", lambda: False)())
                    and not logging.getLogger().isEnabledFor(logging.DEBUG))
        self.refresh_interval = refresh_interval
        self.heartbeat_interval = heartbeat_interval
        self.current: str | None = None
        self.detail = ""
        self.started = time.monotonic()
        self.current_started = self.started
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lines = 0
        self._last_write = self.started
        self._closed = False

    def __enter__(self):
        self.render(lists=True)
        self._thread = threading.Thread(target=self._heartbeat, name="cli-progress", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            self.render(lists=True, final=True)
            self._closed = True

    @contextmanager
    def track(self, name: str, outcome: Callable[[], str]) -> Iterator[None]:
        self.start(name)
        try:
            yield
        except BaseException:
            self.finish(name, "interrupted")
            raise
        else:
            self.finish(name, outcome())

    def start(self, name: str) -> None:
        with self._lock:
            self.states[name] = "running"
            self.current = name
            self.current_started = time.monotonic()
            self.detail = "Starting"
            self.render()

    def stage(self, detail: str) -> None:
        with self._lock:
            if detail != self.detail:
                self.detail = detail
                self.render()

    def finish(self, name: str, status: str) -> None:
        with self._lock:
            self.states[name] = status
            self.current = None
            self.detail = ""
            label = self.success_label if status == "done" else status.capitalize()
            self.render(event=f"{label}: {name}")

    @staticmethod
    def _duration(seconds: float) -> str:
        seconds = int(seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    def _snapshot(self, *, lists: bool, final: bool) -> list[str]:
        counts = Counter(self.states.values())
        completed = sum(count for status, count in counts.items() if status not in {"pending", "running"})
        total = len(self.states)
        filled = int(20 * completed / total) if total else 20
        elapsed = self._duration(time.monotonic() - self.started)
        lines = [
            f"{self.label} [{'#' * filled}{'-' * (20 - filled)}] {completed}/{total} structures",
            f"{self.success_label}: {counts['done']} | failed: {counts['failed']} | "
            f"pending: {counts['pending']} | elapsed {elapsed}",
        ]
        if self.current is not None:
            duration = self._duration(time.monotonic() - self.current_started)
            lines.append(f"Current ({duration}): {self.current} — {self.detail}")
        else:
            lines.append("Current: none")
        if self.show_lists and lists:
            for label, state in ((self.success_label, "done"), ("Failed", "failed"),
                                 ("Pending", "pending"), ("Interrupted", "interrupted")):
                names = [name for name, status in self.states.items() if status == state]
                if state == "interrupted" and not names:
                    continue
                # Keep a live display within the viewport; the final lists are complete.
                visible = names if final or not self.tty else names[:5]
                suffix = f", … (+{len(names) - len(visible)} more)" if len(visible) < len(names) else ""
                lines.append(f"{label} ({len(names)}): {', '.join(visible) or 'none'}{suffix}")
        if self.tty and not final:
            columns, rows = shutil.get_terminal_size()
            width = max(columns - 1, 1)
            # One line per group prevents wrapping from breaking cursor movement.
            lines = [textwrap.shorten(line, width=width, placeholder="…") for line in lines]
            lines = lines[:max(rows - 1, 1)]
        return lines

    def render(self, *, event: str = "", lists: bool = False, final: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            if self.tty:
                if self._lines:
                    self.stream.write(f"\033[{self._lines}A")
                    for _ in range(self._lines):
                        self.stream.write("\r\033[2K\n")
                    self.stream.write(f"\033[{self._lines}A")
                lines = self._snapshot(lists=True, final=final)
                self.stream.write("\n".join(lines) + "\n")
                self._lines = len(lines)
            else:
                lines = self._snapshot(lists=lists, final=final)
                if event:
                    lines = [event, *lines[:2]]
                elif not lists:
                    lines = lines[1:]
                self.stream.write("\n".join(lines) + "\n")
            self.stream.flush()
            self._last_write = time.monotonic()

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.refresh_interval):
            with self._lock:
                if self.tty or time.monotonic() - self._last_write >= self.heartbeat_interval:
                    self.render()
