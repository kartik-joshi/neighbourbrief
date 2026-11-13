"""Build the static public site: plain files that can be copied to any web host (no server, no login).

    .venv/bin/python scripts/build_public_site.py                      → public_site/
    .venv/bin/python scripts/build_public_site.py --hoods all          (profiles for all 158; slower)
    .venv/bin/python scripts/build_public_site.py --base-url https://example.org/nb/   (absolute links in the feed)

Contents: home with the latest published bulletins (every language, print-ready), neighbourhood
profiles (+ JSON), How we count, Data health, Accessibility statement, an Atom feed and embed cards
for partner websites. Only bulletins that are published AND still pass every number check are included.
Pages are rendered by the app itself in "public" mode (resident navigation only), then links are
rewritten to relative files; any link the site can't serve stops the build.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PUBLIC = {"x-nb-public": "1"}


class BuildError(RuntimeError):
    pass


def target_for(path: str) -> str | None:
    """Site-relative file for an app path (None = not part of the public site)."""
    base, _, frag = path.partition("#")
    base, _, query = base.partition("?")
    frag = "#" + frag if frag else ""
    fixed = {"/": "index.html", "/public": "index.html", "/how-we-count": "how-we-count.html",
             "/data-health": "data-health.html", "/data-health.json": "data-health.json",
             "/accessibility": "accessibility.html", "/privacy-ai": "privacy-ai.html", "/how-it-works": "how-it-works.html", "/neighbourhoods": "neighbourhoods/index.html",
             "/feed.xml": "feed.xml", "/api/boundaries.geojson": "boundaries.geojson"}
    if base in fixed:
        return fixed[base] + frag
    if base.startswith("/static/"):
        return base[1:]
    m = re.fullmatch(r"/neighbourhoods/(\d{3})(/data\.json|/feed\.xml)?", base)
    if m:
        ext = {"/data.json": "json", "/feed.xml": "xml"}.get(m.group(2) or "", "html")
        return f"neighbourhoods/{m.group(1)}.{ext}" + frag
    m = re.fullmatch(r"/bulletin/([\w-]+)/(numbers\.csv|numbers\.json|meeting\.ics|all|[\w-]+)", base)
    if m:
        name = m.group(2)
        if name in ("numbers.csv", "numbers.json", "meeting.ics"):
            return f"bulletins/{m.group(1)}/{name}"
        if name != "all" and "large=1" in query:
            return f"bulletins/{m.group(1)}/{name}-large.html" + frag
        return f"bulletins/{m.group(1)}/{name}.html" + frag
    if base == "/wards":
        return "wards/index.html" + frag
    m = re.fullmatch(r"/wards/(\d{1,2})", base)
    if m:
        return f"wards/{int(m.group(1))}.html" + frag
    m = re.fullmatch(r"/embed/(\d{1,3})", base)
    if m:
        return f"embed/{m.group(1).zfill(3)}.html"
    return None


def rewrite(html: str, out_path: str, known: set[str]) -> str:
    """Make every root-relative href/src point at a file of the site, relative to `out_path`."""
    prefix = "../" * out_path.count("/")

    def sub(m):
        attr, url = m.group(1), m.group(2)
        if url.startswith("//"):
            return m.group(0)
        t = target_for(url)
        if t is None or t.split("#")[0] not in known:
            raise BuildError(f"{out_path}: link to {url} is not part of the public site")
        return f'{attr}="{prefix}{t}"'
    return re.sub(r'\b(href|src)="(/[^"]*)"', sub, html)


def build(out: Path, hoods: str = "published", base_url: str = "", auto_bulletins: bool = True) -> dict:
    from fastapi.testclient import TestClient

    from app import main
    from app.data import facts as F

    client = TestClient(main.app)
    bulletins = main.published_bulletins()
    if hoods == "all":
        hood_ids = [h["id"] for h in F.list_neighbourhoods()]
    elif hoods == "published":
        hood_ids = sorted({b["hood"]["id"] for b in bulletins})
    else:
        hood_ids = [h.strip().zfill(3) for h in hoods.split(",") if h.strip()]

    pages: dict[str, str] = {  # site file -> app URL
        "index.html": "/public", "how-we-count.html": "/how-we-count", "data-health.html": "/data-health",
        "data-health.json": "/data-health.json", "accessibility.html": "/accessibility", "privacy-ai.html": "/privacy-ai", "how-it-works.html": "/how-it-works",
        "neighbourhoods/index.html": "/neighbourhoods?only=" + ",".join(hood_ids), "feed.xml": "/feed.xml",
        "boundaries.geojson": "/api/boundaries.geojson",
    }
    for hid in hood_ids:
        pages[f"neighbourhoods/{hid}.html"] = f"/neighbourhoods/{hid}"
        pages[f"neighbourhoods/{hid}.json"] = f"/neighbourhoods/{hid}/data.json"
        pages[f"neighbourhoods/{hid}.xml"] = f"/neighbourhoods/{hid}/feed.xml"
    autos = []  # automatic bulletins where no officer bulletin covers the profile's month
    if auto_bulletins:
        from app import auto
        for hid in hood_ids:
            try:
                rec = auto.build(hid)  # translates (Azure) and checks once; cached for the pages below
            except Exception as exc:
                print(f"  no automatic bulletin for {hid}: {exc}")
                continue
            mine = [b for b in bulletins if b["hood"]["id"] == hid and b["month_start"] == rec["facts"]["period"]["month_start"]]
            if not mine and auto.showable(rec):
                autos.append({"id": rec["id"], "rec": rec, "codes": ["en"] + rec["languages"]})
    for b in bulletins + autos:
        for c in b["codes"]:
            pages[f"bulletins/{b['id']}/{c}.html"] = f"/bulletin/{b['id']}/{c}"
            pages[f"bulletins/{b['id']}/{c}-large.html"] = f"/bulletin/{b['id']}/{c}?large=1"
        pages[f"bulletins/{b['id']}/all.html"] = f"/bulletin/{b['id']}/all"
        pages[f"bulletins/{b['id']}/easy.html"] = f"/bulletin/{b['id']}/easy"
        pages[f"bulletins/{b['id']}/numbers.csv"] = f"/bulletin/{b['id']}/numbers.csv"
        pages[f"bulletins/{b['id']}/numbers.json"] = f"/bulletin/{b['id']}/numbers.json"
        if (b["rec"].get("officer") or {}).get("meeting"):
            pages[f"bulletins/{b['id']}/meeting.ics"] = f"/bulletin/{b['id']}/meeting.ics"
    if len(hood_ids) >= 158 and auto_bulletins:  # ward briefs link to every neighbourhood and its bulletin
        from app.data import ward
        pages["wards/index.html"] = "/wards"
        for w in ward.wards():
            pages[f"wards/{w['number']}.html"] = f"/wards/{w['number']}"
    for hid in sorted({b["hood"]["id"] for b in bulletins} | {a["rec"]["facts"]["neighbourhood"]["id"] for a in autos}):
        pages[f"embed/{hid}.html"] = f"/embed/{hid}"

    static = {f"static/{p.relative_to(ROOT / 'app' / 'static').as_posix()}" for p in (ROOT / "app" / "static").rglob("*") if p.is_file()}
    known = set(pages) | static
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(ROOT / "app" / "static", out / "static")
    skipped = []
    for file, url in pages.items():
        r = client.get(url, headers=PUBLIC)
        if r.status_code != 200:
            if file.startswith("neighbourhoods/") and file != "neighbourhoods/index.html":
                skipped.append(f"{file} ({r.status_code})")
                continue
            raise BuildError(f"{url} returned {r.status_code}")
        dest = out / file
        dest.parent.mkdir(parents=True, exist_ok=True)
        if file.endswith(".html"):
            dest.write_text(rewrite(r.text, file, known), encoding="utf-8")
        elif file.endswith(".xml"):
            site = base_url.rstrip("/") + "/" if base_url else ""
            text = re.sub(r"http://testserver/bulletin/([\w-]+)/(\w[\w-]*)",
                          lambda m: f"{site}bulletins/{m.group(1)}/{m.group(2)}.html", r.text)
            text = re.sub(r"http://testserver/neighbourhoods/(\d{3})/feed\.xml", lambda m: f"{site}neighbourhoods/{m.group(1)}.xml", text)
            dest.write_text(text.replace("http://testserver/", site), encoding="utf-8")
        else:
            dest.write_bytes(r.content)
    (out / "README.txt").write_text(
        "NeighbourBrief TO public site: plain files, copy this folder to any web host.\n"
        "Built by scripts/build_public_site.py. Prototype built on Toronto Police Service open data\n"
        "(Open Government Licence – Ontario). Not affiliated with or endorsed by the Toronto Police Service.\n",
        encoding="utf-8")
    return {"files": len(list(out.rglob("*"))), "bulletins": len(bulletins), "automatic": len(autos), "profiles": len(hood_ids) - len(skipped),
            "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "public_site"))
    ap.add_argument("--hoods", default="published", help="published (default), all, or comma-separated ids")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--no-auto", action="store_true", help="leave out automatic bulletins")
    a = ap.parse_args()
    res = build(Path(a.out), a.hoods, a.base_url, auto_bulletins=not a.no_auto)
    print(f"Built {a.out}: {res['bulletins']} bulletins, {res['automatic']} automatic bulletins, {res['profiles']} neighbourhood profiles, {res['files']} files")
    for s in res["skipped"]:
        print("  skipped", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
