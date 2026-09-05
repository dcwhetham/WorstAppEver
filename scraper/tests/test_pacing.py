"""Pacing policy: budgets, delay scaling, jitter, ETA."""

from __future__ import annotations

from worker.config import RuntimeSettings
from worker.pacing import EtaTracker, jittered_delay, plan_run

BASE = RuntimeSettings(
    min_delay_ms=4000,
    max_delay_ms=15000,
    items_per_run=25,
    backlog_pace_threshold=5,
    new_account_ramp_runs=6,
    jitter_ratio=0.4,
)


def test_empty_backlog_plans_no_work():
    plan = plan_run(backlog=0, is_new=False, is_favorite=False, settings=BASE)
    assert plan.budget == 0
    assert plan.requeue_delay_seconds == 0.0


def test_small_backlog_runs_unpaced_and_fast():
    plan = plan_run(backlog=3, is_new=False, is_favorite=False, settings=BASE)
    assert plan.budget == 3
    assert plan.paced is False
    assert plan.spread_over_runs == 1
    # Near the fast end of the configured range.
    assert plan.base_delay_ms < (BASE.min_delay_ms + BASE.max_delay_ms) / 2


def test_backlog_above_threshold_switches_on_pacing():
    plan = plan_run(backlog=40, is_new=False, is_favorite=False, settings=BASE)
    assert plan.paced is True
    assert plan.budget == BASE.items_per_run
    assert plan.requeue_delay_seconds > 0


def test_bigger_backlog_means_slower_not_faster():
    """The core pacing rule: a large catch-up gets gentler, not more aggressive."""
    small = plan_run(backlog=10, is_new=False, is_favorite=False, settings=BASE)
    large = plan_run(backlog=400, is_new=False, is_favorite=False, settings=BASE)
    assert large.base_delay_ms > small.base_delay_ms
    assert large.base_delay_ms <= BASE.max_delay_ms


def test_new_account_first_sync_is_spread_over_many_runs():
    plan = plan_run(backlog=900, is_new=True, is_favorite=False, settings=BASE)
    assert plan.paced is True
    # Never the whole profile in one go, however large the per-run cap.
    assert plan.budget <= BASE.items_per_run
    assert plan.spread_over_runs > 1
    assert "first sync" in plan.reason


def test_small_new_account_still_ramps():
    plan = plan_run(backlog=12, is_new=True, is_favorite=False, settings=BASE)
    assert plan.budget == 2  # ceil(12 / 6 ramp runs)
    assert plan.requeue_delay_seconds > 0


def test_favorites_get_a_speed_up_within_the_configured_range():
    normal = plan_run(backlog=50, is_new=False, is_favorite=False, settings=BASE)
    favorite = plan_run(backlog=50, is_new=False, is_favorite=True, settings=BASE)
    assert favorite.base_delay_ms < normal.base_delay_ms
    assert favorite.base_delay_ms >= BASE.min_delay_ms


def test_repeated_failures_back_further_off():
    healthy = plan_run(backlog=20, is_new=False, is_favorite=False, settings=BASE)
    failing = plan_run(backlog=20, is_new=False, is_favorite=False, settings=BASE, consecutive_failures=3)
    assert failing.base_delay_ms > healthy.base_delay_ms


def test_delays_stay_inside_the_configured_bounds():
    for backlog in (1, 5, 25, 100, 1000):
        for new in (True, False):
            plan = plan_run(backlog=backlog, is_new=new, is_favorite=False, settings=BASE)
            assert BASE.min_delay_ms <= plan.base_delay_ms <= BASE.max_delay_ms


def test_jitter_varies_and_occasionally_pauses_long():
    samples = [jittered_delay(5000, BASE) for _ in range(400)]
    assert len(set(samples)) > 350, "delays must not repeat; regular intervals are a fingerprint"
    # The occasional long pause is what breaks up an otherwise uniform stream.
    assert max(samples) > 5.0 * 1.5


def test_zero_jitter_still_returns_a_positive_delay():
    settings = RuntimeSettings(min_delay_ms=1, max_delay_ms=2, jitter_ratio=0.0)
    assert jittered_delay(1, settings) > 0


def test_eta_withholds_a_guess_until_it_has_samples():
    tracker = EtaTracker(window=5)
    assert tracker.eta_seconds(10) is None
    tracker.record(2.0)
    # One sample is noise, not an estimate.
    assert tracker.eta_seconds(10) is None
    tracker.record(2.0)
    assert tracker.eta_seconds(10) == 20


def test_eta_median_ignores_one_huge_outlier():
    tracker = EtaTracker(window=10)
    for _ in range(9):
        tracker.record(2.0)
    tracker.record(600.0)  # one enormous video
    assert tracker.eta_seconds(10) == 20


def test_eta_is_zero_when_nothing_remains():
    tracker = EtaTracker()
    tracker.record(3.0)
    assert tracker.eta_seconds(0) == 0


def test_seed_gives_the_ui_a_denominator_before_real_timings():
    tracker = EtaTracker()
    tracker.seed(5000)
    assert tracker.per_item_seconds is not None
    assert tracker.per_item_seconds > 5.0


def test_scheduled_block_handles_midnight_wraparound():
    overnight = RuntimeSettings(block_start_hour=22, block_end_hour=4)
    assert overnight.in_scheduled_block(23)
    assert overnight.in_scheduled_block(2)
    assert not overnight.in_scheduled_block(12)

    daytime = RuntimeSettings(block_start_hour=2, block_end_hour=6)
    assert daytime.in_scheduled_block(3)
    assert not daytime.in_scheduled_block(9)

    always = RuntimeSettings(block_start_hour=0, block_end_hour=0)
    assert all(always.in_scheduled_block(hour) for hour in range(24))
