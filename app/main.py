"""NeighbourBrief TO: FastAPI routes."""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import auto, feedback, share, store
from app.ai import llm, translate, writer
from app.config import get_settings
from app.data import facts as F
from app.data import tps
from app.languages import labels, lang_info, month_label, other_languages
from app.verify import checks, content

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("neighbourbrief")

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app):
    resumed = resume_interrupted_jobs()
    if resumed:
        log.info("Resumed interrupted translations: %s", resumed)
    yield


app = FastAPI(title="NeighbourBrief TO", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return ""
    d = dt.datetime.fromisoformat(iso)
    return d.strftime("%b %-d, %Y, %-I:%M %p").replace("AM", "a.m.").replace("PM", "p.m.")


templates.env.filters["fmt_dt"] = _fmt_dt
templates.env.globals.update(labels=labels, lang_info=lang_info)


def is_public(request: Request | None) -> bool:
    """Resident navigation only: the static public site, the public pages, and every page a visitor (not
    signed-in staff) sees on a public deployment."""
    if not request:
        return False
    if get_settings().public_deploy and not getattr(request.state, "staff", False):
        return True
    return request.headers.get("x-nb-public") == "1" or request.url.path in ("/public", "/accessibility", "/privacy-ai")


def ctx(request: Request, **kw) -> dict:
    s = get_settings()
    static_build = request.headers.get("x-nb-public") == "1" and not s.public_deploy
    return {"request": request, "offline": s.demo_offline, "public": is_public(request), "live_site": s.public_deploy,
            "feedback_on": not static_build, "fb_topics": feedback.TOPICS, "static_build": static_build,
            "publisher": {"name": s.publisher_name, "contact": s.publisher_contact}, **kw}


def page(request: Request, name: str, status_code: int = 200, **kw):
    return templates.TemplateResponse(request, name, ctx(request, **kw), status_code=status_code)


# ---------------------------------------------------------------- local-only guard

_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testserver", ""}


STAFF_PATHS = ("/new", "/draft", "/review", "/publish", "/history", "/translations", "/status", "/api/status",
               "/api/neighbourhood", "/api/bulletins", "/offline", "/impact", "/staff")


def _is_staff_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in STAFF_PATHS)


def _staff_ok(request: Request) -> bool:
    """HTTP Basic sign-in with STAFF_PASSWORD (any user name). Constant-time comparison."""
    import base64
    import hmac
    pw = get_settings().staff_password
    auth = request.headers.get("authorization") or ""
    if not pw or not auth.lower().startswith("basic "):
        return False
    try:
        given = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8").split(":", 1)[1]
    except (ValueError, IndexError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(given.encode(), pw.encode())


def _host_allowed(host: str) -> bool:
    """ALLOWED_HOSTS: exact names, or ".example.com" for any subdomain of it."""
    for h in (x.strip().lower() for x in get_settings().allowed_hosts.split(",")):
        if h and (host == h.lstrip(".") or (h.startswith(".") and host.endswith(h))):
            return True
    return False


def _in_allowed_networks(host: str) -> bool:
    """ALLOW_CLIENTS: comma-separated networks that count as "this computer" (Docker's bridge)."""
    import ipaddress
    nets = [n.strip() for n in get_settings().allow_clients.split(",") if n.strip()]
    try:
        ip = ipaddress.ip_address(host)
        return any(ip in ipaddress.ip_network(n, strict=False) for n in nets)
    except ValueError:
        return False


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Drafts hold officer names/contacts: answer only this computer unless ALLOW_LAN=true,
    even if the server was started with --host 0.0.0.0 by mistake."""
    host = request.client.host if request.client else ""
    if get_settings().public_deploy and request.headers.get("x-forwarded-proto") == "https":
        request.scope["scheme"] = "https"  # behind the tunnel: links and the embed snippet use https
    if host not in _LOCAL_CLIENTS and not host.startswith("127.") and not get_settings().allow_lan \
            and not _in_allowed_networks(host):
        log.warning("Refused request from %s (not this computer)", host)
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>NeighbourBrief TO</title>"
            "<p style='font:16px system-ui;margin:2rem'>NeighbourBrief only works on the computer it "
            "runs on. (To allow other devices on a trusted network, set ALLOW_LAN=true.)</p>",
            status_code=403)
    # DNS rebinding: a hostile name that resolves to 127.0.0.1 still carries its own Host header.
    host_header = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    if host_header not in _LOCAL_HOSTS and not _host_allowed(host_header) and not get_settings().allow_lan:
        log.warning("Refused request for host %r", host_header)
        return HTMLResponse("Unknown host", status_code=403)
    # Cross-site form posts: another website open in this browser must not be able to publish,
    # delete or change bulletins. Browsers label such requests Sec-Fetch-Site: cross-site.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("sec-fetch-site") == "cross-site":
        log.warning("Refused cross-site %s %s", request.method, request.url.path)
        return HTMLResponse("Cross-site requests are not allowed", status_code=403)
    # Public deployment: staff pages need the staff password; everything else is the resident site.
    if get_settings().public_deploy:
        request.state.staff = _staff_ok(request)
        if _is_staff_path(request.url.path) and not request.state.staff:
            if not get_settings().staff_password:
                return HTMLResponse("This page isn't part of the public site.", status_code=404)
            return HTMLResponse("Staff sign-in needed.", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="NeighbourBrief staff", charset="UTF-8"'})
        if request.url.path.startswith("/bulletin/") and not request.state.staff:
            parts = request.url.path.split("/")
            try:
                published = len(parts) > 2 and (auto.is_auto(parts[2]) or store.load(parts[2]).get("status") == "published")
            except (KeyError, ValueError, OSError):
                published = False
            if not published:  # drafts stay private even if someone guesses the address
                return HTMLResponse("We couldn't find that bulletin.", status_code=404)
    response = await call_next(request)
    if (get_settings().public_deploy and request.method == "GET" and response.status_code == 200
            and not getattr(request.state, "staff", False)):
        from app import usage  # totals per day only: no cookies, IPs or visitor ids
        usage.record(request.url.path, request.headers.get("user-agent", ""))
    embed = request.url.path.startswith("/embed/")  # the embed card is meant to be framed by partner sites
    for k, v in SECURITY_HEADERS.items():
        if not (embed and k == "X-Frame-Options"):
            response.headers.setdefault(k, v)
    return response


# Inline scripts/styles are used by the templates and by Tailwind's runtime; everything else is
# this app, Google Fonts or OpenStreetMap map tiles. No framing, no referrer leaks to WhatsApp/email;
# location only for "Use my location", which never leaves the browser.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(self), payment=()",
}


# ---------------------------------------------------------------- errors

from fastapi.exceptions import RequestValidationError  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

_ERROR_TITLES = {404: "Page not found", 422: "We couldn't open that", 500: "Something went wrong"}


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return request.url.path.startswith("/api/") or ("application/json" in accept and "text/html" not in accept)


def _error_page(request: Request, code: int, message: str):
    if _wants_json(request):
        return JSONResponse({"error": message}, status_code=code)
    return page(request, "error.html", status_code=code, title=_ERROR_TITLES.get(code, "Something went wrong"),
                message=message)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    msg = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found", "Method Not Allowed") else (
        "That page doesn't exist. Check the link, or start from your saved bulletins.")
    return _error_page(request, exc.status_code, msg)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return _error_page(request, 422, "Some required information was missing from the form. Please go back and try again.")


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    log.exception("Unexpected error on %s", request.url.path)
    return _error_page(request, 500, "Something unexpected went wrong. Your saved bulletins are safe. "
                                     "Please try again; if it keeps happening, check the Status page.")


# ---------------------------------------------------------------- helpers

def bulletin_view(rec: dict, code: str, highlight: bool = False) -> dict:
    """Everything bulletin.html needs for one language."""
    facts = rec["facts"]
    is_en = code == "en"
    tr = None if is_en else (rec.get("translations", {}).get(code) or {})
    doc = rec["english"] if is_en else (tr or {}).get("doc")
    info = lang_info(code)
    en_text = " ".join(rec["english"].get("bullets", []) + [rec["english"].get(k, "") for k in
                                                         ("headline", "summary", "followup", "officer_section", "closing")])

    def r(text):
        return checks.render(text, facts, code, en_text, highlight=highlight)

    by_key = {l["key"]: l for l in facts["categories"]}
    shown = [by_key[k] for k in facts["display"] if k in by_key]
    bullets = (doc or {}).get("bullets", [])
    chips = [chip(l, labels(code)) for l in shown] if len(bullets) == len(shown) else [None] * len(bullets)
    v = (rec.get("verification") or {}).get(code) or {}
    shown_ids = rec.get("services_shown", [])
    services = [s for s in facts.get("services", []) if s["id"] in shown_ids]
    return {
        "services": services,
        "code": code,
        "info": info,
        "L": labels(code),
        "doc": doc,
        "title": share.title(rec, code),
        "month": month_label(dt.date.fromisoformat(facts["period"]["month_start"]), code),
        "headline": r(doc.get("headline", "")) if doc else "",
        "summary": r(doc.get("summary", "")) if doc else "",
        "bullets": [(r(b), chips[i] if i < len(chips) else None) for i, b in enumerate(bullets)],
        "officer_section": r(doc.get("officer_section", "")) if doc else "",
        "followup": r(doc.get("followup", "")) if doc else "",
        "closing": r(doc.get("closing", "")) if doc else "",
        "verification": v,
        "translation": tr,
        "facts": facts,
        "rec": rec,
    }


def chip(line: dict, L: dict) -> dict | None:
    """The single small comparison element for a category (YTD vs same period last year)."""
    if line["ytd_prev"] is None:
        return None
    arrow = {"up": "▲", "down": "▼", "same": "="}[line["direction"]]
    if line["pct_change"] is not None:
        text = f"{arrow} {abs(line['pct_change'])}%"
    else:
        text = f"{arrow} {line['ytd']} {L['vs']} {line['ytd_prev']}"
    title = (f"Year to date: {line['ytd']} vs {line['ytd_prev']} in the same period last year")
    return {"text": text, "direction": line["direction"], "title": title}


def _load(bid: str) -> dict:
    if auto.is_auto(bid):  # automatic bulletin: rebuilt from the open data (translations cached)
        # public deployment: visitors get new translations made for the latest month only (older months
        # show what's cached), so walking every old month's address can't use up the translation quota
        latest = F.available_months()[0].isoformat()[:7]
        try:
            rec = auto.from_id(bid, translate_missing=False if get_settings().public_deploy and not bid.endswith(latest) else None)
        except (KeyError, ValueError):
            raise HTTPException(404, "We couldn't find that bulletin. Pick a neighbourhood and month that exist.")
        except tps.TPSUnavailable as exc:
            raise HTTPException(503, str(exc))
        if not auto.showable(rec):
            raise HTTPException(404, "There's no automatic bulletin for this neighbourhood and month.")
        return rec
    try:
        rec = store.load(bid)
    except KeyError:
        raise HTTPException(404, "We couldn't find that bulletin. It may have been deleted, or the link is incomplete.")
    except (ValueError, OSError) as exc:  # damaged / half-written JSON file
        log.error("Bulletin %s can't be read: %s", bid, exc)
        raise HTTPException(422, "This bulletin's saved file is damaged and can't be opened. "
                                 "Other bulletins are fine; you can create this one again.")
    if not isinstance(rec, dict) or not all(k in rec for k in ("facts", "english")):
        raise HTTPException(422, "This bulletin's saved file is incomplete and can't be opened.")
    return rec


# ---------------------------------------------------------------- screens

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Landing page: what NeighbourBrief does + the main tasks, in the data-portal card style."""
    if is_public(request) and get_settings().public_deploy:
        return public_home(request)
    try:
        hoods = F.list_neighbourhoods()
        data_error = ""
    except tps.TPSUnavailable as exc:
        hoods, data_error = [], str(exc)
    from app.languages import LANGUAGES, census_languages
    recs = store.list_all()
    stats = {"neighbourhoods": len(hoods) or 158, "languages": len(LANGUAGES),
             "with_languages": sum(1 for h in census_languages().values()
                                   if any(l["code"] and l["share"] >= 3 for l in h["languages"])),
             "bulletins": len(recs), "published": sum(1 for r in recs if r.get("status") == "published")}
    return page(request, "home.html", hoods=hoods, data_error=data_error, recent=recs[:3], stats=stats,
                monthly=monthly_todo(recs))


def monthly_todo(recs: list[dict]) -> dict | None:
    """The monthly routine: for each neighbourhood with a bulletin, is the latest month done yet?"""
    try:
        latest = F.available_months()[0]
    except Exception:
        return None
    key = latest.isoformat()
    by_hood: dict[str, list[dict]] = {}
    for r in recs:  # newest first
        by_hood.setdefault(r["facts"]["neighbourhood"]["id"], []).append(r)
    rows = []
    for hid, rs in by_hood.items():
        current = next((r for r in rs if r["facts"]["period"]["month_start"] == key), None)
        last = rs[0]
        rows.append({"name": last["facts"]["neighbourhood"]["name"], "current": current, "last": last,
                     "last_month": last["facts"]["period"]["month_label"]})
    rows.sort(key=lambda x: (x["current"] is not None, x["name"]))
    return {"month": latest.strftime("%B %Y"), "rows": rows, "todo": sum(1 for x in rows if not x["current"])} if rows else None


def _usable_hoods() -> tuple[list[dict], str]:
    """All neighbourhoods (only the saved ones in offline mode) and any data error."""
    try:
        hoods = F.list_neighbourhoods()
    except tps.TPSUnavailable as exc:
        return [], str(exc)
    if get_settings().demo_offline:
        from app import snapshot
        saved_ids = set(snapshot.saved_neighbourhoods())
        hoods = [h for h in hoods if h["id"] in saved_ids] or hoods
    return hoods, ""


@app.get("/neighbourhoods", response_class=HTMLResponse)
def neighbourhoods_page(request: Request, only: str = ""):
    """Directory of neighbourhoods: officers, division and main languages; links to each profile.
    `only` (comma-separated ids) limits it to the profiles built for the static public site."""
    hoods, data_error = _usable_hoods()
    if only:
        keep = {x.strip().zfill(3) for x in only.split(",") if x.strip()}
        hoods = [h for h in hoods if h["id"] in keep]
    from app.languages import census_languages
    census = census_languages()
    rows = []
    for h in hoods:
        nco = F.nco_info(h["id"]) or {}
        langs = [l["language"] for l in (census.get(h["id"]) or {}).get("languages", [])[:3]]
        rows.append({**h, "division_label": F.division_label(h.get("division", "")), "has_nco": bool(nco.get("has_nco")),
                     "languages": langs})
    return page(request, "neighbourhoods.html", rows=rows, data_error=data_error,
                nco_count=sum(1 for r in rows if r["has_nco"]), wards_in_site=not only or len(rows) >= 158)


def _profile_or_error(request: Request, hid: str, month: str):
    from app.data import profile as P
    try:
        return P.build_profile(hid, month=month or None), None
    except F.MonthNotAvailable as exc:  # (a ValueError too, so it must come first)
        return None, page(request, "error.html", status_code=400, title="That month isn't available", message=str(exc),
                          link=(f"/neighbourhoods/{hid}", "Show the latest month"))
    except ValueError:
        return None, page(request, "error.html", status_code=404, title="Neighbourhood not found",
                          message="There's no Toronto neighbourhood with that number or name.")
    except tps.TPSUnavailable as exc:
        return None, page(request, "error.html", status_code=503, title="Data unavailable", message=str(exc))


@app.get("/neighbourhoods/{hid}", response_class=HTMLResponse)
def neighbourhood_profile(request: Request, hid: str, month: str = ""):
    """One neighbourhood across several open datasets, each with its source and date."""
    p, err = _profile_or_error(request, hid, month)
    if err:
        return err
    hid = p["facts"]["neighbourhood"]["id"]
    from app.data import provenance
    try:
        sources = provenance.queries(p["facts"])
    except Exception:
        sources = {}
    from app.data import profile as P
    return page(request, "profile.html", p=p, f=p["facts"], months=F.available_months(), selected_month=month, sources=sources,
                public_data=P.public_json(p),
                map=neighbourhood_map(hid), bulletins=(mine := [b for b in published_bulletins() if b["hood"]["id"] == hid][:1]),
                auto_b=None if (mine and mine[0]["month_start"] == p["facts"]["period"]["month_start"])
                else auto_summary(hid, month or None, offer_untried=not ctx(request)["static_build"]))


def auto_summary(hid: str, month: str | None = None, offer_untried: bool = True) -> dict | None:
    """The automatic bulletin for a neighbourhood (latest month by default), for links and cards.
    Languages not translated yet are offered too: they're made (and checked) when opened."""
    try:
        rec = auto.build(hid, month=month, translate_missing=False)
    except Exception:
        return None
    if not auto.showable(rec):
        return None
    # untried languages are made when opened: not on the static site, which has no server to make them
    codes = ["en"] + rec["languages"] + (rec["untried"] if offer_untried and auto.can_translate_now() else [])
    return {"id": rec["id"], "rec": rec, "codes": codes, "langs": [lang_info(c) for c in codes],
            "headline": rec["english"]["headline"], "summary": rec["english"]["summary"],
            "month": rec["facts"]["period"]["month_label"], "hood": rec["facts"]["neighbourhood"]}


@app.post("/feedback/{hid}")
def post_feedback(request: Request, hid: str, topic: str = Form(""), comment: str = Form(""), useful: str = Form(""),
                  lang: str = Form("en"), back: str = Form("")):
    """A resident's answer to "What should your officers focus on next month?" (see app/feedback.py)."""
    import re as _re
    from urllib.parse import quote
    hid = hid.zfill(3)
    if not F.find_neighbourhood(hid):
        raise HTTPException(404, "Unknown neighbourhood.")
    # back to the page the form was on (own pages only: never an outside address)
    back = back if _re.fullmatch(r"/(bulletin/[\w-]+/[\w-]+|neighbourhoods/\d{3})", back or "") else f"/neighbourhoods/{hid}"
    from app import ratelimit
    visitor = ratelimit.visitor(request)  # rate limiting only, never stored
    try:
        feedback.add(hid, topic, comment, useful, lang, visitor=visitor)
    except feedback.Refused as exc:
        return RedirectResponse(f"{back}?fb_error={quote(str(exc))}#feedback", status_code=303)
    return RedirectResponse(f"{back}?thanks=1#feedback", status_code=303)


@app.get("/neighbourhoods/{hid}/data.json")
def neighbourhood_profile_json(request: Request, hid: str, month: str = ""):
    from app.data import profile as P
    p, err = _profile_or_error(request, hid, month)
    if err:
        return JSONResponse({"error": "Unknown neighbourhood or data unavailable"}, status_code=err.status_code)
    return JSONResponse(P.public_json(p), headers={"Access-Control-Allow-Origin": "*"})


def published_bulletins() -> list[dict]:
    """Published bulletins whose numbers still pass every check (the same gate as Publish)."""
    out = []
    for r in store.list_all():
        if r.get("status") != "published":
            continue
        checks.verify_record(r)  # today's rules, not the result stored when it was published
        if checks.publish_blocked(r):  # manual overrides stay off the public site too
            continue
        codes = ["en"] + [c for c in r.get("languages", []) if (r.get("translations", {}).get(c) or {}).get("doc")]
        out.append({"id": r["id"], "rec": r, "codes": codes, "title": bulletin_view(r, "en")["title"],
                    "hood": r["facts"]["neighbourhood"], "month": r["facts"]["period"]["month_label"],
                    "month_start": r["facts"]["period"]["month_start"], "published_at": r.get("published_at") or r.get("updated_at")})
    return sorted(out, key=lambda b: (b["month_start"], b["hood"]["name"]), reverse=True)


@app.get("/public", response_class=HTMLResponse)
def public_home(request: Request):
    """Home page of the static public site (preview it here; build it with scripts/build_public_site.py)."""
    return page(request, "public_home.html", bulletins=published_bulletins())


def _ask_engines() -> tuple[list[tuple[str, str]], dict]:
    from app.ai import cohere
    from app.rag import answer as A
    labels = {"cohere": cohere.label(), "local": f"{llm.provider_label()}", "extractive": "Sources only (no AI)", "rule": "rule"}
    opts = [("auto", "Best available")] + [(e, labels[e] + ("" if A.engine_available(e) else " (not available)"))
                                            for e in ("cohere", "local", "extractive")]
    return opts, labels


@app.get("/ask", response_class=HTMLResponse)
def ask_page(request: Request, q: str = "", hood: str = ""):
    hoods, _ = _usable_hoods()
    engines, labels = _ask_engines()
    return page(request, "ask.html", hoods=hoods, engines=engines, engine_labels=labels, engine="auto", question=q, selected=hood, a=None)


ASK_PER_VISITOR, ASK_PER_HOUR = 20, 300  # public deployment only


@app.post("/ask", response_class=HTMLResponse)
def ask_submit(request: Request, question: str = Form(""), neighbourhood: str = Form(""), engine: str = Form("auto")):
    """Ask about a neighbourhood: retrieval over the checked facts, answer with citations, number guard."""
    from app.rag import answer as A
    hoods, _ = _usable_hoods()
    engines, labels = _ask_engines()
    hood = F.find_neighbourhood(neighbourhood) if neighbourhood.strip() else None
    engine = engine if engine in ("auto", "cohere", "local", "extractive") else "auto"
    if engine != "auto" and not A.engine_available(engine):
        engine = "auto"
    from app import ratelimit
    if get_settings().public_deploy and question.strip() and not (
            ratelimit.allow("ask", ratelimit.visitor(request), ASK_PER_VISITOR)
            and ratelimit.allow("ask", "*", ASK_PER_HOUR)):  # each question uses Azure and Cohere quota
        a = {"question": question, "answer": "", "warnings": ["Lots of questions right now. Please try again in a little while."],
             "sources": [], "citations": []}
        return page(request, "ask.html", hoods=hoods, engines=engines, engine_labels=labels, engine=engine,
                    question=question, selected=neighbourhood, a=a, rendered="")
    a = A.ask(question, hood["id"] if hood else None, engine=engine)
    return page(request, "ask.html", hoods=hoods, engines=engines, engine_labels=labels, engine=engine, question=question,
                selected=neighbourhood, a=a, rendered=A.render(a) if a.get("answer") else "")


@app.get("/how-it-works", response_class=HTMLResponse)
def how_it_works(request: Request):
    return page(request, "how_it_works.html")


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return page(request, "help.html")


@app.get("/privacy-ai", response_class=HTMLResponse)
def privacy_ai(request: Request):
    return page(request, "privacy_ai.html")


@app.get("/accessibility", response_class=HTMLResponse)
def accessibility(request: Request):
    return page(request, "accessibility.html")


@app.get("/embed/{hid}", response_class=HTMLResponse)
def embed_card(request: Request, hid: str):
    """A small card for partner websites (CPLC, BIA, councillor): this neighbourhood's latest published bulletin."""
    hid = hid.zfill(3)
    latest = next((b for b in published_bulletins() if b["hood"]["id"] == hid), None)
    resp = page(request, "embed.html", b=latest, hid=hid,
                auto_b=None if latest else auto_summary(hid, offer_untried=not ctx(request)["static_build"]))
    resp.headers["Content-Security-Policy"] = SECURITY_HEADERS["Content-Security-Policy"].replace("frame-ancestors 'none'", "frame-ancestors *")
    return resp


@app.get("/feed.xml")
def feed(request: Request):
    """Atom feed of published bulletins (subscribe in any feed reader)."""
    return _feed(request, published_bulletins(), "NeighbourBrief TO: neighbourhood safety bulletins", "/feed.xml")


@app.get("/divisions", response_class=HTMLResponse)
def divisions_page(request: Request):
    from app.data import division
    try:
        divs = division.divisions()
    except tps.TPSUnavailable as exc:
        return page(request, "error.html", status_code=503, title="Data unavailable", message=str(exc))
    return page(request, "divisions.html", divs=divs)


@app.get("/divisions/{number}", response_class=HTMLResponse)
def division_page(request: Request, number: str, month: str = ""):
    from app.data import division
    if not number.isdigit():
        return page(request, "error.html", status_code=404, title="Division not found", message="There's no division with that number.")
    number = int(number)
    try:
        d = division.build_division(number, month=month or None)
    except ValueError:
        return page(request, "error.html", status_code=404, title="Division not found", message="There's no division with that number.")
    except tps.TPSUnavailable as exc:
        return page(request, "error.html", status_code=503, title="Data unavailable", message=str(exc))
    return page(request, "division.html", d=d)


@app.get("/wards", response_class=HTMLResponse)
def wards_page(request: Request):
    from app.data import ward
    return page(request, "wards.html", wards=ward.wards())


@app.get("/wards/{number}", response_class=HTMLResponse)
def ward_page(request: Request, number: str, month: str = ""):
    """Ward brief for a city councillor: the ward's neighbourhoods, each with its own counts and bulletin."""
    from app.data import ward
    not_found = dict(status_code=404, title="Ward not found", message="Toronto has 25 wards, numbered 1 to 25.")
    if not number.isdigit():
        return page(request, "error.html", **not_found)
    try:
        w = ward.build_ward(int(number), month=month or None)
    except F.MonthNotAvailable as exc:
        return page(request, "error.html", status_code=400, title="That month isn't available", message=str(exc),
                    link=(f"/wards/{int(number)}", "Show the latest month"))
    except ValueError:
        return page(request, "error.html", **not_found)
    except tps.TPSUnavailable as exc:
        return page(request, "error.html", status_code=503, title="Data unavailable", message=str(exc))
    pub = {(b["hood"]["id"], b["month_start"]): b["id"] for b in published_bulletins()}
    for r in w["rows"]:  # an officer's bulletin for the month replaces the automatic one
        bid = pub.get((r["id"], w["period"]["month_start"]))
        r["bulletin"], r["by_officers"] = (f"/bulletin/{bid}/en", True) if bid else (f"/bulletin/{r['auto_id']}/en", False)
    return page(request, "ward.html", w=w)


@app.get("/neighbourhoods/{hid}/feed.xml")
def neighbourhood_feed(request: Request, hid: str):
    """One neighbourhood's published bulletins (for a CPLC, a residents' association, a resident)."""
    hood = F.find_neighbourhood(hid)
    if not hood:
        raise HTTPException(404, "Unknown neighbourhood")
    items = [b for b in published_bulletins() if b["hood"]["id"] == hood["id"]]
    return _feed(request, items, f"NeighbourBrief TO: {hood['name']} safety bulletins", f"/neighbourhoods/{hood['id']}/feed.xml")


def _feed(request: Request, bulletins: list[dict], title: str, self_path: str):
    from xml.sax.saxutils import escape as x
    base = str(request.base_url).rstrip("/")
    entries = []
    for b in bulletins:
        url = f"{base}/bulletin/{b['id']}/en"
        langs = ", ".join(lang_info(c)["name"] for c in b["codes"])
        entries.append(f"""  <entry>
    <title>{x(b['title'])}</title>
    <id>{x(url)}</id>
    <link rel="alternate" href="{x(url)}"/>
    <updated>{x(b['published_at'] or b['month_start'] + 'T00:00:00')}</updated>
    <summary>{x(b['hood']['name'])}, {x(b['month'])}. Languages: {x(langs)}.</summary>
  </entry>""")
    updated = max((b["published_at"] or "" for b in bulletins), default="") or dt.datetime.now(dt.timezone.utc).isoformat()
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{x(title)}</title>
  <subtitle>Prototype built on Toronto Police Service open data. Not affiliated with or endorsed by the Toronto Police Service.</subtitle>
  <id>{x(base)}{x(self_path)}</id>
  <link rel="self" href="{x(base)}{x(self_path)}"/>
  <updated>{x(updated)}</updated>
{chr(10).join(entries)}
</feed>
"""
    return Response(xml, media_type="application/atom+xml; charset=utf-8")


@app.get("/impact", response_class=HTMLResponse)
def impact_page(request: Request):
    from app import impact
    from app import usage
    return page(request, "impact.html", m=impact.compute(), fb=feedback.totals(), use=usage.last_days(30))


@app.get("/data-health", response_class=HTMLResponse)
def data_health(request: Request):
    """Freshness, shape and known gaps of every open dataset the app uses."""
    from app.data import health
    return page(request, "data_health.html", h=health.run(), offline=get_settings().demo_offline)


@app.get("/data-health.json")
def data_health_json():
    from app.data import health
    return health.run()


def _road_for(facts: dict) -> dict | None:
    """Traffic collisions for the same neighbourhood and month (optional for Check my numbers)."""
    try:
        from app.data import profile as P
        return P.road_safety(facts["neighbourhood"]["id"], dt.date.fromisoformat(facts["period"]["month_start"]))
    except (tps.TPSUnavailable, ValueError, KeyError):
        return None


@app.get("/check", response_class=HTMLResponse)
def check_page(request: Request, hood: str = "", example: str = ""):
    """`?hood=…&example=1` pre-fills a sample built from the current data with one mistake of each kind."""
    from app.verify import factcheck as FC
    hoods, _ = _usable_hoods()
    text = ""
    if example and hood:
        try:
            f = F.build_facts(hood)
            text = FC.example_text(f, _road_for(f))
        except (ValueError, tps.TPSUnavailable, F.MonthNotAvailable):
            text = ""
    return page(request, "check.html", hoods=hoods, months=F.available_months(), text=text, result=None, selected=hood)


@app.post("/check", response_class=HTMLResponse)
def check_submit(request: Request, neighbourhood: str = Form(...), text: str = Form(""), month: str = Form("")):
    """Fact-check pasted text against the TPS facts for a neighbourhood and month."""
    from app.verify import factcheck as FC
    try:
        hoods = F.list_neighbourhoods()
    except tps.TPSUnavailable:
        hoods = []
    text = (text or "")[:6000]
    error, result, rendered, facts = "", None, None, None
    try:
        facts = F.build_facts(neighbourhood, month=month or None)
        result = FC.check_text(text, facts, _road_for(facts))
        rendered = FC.render(text, result)
    except (tps.TPSUnavailable, F.MonthNotAvailable) as exc:
        error = str(exc)
    except ValueError:
        error = "Please choose a neighbourhood from the list."
    return page(request, "check.html", hoods=hoods, months=F.available_months(), text=text, result=result,
                rendered=rendered, facts=facts, error=error, selected=neighbourhood, selected_month=month)


@app.get("/how-we-count", response_class=HTMLResponse)
def how_we_count(request: Request):
    return page(request, "how_we_count.html", categories=F.CATEGORIES, excluded=sorted(F.EXCLUDED),
                small=F.SMALL_NUMBER_MIN, layers=F.SOURCE["layers"])


@app.get("/new", response_class=HTMLResponse)
def new_bulletin(request: Request, hood: str = "", error: str = "", form: dict | None = None, error_kind: str = "input"):
    """`form`: what the officer typed, put back when the form comes back with an error (nothing is lost)."""
    try:
        hoods = F.list_neighbourhoods()
        data_error = ""
    except tps.TPSUnavailable as exc:
        hoods, data_error = [], str(exc)
    try:
        months = F.available_months()
    except Exception:  # never let the month list break the form
        months = []
    if get_settings().demo_offline:  # only saved neighbourhoods work offline
        from app import snapshot
        saved_ids = set(snapshot.saved_neighbourhoods())
        hoods = [h for h in hoods if h["id"] in saved_ids] or hoods
    from app import tips
    return page(request, "new.html", hoods=hoods, data_error=data_error, error=error, error_kind=error_kind, form=form or {},
                reuse=request.query_params.get("reuse", ""),
                tips=tips.TIPS,
                selected=hood, lang_choices=translate.language_choices(), months=months,
                llm_status=llm.status(), engine=translate.engine_label(translate.engine_for()[0]))


@app.get("/api/neighbourhood/{hid}")
def api_neighbourhood(hid: str, reuse: str = ""):
    try:
        hood = F.find_neighbourhood(hid)
        if not hood:
            return JSONResponse({"error": "Unknown neighbourhood"}, status_code=404)
        fresh = tps.data_freshness_ms()
        fresh_dt = F.to_toronto(fresh) if fresh else None
        prev = previous_bulletin(hood["id"], reuse or None)
        try:  # the categories this month's bulletin will show: used to suggest matching tips
            top = F.build_facts(hood["id"])["display"]
        except (tps.TPSUnavailable, ValueError):
            top = []
        return {
            "id": hood["id"],
            "name": hood["name"],
            "division": F.division_label(hood.get("division", "")),
            "nco": F.nco_info(hood["id"]),
            "languages": [l["code"] for l in F.suggested_languages(hood["name"], hood["id"])],
            "language_notes": F.suggested_languages(hood["name"], hood["id"]),
            "other_languages": other_languages(hood["name"], hood["id"]),
            "freshness": _fmt_dt(fresh_dt.isoformat()) if fresh_dt else "unknown",
            "previous": prev,
            "residents": feedback.summary(hood["id"]),
            "categories": top,
            "map": neighbourhood_map(hood["id"]),
            "existing": [
                {"id": r["id"], "month": r["facts"]["period"]["month_start"][:7],
                 "month_label": r["facts"]["period"]["month_label"], "status": r.get("status", "draft")}
                for r in store.list_all() if r["facts"]["neighbourhood"]["id"] == hood["id"]
            ],
        }
    except tps.TPSUnavailable as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


def neighbourhood_map(hood_id: str) -> dict | None:
    """Boundary + community service pins for the locator map. No crime locations, ever."""
    from app import geo
    try:
        f = geo.feature(hood_id)
    except tps.TPSUnavailable:
        return None
    if not f:
        return None
    x0, y0, x1, y1 = geo.bbox(f["geometry"])
    near = 0.01  # about 1 km: services listed here but located elsewhere (head offices) stay off the map
    services = [{"name": s["name"], "category": s["category"], "address": s["address"], "lat": s["lat"], "lon": s["lon"]}
                for s in F.local_services(hood_id)
                if "lat" in s and x0 - near <= s["lon"] <= x1 + near and y0 - near <= s["lat"] <= y1 + near]
    return {"geometry": f["geometry"], "bbox": geo.bbox(f["geometry"]), "outline": geo.outline(hood_id, points=[(s["lon"], s["lat"]) for s in services]),
            "services": services, "tiles": not get_settings().demo_offline}


@app.get("/api/boundaries.geojson")
def api_boundaries():
    """All 158 neighbourhood boundaries, for "Use my location" (the point-in-polygon runs in the browser)."""
    from app import geo
    try:
        data = geo.boundaries()
    except tps.TPSUnavailable as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    return JSONResponse(data, headers={"Cache-Control": "max-age=86400"})


def previous_bulletin(hood_id: str, prefer_id: str | None = None) -> dict | None:
    """The officer's most recent bulletin for this neighbourhood (or `prefer_id` if it belongs to
    it), so monthly text can be reused."""
    recs = store.list_all()  # newest first
    recs.sort(key=lambda r: r["id"] != prefer_id)  # stable: the preferred one first
    for r in recs:
        if r.get("facts", {}).get("neighbourhood", {}).get("id") == hood_id:
            o = r.get("officer") or {}
            return {"id": r["id"], "month": r["facts"]["period"]["month_label"],
                    "status": r.get("status"), "priority": o.get("priority", ""),
                    "action_taken": o.get("action_taken", ""),
                    "meeting_note": o.get("meeting_note", ""), "languages": r.get("languages", []),
                    "tip": o.get("tip", ""), "tip_id": o.get("tip_id", ""), "signoff": r.get("signoff", "")}
    return None


@app.post("/draft")
def create_draft(
    request: Request,
    neighbourhood: str = Form(""),
    priority: str = Form(""),
    action_taken: str = Form(""),
    meeting_note: str = Form(""),
    languages: list[str] = Form([]),
    writer_mode: str = Form("auto"),
    month: str = Form(""),
    tip_id: str = Form(""),
    tip_custom: str = Form(""),
    signoff: str = Form(""),
    prev_priority: str = Form(""),
    followup: str = Form(""),
    followup_status: str = Form(""),
    author: str = Form(""),
    meeting_date: str = Form(""),
    meeting_time: str = Form(""),
    meeting_place: str = Form(""),
):
    form = {"priority": priority, "action_taken": action_taken, "meeting_note": meeting_note, "languages": languages,
            "writer_mode": writer_mode, "month": month, "tip_id": tip_id, "tip_custom": tip_custom, "signoff": signoff,
            "followup": followup, "followup_status": followup_status, "author": author, "meeting_date": meeting_date,
            "meeting_time": meeting_time, "meeting_place": meeting_place}
    if not neighbourhood.strip():
        return new_bulletin(request, error="Please choose a neighbourhood from the list.", form=form)
    meeting = _meeting(meeting_date, meeting_time, meeting_place)
    if meeting and not meeting_note.strip():
        meeting_note = meeting["text"]
    from app import tips
    officer, warnings = writer.clean_officer_inputs(
        {"priority": priority, "action_taken": action_taken, "meeting_note": meeting_note,
         "tip": tips.resolve(tip_id, tip_custom), "prev_priority": prev_priority, "followup": followup})
    officer["tip_id"] = "" if tip_custom.strip() else tip_id
    officer["followup_status"] = followup_status if followup_status in writer.FOLLOWUP_STATUS else ""
    if meeting:
        officer["meeting"] = {k: meeting[k] for k in ("date", "time", "place")}
    warnings += content.officer_warnings(officer)
    try:
        facts = F.build_facts(neighbourhood, month=month or None)
    except tps.TPSUnavailable as exc:
        return new_bulletin(request, hood=neighbourhood, error=str(exc), form=form, error_kind="data")
    except F.MonthNotAvailable as exc:
        return new_bulletin(request, hood=neighbourhood, error=str(exc), form=form)
    except ValueError:
        return new_bulletin(request, hood=neighbourhood, form=form,
                            error=f"“{neighbourhood}” isn't one of Toronto's 158 neighbourhoods. Please choose one from the list.")
    facts["allowed_numbers"] = F.allowed_numbers(facts, officer)
    listed = [o["name"] for o in ((facts.get("nco") or {}).get("officers") or [])]
    signoff = signoff if signoff in listed else ""  # only officers TPS publishes for this area
    t0 = time.monotonic()
    result = writer.write_draft(facts, officer, use_llm=(writer_mode != "template"))
    result["doc"]["followup"] = writer.followup_text(officer)  # "You said, we did" (code, not AI)
    seconds = round(time.monotonic() - t0, 1)
    codes = [c for c in dict.fromkeys(languages) if c and c != "en" and c in translate.LANGUAGES]
    rec = {
        "id": store.new_id(facts["neighbourhood"]["name"], facts["period"]["month_start"]),
        "created_at": store.now_iso(),
        "status": "draft",
        "facts": facts,
        "officer": officer,
        "officer_warnings": warnings,
        "languages": codes,
        "english": result["doc"],
        "draft_source": result["source"],
        "draft_note": result["note"],
        "draft_info": result.get("info", {}),
        "translations": {},
        "services_shown": [s["id"] for s in facts.get("services", [])[:F.SERVICES_SHOWN]],
        "signoff": signoff,
        "author": " ".join(author.split())[:60],
        "manual_override": False,
        "timings": {"draft_seconds": seconds, "draft_model": llm.provider_label() if result["source"] == "ai" else "template"},
    }
    checks.verify_record(rec)
    from app import approval
    approval.log(rec, "drafted", rec["author"], approval.roles()["author"],
                 f"{'AI' if result['source'] == 'ai' else 'Template'} draft for {facts['period']['month_label']}")
    store.save(rec)
    log.info("Draft %s created (%s) in %.1fs", rec["id"], result["source"], seconds)
    return RedirectResponse(f"/review/{rec['id']}", status_code=303)


MAX_VERSIONS = 10


def _keep_version(rec: dict) -> None:
    """Keep the English text being replaced, so it can be restored (last 10)."""
    versions = rec.setdefault("versions", [])
    versions.append({"at": store.now_iso(), "english": rec["english"]})
    del versions[:-MAX_VERSIONS]


@app.post("/review/{bid}/restore/{index}")
def restore_version(bid: str, index: int):
    """Bring back an earlier English text (the current one becomes a version too)."""
    rec = _load(bid)
    if not 0 <= index < len(rec.get("versions") or []):
        raise HTTPException(404, "No such version")

    def fn(r):
        old = r["versions"][index]
        from app import approval
        approval.log(r, "restored", detail=f"English text from {_fmt_dt(old['at'])} restored; translations cleared")
        _keep_version(r)
        r["english"] = writer.normalize_doc(old["english"])
        r["translations"] = {}
        r["status"] = "draft"
        checks.verify_record(r)
    store.update(bid, fn)
    return RedirectResponse(f"/review/{bid}#preview", status_code=303)


@app.get("/review/{bid}", response_class=HTMLResponse)
def review(request: Request, bid: str):
    rec = _load(bid)
    checks.verify_record(rec)
    return page(request, "review.html", rec=rec, view=bulletin_view(rec, "en", highlight=True),
                content_check=content.check(rec["english"], rec["facts"]),
                data_warnings=rec["facts"].get("data_warnings", []),
                lang_choices=translate.language_choices(),
                engine=translate.engine_label(translate.engine_for()[0]),
                job=rec.get("job"))


@app.post("/review/{bid}/save")
def review_save(
    bid: str,
    headline: str = Form(""),
    summary: str = Form(""),
    bullets: str = Form(""),
    officer_section: str = Form(""),
    closing: str = Form(""),
    followup: str = Form(""),
):
    def fn(rec):
        new = writer.normalize_doc({"headline": headline, "summary": summary,
                                    "bullets": bullets.splitlines(),
                                    "officer_section": officer_section, "closing": closing, "followup": followup})
        if new != writer.normalize_doc(rec["english"]):  # older records lack newer optional fields
            from app import approval
            approval.log(rec, "edited", detail="English text changed; translations cleared")
            _keep_version(rec)
            rec["english"] = new
            rec["translations"] = {}  # English changed: old translations are stale
            rec["status"] = "draft"
        checks.verify_record(rec)
    _load(bid)
    store.update(bid, fn)
    return RedirectResponse(f"/review/{bid}#preview", status_code=303)


@app.post("/review/{bid}/signoff")
def review_signoff(bid: str, signoff: str = Form("")):
    rec = _load(bid)
    listed = [o["name"] for o in ((rec["facts"].get("nco") or {}).get("officers") or [])]
    store.update(bid, lambda r: r.update(signoff=signoff if signoff in listed else ""))
    return RedirectResponse(f"/review/{bid}#signoff", status_code=303)


@app.post("/review/{bid}/services")
def review_services(bid: str, services: list[str] = Form([])):
    """Choose which local services appear on the bulletin (max 4)."""
    rec = _load(bid)
    valid = [s["id"] for s in rec["facts"].get("services", [])]
    chosen = [s for s in dict.fromkeys(services) if s in valid][:F.SERVICES_SHOWN]
    store.update(bid, lambda r: r.update(services_shown=chosen))
    return RedirectResponse(f"/review/{bid}#services", status_code=303)


# ---------------------------------------------------------------- translation job

_jobs_lock = threading.Lock()
_running: set[str] = set()


def _back(doc: dict, code: str, info: dict | None = None) -> dict | None:
    """Back-translation for the meaning check, tied to the exact translation it describes."""
    from app.verify import meaning
    back = translate.back_for(doc, code, info or {})
    return {"doc": back, "for": meaning.doc_hash(doc), "at": store.now_iso()} if back else None


def run_translation(bid: str, codes: list[str]) -> None:
    try:
        for code in codes:
            def mark(rec, code=code):
                rec["job"]["langs"][code] = {"status": "running", "started": time.time()}
            store.update(bid, mark)
            rec = store.load(bid)
            try:
                doc, info = translate.translate_doc(rec["english"], code,
                                                    names=[rec["facts"]["neighbourhood"]["name"]])
                entry = {"doc": doc, **{k: v for k, v in info.items() if k != "memory_back"}, "translated_at": store.now_iso()}
                entry["back"] = _back(doc, code, info)
                if (info.get("memory") or {}).get("all_reviewed"):  # every line is one a translator approved
                    entry.update(human_reviewed=True, reviewed_by="translation memory", reviewed_at=store.now_iso())
                status = "done"
            except translate.TranslationFailed as exc:
                entry = {"doc": None, "error": str(exc), "translated_at": store.now_iso()}
                status = "failed"
            except Exception as exc:  # never leave the job hanging
                log.exception("Translation to %s failed", code)
                entry = {"doc": None, "error": f"Unexpected error: {exc}", "translated_at": store.now_iso()}
                status = "failed"

            def save_lang(rec, code=code, entry=entry, status=status):
                rec.setdefault("translations", {})[code] = entry
                j = rec["job"]["langs"][code]
                j.update(status=status, seconds=entry.get("seconds"))
                rec.setdefault("timings", {}).setdefault("translate_seconds", {})[code] = entry.get("seconds")
                checks.verify_record(rec)
            rec = store.update(bid, save_lang)
            from app import tm
            tm.learn(rec, code)  # lines that passed every check become machine entries in the memory

        def finish(rec):
            rec["job"]["status"] = "done"
            rec["job"]["finished"] = time.time()
        store.update(bid, finish)
    finally:
        with _jobs_lock:
            _running.discard(bid)


def resume_interrupted_jobs(background: bool = True) -> dict[str, list[str]]:
    """After a restart, finish translation jobs that were running when the app stopped."""
    resumed = {}
    for rec in store.list_all():
        job = rec.get("job") or {}
        if job.get("status") != "running":
            continue
        bid = rec["id"]
        todo = [c for c in rec.get("languages", [])
                if not (rec.get("translations", {}).get(c) or {}).get("doc")
                and (job.get("langs", {}).get(c) or {}).get("status") in ("pending", "running", None)]

        def fn(r, todo=todo):
            for c in todo:
                r["job"].setdefault("langs", {})[c] = {"status": "pending"}
            r["job"]["resumed_at"] = time.time()
            if not todo:
                r["job"]["status"] = "done"
        store.update(bid, fn)
        if not todo:
            continue
        with _jobs_lock:
            if bid in _running:
                continue
            _running.add(bid)
        resumed[bid] = todo
        if background:
            threading.Thread(target=run_translation, args=(bid, todo), daemon=True).start()
        else:
            run_translation(bid, todo)
    return resumed


@app.post("/review/{bid}/translate")
def start_translation(bid: str, languages: list[str] = Form([]), wait: str = Form("")):
    rec = _load(bid)
    codes = [c for c in dict.fromkeys(languages) if c and c != "en" and c in translate.LANGUAGES]
    with _jobs_lock:
        if bid in _running:
            return RedirectResponse(f"/review/{bid}?translating=1", status_code=303)
        _running.add(bid)

    def fn(r):
        r["languages"] = codes
        # keep translations that are still valid for the current English text
        r["translations"] = {c: t for c, t in (r.get("translations") or {}).items() if c in codes and t.get("doc")}
        todo = [c for c in codes if c not in r["translations"]]
        r["job"] = {"status": "running" if todo else "done", "started": time.time(),
                    "engine": translate.engine_label(translate.engine_for()[0]),
                    "langs": {c: {"status": "done" if c in r["translations"] else "pending"} for c in codes},
                    "todo": todo}
        checks.verify_record(r)
    rec = store.update(bid, fn)
    todo = rec["job"]["todo"]
    if not todo:
        with _jobs_lock:
            _running.discard(bid)
        return RedirectResponse(f"/publish/{bid}", status_code=303)
    if wait:  # synchronous (tests / scripts)
        run_translation(bid, todo)
        return RedirectResponse(f"/publish/{bid}", status_code=303)
    threading.Thread(target=run_translation, args=(bid, todo), daemon=True).start()
    return RedirectResponse(f"/review/{bid}?translating=1", status_code=303)


@app.get("/api/bulletins/{bid}/progress")
def progress(bid: str):
    rec = _load(bid)
    job = rec.get("job") or {"status": "none", "langs": {}}
    now = time.time()
    langs = []
    for code, j in job.get("langs", {}).items():
        elapsed = j.get("seconds")
        if j.get("status") == "running" and j.get("started"):
            elapsed = round(now - j["started"])
        langs.append({"code": code, "name": lang_info(code)["name"], "status": j.get("status"),
                      "seconds": elapsed})
    running = job.get("status") == "running" and bid not in _running and job.get("started", 0) < now - 5
    return {"status": "stalled" if running else job.get("status"), "langs": langs,
            "engine": job.get("engine", "")}


# ---------------------------------------------------------------- publish

@app.get("/publish/{bid}", response_class=HTMLResponse)
def publish_page(request: Request, bid: str, lang: str = "en"):
    rec = _load(bid)
    checks.verify_record(rec)
    codes = ["en"] + [c for c in rec.get("languages", [])]
    unpublished = rec.get("status") != "published"
    views = [bulletin_view(rec, c, highlight=unpublished or not rec["verification"].get(c, {}).get("ok"))
             for c in codes]
    blocked = checks.publish_blocked(rec)
    content_blocked = checks.content_blockers(rec)
    texts = {c: share.plain_text(rec, c) for c in codes if c == "en" or (rec["translations"].get(c) or {}).get("doc")}
    newsletter = {c: share.newsletter_text(rec, c) for c in texts}
    from app import approval
    return page(request, "publish.html", rec=rec, views=views, blocked=blocked, newsletter=newsletter,
                approval=approval.state(rec), error=request.query_params.get("error", ""),
                content_blocked=content_blocked, active=lang if lang in codes else "en",
                texts=texts, wa={c: share.whatsapp_url(t) for c, t in texts.items()},
                mailto={c: share.mailto_url(rec, c) for c in texts})


@app.post("/publish/{bid}")
def publish(bid: str, manual_ok: str = Form("")):
    rec = _load(bid)
    checks.verify_record(rec)
    blocked = checks.publish_blocked(rec)
    content_blocked = [f["title"] for f in checks.content_blockers(rec)]
    if (blocked or content_blocked) and not manual_ok:
        return RedirectResponse(f"/publish/{bid}?error=blocked", status_code=303)
    from app import approval
    st = approval.state(rec)
    if st["required"] and not st["current"]:
        return RedirectResponse(f"/publish/{bid}?error=review#second-check", status_code=303)

    def fn(r):
        rv = r.get("review") if st["current"] else None
        approval.log(r, "published", (rv or {}).get("name", ""), (rv or {}).get("role", ""),
                     ("Second check by " + rv["name"]) if rv else "Published without a second check")
        r["published_review"] = rv
        r["status"] = "published"
        r["published_at"] = store.now_iso()
        r["manual_override"] = bool(blocked or content_blocked)
        r["manual_override_langs"] = blocked
        r["manual_override_content"] = content_blocked
    store.update(bid, fn)
    return RedirectResponse(f"/publish/{bid}", status_code=303)


@app.post("/publish/{bid}/unpublish")
def unpublish(bid: str):
    """Take a published bulletin down: back to draft (off the public site, feeds and embed cards), recorded in its history."""
    from app import approval
    rec = _load(bid)
    if rec.get("status") != "published":
        return RedirectResponse(f"/publish/{bid}", status_code=303)

    def fn(r):
        r["status"] = "draft"
        r["unpublished_at"] = store.now_iso()
        approval.log(r, "unpublished", detail="Taken down; back to draft")
    store.update(bid, fn)
    return RedirectResponse(f"/publish/{bid}?taken_down=1", status_code=303)


@app.post("/publish/{bid}/review-signoff")
def review_signoff_second(bid: str, name: str = Form(""), confirm: str = Form("")):
    """The second person's sign-off (no accounts: name + configurable role, tied to this exact text)."""
    from app import approval
    _load(bid)
    if not confirm:
        return RedirectResponse(f"/publish/{bid}?error=confirm#second-check", status_code=303)
    errors = []
    store.update(bid, lambda r: errors.append(approval.sign_off(r, name)))
    if errors and errors[0]:
        return RedirectResponse(f"/publish/{bid}?error={errors[0]}#second-check", status_code=303)
    return RedirectResponse(f"/publish/{bid}#second-check", status_code=303)


@app.post("/publish/{bid}/retry/{code}")
def retry_language(bid: str, code: str, wait: str = Form("")):
    """Translate one language again (e.g. after a ⚠️ mixed-script or number mismatch)."""
    rec = _load(bid)
    if code not in rec.get("languages", []):
        raise HTTPException(404, "Language not in this bulletin")

    def fn(r):
        r["translations"].pop(code, None)
        if r.get("status") == "published":
            r["status"] = "draft"
    store.update(bid, fn)
    return start_translation(bid, languages=rec["languages"], wait=wait)


@app.post("/publish/{bid}/edit/{code}")
def edit_translation(
    bid: str,
    code: str,
    headline: str = Form(""),
    summary: str = Form(""),
    bullets: str = Form(""),
    officer_section: str = Form(""),
    closing: str = Form(""),
    followup: str = Form(""),
    human_reviewed: str = Form(""),
    reviewer: str = Form(""),
    next: str = Form(""),
):
    """Fix or paste a translation by hand (e.g. from a community translator). Every number and
    script check still applies; the officer can mark it as reviewed by a human translator."""
    rec = _load(bid)
    if code not in rec.get("languages", []):
        raise HTTPException(404, "Language not in this bulletin")

    edited: list[dict] = []

    def fn(r):
        t = r.setdefault("translations", {}).get(code) or {}
        new = writer.normalize_doc({"headline": headline, "summary": summary,
                                    "bullets": bullets.splitlines(),
                                    "officer_section": officer_section, "closing": closing, "followup": followup}, code)
        changed = new != (writer.normalize_doc(t["doc"], code) if t.get("doc") else None)
        if changed:
            from app import approval
            approval.log(r, "edited", detail=f"{lang_info(code)['name']} translation edited by hand")
            t.update(doc=new, edited=True, edited_at=store.now_iso(), error=None)
            edited.append(new)
            if not t.get("engine"):
                t["engine"] = "manual"
                t["engine_label"] = "Entered by hand"
        was = bool(t.get("human_reviewed"))
        t["human_reviewed"] = bool(human_reviewed)
        name = " ".join(reviewer.split())[:60]
        if human_reviewed and (not was or changed or name):
            t.update(reviewed_by=name or t.get("reviewed_by", ""), reviewed_at=store.now_iso())
            from app import approval
            approval.log(r, "translation reviewed", name, "Translator", f"{lang_info(code)['name']}: read and confirmed")
        elif not human_reviewed:
            t.pop("reviewed_by", None)
            t.pop("reviewed_at", None)
        r["translations"][code] = t
        if changed and r.get("status") == "published":
            r["status"] = "draft"  # re-publish after changing text
        checks.verify_record(r)
    store.update(bid, fn)
    if edited:  # read the new text back in English (outside the store lock: it's a network call)
        back = _back(edited[0], code)

        def set_back(r):
            t = r["translations"].get(code) or {}
            if back and t.get("doc") == edited[0]:
                t["back"] = back
            checks.verify_record(r)
        store.update(bid, set_back)
    if human_reviewed:  # a translator confirmed it: its standard lines become reviewed memory for every bulletin
        from app import tm
        tm.learn(store.load(bid), code, reviewed_by=" ".join(reviewer.split())[:60] or "translator")
    if next.startswith("/translations"):  # came from the translator review queue
        return RedirectResponse(next, status_code=303)
    return RedirectResponse(f"/publish/{bid}?lang={code}", status_code=303)


def numbers_table(rec: dict) -> list[dict]:
    f = rec["facts"]
    p = f["period"]
    from app.data import provenance
    try:
        q = provenance.queries(f)
    except Exception:  # links are a convenience; never let them break the download
        q = {}
    rows = []
    for l in f["categories"]:
        if l["counts_only"]:
            note = "Counts only (never a percent change)"
        elif l["ytd_prev"] is None:
            note = "No comparable history in TPS open data"
        elif l["pct_change"] is None:
            note = "Percent change not shown: a count is under 10"
        else:
            note = ""
        rows.append({
            "category": l["label"],
            "tps_category": l["key"],
            f"reports_{p['month_label']}": l["month"],
            f"year_to_date_{p['year']}": l["ytd"],
            f"same_period_{p['prev_year']}": "" if l["ytd_prev"] is None else l["ytd_prev"],
            "percent_change": "" if l["pct_change"] is None else l["pct_change"],
            "on_bulletin": "yes" if l["key"] in f["display"] else "no",
            "note": note,
            "check_month_url": (q.get(l["key"]) or {}).get("month") or "",
            "check_year_to_date_url": (q.get(l["key"]) or {}).get("ytd") or "",
            "check_same_period_url": (q.get(l["key"]) or {}).get("prev") or "",
        })
    return rows


def _download_name(rec: dict, ext: str) -> str:
    return f"neighbourbrief-{rec['id'].rsplit('-', 1)[0]}-numbers.{ext}"


def _meeting(date_s: str, time_s: str, place: str) -> dict | None:
    """Structured next meeting (for the calendar file) and its plain sentence."""
    try:
        d = dt.date.fromisoformat(date_s.strip())
    except ValueError:
        return None
    t = None
    try:
        t = dt.time.fromisoformat(time_s.strip()) if time_s.strip() else None
    except ValueError:
        t = None
    place = " ".join(place.split())[:120]
    when = d.strftime("%A, %B %-d")
    if t:
        hour = t.strftime("%-I") + (t.strftime(":%M") if t.minute else "")
        when += f" at {hour} {'a.m.' if t.hour < 12 else 'p.m.'}"
    return {"date": d.isoformat(), "time": t.strftime("%H:%M") if t else "", "place": place,
            "text": when + (f", {place}" if place else "")}


@app.get("/bulletin/{bid}/meeting.ics")
def meeting_ics(bid: str):
    """The next community meeting as a calendar file (Add to calendar)."""
    rec = _load(bid)
    m = (rec.get("officer") or {}).get("meeting")
    if not m:
        raise HTTPException(404, "No meeting date for this bulletin")
    from zoneinfo import ZoneInfo
    hood = rec["facts"]["neighbourhood"]["name"]

    def esc(s):
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    d = dt.date.fromisoformat(m["date"])
    if m.get("time"):
        start = dt.datetime.combine(d, dt.time.fromisoformat(m["time"]), tzinfo=ZoneInfo("America/Toronto"))
        utc = start.astimezone(dt.timezone.utc)
        when = [f"DTSTART:{utc:%Y%m%dT%H%M%SZ}", f"DTEND:{(utc + dt.timedelta(hours=1, minutes=30)):%Y%m%dT%H%M%SZ}"]
    else:
        when = [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{(d + dt.timedelta(days=1)):%Y%m%d}"]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//NeighbourBrief TO//EN", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
             f"UID:{bid}-meeting@neighbourbrief", f"DTSTAMP:{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}", *when,
             f"SUMMARY:{esc('Community safety meeting: ' + hood)}",
             *( [f"LOCATION:{esc(m['place'])}"] if m.get("place") else [] ),
             f"DESCRIPTION:{esc('Neighbourhood safety update with your Neighbourhood Community Officers. From NeighbourBrief TO (not affiliated with or endorsed by the Toronto Police Service).')}",
             "END:VEVENT", "END:VCALENDAR"]
    return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="meeting-{bid.rsplit("-", 1)[0]}.ics"'})


@app.get("/bulletin/{bid}/numbers.csv")
def numbers_csv(bid: str):
    """The numbers behind a bulletin, for anyone who asks (CPLC, residents, councillors)."""
    import csv
    import io
    rec = _load(bid)
    rows = numbers_table(rec)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    f = rec["facts"]
    buf.write("\n")
    for line in (
        f"Neighbourhood,{f['neighbourhood']['name']} ({f['neighbourhood']['id']}); {f['neighbourhood']['division']}",
        f"Period,\"{f['period']['month_label']}; year to date {f['period']['ytd_label']} vs {f['period']['ytd_prev_label']}\"",
        "Counting,\"Reports to police by report date (Toronto time); one per occurrence and category; a report does not mean anyone was charged\"",
        f"Excluded,{'; '.join(f['excluded'])}",
        f"TPS data updated,\"{f['freshness']['tps_updated_label']}\"",
        f"Source,\"{f['source']['name']} ({f['source']['licence']}): {', '.join(f['source']['layers'])}\"",
        "Note,Prototype; not affiliated with or endorsed by the Toronto Police Service",
    ):
        buf.write(line + "\n")
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_download_name(rec, "csv")}"'})


@app.get("/bulletin/{bid}/numbers.json")
def numbers_json(bid: str):
    rec = _load(bid)
    f = rec["facts"]
    body = {
        "neighbourhood": f["neighbourhood"], "period": f["period"], "rows": numbers_table(rec),
        "totals": f["totals"], "excluded": f["excluded"], "freshness": f["freshness"],
        "source": f["source"], "rules": {
            "counting": "Reports to police by report date (Toronto time); unique (EVENT_UNIQUE_ID, category)",
            "percent_change": "Only when both year-to-date counts are 10 or more; whole numbers",
            "counts_only": ["Shooting", "Homicide"],
        },
    }
    return JSONResponse(body, headers={"Content-Disposition": f'attachment; filename="{_download_name(rec, "json")}"'})


@app.get("/bulletin/{bid}/all", response_class=HTMLResponse)
def bulletin_all(request: Request, bid: str, print: str = ""):
    """Every language of a bulletin, one Letter page each: for meeting handouts."""
    rec = _load(bid)
    checks.verify_record(rec)
    codes = ["en"] + [c for c in rec.get("languages", [])
                      if (rec.get("translations", {}).get(c) or {}).get("doc")]
    return page(request, "bulletin_all.html", views=[bulletin_view(rec, c) for c in codes], rec=rec,
                auto_print=bool(print))


@app.get("/bulletin/{bid}/easy", response_class=HTMLResponse)
def bulletin_easy_read(request: Request, bid: str):
    """Easy Read version (English): one short sentence per line, with pictures. Written by code from the facts."""
    from app import easyread
    rec = _load(bid)
    checks.verify_record(rec)
    if not rec["verification"]["en"]["ok"]:
        raise HTTPException(404, "This bulletin's numbers need checking, so there's no Easy Read version yet.")
    return page(request, "easy_read.html", rec=rec, lines=easyread.lines(rec), f=rec["facts"])


@app.get("/bulletin/{bid}/{code}", response_class=HTMLResponse)
def bulletin_standalone(request: Request, bid: str, code: str, large: str = "", print: str = ""):
    """One language, print-ready, nothing else on the page (used for PDF export).
    `?large=1` gives a large-print version (bigger type, may run to 2 pages)."""
    rec = _load(bid)
    checks.verify_record(rec)
    if code != "en" and code not in rec.get("languages", []):
        raise HTTPException(404, "That language isn't in this bulletin.")
    return page(request, "bulletin_page.html", v=bulletin_view(rec, code), rec=rec,
                large=bool(large), auto_print=bool(print))


def translation_queue() -> list[dict]:
    """Every translation with its review state, those needing a human review first."""
    rows = []
    for r in store.list_all():
        for code in r.get("languages", []):
            t = (r.get("translations") or {}).get(code) or {}
            if not t.get("doc"):
                continue
            v = (r.get("verification") or {}).get(code) or {}
            rows.append({"bid": r["id"], "code": code, "lang": lang_info(code), "hood": r["facts"]["neighbourhood"]["name"],
                         "month": r["facts"]["period"]["month_label"], "status": r.get("status"),
                         "reviewed": bool(t.get("human_reviewed")), "reviewed_by": t.get("reviewed_by", ""),
                         "engine": t.get("engine_label") or t.get("engine") or "", "edited": bool(t.get("edited")),
                         "checks_ok": bool(v.get("ok")), "flags": len((v.get("quality") or {}).get("flags") or [])})
    return sorted(rows, key=lambda x: (x["reviewed"], not x["flags"], x["status"] != "published", x["lang"]["name"]))  # flagged lines first


@app.get("/translations/memory", response_class=HTMLResponse)
def memory_overview(request: Request):
    """Translation memory: the standard lines per language, how many a translator has approved."""
    from app import tm
    return page(request, "memory.html", stats=tm.stats(), code=None)


@app.get("/translations/memory/{code}", response_class=HTMLResponse)
def memory_language(request: Request, code: str, reviewer: str = "", error: str = ""):
    from app import tm
    if code not in translate.LANGUAGES or code == "en":
        raise HTTPException(404, "Unknown language")
    entries = sorted(tm.load(code).items(), key=lambda kv: (kv[1]["status"] == "reviewed", kv[1]["en"]))
    return page(request, "memory.html", code=code, info=lang_info(code), entries=entries, reviewer=reviewer, error=error,
                stats=None, number_forms=tm.number_forms)


@app.post("/translations/memory/{code}/review")
def memory_review(code: str, key: str = Form(...), text: str = Form(""), reviewer: str = Form("")):
    """A translator approves a line (and may correct it; the {0}, {1}… and {name} slots must stay)."""
    from urllib.parse import quote
    from app import tm
    from app.verify import script as S
    if code not in translate.LANGUAGES or code == "en":
        raise HTTPException(404, "Unknown language")
    back = f"/translations/memory/{code}?reviewer={quote(reviewer)}"
    if not reviewer.strip():
        return RedirectResponse(back + "&error=" + quote("Add your name first: it's shown with the line.") + "#top", status_code=303)
    plain = re.sub(r"\{(\d+|name)\}", " ", text)  # the alphabet check, without the slots
    scr = S.check_doc({"headline": plain, "summary": "", "bullets": [], "officer_section": "", "closing": ""}, code, {})
    if text.strip() and not scr["ok"]:
        return RedirectResponse(back + "&error=" + quote(S.describe(scr)) + "#top", status_code=303)
    try:
        tm.review(code, key, reviewer, new_text=text if text.strip() else None)
    except KeyError:
        raise HTTPException(404, "That line isn't in the memory any more.")
    except ValueError as exc:
        return RedirectResponse(back + "&error=" + quote(str(exc)) + "#top", status_code=303)
    return RedirectResponse(back + "#top", status_code=303)


@app.get("/translations", response_class=HTMLResponse)
def translations_page(request: Request, lang: str = ""):
    rows = translation_queue()
    langs = sorted({(x["code"], x["lang"]["name"]) for x in rows}, key=lambda c: c[1])
    if lang:
        rows = [x for x in rows if x["code"] == lang]
    return page(request, "translations.html", rows=rows, langs=langs, lang=lang,
                todo=sum(1 for x in rows if not x["reviewed"]))


@app.get("/translations/{bid}/{code}", response_class=HTMLResponse)
def translation_review(request: Request, bid: str, code: str):
    rec = _load(bid)
    t = (rec.get("translations") or {}).get(code) or {}
    if code not in rec.get("languages", []) or not t.get("doc"):
        raise HTTPException(404, "No translation to review")
    checks.verify_record(rec)
    from app.verify import meaning
    m = meaning.check(t, rec["english"])
    back = (t.get("back") or {}).get("doc") if not m.get("stale") else None
    en, tr = rec["english"], writer.normalize_doc(t["doc"], code)
    fields = [("headline", "Headline"), ("summary", "Summary")] + \
             [(f"bullets.{i}", f"Line {i + 1}") for i in range(len(en.get("bullets", [])))] + \
             [("followup", "Since last month"), ("officer_section", "From your officers"), ("closing", "Closing")]

    def get(doc, key):
        if not doc:
            return ""
        if key.startswith("bullets."):
            i = int(key.split(".")[1])
            b = doc.get("bullets") or []
            return b[i] if i < len(b) else ""
        return doc.get(key, "")
    rows = [{"key": k, "label": lbl, "en": get(en, k), "tr": get(tr, k), "back": get(back, k)}
            for k, lbl in fields if get(en, k) or get(tr, k)]
    flagged = {(i["field"], i["index"]) for i in m.get("issues", [])}
    for row in rows:
        f, _, idx = row["key"].partition(".")
        row["flag"] = (f, int(idx) if idx else None) in flagged
    return page(request, "translation_review.html", rec=rec, code=code, info=lang_info(code), t=t, tr=tr, rows=rows,
                verification=rec["verification"].get(code) or {}, meaning=m)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, deleted: str = "", deleted_id: str = "", restored: str = "", error: str = ""):
    items = store.list_all()
    for r in items:
        checks.verify_record(r)  # rules may have changed since the record was saved
    return page(request, "history.html", items=items, deleted=deleted, deleted_id=deleted_id,
                restored=restored, error=error, trash=store.list_trash())


@app.get("/history/backup.zip")
def history_backup():
    stamp = dt.date.today().isoformat()
    return Response(store.backup_zip(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="neighbourbrief-backup-{stamp}.zip"'})


@app.post("/history/import")
async def history_import(file: UploadFile = File(...)):
    from urllib.parse import quote
    data = await file.read(store.MAX_IMPORT_BYTES + 1)
    try:
        if len(data) > store.MAX_IMPORT_BYTES:
            raise ValueError("That backup is too large to import.")
        res = store.import_zip(data)
    except ValueError as exc:
        return RedirectResponse("/history?error=" + quote(str(exc)), status_code=303)
    msg = (f"Imported {len(res['added'])} bulletin{'s' if len(res['added']) != 1 else ''}"
           + (f"; {len(res['skipped_existing'])} already here (kept yours)" if res["skipped_existing"] else "")
           + (f"; {len(res['rejected'])} file(s) ignored" if res["rejected"] else "") + ".")
    return RedirectResponse("/history?restored=" + quote(msg), status_code=303)


@app.post("/history/{bid}/restore")
def history_restore(bid: str):
    from urllib.parse import quote
    try:
        rec = store.restore(bid)
    except KeyError:
        return RedirectResponse("/history?error=" + quote("That bulletin isn't in the trash."), status_code=303)
    except FileExistsError:
        return RedirectResponse("/history?error=" + quote("A bulletin with the same id already exists."), status_code=303)
    label = f"{rec['facts']['neighbourhood']['name']} · {rec['facts']['period']['month_label']}"
    return RedirectResponse("/history?restored=" + quote(label), status_code=303)


@app.post("/history/{bid}/delete")
def history_delete(bid: str):
    from urllib.parse import quote
    rec = _load(bid)
    if rec.get("demo"):
        return RedirectResponse("/history?error=" + quote("Demo bulletins can't be deleted from here."), status_code=303)
    store.delete(bid)
    label = f"{rec['facts']['neighbourhood']['name']} · {rec['facts']['period']['month_label']}"
    return RedirectResponse("/history?deleted=" + quote(label) + "&deleted_id=" + quote(bid), status_code=303)


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request, saved: str = "", error: str = ""):
    from app import snapshot, status
    try:
        hoods = F.list_neighbourhoods()
    except tps.TPSUnavailable:
        hoods = []
    return page(request, "status.html", st=status.run_all(), hoods=hoods, saved=saved, error=error,
                offline_hoods=snapshot.saved_neighbourhoods())


@app.post("/offline/save")
def offline_save(neighbourhood: str = Form(...)):
    """Download a neighbourhood's TPS data into the offline snapshot."""
    from urllib.parse import quote

    from app import snapshot
    try:
        done = snapshot.save_for_offline([neighbourhood])
    except (tps.TPSUnavailable, ValueError) as exc:
        return RedirectResponse(f"/status?error={quote(str(exc))}#offline", status_code=303)
    return RedirectResponse(f"/status?saved={quote(done[0]['name'])}#offline", status_code=303)


@app.get("/api/status")
def status_api():
    from app import status
    return status.run_all()


@app.get("/api/health")
def health():
    s = get_settings()
    return {"offline": s.demo_offline, "llm": llm.status(),
            "translator": translate.engine_label(translate.engine_for()[0])}
