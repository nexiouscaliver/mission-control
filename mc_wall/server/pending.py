"""Pending launch record + fsync'd, mutex-guarded pending.json store."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import pathlib
import threading
import time
import typing

STATUS_PROMPT_ARMED = "prompt-armed"
STATUS_AWAIT_BIRTH = "await-birth"
STATUS_GOAL_ARMED = "goal-armed"
STATUS_CLEARED = "cleared"
STATUS_FLAGGED = "flagged"
TERMINAL_STATUSES = frozenset({STATUS_CLEARED, STATUS_FLAGGED})
FLAG_AMBIGUOUS = "ambiguous"
PENDING_FILENAME = "pending.json"

_LOGGER = logging.getLogger(__name__)


def _epoch_ms() -> int:
    return int(time.time() * 1000)


@dataclasses.dataclass
class PendingRecord:
    status: str
    flag: typing.Optional[str] = None
    reason: typing.Optional[str] = None
    row_id: str = ""
    lane_tag: str = ""
    repo_root: str = ""
    prompt_sha256: str = ""
    launch_click_ms: int = 0
    matched_session_id: typing.Optional[str] = None
    matched_at_ms: typing.Optional[int] = None
    last_eval_ms: typing.Optional[int] = None
    advisory_120s_fired: bool = False
    canary_fired: bool = False
    updated_at_ms: int = 0
    version: int = 1

    def to_dict(self) -> dict:
        # Key order follows the field order above — the pinned spec schema.
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PendingRecord":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def summary(self) -> dict:
        # The pinned audit triple (AC-28) — nothing else may leak into logs.
        return {"status": self.status, "row_id": self.row_id, "flag": self.flag}


class PendingStore:
    def __init__(
        self,
        state_dir: pathlib.Path,
        clock: typing.Optional[typing.Callable[[], int]] = None,
    ):
        self.state_dir = pathlib.Path(state_dir)
        self._lock = threading.RLock()
        self._record: typing.Optional[PendingRecord] = None
        self._clock = clock or _epoch_ms

    def load(self) -> typing.Optional[PendingRecord]:
        with self._lock:
            path = self.state_dir / PENDING_FILENAME
            try:
                raw = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._record = None
                return None
            except OSError as exc:
                self._record = None
                self.quarantine(type(exc).__name__)
                return None
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._record = None
                self.quarantine(type(exc).__name__)
                return None
            if not isinstance(parsed, dict):
                self._record = None
                self.quarantine("NotADict")
                return None
            self._record = PendingRecord.from_dict(parsed)
            return self._record

    def quarantine(self, reason: str = "Unknown") -> pathlib.Path:
        with self._lock:
            dest = self.state_dir / f"pending.corrupt-{self._clock()}"
            os.replace(self.state_dir / PENDING_FILENAME, dest)
            _LOGGER.warning(
                "pending-state corrupt quarantined reason=%s", reason
            )
            return dest

    def snapshot(self) -> typing.Optional[PendingRecord]:
        with self._lock:
            return self._record

    def slot_occupied(self) -> bool:
        snapshot = self.snapshot()
        return snapshot is not None and snapshot.status not in TERMINAL_STATUSES

    def create(self, record: PendingRecord) -> PendingRecord:
        with self._lock:
            record.updated_at_ms = self._clock()
            self._write_atomic(record)
            self._record = record
            return record

    def update(
        self,
        mutate: typing.Callable[
            [typing.Optional[PendingRecord]], typing.Optional[PendingRecord]
        ],
    ) -> typing.Optional[PendingRecord]:
        with self._lock:  # RLock so handlers may nest
            new = mutate(self._record)
            if new is None:
                return None
            new.updated_at_ms = self._clock()
            self._write_atomic(new)
            self._record = new
            return new

    def _write_atomic(self, record: PendingRecord) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / PENDING_FILENAME
        tmp = self.state_dir / f"{PENDING_FILENAME}.tmp-{os.getpid()}-{threading.get_ident()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        dir_fd = os.open(self.state_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
