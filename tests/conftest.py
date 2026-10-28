import shutil
from pathlib import Path

import pytest
import yaml

from vi_precip.config import load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path):
    raw = yaml.safe_load((ROOT / "config" / "config.yaml").read_text())
    raw["eccc"]["base_url"] = "https://api.example.test"
    raw["eccc"]["request_pause_s"] = 0
    raw["eccc"]["page_limit"] = 400
    raw["basemap"]["enabled"] = False
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "region_overrides.csv", tmp_path / "config")
    (tmp_path / "config" / "composites.csv").write_text(
        "composite_id,order,climate_id,use_from,use_to,note\n"
        "C_1021830,1,1021830,,2013-06-30,test\n"
        "C_1021830,2,1021831,2013-07-01,,\n"
    )
    p = tmp_path / "config" / "config.yaml"
    p.write_text(yaml.safe_dump(raw))
    return load_config(p, root=tmp_path)
