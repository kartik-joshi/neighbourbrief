# NeighbourBrief TO: full guide

Everything the app does, how to run each part, and how to troubleshoot it. The project overview is in the [README](../README.md).

## What it does

1. **Facts from TPS open data (code, not AI).** Last full month's reports to police, year to date,
   and the same period last year for one of Toronto's 158 neighbourhoods. Counted by report date in
   Toronto time, one per occurrence and category. Small-number rule: percent change only when both
   counts are ≥ 10; shootings and homicides are counts only.
2. **Plain-language draft.** A local AI model (Ollama `gemma3:12b` by default) writes a Grade 6
   draft around the officer's own "priority" and "what we did". If the model is unavailable or
   invents a number, a template writer produces the same structure from the facts.
3. **Translation** into the neighbourhood's main languages (2021 Census), by Azure Translator when a
   key is set, otherwise by the AI model. Numbers are protected during translation.
4. **Verification.** Every number in English and every translation must match the facts, and each
   translation must use the right script (e.g. no Devanagari in Urdu). Each translation is also **read back in English**
   (Azure) and every line must keep its direction: "down 3%" can't become "up 3%". Publishing is blocked until
   checks pass or the officer confirms a manual review.
5. **Plain-language & safety checks** on the text you'll publish (tone, personal information,
   statements about groups, "report ≠ charge" reminder, length, reading level), plus warnings when
   the TPS data may not cover the whole month.
6. **Fix any language**: translate one language again, or edit/paste a translation (e.g. from a
   community translator) and mark it human-reviewed; all checks still apply.
7. **Print / PDF, WhatsApp, email**: one language, **all languages at once** (one page each, for
   meeting handouts), or **large print** (14 pt, two pages). Email/WhatsApp text is kept short enough
   for every app and always keeps the translation notice.
8. **Monthly workflow helpers**: pick any complete month this year; **reuse** last month's priority,
   actions, meeting, languages and sign-off; optional **prevention tip** (10 vetted tips, the ones
   matching this month's categories first) and **"Prepared by"** line (only officers TPS lists for the
   neighbourhood); **local support services** (up to 4, from TPS community assets data).
9. **History**: search, filter drafts/published, "New from this", delete to a recoverable trash.
10. **Status page** (`/status`): pre-flight check before a meeting: TPS reachable, data freshness,
    AI model, translator, offline snapshot; **save any neighbourhood for offline use**.
11. **The numbers behind each bulletin**: CSV / JSON download with every category and the rules.
12. **You said, we did**: an optional follow-up on last month's priority (what happened, done or still
    working on it), written by code in the officer's words, then translated and checked like the rest.
13. **Neighbourhood map**: the City boundary and numbered pins for nearby community services, with
    "Use my location" (worked out in the browser; the position is never sent or stored). Never
    incident locations, heatmaps or "hot" areas. Offline: a plain outline with the same pins.

14. **Two-person check** (no accounts): a second person signs off on the Publish page by name and
    role; the sign-off is tied to the exact text and goes stale if anything changes. Every bulletin
    keeps a history (drafted, edited, reviewed, published, by whom). `REQUIRE_REVIEW=true` blocks
    publishing without a current sign-off; role labels via `ROLE_AUTHOR` / `ROLE_REVIEWER` (e.g.
    Officer / Sergeant, or Volunteer / CPLC chair).

15. **Translator review queue** (`/translations`): side-by-side English, translation and back-translation;
    a community translator fixes and confirms, and the bulletin then says it was reviewed by a person.
16. **Monthly routine on Home**: which neighbourhoods still need this month's bulletin, one click to
    start from last month's text. **Version history** with restore. **Add the meeting to a calendar**
    (.ics). **Image to share** (a square card per language for WhatsApp groups). **Listen** (read aloud).
17. **Help** (`/help`) and **How it works** (`/how-it-works`, a one-slide diagram).

### Open data tools (for residents, the City and community groups)

- **Automatic bulletins for all 158 neighbourhoods** (`app/auto.py`): written from the open data alone
  (no AI for the English), in English, the neighbourhood's top home languages and French; same checks and
  publish gate as officers' bulletins (a language that fails any check is left out). On every profile
  ("This month in plain language"); an officer's bulletin for the month replaces it.
- **Easy Read** (`/bulletin/{id}/easy`): one short sentence per line with pictures, no percentages.
- **Residents' voice**: "What should your officers focus on next month?" on bulletins and profiles,
  anonymous (no names, contacts, IPs or cookies; comments with contact details or links refused), shown
  to staff when they start the next bulletin. Never published.
- **Ward briefs** (`/wards`): each ward's neighbourhoods with their own counts and bulletins, for
  councillors (counts never split or summed across boundaries that don't line up).
- **Share**: WhatsApp and the phone's share sheet, in the bulletin's language.
- **Usage counts** on a public deployment: totals per day by page type and language, nothing else.

- **Neighbourhood profiles** (`/neighbourhoods`, all 158): reported crime this month and year to date,
  **road safety** (TPS Traffic Collisions: injuries, pedestrians, cyclists, fatal; 12-month chart that
  marks a month still being filled in), **twelve years of yearly counts** (TPS Neighbourhood Crime
  Rates), languages (2021 Census), officers, services and map. Each block names its dataset and date.
  Same numbers as open JSON: `/neighbourhoods/{id}/data.json`.
- **Check my numbers** (`/check`): paste any newsletter, release or post; every number is checked
  against the open data: matches, wrong category, wrong direction ("up" when it went down) or not in
  the data. Dates, times, phone numbers and addresses are skipped.
- **Data health** (`/data-health`): each of the 8 open data layers with last update, newest record,
  record count and a check that every field the app relies on still exists; plus what we noticed
  while joining them (feedback for the publishers).
- **Division summaries** (`/divisions`): a division's neighbourhoods A to Z with combined counts, for
  Community Police Liaison Committees (sums of neighbourhood counts, labelled as such).
- **Impact** (`/impact`): bulletins, pages, numbers checked and residents reached in their home
  language, measured from saved records.
- **Static public site** (`scripts/build_public_site.py`): published bulletins in every language
  (regular and large print), neighbourhood profiles, How we count, Data health, an accessibility
  statement, Atom feeds (all and per neighbourhood), an **embed card** partner websites can iframe, and an
  in-browser **Check a text** on every profile (a JavaScript port of Check my numbers, kept identical by
  `scripts/js_parity_check.py`). Plain files for any web
  host; no server, no login.

## Setup (macOS)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
ollama pull gemma3:12b        # if not already pulled
```

## Run

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open http://127.0.0.1:8000 (use another port, e.g. `--port 8010`, if 8000 is busy). The server only listens on 127.0.0.1. No login, no personal data.

**Offline demo (venue Wi-Fi):** set `DEMO_OFFLINE=true` in `.env` and restart. The app then uses
only `data/demo_snapshot/` (Thorncliffe Park, Milliken, Westminster-Branson) and makes no internet
requests; the local Ollama model still works. Refresh the snapshot the day before with:

```bash
.venv/bin/python scripts/snapshot_demo.py
```

(Downloads the data and pre-generates one full bulletin per demo neighbourhood, ≈10–15 minutes.)

## Public demo link (from this Mac)

```bash
scripts/demo_tunnel.sh --hours 4 --background   # prints a https://….trycloudflare.com link (see deploy/logs/)
scripts/demo_tunnel.sh --stop                   # close it early
```

Runs the Docker image with `PUBLIC_DEPLOY=true` and opens a temporary Cloudflare quick-tunnel link (no
account, no server). Visitors get the resident site; staff pages ask for the password in
`deploy/.env.demo` (any user name). Before the link opens, the script checks from inside the container that
it can't reach this Mac or the home network (a firewall in colima's VM), and that staff pages are closed.
No API keys unless `--azure` / `--cohere`. It closes itself after `--hours` (max 12). For a permanent site, host the static `public_site/` or the image
on a server instead.

## Docker (optional)

```bash
docker build -t neighbourbrief .
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -v "$PWD/data:/app/data" neighbourbrief
```

Publish the port on `127.0.0.1` only. The container reaches the local model on the host
(`host.docker.internal:11434`) and treats Docker's bridge as "this computer" (`ALLOW_CLIENTS`);
requests for any other host name are refused. Saved bulletins live in the mounted `data/` folder.

## Troubleshooting

| What you see | What to do |
|---|---|
| "Address already in use" | Another app has the port: add `--port 8010` (and open that port). |
| Writer says "not available" / drafts use the template | Start Ollama (`ollama serve`) and check `ollama list` shows `gemma3:12b`. The template draft is still checked and usable. |
| "Toronto Police open data is being refreshed right now" | TPS reloads its current-year layer daily (it empties for about 10 minutes). Wait and retry; neighbourhoods used earlier keep working from their last good copy. Status shows the reload. |
| No internet at the venue | `DEMO_OFFLINE=true` in `.env`, restart. Thorncliffe Park, Milliken, Westminster-Branson and Steeles work fully; save more on Status → "Save for offline" beforehand. |
| Translation fails or a tab shows ⚠️ | "Translate again" on that tab, or edit it by hand. With an Azure key set, check the key and region in `.env` (free tier limits bursts; the app waits and retries). |
| Squares instead of letters in a language | Fonts come from Google Fonts when online; offline, macOS system fonts are used. Print from the browser after the page has fully loaded. |
| "NeighbourBrief only works on the computer it runs on" | You opened it from another device. That's on purpose; set `ALLOW_LAN=true` only on a trusted network. |
| A bulletin prints on two pages | The page scales itself to fit when printing; if it still spills, shorten the officer's text or the follow-up. |

## Public site

**Monthly update (automatic).** The live site shows the latest complete month, so it is rebuilt once a month.
`scripts/schedule_monthly_update.sh` installs a macOS launchd job that runs `scripts/monthly_update.sh` at 7 a.m.
on days 4 to 10 of each month. It publishes once TPS has updated its data at least 3 days after the month ended
(so late reports are in), crawls the built site first, publishes only if the crawl is clean, and then stops until
the next month. A Mac notification reports the result. Logs are in `deploy/logs/monthly-YYYY-MM.log`.
The Mac needs to be on (a run it slept through happens on wake), with the Azure key in `.env` and the SSH key for
the site repository. `scripts/monthly_update.sh --check` runs only the checks;
`--now` publishes immediately; `scripts/schedule_monthly_update.sh --remove` removes the schedule.

```bash
scripts/publish_site.sh --owner kartik-joshi --ssh-host github-kartik   # build everything and publish to GitHub Pages
.venv/bin/python scripts/build_public_site.py                  # published bulletins' neighbourhoods
.venv/bin/python scripts/build_public_site.py --hoods all      # all 158 profiles, automatic bulletins, ward briefs
.venv/bin/python scripts/check_public_site.py                  # crawl it like a web host would serve it
```

**Live:** https://kartik-joshi.github.io/neighbourbrief-to/ (rebuild and publish with
`scripts/publish_site.sh`, e.g. the day before a demo, so it shows the latest month; only the built site
is pushed, never the source, `.env`, drafts or feedback). Or copy `public_site/` to any static host. Set `PUBLISHER_NAME` (and optionally `PUBLISHER_CONTACT`) in `.env` so
the site says who publishes it, e.g. a CPLC or residents' association (never TPS branding). Only bulletins that are published and still pass every
number check are included; any link the site can't serve stops the build.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Visual check (Playwright + Chromium; screenshots and PDFs into `test_artifacts/`, fails if a
bulletin doesn't fit one page or comes within 10 px of the limit):

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/visual_check.py
```

Accessibility (axe-core, WCAG 2.1 AA, plus a keyboard test):

```bash
.venv/bin/python scripts/a11y_check.py
```

Live checks with real TPS data and the local model, and the offline check:

```bash
.venv/bin/python scripts/live_checks.py --round 4 --part all
.venv/bin/python scripts/offline_check.py
```

## Switching to Azure (only `.env` changes)

**Azure Translator** (recommended for translation quality):

```
TRANSLATOR=auto                 # or: azure
AZURE_TRANSLATOR_KEY=<key>
AZURE_TRANSLATOR_REGION=<region, e.g. canadacentral>
AZURE_TRANSLATOR_ENDPOINT=https://api.cognitive.microsofttranslator.com
```

With `TRANSLATOR=auto`, the key alone switches translation to Azure. If Azure fails at runtime,
that language falls back to the AI model and the tab says so.

**Azure OpenAI** as the writer model:

```
LLM_PROVIDER=azure_openai
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
AZURE_OPENAI_API_KEY=<key>
AZURE_OPENAI_DEPLOYMENT=<deployment name>
AZURE_OPENAI_API_VERSION=2024-10-21
```

Restart the server after editing `.env`. The New-bulletin screen shows which writer and translator
are active. (Anthropic is also supported: `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`,
`ANTHROPIC_MODEL`.) The Azure and Anthropic paths are implemented but were not tested live.

## Layout

```
app/main.py            routes            app/data/tps.py    ArcGIS fetch + cache + snapshot
app/data/facts.py      facts JSON        app/ai/llm.py      provider-agnostic complete()
app/ai/writer.py       AI + template     app/ai/translate.py Azure / LLM translation
app/verify/numbers.py  number checks     app/verify/script.py script (mixed-script) check
app/templates/         screens + bulletin
app/auto.py            automatic bulletins   app/feedback.py   residents' answers   app/usage.py  usage totals
app/easyread.py        Easy Read             app/data/ward.py  ward briefs          app/rag/      Ask (RAG)
config/neighbourhood_languages*.json, config/neighbourhood_wards.json   data/bulletins/   data/demo_snapshot/
tools/tailwind/        pre-built CSS (npm install && npm run build → app/static/tw.css)
```

Other documents in this folder: `DEMO.md` (demo script), `PITCH.md` (talk track), `DECISIONS.md` (choices made),
`COHERE.md` (Ask with Cohere RAG), `TEST_REPORT.md` (test rounds), `VALIDATION.md` (15-minute user test kit).

