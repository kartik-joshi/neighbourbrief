# Demo script: Toronto Open Data Jam (November 2026)

Five minutes, one story: **a resident, then the officer, then why you can trust it, then the whole city.**
A three-minute cut is marked ✂. Talk track and judging notes are in `docs/PITCH.md`.

## The day before (on the laptop you present from)

1. **Get the latest code.** First time: `git clone <repo URL> neighbourbrief && cd neighbourbrief && zsh scripts/setup.sh`.
   Already cloned: `git pull`, then re-run the setup script.
2. **`.env`**: copy `.env.example` to `.env` and add the keys (Azure Translator; Cohere for Ask). Never commit it
   or share it in chat.
3. **October bulletins**: already made (meetings Dec 1 to 3) and live on the public site; October stays the
   latest complete month until Dec 1, so there is nothing to remake for a November demo. (To remake them
   later: `.venv/bin/python scripts/snapshot_demo.py`, then republish the site with `scripts/publish_site.sh`
   so the live site matches.)
4. **Local model**: `ollama list` must show `gemma3:12b` and `bge-m3` (if not: `ollama pull gemma3:12b` and
   `ollama pull bge-m3`, about 9 GB). Then **start the app** and open **Status**. Everything should be ✅, and
   the Cohere line shows this month's calls (the app stops at 300; Ask then falls back to the local model).
5. **Refresh the offline data** (the Status page warns when it's more than 2 days old; takes about a minute,
   keeps the demo bulletins as they are): Status → "Save for offline", or
   `.venv/bin/python -c "from app import snapshot; snapshot.save_for_offline(['Thorncliffe Park', 'Milliken', 'Westminster-Branson', 'Steeles'])"`.
   Status should then say "Ready for your meeting".
6. **Rehearse once** with `DEMO_OFFLINE=true` in `.env` (restart), then switch back to `false`.
7. **Optional backup link**: `scripts/demo_tunnel.sh --background --hours 6` gives a public link to the
   same app (staff pages behind a password). The permanent public site is listed in `README.md`.
8. **Backup video**: `NeighbourBrief_demo_backup.mp4` (`demo_video/`, made with `scripts/record_demo_video.py`). Play it once on the presenting laptop.

## Before you go on

- Start the app: `.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010`.
- **http://127.0.0.1:8010/status** should say "Ready for your meeting".
- Tabs, left to right: **Thorncliffe Park profile** (`/neighbourhoods/055`), **Home**, **Check my numbers**
  example (`/check?hood=Thorncliffe%20Park&example=1`), **Ward 15** (`/wards/15`), **Impact**. Open them all before
  you go on: the first Ward 15 load fetches every neighbourhood in the ward (about 25 s); after that it is instant.
- Bad Wi-Fi: `DEMO_OFFLINE=true` and restart. The 3 demo neighbourhoods and Steeles work fully offline
  (profiles, automatic bulletins in English, maps as outlines).
- Ask slow or out of Cohere calls: switch "Answer with" to the local model or sources only.
- Slow model: skip the live draft and open the saved bulletin. Backup video: `demo_video/NeighbourBrief_demo_backup.mp4`.

## 0:00 The problem (20 s)

> "Toronto Police publish excellent open data every day. But it lives in dashboards. In Thorncliffe
> Park one in five residents grew up speaking Urdu; in Milliken it's Cantonese, Mandarin and Tamil.
> Nobody reads a dashboard at a community meeting or in a WhatsApp group."

> "NeighbourBrief turns the open data into a one-page monthly bulletin for every neighbourhood, in
> plain language and the languages people speak at home, and it checks every single number."

## 0:20 A resident (60 s)

**Thorncliffe Park profile** (as a resident would find it: Home → Find your neighbourhood).
- Top card, **This month in plain language**: "Every one of the 158 neighbourhoods gets this,
  written from the open data and checked, even where no officer has written one yet."
- Open the bulletin in **اردو** (Urdu): right to left, proper font, same numbers, the
  "numbers checked" badge. "Translated in about a second, then every number checked again, and the
  whole thing translated back to English to make sure 'down' didn't become 'up'."
- **Easy Read** link: "Short sentences, pictures, no percentages, for residents with low literacy."
- **Share on WhatsApp**: "Headline and summary in Urdu, with the link. That's how this community shares news."
- Scroll to **What should your officers focus on next month?** Pick *Package thefts*. Send.
  "Anonymous: no names, no contact details, and comments are never published."

## 1:20 The officer (70 s)

**Home** → "October: N bulletins to make" → **Start** Thorncliffe Park.
- **What residents asked for** appears above the priority: "The officer sees what residents just told us."
- **Since last month** is filled from last month's bulletin: "Residents want follow-through. This is
  'you said, we did'."
- Officer's words: *"Package thefts from apartment lobbies."* / *"Building visits with property managers."*
- **Draft bulletin** (about 30 s on the local model; nothing leaves this laptop). While it drafts:
  "Code counts the numbers. The AI only writes sentences around them."

✂ *Three-minute cut: open the saved Thorncliffe Park bulletin instead of drafting live.*

- **Review**: hover a number: *"Matches TPS data…"*. Swap two digits in the assaults count and **Re-check**: red, publishing
  blocked. "An officer can't accidentally publish a wrong number."
- **Translate**: Urdu, Pashto, Arabic in seconds, each checked. **Publish**: print all languages, one page each.

## 2:30 Why you can trust it (50 s)

- On the profile, **Check it yourself → month** on any row: the TPS portal returns the same count.
  "Don't trust us: every number links to the official query that produces it."
- **Check my numbers** (example loaded) → **Check**: ✅ correct; ⚠️ right number, wrong crime; ⚠️ "up"
  when the data says down; ❌ not in the data. "Any councillor's newsletter or news story, checked in seconds."
- "True story: TPS reloads its current-year data by emptying it first. For a few minutes every
  neighbourhood has zero reports. A naive tool would publish '0 assaults'. NeighbourBrief notices,
  keeps the last good copy and says so on the Status page."

- **Ask** (`/ask`, optional, 30 s): choose Thorncliffe Park and ask *"How many assaults were reported in
  Thorncliffe Park last month?"*. The answer cites its source (Cohere Embed, Rerank and Command A), and every number
  in it was matched to the data. Then ask *"Which is the most dangerous neighbourhood in Toronto?"*: declined
  by a rule before any model runs. "Same data, same checks: no rankings, no invented numbers." (About 3 Cohere
  calls per question; the trial key's monthly quota is shared, so rehearse it once, not ten times.)

✂ *Three-minute cut: skip to 4:20.*

## 3:20 The whole city (60 s)

- **Ward 15** (`/wards/15`): "Councillors get a brief per ward: each neighbourhood's own numbers and its
  bulletin to put in a newsletter. Never ranked, never summed across boundaries that don't line up."
- **Impact**: 158 neighbourhoods with a monthly bulletin, 23 languages (French on every automatic
  bulletin), numbers checked, resident answers, bulletins opened by language (counted without cookies).
- **Data health**: "Every dataset we use, checked daily, with feedback for the publishers."

## 4:20 The ask (25 s)

> "What I'd like from the City and TPS: (1) confirm the current-year crime feed as a supported source,
> (2) a home for the public site (it's plain files, live today at the link on screen), and (3) community
> translators to review the AI translations. It's MIT-licensed and ready to hand over."

## If asked

- *Is this an official TPS product?* No. An independent prototype on open data; not affiliated with
  or endorsed by the Toronto Police Service. It says so on every page.
- *Why not a dashboard?* Residents don't read dashboards. They read a one-pager at the meeting or a
  WhatsApp message in their own language.
- *Can the AI make up numbers?* It can try; it can't publish them. Numbers come from code, and every
  number in every language is checked against the data before publishing. Automatic bulletins and
  Easy Read use no AI for the English at all.
- *What do residents' answers do?* Officers see a summary when they start the next bulletin. Nothing
  personal is kept: no names, contacts, IP addresses or cookies; comments with a phone number, email
  or link are refused.
- *Privacy of the usage numbers?* Totals per day by page type and language, nothing else.
- *Cost?* Local model is free; Azure Translator's free tier covers this; the public site is static files.
- *Other cities?* Anything with neighbourhood-level open data on ArcGIS or CKAN: the data layer is one module.
