"""Handshake monitor: the background thread driving await-birth to goal-armed.

One daemon thread, one tick per interval: snapshot the pending record and, for
an await-birth record, run the pinned sequence — the 120 s one-shot advisory
FIRST (notification only, the record never auto-clears), then tag matching
grouped by session with realpath confirmation, the ambiguity flag (an action-
free holding state), the single-match sequence (linkage -> goal resolve with
defer-on-ANY-failure -> copy -> notify -> ONE goal-armed patch), and the
conflict flag. A goal-armed record then runs the duplicate-paste re-copy, the
one-shot canary nudge, and confirmation (cleared). Every store mutation is
status- AND identity-conditional (same row_id + launch_click_ms), every
runner side effect is individually contained (copy failure -> retry next
tick; notify failure -> one WARNING line and advance), and the evaluation
cursor is data-derived from the session db rows, never from the wall clock.
The loop NEVER dies: any tick exception is a single ERROR line carrying the
exception type name only.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
import time
import typing

from mc_wall.server import state_contract, templates
from mc_wall.server.matcher import Matcher
from mc_wall.server.pending import (
    FLAG_AMBIGUOUS,
    STATUS_AWAIT_BIRTH,
    STATUS_CLEARED,
    STATUS_FLAGGED,
    STATUS_GOAL_ARMED,
    TERMINAL_STATUSES,
    PendingRecord,
    PendingStore,
)
from mc_wall.server.runner import Runner

ADVISORY_120S_MS = 120_000
CANARY_MS = 60_000


def _epoch_ms() -> int:
    return int(time.time() * 1000)


class HandshakeMonitor(threading.Thread):
    def __init__(
        self,
        pending: PendingStore,
        matcher: typing.Optional[Matcher],
        runner: Runner,
        collect_state: typing.Callable[[], dict],
        logger: logging.Logger,
        interval_s: float = 2.0,
        clock: typing.Optional[typing.Callable[[], int]] = None,
    ):
        super().__init__(daemon=True, name="mc-wall-handshake")
        self.pending = pending
        self.matcher = matcher
        self.runner = runner
        self.collect_state = collect_state
        self.logger = logger
        self.interval_s = interval_s
        self.clock = clock or _epoch_ms
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # never die — one line, type name only
                self.logger.error("monitor-tick-failed error=%s", type(exc).__name__)
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ tick

    def _tick(self) -> None:
        rec = self.pending.snapshot()
        if rec is None or rec.status in TERMINAL_STATUSES:
            return
        if rec.status == STATUS_AWAIT_BIRTH:
            self._await_birth_tick(rec)
        elif rec.status == STATUS_GOAL_ARMED:
            self._goal_armed_tick(rec)

    def _goal_armed_tick(self, rec: PendingRecord) -> None:
        """Pinned order per tick, all against the SAME rec snapshot:
        (1) duplicate paste -> goal re-copy (no status change), (2) one-shot
        canary nudge, (3) confirmation -> cleared, (4) persist the evaluation
        cursor so a restart never re-fires."""
        now = self.clock()
        if self.matcher is None:
            return
        matched = rec.matched_session_id
        if matched is None or rec.matched_at_ms is None:
            # No linkage to evaluate (never produced by the match sequence).
            return
        # 1. Duplicate paste: the prompt (sha-pinned) landed in the matched
        #    session again after the last evaluation cursor -> re-copy the
        #    goal. NO status change: the user may still be mid-handshake.
        dup_ms = self.matcher.find_duplicate_paste_ms(
            matched, rec.prompt_sha256, rec.last_eval_ms or 0
        )
        if dup_ms:
            try:
                row = state_contract.find_row(self.collect_state(), rec.row_id)
                goal_text = row.get(state_contract.GOAL_TEXT_KEY, "")
            except Exception as exc:  # UnknownRowError or any tower failure
                # Defer WITHOUT advancing the cursor: the duplicate must be
                # re-detected and re-copied once the tower is reachable again
                # (same defer rule as the match sequence).
                self.logger.info("goal-recopy-deferred reason=%s", type(exc).__name__)
                return
            try:
                self.runner.copy(
                    templates.render_file(
                        "goal_block.txt",
                        {
                            "goal_text": goal_text,
                            "lane_tag": rec.lane_tag,
                            "repo_root": rec.repo_root,
                        },
                    )
                )
            except Exception as exc:
                # The re-copy did not land: no cursor advance, retry next tick.
                self.logger.warning(
                    "monitor-copy-failed error=%s", type(exc).__name__
                )
                return
            try:
                self.runner.notify(*templates.notification("goal-re-copied", {}))
            except Exception as exc:
                # The clipboard is already correct — the tick still completes.
                self.logger.warning(
                    "monitor-notify-failed error=%s", type(exc).__name__
                )
        # 2. + 3. share one has_target_since probe: the canary fires only when
        #    NO target is seen, the confirmation only when one is — the two
        #    conditions are exclusive against this tick's snapshot.
        target_seen = self.matcher.has_target_since(matched, rec.matched_at_ms)
        if target_seen:
            # Confirmation: the /goal landed in the matched session -> the
            # slot is freed and the wall indicator drops.
            self._patch(
                rec, STATUS_GOAL_ARMED, status=STATUS_CLEARED, reason="goal-confirmed"
            )
            self.logger.info(
                "handshake status=cleared reason=goal-confirmed row_id=%s",
                rec.row_id,
            )
            return
        patches: typing.Dict[str, typing.Any] = {}
        if not rec.canary_fired and now - rec.matched_at_ms >= CANARY_MS:
            # target_seen is False here (the canary's has_target_since guard).
            try:
                self.runner.notify(
                    *templates.notification(
                        "canary-nudge", {"lane_tag": rec.lane_tag}
                    )
                )
            except Exception as exc:
                self.logger.warning(
                    "monitor-notify-failed error=%s", type(exc).__name__
                )
            patches["canary_fired"] = True
        # 4. The cursor stays DATA-DERIVED (the latest matching paste's
        #    time_created): it never advances past rows it has not evaluated,
        #    so a committed duplicate is always caught exactly once — and the
        #    persisted value means a restart cannot re-fire it either.
        new_cursor = max(dup_ms, rec.last_eval_ms or 0)
        if patches or new_cursor != (rec.last_eval_ms or 0):
            self._patch(rec, STATUS_GOAL_ARMED, last_eval_ms=new_cursor, **patches)

    def _patch(
        self, rec: PendingRecord, status_expected: str, **kw: typing.Any
    ) -> typing.Optional[PendingRecord]:
        """Status- AND identity-CONDITIONAL update: the mutation applies only
        when the LIVE record is still the one this tick snapshotted — same
        status, row_id, and launch_click_ms (unique per launch click). A
        cancel racing the tick leaves a terminal record untouched (never
        resurrected), and a cancel+relaunch that installed a NEW await-birth
        record can never be hijacked by a patch built for its predecessor."""

        def _mutate(current: typing.Optional[PendingRecord]):
            if (
                current is not None
                and current.status == status_expected
                and current.row_id == rec.row_id
                and current.launch_click_ms == rec.launch_click_ms
            ):
                return dataclasses.replace(current, **kw)
            return current

        return self.pending.update(_mutate)

    def _await_birth_tick(self, rec: PendingRecord) -> None:
        now = self.clock()
        # 120s one-shot advisory FIRST: notification only — no status change,
        # the pending record NEVER auto-clears. A failed notification is one
        # WARNING line; the one-shot flag still persists.
        if (
            not rec.advisory_120s_fired
            and now - rec.launch_click_ms >= ADVISORY_120S_MS
        ):
            try:
                self.runner.notify(
                    *templates.notification(
                        "still-no-session", {"lane_tag": rec.lane_tag}
                    )
                )
            except Exception as exc:
                self.logger.warning(
                    "monitor-notify-failed error=%s", type(exc).__name__
                )
            self._patch(rec, STATUS_AWAIT_BIRTH, advisory_120s_fired=True)
        if self.matcher is None:
            return
        candidates = self.matcher.find_candidates(rec.lane_tag, rec.launch_click_ms)
        by_session: typing.Dict[str, dict] = {}
        for cand in candidates:
            group = by_session.setdefault(
                cand["session_id"], {"directory": cand["directory"], "inputs": []}
            )
            group["inputs"].append(cand)
        valid = [
            sid
            for sid, group in by_session.items()
            if os.path.realpath(group["directory"]) == os.path.realpath(rec.repo_root)
        ]
        if len(valid) >= 2:
            # Ambiguity: flag only — status unchanged, nothing copied, no
            # notification; the slot stays held until the user re-copies.
            if rec.flag != FLAG_AMBIGUOUS:
                self._patch(rec, STATUS_AWAIT_BIRTH, flag=FLAG_AMBIGUOUS)
            return
        if len(valid) == 1:
            self._match(rec, valid[0], now)
            return
        # len(valid) == 0 with tag-matching sessions elsewhere (the SQL cursor
        # already guarantees they were created after the launch click).
        if by_session:
            self._patch(
                rec,
                STATUS_AWAIT_BIRTH,
                status=STATUS_FLAGGED,
                reason="conflict",
                flag=None,
            )
            try:
                self.runner.notify(
                    *templates.notification("conflict", {"lane_tag": rec.lane_tag})
                )
            except Exception as exc:
                # The flag is already durably persisted — the tick completes.
                self.logger.warning(
                    "monitor-notify-failed error=%s", type(exc).__name__
                )
            self.logger.info(
                "handshake status=flagged reason=conflict row_id=%s", rec.row_id
            )

    def _match(self, rec: PendingRecord, session_id: str, now: int) -> None:
        """Pinned match sequence: (1) linkage, (2) goal resolve (defer on ANY
        failure), (3) copy, (4) notify, (5) ONE goal-armed patch."""
        # (1) Persist the linkage first — a defer below keeps it and the next
        # tick re-finds and re-copies idempotently.
        self._patch(
            rec, STATUS_AWAIT_BIRTH, matched_session_id=session_id, matched_at_ms=now
        )
        try:
            row = state_contract.find_row(self.collect_state(), rec.row_id)
            goal_text = row.get(state_contract.GOAL_TEXT_KEY, "")
        except Exception as exc:  # UnknownRowError or any tower failure
            self.logger.info("match-deferred reason=%s", type(exc).__name__)
            return
        # (3) A failed copy leaves the record await-birth (linkage persisted):
        # the next tick re-finds and retries. Contained — never escapes the
        # tick, or the goal block would overwrite the clipboard every interval.
        try:
            self.runner.copy(
                templates.render_file(
                    "goal_block.txt",
                    {
                        "goal_text": goal_text,
                        "lane_tag": rec.lane_tag,
                        "repo_root": rec.repo_root,
                    },
                )
            )
        except Exception as exc:
            self.logger.warning("monitor-copy-failed error=%s", type(exc).__name__)
            return
        # (4) A failed notification must NOT block the handshake: the copy
        # succeeded, so the goal-armed transition still runs.
        try:
            self.runner.notify(
                *templates.notification("match-found", {"lane_tag": rec.lane_tag})
            )
        except Exception as exc:
            self.logger.warning(
                "monitor-notify-failed error=%s", type(exc).__name__
            )
        # (5) The evaluation cursor is DATA-DERIVED: the latest sendText
        # already sitting in the matched session, never the tick's clock
        # value. A clock read taken before the queries would leave a cursor
        # BEHIND a birth paste committed in between (the advisory-notify
        # window) — and the next goal-armed tick would read the user's OWN
        # birth paste as a duplicate ("prompt again" on a clean launch).
        data_cursor = self.matcher.session_input_cursor(
            session_id, rec.launch_click_ms
        )
        self._patch(
            rec,
            STATUS_AWAIT_BIRTH,
            status=STATUS_GOAL_ARMED,
            flag=None,
            last_eval_ms=max(data_cursor, rec.last_eval_ms or 0),
        )
        self.logger.info("handshake status=goal-armed row_id=%s", rec.row_id)
