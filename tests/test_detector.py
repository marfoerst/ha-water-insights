"""Tests for the event detector."""
from custom_components.water_insights.detector import EventDetector
from custom_components.water_insights.models import DetectorConfig

T0 = 1_800_000_000.0


def run_flow(series, step=2.0, det=None):
    """series: list of (duration_s, flow or None)."""
    det = det or EventDetector(DetectorConfig(), "flow")
    t, events = T0, []
    for dur, val in series:
        end = t + dur
        while t < end:
            events += det.feed(t, val)
            t += step
    events += det.tick(t + 300)
    return det, events


def test_flow_shower_detected():
    _, ev = run_flow([(120, 0.2), (300, 12.0), (300, 0.2)])
    assert len(ev) == 1
    assert abs(ev[0].volume_l - 60) < 1
    assert abs(ev[0].duration_s - 300) < 4


def test_below_min_volume_ignored():
    det, ev = run_flow([(60, 0.0), (20, 3.0), (200, 0.0)])
    assert ev == []
    assert abs(det.integral_l - 1.0) < 0.15


def test_short_pause_merges():
    _, ev = run_flow([(30, 0.0), (30, 8.0), (40, 0.0), (30, 8.0), (200, 0.0)])
    assert len(ev) == 1
    assert abs(ev[0].volume_l - 8.0) < 0.5


def test_long_pause_splits():
    _, ev = run_flow([(30, 0.0), (30, 8.0), (120, 0.0), (30, 8.0), (200, 0.0)])
    assert len(ev) == 2


def test_unavailable_closes_without_invention():
    det, ev = run_flow([(30, 0.0), (60, 10.0), (3600, None)])
    assert len(ev) == 1
    assert abs(ev[0].volume_l - 10) < 0.5
    assert det.integral_l < 11


def test_steady_flow_without_updates_counted_by_tick():
    det = EventDetector(DetectorConfig(), "flow")
    det.feed(T0, 0.0)
    det.feed(T0 + 10, 12.0)
    ev = []
    for i in range(1, 31):  # ticks every 10 s for 300 s, no new readings
        ev += det.tick(T0 + 10 + i * 10)
    ev += det.feed(T0 + 310, 0.0)
    ev += det.tick(T0 + 500)
    assert len(ev) == 1
    assert abs(ev[0].volume_l - 60) < 1


def test_counter_mode_jump_ignored():
    det = EventDetector(DetectorConfig(), "counter")
    ev = []
    ev += det.feed(T0, 199_900.0)
    ev += det.feed(T0 + 60, 158_368.0)  # backwards jump
    ev += det.feed(T0 + 120, 158_369.0)
    ev += det.feed(T0 + 180, 200_000.0)  # forward jump
    for i in range(1, 13):
        ev += det.feed(T0 + 180 + i * 10, 200_000.0 + i * 0.4)
    ev += det.tick(T0 + 1000)
    assert all(0 < e.volume_l < 10 for e in ev)
    assert det.integral_l < 10


def test_counter_mode_derives_flow():
    det = EventDetector(DetectorConfig(), "counter")
    ev = []
    c = 1000.0
    ev += det.feed(T0, c)
    for i in range(1, 31):
        c += 2.0  # 2 L per 10 s = 12 L/min
        ev += det.feed(T0 + i * 10, c)
    ev += det.tick(T0 + 31 * 10 + 120)
    assert len(ev) == 1
    assert abs(ev[0].volume_l - 60) < 0.5


def test_samples_capped():
    _, ev = run_flow([(10, 0.0), (7200, 5.0), (200, 0.0)], step=1.0)
    assert len(ev) == 1
    assert len(ev[0].samples) <= 400


def test_implausible_flow_treated_as_gap():
    det, ev = run_flow([(30, 0.0), (10, 500.0), (60, 0.0)])
    assert ev == []
    assert det.integral_l < 0.1


def test_stale_flow_sensor_no_phantom_volume():
    det = EventDetector(DetectorConfig(), "flow")
    det.feed(T0, 0.0)
    det.feed(T0 + 10, 8.0)  # then the sensor silently stops reporting
    ev = []
    for i in range(1, 24 * 360):  # 24 h of ticks every 10 s
        ev += det.tick(T0 + 10 + i * 10)
    assert len(ev) == 1
    assert det.integral_l < 8.0 * 15 + 1  # at most the stale window
    assert not det.active
    assert len(ev[0].samples) <= 400
