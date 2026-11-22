"""Ebbinghaus forgetting-curve calculations for spaced repetition."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

_STAGE_STRENGTH_DAYS = (0.5, 1.0, 2.0, 4.0, 7.0, 15.0, 30.0)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def clamp_mastery(value: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(3, v))


def clamp_forgetting_stage(value: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(6, v))


def forgetting_strength_days(stage: int) -> float:
    return _STAGE_STRENGTH_DAYS[clamp_forgetting_stage(stage)]


def parse_study_quality(raw: Optional[str]) -> str:
    text = (raw or "").strip().lower()
    if text in {"poor", "bad", "weak", "差", "生疏", "忘", "不会"}:
        return "poor"
    if text in {"fair", "ok", "okay", "medium", "一般", "还行", "半对", "有印象"}:
        return "fair"
    return "good"


def reviewed_at_for_quality(stage: int, quality: str, now: Optional[datetime] = None) -> str:
    now_dt = now or utc_now()
    if quality == "poor":
        target = 0.4
    elif quality == "fair":
        target = 0.72
    else:
        return now_dt.isoformat().replace("+00:00", "Z")
    days = -math.log(target) * max(forgetting_strength_days(stage), 0.25)
    adjusted = now_dt - timedelta(days=days)
    return adjusted.isoformat().replace("+00:00", "Z")


def stage_base_mastery(stage: int) -> int:
    s = clamp_forgetting_stage(stage)
    if s == 0:
        return 1
    if s in (1, 2, 3):
        return 2
    return 3


def parse_reviewed_at(raw: Optional[str]) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def lapse_forgetting(stage: int, days: float) -> Tuple[int, float, float]:
    stage = clamp_forgetting_stage(stage)
    days = max(0.0, float(days))
    retention = 1.0
    while stage > 0 and days > forgetting_strength_days(stage):
        retention *= math.exp(-1.0)
        days -= forgetting_strength_days(stage)
        stage -= 1
    retention *= math.exp(-days / forgetting_strength_days(stage))
    return stage, days, retention


def days_since_reviewed(raw: Optional[str], now: Optional[datetime] = None) -> float:
    dt = parse_reviewed_at(raw)
    if dt is None:
        return 0.0
    now_dt = now or utc_now()
    return max(0.0, (now_dt - dt).total_seconds()) / 86400.0


def days_between_reviewed(from_raw: Optional[str], to_raw: str) -> float:
    start = parse_reviewed_at(from_raw)
    if start is None:
        return 0.0
    end = parse_reviewed_at(to_raw) or utc_now()
    return max(0.0, (end - start).total_seconds()) / 86400.0


def next_forgetting_stage(
    prev_stage: int,
    prev_reviewed: Optional[str],
    remembered: bool,
    at: str,
) -> int:
    had_review = bool((prev_reviewed or "").strip())
    if not had_review and clamp_forgetting_stage(prev_stage) <= 0:
        return 0
    days = days_between_reviewed(prev_reviewed, at)
    stage, _days, retention = lapse_forgetting(prev_stage, days)
    if not remembered:
        return max(0, stage - 1)
    too_soon = days < forgetting_strength_days(prev_stage) * 0.35 and days < 0.75
    if too_soon and retention >= 0.85:
        return stage
    if retention >= 0.3:
        return min(6, stage + 1)
    return max(0, stage - 1)


def retention_ratio(stage: int, last_reviewed: Optional[str], now: Optional[datetime] = None) -> float:
    if not (last_reviewed or "").strip():
        return 0.0
    _s, _d, retention = lapse_forgetting(stage, days_since_reviewed(last_reviewed, now=now))
    return round(max(0.0, min(1.0, retention)), 4)


def effective_mastery(
    stored: int,
    stage: int,
    last_reviewed: Optional[str],
    now: Optional[datetime] = None,
) -> int:
    reviewed = (last_reviewed or "").strip() or None
    if stored == 0 and clamp_forgetting_stage(stage) == 0 and reviewed is None:
        return 0
    lapsed_stage, _days, retention = lapse_forgetting(stage, days_since_reviewed(reviewed, now=now))
    base = stage_base_mastery(lapsed_stage)
    if retention >= 0.7:
        return base
    if retention >= 0.4:
        return max(1, min(2, base))
    return 1


def next_review_due_iso(stage: int, last_reviewed: Optional[str]) -> Optional[str]:
    dt = parse_reviewed_at(last_reviewed)
    if dt is None:
        return None
    # Review recommended once retention approaches ~0.7 -> t = -ln(0.7) * strength (~0.357 * S)
    # Use full stage strength for scheduled review interval display.
    due = dt + timedelta(days=forgetting_strength_days(stage))
    return due.isoformat().replace("+00:00", "Z")
