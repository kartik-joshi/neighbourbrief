# Pitch kit: NeighbourBrief TO

## One line

Toronto's open safety data, turned into a one-page monthly bulletin for every one of the 158
neighbourhoods, in plain language and residents' own languages, with every number checked against the data.

## The 30-second version

Toronto Police publish neighbourhood crime, collision and community data every day, but it lives in
dashboards. The people who need it are at community meetings and in WhatsApp groups: in Thorncliffe
Park one in five residents' mother tongue is Urdu; in Milliken it's Cantonese, Mandarin and Tamil.
Every neighbourhood gets a checked, plain-language monthly bulletin in its top home languages and
French, written from the open data; Neighbourhood Community Officers add their own priorities and
"you said, we did" in minutes, starting from what residents told them. Councillors get ward briefs;
anyone can check a newsletter or news story against the data.

## How it maps to typical judging criteria

| Criterion | What to point at |
|---|---|
| **Impact / usefulness** | **All 158 neighbourhoods** get a monthly bulletin (automatic from open data where no officer has written one), plus Easy Read and WhatsApp sharing. Residents answer "What should your officers focus on?" (anonymous) and officers see it when they write the next bulletin. Ward briefs for the 25 councillors. Real users and a real ritual: NCO community meetings, CPLC newsletters, WhatsApp groups. Languages chosen from the 2021 Census for all 158 neighbourhoods (146 get suggestions). Impact page: residents reached in their home language, measured from saved bulletins. "You said, we did" follow-through. |
| **Use of open data** | 8 open data layers from TPS and the City: current-year crime, Major Crime Indicators, Traffic Collisions, Neighbourhood Crime Rates (2014–2025), NCO neighbourhoods, Community Assets, neighbourhood boundaries, 2021 Neighbourhood Profiles. Data health page checks every one and lists feedback for the publishers. Numbers re-published as open JSON, and **every count links to a query on the TPS portal that returns the same number** (checked live: every count in three neighbourhoods, this year and last, matched). |
| **Innovation / quality** | AI writes sentences; code owns every number. Every number in every language is verified (multiset match, category binding, percent binding, alphabet check) and every translation is read back in English to catch a flipped direction ("down 3%" written as "up 3%": same numbers, opposite meaning); publishing is blocked otherwise. "Check my numbers" applies the same engine to anyone's text, and on the public site it runs in the reader's browser, so a journalist can check a story against the open data with no server at all. Honest about data lag (incomplete months are marked, not hidden). |
| **Ready for community translators** | A review queue across all bulletins and a side-by-side page (English, the translation, a rough back-translation; fix in place; confirm with a name). Once confirmed, the bulletin says "Translation reviewed by a human translator". On its first day it caught Azure writing "September 2026" in Chinese as "the 2026th day of September". |
| **Feasibility / adoption** | Runs on one laptop with a free local model; Azure Translator via one `.env` key; works offline. Static public site: plain files on any host, no server, no login, no personal data. MIT licence. 256 automated tests (plus visual, accessibility, public-site crawl and live checks), accessibility checked to WCAG 2.1 AA with zero axe violations, link-checked site build. |
| **Responsible design** | No incident locations, heatmaps or rankings; counts only; small numbers never turned into percentages; "a report doesn't mean anyone was charged"; every page says it's independent of TPS; translations labelled AI-assisted until a human reviews them. |
| **Presentation** | Five-minute demo in `DEMO.md`, three-minute cut marked; backup video; offline mode for bad Wi-Fi. |

## Numbers you can say (all checked in the app)

- 158 neighbourhoods; 146 with suggested languages from the 2021 Census (was 131 before Vietnamese, Polish,
  Bengali, Greek, Gujarati, Ukrainian, Punjabi and Somali were added).
- 158 neighbourhoods with a monthly bulletin; 25 ward briefs.
- 23 languages (English, French + 21 community languages), including right-to-left Urdu, Pashto, Arabic and Persian, and Bengali,
  Gujarati, Gurmukhi (Punjabi), Tamil, Greek and Cyrillic scripts; each with its own alphabet check.
- 8 open data layers joined on each neighbourhood profile.
- 3 published demo bulletins: 11 printed pages, 267 numbers checked, 28,970 residents reached in
  their home language (Impact page; these are demo bulletins, made in scripted runs).
- Draft about 25–45 s on a local model; Azure translation about 1 s per bulletin.

Re-check these on the Impact and Data health pages the morning of the event; they're computed live.

## Questions judges may ask

- **Why would TPS or the City trust AI text?** They don't have to: the numbers never come from the
  AI, and the checker blocks anything that doesn't match. The officer reviews and owns the words.
- **What stops it being used to stigmatize a neighbourhood?** No rankings, no comparisons between
  neighbourhoods, no hotspot maps, banned words ("dangerous", "crime wave"), and small numbers stay
  counts.
- **What happens when the open data itself breaks?** True story from building it: TPS reloads the
  current-year layer by emptying it first. For a few minutes every neighbourhood has zero reports,
  and a naive tool would happily publish "0 assaults". NeighbourBrief checks each layer's size
  against what it has seen this year; empty or partly loaded data is refused, the last good copy is
  kept, and the Status page says "TPS is reloading (19,372 of about 42,107 records)".
- **How current is the data?** The current-year layer updates daily; the Data health page shows the
  last update and newest record for each dataset.
- **What would it take to run this for real?** A home for the static site, a translator review
  partner, and confirmation of the current-year feed. Hosting with staff sign-in can come later.
- **Could another city use it?** Yes, if it has neighbourhood-level open data; the data layer is
  one module (`app/data/`).

## What's next (if asked)

Serbian, Armenian and Tibetan (the largest languages still without
translation, each in one neighbourhood).
