"""Regression tests: one stop distance drives the ROI stop, the emergency cut and the dashboard SL."""
from src.risk.trade_manager import TradeManager, PositionTracker


def _tm(tmp_path):
    return TradeManager(
        reports_dir=str(tmp_path),
        min_holding_seconds=60.0,
        mae_roi_pct=-1.5,
        mae_loss_usd=-5.0,
        take_profit_roi_pct=5.0,
    )


def test_compute_stop_pct_only_counts_losing_side():
    assert PositionTracker.compute_stop_pct("LONG", 100.0, 97.5) == 2.5
    assert PositionTracker.compute_stop_pct("SHORT", 100.0, 102.5) == 2.5
    # A stop on the winning side (e.g. a ratcheted/breakeven stop) is not an initial risk distance
    assert PositionTracker.compute_stop_pct("LONG", 100.0, 101.0) is None
    assert PositionTracker.compute_stop_pct("LONG", 100.0, None) is None


def test_tracker_records_initial_stop_distance(tmp_path):
    tm = _tm(tmp_path)
    tr = tm.register_position("PONS", "LONG", 900.0, 0.3300, strategy="X", entry_time=1000.0, stop_price=0.3218)
    assert abs(tr.initial_stop_pct - 2.4848) < 0.01


def test_wide_stop_not_cut_by_fixed_dollar_or_roi_threshold(tmp_path):
    """Old behaviour: a -1.6% move on a 2.5% stop was cut by the fixed -1.5% / -$5 MAE. Now it must be held."""
    tm = _tm(tmp_path)
    entry = 0.3300
    tm.register_position("PONS", "LONG", 900.0, entry, strategy="X", entry_time=1000.0, stop_price=entry * 0.975)
    mark = entry * (1 - 0.016)  # -1.6% => about -$4.75 on 900 units: inside the stop
    act = tm.update_position("PONS", "LONG", 900.0, entry, mark, strategy="X", entry_time=1000.0, current_time=1000.0 + 300.0)
    assert not act.should_close, act.reason


def test_stop_enforced_at_displayed_stop_price(tmp_path):
    tm = _tm(tmp_path)
    entry = 0.3300
    stop = entry * 0.975
    tm.register_position("PONS", "LONG", 900.0, entry, strategy="X", entry_time=1000.0, stop_price=stop)
    act = tm.update_position("PONS", "LONG", 900.0, entry, stop * 0.9999, strategy="X", entry_time=1000.0, current_time=1000.0 + 300.0)
    assert act.should_close
    assert "Stop loss" in act.reason or "MAE" in act.reason


def test_emergency_cut_scales_with_stop_not_fixed_threshold(tmp_path):
    """Inside the min-hold window Rule 4 is suppressed; the emergency cut fires at 1.25x the initial stop."""
    tm = _tm(tmp_path)
    entry = 100.0
    tm.register_position("ABC", "LONG", 10.0, entry, strategy="X", entry_time=1000.0, stop_price=97.5)
    # -3.0% is beyond the stop (2.5%) but inside 1.25x (3.125%): suppressed by min-hold
    held = tm.update_position("ABC", "LONG", 10.0, entry, 97.0, strategy="X", entry_time=1000.0, current_time=1010.0)
    assert not held.should_close
    # -3.5% is beyond 1.25x: emergency cut
    cut = tm.update_position("ABC", "LONG", 10.0, entry, 96.5, strategy="X", entry_time=1000.0, current_time=1015.0)
    assert cut.should_close and "MAE" in cut.reason


def test_position_without_stop_still_uses_fixed_thresholds(tmp_path):
    tm = _tm(tmp_path)
    entry = 100.0
    tm.register_position("NOSTOP", "LONG", 10.0, entry, strategy="X", entry_time=1000.0)
    act = tm.update_position("NOSTOP", "LONG", 10.0, entry, 98.0, strategy="X", entry_time=1000.0, current_time=1010.0)
    assert act.should_close and "MAE" in act.reason
