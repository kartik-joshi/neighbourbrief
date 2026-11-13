# Test report

Rules followed: no test was weakened, skipped or deleted to make it pass. The only test *change*
is noted explicitly under the round where it happened.

Artifacts: `test_artifacts/` (screenshots, PDFs, `visual_report.json`, `live_round<N>.json`,
`e2e_bulletins_round<N>/`).

---

## Round 1 — 2026-09-29, ~19:57–20:09

| # | Area | What was tested | Result |
|---|------|-----------------|--------|
| 1 | Unit tests | `pytest` (facts, numbers, script check incl. the real gemma3 "…موصول हुईं." sample, writer, translation, app edge cases, offline) | ✅ 84 passed |
| 2 | Live data | Thorncliffe Park, Milliken, Westminster-Branson + random Centennial Scarborough, Briar Hill-Belgravia; independent re-count from raw rows in Toronto time; non-negative; dedupe; small-number rule | ✅ all 5 OK. Raw rows 234/316/170/75/182, duplicate (event, type) rows: 0 in every neighbourhood (layer is already one row per pair). UTC-vs-Toronto bucketing differences: 0 for August (TPS stores dates as Toronto midnight) — Toronto conversion kept anyway and unit-tested across a month boundary. |
| 3 | End-to-end (TestClient, real gemma3:12b) | New → Draft → Review → Translate → Publish, 3 demo neighbourhoods, 9 languages | ⚠️ 7/9 languages passed. **Milliken** (zh-Hant, zh-Hans, ta) ✅ published. **Thorncliffe ur** ⚠️ blocked: mixed script — model transliterated "Thorncliffe" as "ٿرنکلف" with Sindhi ٿ, still wrong after the one retry. **Westminster-Branson ru** ⚠️ blocked: "added in translation: 21" — model wrote "21 октября 21" (kept the placeholder *and* typed the digits). Publish gate blocked both correctly. |
| 4 | Edge cases (unit, fake model/TPS) | zero reports; no NCO info; NCO=No (division contact); empty officer fields; 1,500-char officer text; emoji + Urdu officer text; Ollama stopped → template + clear message; Azure key missing → LLM translation; TPS unreachable → clear error, no crash; unknown neighbourhood; WhatsApp ≤ 1000 chars | ✅ all pass |
| 5 | Offline mode | `DEMO_OFFLINE=true`, every non-localhost socket blocked in code; full flow from the snapshot; other neighbourhood → clear message; no Google Fonts/Azure | ✅ pass |
| 6 | Visual (Playwright/Chromium, 1280 & 390 px, 34 screenshots, 12 PDFs) | overflow, RTL, fonts, 1 page per bulletin | ⚠️ `/history` overflowed at 390 px (501 px); Milliken **Tamil PDF = 2 pages** (legal footer spilled). Reviewed screenshots by eye: Urdu Nastaliq, Arabic/Pashto Naskh, Chinese TC/SC, Tamil render with no empty boxes; RTL correct. |
| 7 | Timing (gemma3:12b, Mac mini M4) | | Draft 41–42 s (1 attempt each). Translation per language: ur 107 s (with retry), ps 58 s, ar 50 s, zh-Hant 45 s, zh-Hans 40 s, ta 46 s, ru 51 s, fil 55 s, it 47 s. Whole bulletin (draft + 3 languages) ≈ 3.5 min. |

Also found while pre-generating the demo bulletins (just before this round): gemma3's zh-Hant wrote
"8月"/"10月" (= August/October) and the number check flagged them as added numbers.

**Fixes made in round 1**
- Month names written as `N月`/`N월` (1–12) are dates, not counts → excluded from number extraction (+ test).
  *Test change:* an older extraction test used "８月に１２件" expecting 8 as a count, which contradicts
  the new rule; it now uses "１２件と３０件" — still a full-width-digit test, same coverage.
- `/history`: cards stack on mobile; grid track constrained (`minmax(0,1fr)`); verification re-run on
  display (Milliken showed a stale "⚠️ Check"). Now 390 = 390 px.
- Print: Tamil 9 pt; print-time fit-to-one-page safety net (`beforeprint` zoom). Tamil PDF now 1 page;
  all 12 PDFs 1 page.
- Translation: the neighbourhood name is protected as a `⟦P0⟧` token (always kept in Latin), so the
  model can't transliterate it into the wrong alphabet (+ test).
- Translation: the number multiset is now compared inside the retry loop; "digits typed next to a
  placeholder" triggers the retry with the problem stated (+ regression test using the real
  Russian output).

**Still broken after round 1:** nothing known in code; the two blocked translations need a re-run (round 2).

---

## Round 2 — 2026-09-29, ~20:09–20:20

| # | Area | Result |
|---|------|--------|
| 1 | Unit tests | ✅ 86 passed at start of round (88 after this round's fix) |
| 2 | Live data (3 demo + random Birchcliffe-Cliffside, Casa Loma) | ✅ all 5 OK (rows 234/316/170/294/107; 0 duplicate pairs; independent recount matches) |
| 3 | End-to-end, real gemma3:12b | ⚠️ 8/9 languages. Fixed from round 1: **Urdu ✅** (name kept in Latin), **Russian ✅**. Milliken ✅ and Westminster-Branson ✅ published. **Thorncliffe ps** ⚠️ blocked: model left "AUGUST" in Latin, still after the retry. |
| 4 | Edge cases, *live* (no mocks) | ✅ Model endpoint down (closed port 11999) → template draft in 0.3 s with "The AI writer isn't available (is Ollama running?)…", translation shows a clear per-language error, home shows writer "not available". TPS unreachable (bad host, empty cache, no snapshot) → `/` and `/draft` show "Couldn't reach Toronto Police open data…", API 503, no crash. Plus all unit edge cases ✅. |
| 5 | Offline | ✅ (unit test with sockets blocked) |
| 6 | Visual | ✅ re-run after round 1 fixes: `/history` 390 = 390 px; all 12 PDFs 1 page (see round 1 artifacts, regenerated) |
| 7 | Timing | Draft 41–43 s. ur 51 s, ps 108 s (retry), ar 47 s, zh-Hant 89 s (retry), zh-Hans 42 s, ta 49 s, ru 49 s, fil 56 s, it 48 s. |

**Fix made in round 2**
- If, after the retry, the *only* script problems left are English month/weekday names, they're
  filled in from Unicode CLDR calendar names (Babel) for that language and the tab says so. Any
  other Latin word still blocks publish (+ 2 tests, one using the real Pashto output).
  Not done for all month names up front: Russian inflects them ("в августе" vs "августа").

**Also noticed (quality, not caught by checks):** gemma's Pashto once rendered "August" as "لړم"
(an Afghan solar-calendar month) — a wrong month that no automatic check can see. This is why every
translated page carries the "not yet reviewed by a human translator" notice, and why DEMO.md asks
TPS for a human translation review partner.

---

## Round 3 — 2026-09-29, ~20:20–20:35

| # | Area | Result |
|---|------|--------|
| 1 | Unit tests | ✅ 88 passed |
| 2 | Live data (3 demo + random High Park-Swansea, Yonge-Eglinton) | ✅ all 5 OK (rows 234/316/170/201/140; 0 duplicate pairs; independent recount matches; small-number rule holds) |
| 3 | End-to-end, real gemma3:12b | ✅ **9/9 languages verified (numbers + script), all 3 bulletins published.** English bullets now read "Assaults: 10 reports to police in August; …" (prompt fix). These bulletins became the demo bulletins in `data/bulletins/`. |
| 4 | Edge cases | ✅ unit edge cases (see round 1) + live failure checks from round 2 unchanged |
| 5 | Offline, **real model** (`scripts/offline_check.py`): `DEMO_OFFLINE=true`, every non-localhost socket blocked, empty cache | ✅ Thorncliffe Park: AI draft 41.7 s, Urdu 50.1 s verified, published (92 s total). Non-local connection attempts: **none**. History shows the 3 pre-generated bulletins. |
| 6 | Visual (34 screenshots at 1280/390, 12 PDFs) | ✅ No overflow, RTL correct for ur/ps/ar, fonts render (Nastaliq, Naskh, Noto Sans TC/SC, Tamil), **all 12 PDFs = 1 page**. |
| 7 | Timing | Draft 40.8–42.5 s. ur 50.5, ps 58.1, ar 49.3, zh-Hant 46.4, zh-Hans 36.8, ta 45.0, ru 48.5, fil 56.8, it 47.0 s. Full bulletin with 3 languages ≈ 3–3.5 min on a Mac mini M4. |

**Fixes in round 3:** none needed.

## Still open / known issues
- Translation *quality* isn't machine-checkable: the checks guarantee numbers and alphabet, not
  meaning (e.g. Pashto "لړم" for August in one earlier run; Urdu category names are sometimes
  loose). Human translator review is still required — the bulletin says so on every page.
- gemma3:12b occasionally needs its one retry (≈ +50 s for that language); in 3 rounds × 9
  languages, 2 languages still failed after the retry in rounds 1–2 (both then fixed in code).
  The gate blocked them each time, as designed.
- Azure Translator / Azure OpenAI / Anthropic paths are unit-tested with mocked HTTP only.
- Print-fit safety net (`beforeprint` zoom) is exercised by Chrome's print dialog; the Playwright
  PDFs pass at natural size, so the zoom path itself wasn't needed in these runs.

---

## Addendum — product review additions (after round 3)
- `pytest`: ✅ 102 passed (new: `tests/test_content.py`, retry / paste-translation / human-reviewed / edit-after-publish / reuse / review-panel app tests, data warnings, writer time budget).
- A test caught a real template bug before it shipped: `f.items` in Jinja resolves to the dict's `.items()` method, crashing Review whenever a finding existed; key renamed to `matches`.
- One new test's fake clock was ordered wrong (test bug); fixed the clock, not the logic.
- Visual check re-run: ✅ no overflow at 1280/390 px, 12/12 PDFs one page. Browser: reuse fills fields + languages; checks panel; RTL edit form on the Urdu tab.
- Not re-run with the real model: the retry route reuses the same translation job code exercised in rounds 1–3.

---

## Addendum 2 — blocking safety checks + shorter drafts
- `pytest`: ✅ 109 passed (new: safety findings block publish until confirmed, override recorded; length advice doesn't block; too-long AI draft asks once for shorter; still-long accepted; shorter-but-broken retry keeps the valid long draft; broken-JSON retry keeps it too; template within length).
- Test fixes (premise/fixture errors, not weakened): a test used a phone number to test the *wording* gate, but its digits also (correctly) fail the number check, so it now uses an email address; a fixture's trailing space is stripped by `normalize_doc`, so the comparison strips too.
- Real gemma3:12b, 3 demo neighbourhoods, new prompt: 150 / 149 / 157 words, Grade 5.3 / 5.0 / 4.6, 1 attempt each, 35–41 s.
- Demo regeneration (`scripts/snapshot_demo.py`): drafts 25 s; translations ur 82 s (with retry), ps 48, ar 39, zh-Hant 41, zh-Hans 35, ta 40, ru 40, fil 46, it 38 s — **9/9 verified, 3/3 published**, no safety findings.
- Visual check: ✅ no overflow, 12/12 PDFs one page. Offline check (real model, non-local sockets blocked): ✅ draft 34 s, Urdu 37 s verified, published, 0 non-local connection attempts.

---

## Round 4 — regression after the overnight loop (2026-09-30, ~00:38–00:55)

After 23 overnight changes (see git history), the full set of checks again:

| # | Area | Result |
|---|------|--------|
| 1 | Unit tests | ✅ 143 passed |
| 2 | Live data (3 demo + random High Park-Swansea, L'Amoreaux West — note the apostrophe) | ✅ all 5 OK (rows 234/316/170/201/218; 0 duplicate pairs; independent recount matches; small-number rule holds) |
| 3 | End-to-end, real gemma3:12b (now with glossary, tips-capable writer, services, Urdu repair) | ✅ **9/9 languages verified, 3/3 published.** Drafts 38–39 s. ur 47 s, ps 99 s (1 retry), ar 46, zh-Hant 41, zh-Hans 39, ta 45, ru 43, fil 48, it 42 s — only 1 retry in 9 (rounds 1–2 had 2–3 failures). |
| 4 | Edge cases | ✅ in `pytest` (fake model/TPS): zero reports, no NCO, empty/long/emoji/Urdu officer text, model down, Azure key missing, TPS down, damaged files, LAN client refused, hostile backup zip, curly apostrophes, long share links |
| 5 | Offline, real model, non-local sockets blocked | ✅ draft 37 s, Urdu 42 s verified, published; 0 non-local connection attempts; 3 demo bulletins in History |
| 6 | Visual (38 screenshots, 18 PDFs) | ✅ no overflow at 1280/390; every single-language page 1 page with headroom (tallest 922/945 px); all-languages PDFs 4 pages; large print 2 pages with page numbers |
| 7 | Accessibility | ✅ axe-core 0 violations on every page at both widths; keyboard test OK |

**Fixes in round 4:** none needed.

---

## Azure Translator — first live run (2026-09-30, ~01:10)
- Key in `.env` (Free F0, canadacentral). First call 0.9 s.
- Round 1: all 13 languages passed numbers + script on the Thorncliffe bulletin, but Arabic-script output glued numbers to words ("911کال", "3فیصد") and dropped "%". Round 2 (after fix): **Traditional Chinese blocked** — Azure put two protected numbers side by side ("923%") → number check flagged 923 and blocked publishing, as designed; also a 429 rate limit from 39 back-to-back calls.
- Fixes: space between adjacent protected numbers; `%` protected with its number; spaces restored for Arabic-script only; 429 retry with `Retry-After`. Tests added for each (real outputs as fixtures).
- Round 3 (paced): **39/39 pass** (3 bulletins × 13 languages), no merged or glued numbers. App end to end with Azure: AI draft 46 s, **3 translations in 1.3 s total**, all verified, published; Status and tabs show "Azure Translator". pytest 148 ✅.

---

## Number-binding checks (2026-09-30, ~01:30)
- New checks: category-line binding (English), line-by-line alignment (translations), percent binding, merged-number explanation. Blocking.
- **Corpus run** — 16 real bulletins (12 from test rounds 1–4 + current), 46 translations: flagged the known Russian "21 октября 21" (closing), **Traditional Chinese "11 厘" / "16 厘" / "9 厘" in two Milliken runs — one of them was published in round 3 and passed the old checks**; plus the pre-fix Azure Pashto output. One false alarm (Pashto inflected سلنې) fixed by accepting inflected forms. Current demo bulletins: all pass.
- Real gemma3 zh-Hant with the new "% sign" guidance: 2/2 runs "減少 11%", no issues, first attempt.
- Test fixture change (disclosed): the fake translator in app tests kept only number placeholders and dropped "%"; it now keeps "%" with its number like a real translation. No assertion changed.
- Tests: `tests/test_binding.py` (8, real fixtures). pytest 156 ✅, visual ✅.

---

## Round 5: regression after overnight loop 2 (2026-10-23, ~18:25)

Covers everything added on Oct 23: Check my numbers, maps, neighbourhood profiles, data health, public site, impact, follow-up, 8 new languages, two-person check, back-translation meaning check, refresh guard, provenance, security, Listen, Docker.

| # | Check | Result |
|---|---|---|
| 1 | `pytest` (network and the local model blocked inside tests) | ✅ 191 passed, 2.2 s |
| 2 | Live data (3 demo + random North Riverdale, Brookhaven-Amesbury) | ✅ all 5 OK (rows 261/341/188/136/237; 0 duplicates; independent recount matches) |
| 3 | End-to-end, real gemma3:12b + Azure Translator | ✅ **8/8 languages verified, 3/3 published**; drafts 36–43 s; translations 0.3–0.5 s each; **meaning check (back-translation) ran on all 8** |
| 4 | Offline, real model, non-local sockets blocked | ✅ draft 35 s, Urdu (local model) 75 s verified, published; 0 non-local connection attempts |
| 5 | New languages live (vi, pl, bn, el, gu, uk, pa, so) | ✅ all "All numbers match TPS data" and meaning-checked; 1 printed page each (917–942 px of 945) |
| 6 | Provenance | ✅ every count (month, year to date, last year) for 3 neighbourhoods reproduced exactly by the TPS portal's own queries |
| 7 | Visual | ✅ no overflow at 1280/390 on every page (incl. profiles, check, data health, public pages); single-language bulletins 1 page |
| 8 | Accessibility | ✅ axe-core 0 violations at both widths, now also on an unpublished draft's Review/Publish pages; keyboard test OK |
| 9 | Public site | ✅ build from published bulletins; crawl through a plain file server: 32 pages × 2 widths, no broken links, no console errors |
| 10 | Docker | ✅ image builds; pages 200; offline profile; foreign Host 403; health check healthy |

**Found and fixed during the day:** TPS empties its current-year layer while reloading (would have produced bulletins of zeros: now refused, last good copy kept, Status says so); Vietnamese month names ("tháng 9") read as counts; "$5,000" flagged in Check my numbers; a sentence not ending after a number; Leaflet tiles broken by the CSS reset and a map initialised while hidden; disabled share links failing contrast; cross-site form posts and DNS rebinding; older records losing translations on an unchanged save; a test reaching the real model.

---

## Round 6: final regression of the session (2026-10-23, ~19:50)

After L2-19 … L2-35 (privacy/AI page, Listen, NCO filter, Docker, translator review queue, month-year repair, help, collisions in Check my numbers, all-158 map, feeds per neighbourhood, share image, month-by-month profiles, division summaries, monthly routine, publisher name, How it works, phone navigation, review fixes).

| # | Check | Result |
|---|---|---|
| 1 | `scripts/check_all.sh` (pytest with network and model blocked, visual, axe, keyboard) | ✅ 206 tests; no visual problems; 0 violations |
| 2 | Live data (3 demo + random Bay-Cloverhill, O'Connor-Parkview) | ✅ all 5 OK |
| 3 | End to end, real gemma3:12b + Azure | ✅ 8/8 languages verified **and meaning-checked**, 3/3 published; drafts 29–37 s; translations 0.2–0.5 s; no Chinese month-order repair needed |
| 4 | Offline, real model, sockets blocked | ✅ draft + Urdu (local model, 75 s) verified, published; 0 non-local connections |
| 5 | Public site, all 158 profiles | ✅ 376 files; crawl 188 pages × 2 widths, 351 links, no problems |
| 6 | Dress rehearsal on the running app (port 8010) | ✅ every DEMO.md step; TPS portal reproduced the profile's count |
| 7 | Backup video | ✅ re-recorded (123 s), checked frame by frame |

---

## Round 7: QA pass (2026-10-24)

| # | Check | Result |
|---|---|---|
| 1 | `scripts/check_all.sh` | ✅ 237 tests; no visual problems; axe 0 violations; JS/Python checker parity |
| 2 | `scripts/qa_crawl.py` (staff + public mode) | ✅ ~1,200 pages, no HTTP errors, no bad text, no slow pages |
| 3 | XSS / prompt injection on every form | ✅ escaped everywhere; injected instructions ignored |
| 4 | Long names, live Azure (zh-Hans, ko, pa, ur, ar) | ✅ all verified, 1 printed page each (after the Korean adjacent-number fix) |

Issues found were fixed before this round.

---

## Round 8: after the PM review (2026-10-24, afternoon)

| # | Check | Result |
|---|---|---|
| 1 | `scripts/check_all.sh` (styles up to date, pytest, visual, axe + keyboard, JS parity) | ✅ 256 tests; no visual problems; 0 violations |
| 2 | Automatic bulletins, live Azure (Latin, Arabic, Gujarati, Gurmukhi, French) | ✅ all languages verified and meaning-checked; 1 printed page each |
| 3 | French, live | ✅ verified; found and fixed "Theft from vehicles" → "vehicle theft" (category labels now sent pre-translated) |
| 4 | Last-year counts for theft from vehicles and bike thefts | ✅ the TPS portal's own queries return the same counts (15, 31; assaults 65) |
| 5 | Refreshed demo bulletins (September data, follow-ups, meetings) | ✅ 3/3 published, 8/8 languages verified; print fit now measured at paper width: 1 page each (scaled 96–99% where needed) |
| 6 | Pre-built CSS vs in-browser compiler | ✅ 0 differences in 26 computed properties on every element of 25 pages at 1280 and 390 px |
| 7 | Resident feedback, end to end in a browser | ✅ saved with no personal data; thank-you shown; test answer removed |
| 8 | Offline (`DEMO_OFFLINE=true`, non-local sockets blocked) | ✅ real model draft + Urdu verified and published; profiles, automatic bulletin (Steeles), Easy Read and ward pages load; 0 non-local connections |
