import pytest

from vi_precip.domain import assign_region

# Approximate coordinates of real places, with the region each should land in.
CASES = {
    "Victoria Gonzales": (-123.325, 48.413, "East VI"),
    "Victoria Intl A": (-123.43, 48.647, "East VI"),
    "Sooke": (-123.72, 48.37, "East VI"),
    "Port Renfrew": (-124.42, 48.55, "West VI"),
    "North Cowichan": (-123.72, 48.82, "East VI"),
    "Lake Cowichan": (-124.05, 48.83, "East VI"),
    "Nanaimo A": (-123.87, 49.05, "East VI"),
    "Qualicum": (-124.44, 49.34, "East VI"),
    "Port Alberni": (-124.82, 49.24, "West VI"),
    "Comox A": (-124.89, 49.71, "East VI"),
    "Courtenay": (-125.0, 49.69, "East VI"),
    "Cumberland": (-125.03, 49.62, "East VI"),
    "Campbell River A": (-125.27, 49.95, "East VI"),
    "Tofino A": (-125.78, 49.08, "West VI"),
    "Ucluelet": (-125.54, 48.94, "West VI"),
    "Estevan Point": (-126.55, 49.38, "West VI"),
    "Gold River": (-126.06, 49.78, "West VI"),
    "Port Hardy A": (-127.37, 50.68, "North VI"),
    "Quatsino": (-127.65, 50.53, "North VI"),
    "Cape Scott": (-128.43, 50.78, "North VI"),
    "Sartine Island": (-128.91, 50.82, "North VI"),
    "Solander Island": (-127.94, 50.11, "West VI"),
    "Alert Bay": (-126.93, 50.59, "North VI"),
    "Sheringham Point": (-123.92, 48.38, "East VI"),
    "Pachena Point": (-125.10, 48.72, "West VI"),
    "Race Rocks": (-123.53, 48.30, "East VI"),
    "Discovery Island": (-123.22, 48.42, "East VI"),
    "Saltspring": (-123.50, 48.85, "Gulf Islands"),
    "Saturna": (-123.17, 48.78, "Gulf Islands"),
    "Galiano": (-123.40, 48.95, "Gulf Islands"),
    "Thetis": (-123.67, 48.98, "Gulf Islands"),
    "Gabriola": (-123.80, 49.16, "Gulf Islands"),
    "Texada": (-124.55, 49.76, "Gulf Islands"),
    "Lasqueti": (-124.35, 49.48, "Gulf Islands"),
    "Denman": (-124.80, 49.55, "Gulf Islands"),
    "Hornby": (-124.65, 49.52, "Gulf Islands"),
    "Vancouver Intl": (-123.18, 49.19, "Lower Mainland"),
    "Abbotsford": (-122.36, 49.03, "Lower Mainland"),
    "Agassiz": (-121.76, 49.24, "Lower Mainland"),
    "Hope A": (-121.48, 49.37, "Lower Mainland"),
    "Bowen Island": (-123.37, 49.38, "Lower Mainland"),
    "Point Atkinson": (-123.26, 49.33, "Lower Mainland"),
    "Squamish": (-123.16, 49.78, "Sea-to-Sky"),
    "Whistler": (-122.96, 50.13, "Sea-to-Sky"),
    "Gibsons": (-123.51, 49.40, "Sunshine Coast"),
    "Sechelt": (-123.76, 49.47, "Sunshine Coast"),
    "Pender Harbour": (-124.02, 49.63, "Sunshine Coast"),
    "Powell River A": (-124.50, 49.83, "Sunshine Coast"),
    "Lund": (-124.76, 49.98, "Sunshine Coast"),
    "Pemberton (outside)": (-122.80, 50.30, None),
    "Kelowna (outside)": (-119.4, 49.9, None),
    "Lillooet (outside)": (-121.93, 50.69, None),
}


@pytest.mark.parametrize("name", list(CASES))
def test_region(name):
    lon, lat, expected = CASES[name]
    assert assign_region([lon], [lat])[0] == expected
