"""External survey-grade outcome cross-check (GDACS declared disasters)."""
from __future__ import annotations

import datetime as _dt

from disastermind.ml.validation import external as E


def test_gdacs_fixture_is_real_and_sizable():
    events = E.load_gdacs()
    assert len(events) > 50  # real declared Indian disaster events
    assert {e["eventtype"] for e in events} >= {"FL", "TC"}
    # alert levels present (case-normalised by the consumer)
    assert any(str(e.get("alertlevel", "")).lower() in E.ALERT_RANK for e in events)


def test_flood_event_days_expands_windows_and_honours_min_alert():
    events = [
        {"eventtype": "FL", "alertlevel": "Red", "fromdate": "2020-08-01", "todate": "2020-08-03"},
        {"eventtype": "FL", "alertlevel": "Green", "fromdate": "2020-09-01", "todate": "2020-09-01"},
        {"eventtype": "TC", "alertlevel": "Red", "fromdate": "2020-08-10", "todate": "2020-08-10"},
    ]
    days = E.flood_event_days(events, min_alert="orange")
    # the 3-day Red flood window expands to 3 dated days, all rank 2
    assert days[_dt.date(2020, 8, 2)] == 2
    assert len(days) == 3
    # the Green flood is below min_alert -> excluded; the TC is not a flood -> excluded
    assert _dt.date(2020, 9, 1) not in days
    assert _dt.date(2020, 8, 10) not in days


def test_cross_check_separates_declared_days_and_shows_gradient():
    # model risk is high exactly on the declared window, low otherwise ->
    # the external label must be recovered with high AUC
    events = [
        {"eventtype": "FL", "alertlevel": "Red", "fromdate": "2021-07-10", "todate": "2021-07-12"},
    ]
    dates, risks = [], []
    base = _dt.date(2021, 7, 1)
    for i in range(30):
        d = base + _dt.timedelta(days=i)
        dates.append(d)
        risks.append(0.9 if _dt.date(2021, 7, 10) <= d <= _dt.date(2021, 7, 12) else 0.1)
    res = E.cross_check_flood(dates, risks, events, min_alert="orange")
    assert res["n_declared_event_rows"] == 3
    assert res["auc_vs_declared_events"] == 1.0  # perfect separation recovered
    g = res["mean_risk_by_class"]
    assert g["red"] > g["quiet"]  # severity gradient in the right direction


def test_real_flood_model_cross_check_runs_and_is_honest():
    from disastermind.ml.validation import flood as F
    from disastermind.ml.validation.run import fit_logistic, predict

    rows = F.load_rows()
    tr, te = F.temporal_split(rows)
    step = max(1, len(tr) // 4000)
    trc = tr[::step]
    m = fit_logistic(
        [list(r.features) for r in trc], [r.label for r in trc],
        name="x", epochs=30, balanced=True,
    )
    pte = predict(m, [list(r.features) for r in te])
    by_date: dict = {}
    for r, p in zip(te, pte):
        by_date[r.date] = max(by_date.get(r.date, 0.0), p)
    dates = sorted(by_date)
    res = E.cross_check_flood(dates, [by_date[d] for d in dates], E.load_gdacs())
    # the check is external + real; assert it runs and the severity direction is
    # sane (declared days score at least as high as quiet days), not a fixed AUC
    assert res["n_declared_event_rows"] > 0
    g = res["mean_risk_by_class"]
    if g["red"] is not None and g["quiet"] is not None:
        assert g["red"] >= g["quiet"] - 0.05  # not systematically inverted
