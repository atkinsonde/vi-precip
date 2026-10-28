import numpy as np

from vi_precip import indices

PSL = """ 1950 2026
 1950  -1.99  -1.69  -1.42  -1.54  -1.75  -1.25  -1.17  -1.06  -0.95  -1.23  -1.20  -1.37
 1951  -1.03  -0.81  -0.36   0.02   0.37   0.62   0.98   1.14   1.15   1.15   1.24   1.17
 2026   0.10   0.30   0.50   0.80   1.10   1.50   1.90   2.30   2.84 -99.99 -99.99 -99.99
  -99.99
  Nino Anom 3.4 Index  using ersstv5 from CPC
"""

RONI = """<table><tr><th>Year</th><th>DJF</th><th>JFM</th></tr>
<tr><td>2025</td><td>-0.6</td><td>-0.5</td><td>-0.3</td><td>-0.2</td><td>-0.1</td><td>0.0</td><td>0.1</td><td>0.2</td><td>0.3</td><td>0.2</td><td>0.1</td><td>0.0</td></tr>
<tr><td>2026</td><td>0.1</td><td>0.2</td><td>0.4</td><td>0.6</td><td>0.9</td><td>1.2</td><td>1.5</td><td>1.8</td><td></td><td></td><td></td><td></td></tr></table>"""


def test_parse_psl_and_seasons():
    df = indices.parse_psl(PSL)
    assert df["year"].min() == 1950 and df[df["year"] == 2026]["month"].max() == 9
    s = indices.seasonal(df.assign(index="nino34"))
    jas = s[(s["year"] == 2026) & (s["season"] == "JAS")]["value"].iloc[0]
    assert np.isclose(jas, (1.9 + 2.3 + 2.84) / 3, atol=1e-3)
    assert not ((s["year"] == 2026) & (s["season"] == "ASO")).any()


def test_parse_roni():
    df = indices.parse_roni_html(RONI)
    assert set(df["year"]) == {2025, 2026}
    s = indices.seasonal(df.assign(index="roni"))
    assert s[(s["year"] == 2026) & (s["season"] == "JAS")]["value"].iloc[0] == 1.8  # centre month Aug
    assert s[(s["year"] == 2026) & (s["season"] == "JJA")]["value"].iloc[0] == 1.5
