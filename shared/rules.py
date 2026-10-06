"""Deterministic, clock-injected rules shared by real and simulated agents."""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field, asdict
from uuid import uuid4
from .version import RULE_VERSION

DIRECTIONS = ("DOWN", "LEFT", "RIGHT")


@dataclass
class State:
    lifecycle: str = "READY"
    access: str = "OPEN"
    reason: str | None = None
    lock_id: str | None = None
    version: int = 0
    epoch: int = 1
    strikes: list[dict] = field(default_factory=list)
    locks: int = 0

    def __post_init__(self):
        if self.lifecycle not in (
            "READY",
            "RUNNING",
            "COMPLETED",
        ) or self.access not in ("OPEN", "LOCKED"):
            raise ValueError("INVALID_STATE")
        if any(
            type(x) is not int or x < 0 for x in (self.version, self.epoch, self.locks)
        ):
            raise ValueError("INVALID_STATE_VERSION")
        if not isinstance(self.strikes, list) or len(self.strikes) > 10000:
            raise ValueError("INVALID_STRIKES")
        for strike in self.strikes:
            if (
                not isinstance(strike, dict)
                or not isinstance(strike.get("id"), str)
                or strike.get("direction") not in DIRECTIONS
                or type(strike.get("epoch")) is not int
            ):
                raise ValueError("INVALID_STRIKE")
        if self.access == "LOCKED" and (not self.lock_id or not self.reason):
            raise ValueError("INVALID_LOCK")

    def counts(self):
        return {
            d: sum(
                s["direction"] == d
                and s["epoch"] == self.epoch
                and not s.get("rejected")
                for s in self.strikes
            )
            for d in DIRECTIONS
        }

    def public(self):
        return {**asdict(self), "counts": self.counts()}


class RuleEngine:
    def __init__(self, state: State | None = None):
        self.state = state or State()
        self.reset_observation()

    def reset_observation(self):
        self.last_t = None
        self.previous = "UNKNOWN"
        self.candidate = None
        self.candidate_start = None
        self.seconds = 0.0
        self.away_total = 0.0
        self.away_start = None
        self.counted = False
        self.screen_start = None
        self.unknown_start = None
        self.phone_samples = deque(maxlen=3)
        self.phone_present = False
        self.phone_clear_at = None
        self.absent_start = None
        self.absent_active = False
        self.second_start = None
        self.second_active = False
        self.short_episodes = deque()
        self.last_frequent = -1e10
        self.active_event = None
        self.duration_events = {}
        self.absence_blocked = False

    def event(self, kind, t, **extra):
        return {
            "id": str(uuid4()),
            "type": kind,
            "at": t,
            "start": self.away_start if self.away_start is not None else t,
            "epoch": self.state.epoch,
            "direction": None,
            "category": "REVIEW",
            "rule_version": RULE_VERSION,
            **extra,
        }

    def lock(self, reason):
        if self.state.lifecycle != "RUNNING":
            raise ValueError("SESSION_NOT_RUNNING")
        # A new critical cause supersedes the old lock, making queued unlocks stale.
        if self.state.access == "LOCKED" and self.state.reason == reason:
            return
        self.state.access = "LOCKED"
        self.state.reason = reason
        self.state.lock_id = str(uuid4())
        self.state.version += 1
        self.state.locks += 1

    def start(self):
        if self.state.lifecycle == "RUNNING":
            return
        if self.state.lifecycle == "COMPLETED":
            raise ValueError("SESSION_COMPLETED")
        self.state.lifecycle = "RUNNING"
        self.state.version += 1
        self.reset_observation()

    def unlock(self, lock_id, expected_version):
        if (
            self.state.access != "LOCKED"
            or self.state.lock_id != lock_id
            or self.state.version != expected_version
        ):
            raise ValueError("STATE_CONFLICT")
        if self.phone_present:
            raise ValueError("PHONE_STILL_PRESENT")
        if self.absent_start is not None:
            raise ValueError("FACE_STILL_ABSENT")
        self.state.access = "OPEN"
        self.state.reason = None
        self.state.lock_id = None
        self.state.epoch += 1
        self.state.version += 1

    def end(self):
        self.state.lifecycle = "COMPLETED"
        self.state.access = "OPEN"
        self.state.reason = None
        self.state.lock_id = None
        self.state.version += 1

    def review(self, event_id, decision):
        for strike in self.state.strikes:
            if strike["id"] == event_id:
                strike["rejected"] = decision == "REJECTED"
        self.state.version += 1
        if self.state.lifecycle == "RUNNING" and self.state.access == "OPEN":
            for d, n in self.state.counts().items():
                if n >= 3:
                    self.lock("GAZE_" + d)

    def observe(self, t: float, direction="SCREEN", phone_confidence=0.0, faces=1, phone_aiming=False):
        if self.state.lifecycle != "RUNNING":
            return []
        if self.last_t is not None and t < self.last_t:
            raise ValueError("NON_MONOTONIC_TIME")
        dt = 0.0 if self.last_t is None else t - self.last_t
        gap_event = None
        # No extrapolation across a missing capture interval.
        if dt > 0.5:
            if self.active_event:
                gap_event = {"id": self.active_event, "update": True, "end": self.last_t}
                self.active_event = None
            self.seconds = 0.0
            self.candidate = None
            self.screen_start = None
            self.unknown_start = None
            self.second_start = None
            self.absent_start = None
            self.phone_clear_at = None
            self.phone_samples.clear()
            dt = 0.0
        self.last_t = t
        events = [gap_event] if gap_event else []
        if phone_aiming and phone_confidence >= .65:
            events.append(self.event("PHONE_AIM_REVIEW", t, start=max(0, t - 2.5),
                                     confidence=phone_confidence, detail="Подъём и удержание телефона: возможная попытка съёмки; факт фотографии не установлен"))
        def close_interval(key, end):
            event_id = self.duration_events.pop(key, None)
            if event_id:
                events.append({"id": event_id, "update": True, "end": end})

        def interval(key, kind, **fields):
            event = self.event(kind, t, ongoing=True, **fields)
            self.duration_events[key] = event["id"]
            events.append(event)

        self.phone_samples.append((t, phone_confidence >= 0.65))
        present = sum(yes and t - ts <= 0.6 for ts, yes in self.phone_samples) >= 2
        if present:
            self.phone_clear_at = None
            if not self.phone_present:
                interval("phone", "PHONE_DETECTED", start=t,
                         category="CRITICAL", confidence=phone_confidence)
                self.lock("PHONE_DETECTED")
                self.phone_present = True
        elif phone_confidence < 0.65:
            if self.phone_clear_at is None:
                self.phone_clear_at = t
            if t - self.phone_clear_at >= 1:
                self.phone_present = False
                close_interval("phone", self.phone_clear_at)
        if faces == 0:
            if self.absent_start is None:
                self.absent_start = t
            if t - self.absent_start >= 3 - 1e-9 and not self.absent_active:
                interval("absence", "FACE_ABSENCE_REVIEW", start=self.absent_start)
                self.absent_active = True
            if t - self.absent_start >= 10 - 1e-9 and not self.absence_blocked:
                events.append(self.event("FACE_ABSENCE_TECHNICAL", t,
                                         start=self.absent_start, category="TECHNICAL"))
                self.lock("FACE_ABSENCE_TECHNICAL")
                self.absence_blocked = True
        else:
            close_interval("absence", t)
            self.absent_start = None
            self.absent_active = False
            self.absence_blocked = False
        if faces >= 2:
            if self.second_start is None:
                self.second_start = t
            if t - self.second_start >= 1 and not self.second_active:
                interval("second", "SECOND_FACE_REVIEW", start=self.second_start)
                self.second_active = True
        else:
            close_interval("second", t)
            self.second_start = None
            self.second_active = False
        if faces != 1:
            direction = "UNKNOWN"
        if self.active_event and direction not in (self.previous, "SCREEN"):
            events.append({"id": self.active_event, "update": True, "end": t})
            self.active_event = None
        if direction == "SCREEN":
            self.candidate = None
            self.seconds = 0.0
            if self.screen_start is None:
                self.screen_start = t
            if t - self.screen_start >= 1:
                if (
                    self.away_start is not None
                    and not self.counted
                    and 1 <= self.away_total < 5
                ):
                    self.short_episodes.append((t, self.away_total, self.away_start))
                while self.short_episodes and t - self.short_episodes[0][0] > 60:
                    self.short_episodes.popleft()
                if (
                    len(self.short_episodes) >= 3
                    and sum(x[1] for x in self.short_episodes) >= 6 - 1e-9
                    and t - self.last_frequent >= 60
                ):
                    events.append(
                        self.event(
                            "FREQUENT_GAZE_REVIEW",
                            t,
                            start=self.short_episodes[0][2],
                            intervals=list(self.short_episodes),
                        )
                    )
                    self.last_frequent = t
                if self.active_event:
                    events.append(
                        {
                            "id": self.active_event,
                            "update": True,
                            "end": self.screen_start,
                        }
                    )
                self.candidate = None
                self.seconds = 0
                self.away_total = 0
                self.away_start = None
                self.counted = False
                self.active_event = None
            self.unknown_start = None
        elif direction in DIRECTIONS:
            self.screen_start = None
            if self.away_start is None:
                self.away_start = t
            if self.candidate != direction:
                self.candidate = direction
                self.candidate_start = t
                self.seconds = 0
            elif self.previous == direction:
                self.seconds += dt
            if self.previous in DIRECTIONS:
                self.away_total += dt
            self.unknown_start = None
            if (
                self.seconds >= 5 - 1e-9
                and not self.counted
                and self.state.access == "OPEN"
            ):
                e = self.event(
                    "GAZE_" + direction,
                    t,
                    direction=direction,
                    start=self.candidate_start,
                    category="GAZE_STRIKE",
                    ongoing=True,
                    duration=round(self.seconds, 3),
                )
                self.state.strikes.append(
                    {
                        "id": e["id"],
                        "direction": direction,
                        "epoch": self.state.epoch,
                        "rejected": False,
                    }
                )
                self.state.version += 1
                self.counted = True
                self.active_event = e["id"]
                events.append(e)
                if self.state.counts()[direction] >= 3:
                    self.lock("GAZE_" + direction)
        else:
            self.screen_start = None
            # UNKNOWN is never evidence of a continuous, confident departure.
            self.candidate = None
            self.seconds = 0
            if self.unknown_start is None:
                self.unknown_start = t
            if t - self.unknown_start > 0.5:
                self.candidate = None
                self.seconds = 0
        self.previous = direction
        return events
