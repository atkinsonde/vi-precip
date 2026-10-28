import shutil
from pathlib import Path

import pandas as pd
from fakehydro import FakeHydroSession

from vi_precip import hydro

ROOT = Path(__file__).resolve().parents[1]


def test_parse_wo_csv_bilingual_params():
    txt = "﻿ ID,Date,Parameter/Paramètre,Value/Valeur,Symbol/Symbole\n08HA001,2020-01-01,discharge/débit,1.5,\n" \
          "08HA001,2020-01-01,water level/niveau,3.2,B\n08HA001,2020-01-02T08:00:00Z,6,1.7,\n"
    df = hydro.parse_wo_csv(txt)
    assert list(df["param"]) == ["flow", "level", "flow"] and df["date"].dt.day.tolist() == [1, 1, 2]


def test_hydro_run(cfg):
    shutil.copy(ROOT / "config" / "regulation.csv", cfg.root / "config")
    cfg.raw["hydro"] = {"pause_s": 0, "reviewed_natural": ["08HA003"], "min_years_enso": 30}
    s = FakeHydroSession()
    paths = hydro.run(cfg, session=s)
    r = pd.read_parquet(paths["hydro_ranks"]).set_index("station")
    assert "08HD000" not in r.index and "08MH000" not in r.index  # north of 49.80 / outside
    assert "08HB084" not in r.index  # excluded
    assert r.loc["08HA009", "param"] == "level"
    assert r.loc["08HA011", "regulation"] == "regulated"
    assert r.loc["08HA001", "oct_rank_base"] <= 3 and r.loc["08HA001", "oct_n_base"] == 2026 - 1985 + 1
    assert r.loc["08HA001", "oct_provisional_days"] == 26
    nat = pd.read_parquet(paths["hydro_natural"])
    ids = set(nat["station"])
    # RHBN (08HA001, 08HD000, 08FA000) + reviewed (08HA003); regulated Cowichan, lake, short Harris excluded
    assert ids == {"08HA001", "08HA003", "08HD000", "08FA000"}
    assert nat["year"].min() == 1950
    n = len(s.calls)
    hydro.run(cfg, session=s)
    assert len(s.calls) == n  # all cached
