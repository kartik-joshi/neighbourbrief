"""Build config/neighbourhood_languages_2021.json from the City of Toronto's 2021 Neighbourhood
Profiles (158-neighbourhood model, Toronto Open Data "neighbourhood-profiles").

For every neighbourhood: mother tongue (single responses), non-official languages, share of the
neighbourhood's population, and which ones NeighbourBrief can translate into.

    .venv/bin/python scripts/build_language_profiles.py [path/to/neighbourhood-profiles-2021-158.xlsx]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "reference" / "neighbourhood-profiles-2021-158.xlsx"
OUT = ROOT / "config" / "neighbourhood_languages_2021.json"

# Census language name → NeighbourBrief language code (None = spoken here, not translated yet).
CODE = {
    "Urdu": "ur", "Pashto": "ps", "Arabic": "ar", "Iranian Persian": "fa", "Persian (Farsi), n.o.s.": "fa",
    "Dari": "fa", "Yue (Cantonese)": "zh-Hant", "Mandarin": "zh-Hans", "Tamil": "ta", "Russian": "ru",
    "Tagalog (Pilipino, Filipino)": "fil", "Italian": "it", "Portuguese": "pt-pt", "Spanish": "es",
    "Korean": "ko", "Vietnamese": "vi", "Polish": "pl", "Bengali": "bn", "Greek": "el", "Gujarati": "gu",
    "Ukrainian": "uk", "Punjabi (Panjabi)": "pa", "Somali": "so",
}
DISPLAY = {"Yue (Cantonese)": "Cantonese", "Tagalog (Pilipino, Filipino)": "Tagalog/Filipino",
           "Iranian Persian": "Persian", "Persian (Farsi), n.o.s.": "Persian", "Punjabi (Panjabi)": "Punjabi",
           "Min Nan (Chin Chow, Teochow, Fukien, Taiwanese)": "Min Nan (Teochew, Hokkien, Taiwanese)"}


def indent(label: str) -> int:
    return len(label) - len(label.lstrip(" "))


CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id=neighbourhood-profiles"


def download(path: Path) -> None:
    import httpx
    meta = httpx.get(CKAN, timeout=30).json()["result"]["resources"]
    url = next(r["url"] for r in meta if r["name"] == "neighbourhood-profiles-2021-158-model")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(httpx.get(url, timeout=120, follow_redirects=True).content)


def main(path: Path) -> int:
    import openpyxl
    if not path.exists():
        print("Downloading the 2021 Neighbourhood Profiles from Toronto Open Data…")
        download(path)

    rows = list(openpyxl.load_workbook(path, read_only=True)["hd2021_census_profile"].iter_rows(values_only=True))
    names, numbers = rows[0], rows[1]
    start = next(i for i, r in enumerate(rows) if r[0] and str(r[0]).startswith("Total - Mother tongue"))
    single = next(i for i in range(start, len(rows)) if str(rows[i][0]).strip() == "Single responses")
    end = next(i for i in range(single + 1, len(rows)) if indent(str(rows[i][0])) <= indent(str(rows[single][0])))
    non_official = next(i for i in range(single, end) if str(rows[i][0]).strip() == "Non-official languages")

    # Leaf rows under "Non-official languages" are individual languages.
    leaves = []
    for i in range(non_official + 1, end):
        lab = str(rows[i][0])
        nxt = str(rows[i + 1][0]) if i + 1 < len(rows) else ""
        if indent(nxt) <= indent(lab) or i + 1 == end:
            name = lab.strip()
            if "n.i.e." in name or name.endswith("languages, n.o.s."):
                continue
            leaves.append(i)

    out = {"_source": ("City of Toronto, Neighbourhood Profiles 2021 (158-neighbourhood model), Toronto Open "
                       "Data 'neighbourhood-profiles'. Mother tongue, single responses, 25% sample data. "
                       "Share = speakers / neighbourhood population in private households."),
           "neighbourhoods": {}}
    for col in range(1, len(names)):
        hid = str(numbers[col]).zfill(3)
        total = rows[start][col] or 0
        langs = []
        for i in leaves:
            n = rows[i][col] or 0
            if not n or not total:
                continue
            census = str(rows[i][0]).strip()
            langs.append({"language": DISPLAY.get(census, census), "census_name": census,
                          "code": CODE.get(census), "speakers": int(n),
                          "share": round(100 * n / total, 1)})
        langs.sort(key=lambda x: -x["speakers"])
        out["neighbourhoods"][hid] = {"name": names[col], "population": int(total), "languages": langs[:12]}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(out['neighbourhoods'])} neighbourhoods")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else SRC))
