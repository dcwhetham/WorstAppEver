"""Self-pacing, jitter and ETA estimation.

The counter-intuitive rule that shapes this module: **the bigger the backlog, the
slower we go.** A five-file catch-up can run at the fast end of the delay range
because it is over in a minute. A brand new account with 900 files is the
traffic pattern that actually gets an IP flagged, so it gets the slow end *and*
gets spread across many runs.

Three mechanisms:

* **Budget** — a hard cap on items per run. Work left over is re-queued with a
  delay, so a large backfill becomes a slow drip over hours instead of one
  suspicious burst.
* **Jitter** — every delay is randomised, and occasionally replaced by a much
  longer pause. Perfectly regular intervals are a fingerprint; a clean 8.000s
  gap between every request is not something a human produces.
* **ETA** — median observed seconds-per-item times items remaining, which the
  dashboard renders as the progress timer.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import deque
from dataclasses import dataclass

from .config import RuntimeSettings

# Chance that a pacing pause becomes a "the user got distracted" break.
LONG_PAUSE_PROBABILITY = 1 / 12
LONG_PAUSE_MULTIPLIER = (3.0, 8.0)

# Backlog at which pacing reaches maximum slowness. Beyond this, delays are
# already at the ceiling and only the budget keeps shrinking the exposure.
BACKLOG_SATURATION = 200


@dataclass(frozen=True)
class RunPlan:
    """How much work to do this run, and how slowly to do it."""

    budget: int
    base_delay_ms: int
    #: Human-readable justification, surfaced in the job's `message`.
    reason: str
    #: Runs this backlog is expected to take. 1 means it finishes now.
    spread_over_runs: int
    #: Delay before re-queueing the remainder. 0 when nothing is left over.
    requeue_delay_seconds: float
    paced: bool


def plan_run(
    *,
    backlog: int,
    is_new: bool,
    is_favorite: bool,
    settings: RuntimeSettings,
    consecutive_failures: int = 0,
) -> RunPlan:
    """Decide this run's budget and per-item delay."""
    if backlog <= 0:
        return RunPlan(0, settings.min_delay_ms, "nothing to download", 1, 0.0, False)

    ceiling = max(1, settings.items_per_run)

    if is_new:
        # First sync: never pull a whole profile in one go, however small it
        # looks. A new account downloading 900 files in an hour is the single
        # most detectable thing this tool could do.
        runs = max(1, settings.new_account_ramp_runs)
        budget = min(ceiling, max(1, math.ceil(backlog / runs)))
        reason = f"first sync ramped over ~{runs} runs ({backlog} items outstanding)"
        paced = True
    elif backlog >= max(1, settings.backlog_pace_threshold):
        budget = min(ceiling, backlog)
        reason = f"paced catch-up: {backlog} items missing"
        paced = True
    else:
        # Small delta from a routine incremental sync — the normal case.
        budget = min(ceiling, backlog)
        reason = f"incremental top-up: {backlog} items"
        paced = False

    # Favourites earn a modest speed-up, but stay inside the configured range.
    delay = _scaled_delay(backlog, settings, paced=paced, favorite=is_favorite)

    # Repeated failures usually mean the source is unhappy with us. Back further
    # off rather than retrying at the same cadence.
    if consecutive_failures:
        delay = int(min(settings.max_delay_ms * 4, delay * (1.5 ** min(consecutive_failures, 4))))

    remaining = max(0, backlog - budget)
    spread = max(1, math.ceil(backlog / budget)) if budget else 1
    requeue_delay = _requeue_delay(remaining, delay) if remaining else 0.0

    return RunPlan(
        budget=budget,
        base_delay_ms=delay,
        reason=reason,
        spread_over_runs=spread,
        requeue_delay_seconds=requeue_delay,
        paced=paced,
    )


def _scaled_delay(backlog: int, settings: RuntimeSettings, *, paced: bool, favorite: bool) -> int:
    """Interpolate between the configured min and max delay by backlog size."""
    low, high = sorted((settings.min_delay_ms, settings.max_delay_ms))
    if not paced:
        base = low + (high - low) * 0.2
    else:
        # Linear ramp to the ceiling as the backlog approaches saturation.
        weight = min(1.0, backlog / BACKLOG_SATURATION)
        base = low + (high - low) * (0.35 + 0.65 * weight)
    if favorite:
        base -= (base - low) * 0.25
    return int(max(low, min(high, base)))


def _requeue_delay(remaining: int, delay_ms: int) -> float:
    """Gap before the next slice of a large backfill.

    Scaled to the leftover work with a randomised component, so consecutive
    slices do not land on a predictable clock.
    """
    base = max(300.0, min(4 * 3600.0, remaining * delay_ms / 1000.0 * 0.25))
    return base * random.uniform(0.75, 1.35)


def jittered_delay(base_delay_ms: int, settings: RuntimeSettings) -> float:
    """Randomise one pacing pause. Returns seconds.

    Two layers: a proportional wobble on every delay, plus an occasional much
    longer pause. The long pause matters more than it looks — a request stream
    with no gaps larger than 15s reads as automated no matter how well the short
    intervals are randomised.
    """
    ratio = max(0.0, min(0.9, settings.jitter_ratio))
    delay = base_delay_ms * random.uniform(1.0 - ratio, 1.0 + ratio)
    if random.random() < LONG_PAUSE_PROBABILITY:
        delay *= random.uniform(*LONG_PAUSE_MULTIPLIER)
    return max(0.25, delay / 1000.0)


class EtaTracker:
    """Rolling estimate of seconds-per-item, for the dashboard's ETA timer.

    Median rather than mean: one 400 MB video among fifty photos would drag a
    mean estimate into uselessness, and a progress timer that lies is worse than
    no timer at all.
    """

    def __init__(self, window: int = 20) -> None:
        self._samples: deque[float] = deque(maxlen=max(3, window))

    def record(self, seconds: float) -> None:
        if seconds > 0:
            self._samples.append(seconds)

    @property
    def per_item_seconds(self) -> float | None:
        if not self._samples:
            return None
        return statistics.median(self._samples)

    def eta_seconds(self, remaining: int) -> int | None:
        """Estimated seconds to finish `remaining` items, or None if unknown.

        Returns None rather than a guess until there are at least two samples;
        an ETA extrapolated from a single download is noise dressed as data.
        """
        if remaining <= 0:
            return 0
        if len(self._samples) < 2:
            return None
        return int(round(statistics.median(self._samples) * remaining))

    def seed(self, base_delay_ms: int, assumed_transfer_seconds: float = 2.5) -> None:
        """Prime the estimate before any real timings exist.

        Gives the UI a rough denominator for a first sync instead of an empty
        bar, using the planned pacing delay plus a nominal transfer time.
        """
        self._samples.append(base_delay_ms / 1000.0 + assumed_transfer_seconds)
