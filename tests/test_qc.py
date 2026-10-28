import datetime as dt

import numpy as np
import pandas as pd

from vi_precip import qc

RULES = qc.FlagRules(
    flag_status={"M": "missing", "T": "trace", "A": "accumulated", "F": "accumulated", "E": "estimated",
                 "^": "incomplete"},
    status_treatment={"incomplete": "valid"},
    max_accum_days=10,
)


def frame(vals, flags, start="2026-09-01"):
    idx = pd.date_range(start, periods=len(vals), freq="D")
    d = pd.DataFrame({"date": idx, "precip": vals, "precip_flag": flags, "tmax": 15.0, "tmin": 8.0,
                      "tmax_flag": None, "tmin_flag": None})
    return qc.annotate(qc.complete_index(d, idx[0].date(), idx[-1].date(), "X"), RULES)


def test_trace_is_zero_and_valid():
    a = frame([1.0, 0.0, np.nan], [None, "T", None])
    assert a["precip"].iloc[1] == 0.0
    assert a["daily_valid"].tolist() == [True, True, False]
    assert a["status"].iloc[2] == "missing"


def test_accumulation_encoding_a():
    # total on the flagged day; preceding no-value days are in the block
    a = frame([2.0, np.nan, np.nan, 12.0, 1.0], [None, "M", None, "A", None])
    assert a["acc_block"].tolist() == [-1, 0, 0, 0, -1]
    assert not a["daily_valid"].iloc[1:4].any()
    ps = qc.period_stats(a, dt.date(2026, 9, 1), dt.date(2026, 9, 5))
    assert ps.n_valid_total == 5 and ps.total_mm == 15.0
    # A period that cuts through the block: block days inside are missing, total excluded.
    ps = qc.period_stats(a, dt.date(2026, 9, 3), dt.date(2026, 9, 5))
    assert ps.n_valid_total == 1 and ps.total_mm == 1.0


def test_accumulation_encoding_b():
    # flagged no-value days, total on the following unflagged day
    a = frame([1.0, np.nan, np.nan, 9.0, 0.0], [None, "A", "A", None, None])
    assert a["acc_block"].tolist() == [-1, 0, 0, 0, -1]
    ps = qc.period_stats(a, dt.date(2026, 9, 1), dt.date(2026, 9, 5))
    assert ps.total_mm == 10.0 and ps.n_valid_total == 5 and ps.n_valid_daily == 2


def test_overlong_accumulation_is_unresolved():
    vals = [np.nan] * 12 + [40.0]
    flags = ["M"] * 12 + ["A"]
    a = frame(vals, flags)
    assert (a["acc_block"] == -1).all()
    assert a["daily_valid"].sum() == 0


def test_incomplete_can_be_made_invalid():
    rules = qc.FlagRules(flag_status={"^": "incomplete"}, status_treatment={"incomplete": "invalid"})
    idx = pd.date_range("2026-09-01", periods=2, freq="D")
    d = pd.DataFrame({"date": idx, "precip": [3.0, 4.0], "precip_flag": [None, "^"], "tmax": 1.0, "tmin": 0.0,
                      "tmax_flag": None, "tmin_flag": None})
    a = qc.annotate(qc.complete_index(d, idx[0].date(), idx[-1].date(), "X"), rules)
    assert a["daily_valid"].tolist() == [True, False]
    assert a["status"].iloc[1] == "invalid"


def test_missing_rows_become_missing_days():
    idx = pd.to_datetime(["2026-09-01", "2026-09-03"])
    d = pd.DataFrame({"date": idx, "precip": [1.0, 2.0], "precip_flag": [None, None], "tmax": 1.0, "tmin": 0.0,
                      "tmax_flag": None, "tmin_flag": None})
    a = qc.annotate(qc.complete_index(d, dt.date(2026, 9, 1), dt.date(2026, 9, 3), "X"), RULES)
    assert a["status"].tolist() == ["ok", "missing", "ok"]
    ps = qc.period_stats(a, dt.date(2026, 9, 1), dt.date(2026, 9, 3))
    assert ps.max_consec_missing_daily == 1


def test_flag_priority():
    assert RULES.status_of("MT") == "missing"
    assert RULES.status_of("E") == "estimated"
    assert RULES.status_of("Z") == "unknown"
    assert RULES.status_of(None) == "ok"
