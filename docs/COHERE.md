# Ask about a neighbourhood: grounded answers with Cohere

A retrieval-augmented question-answering feature on top of NeighbourBrief's checked open data,
built so it can run on Cohere (Embed, Rerank and Command with documents) and fall back to a local
model or a sources-only answer when there's no key or no internet.

## The problem

Residents, community groups and officers ask questions like "Are car thefts going up here?" or "Who
are our officers?", often in Urdu, Tamil or Chinese. The answers exist in open data, but a chatbot
that makes up a number about crime in someone's neighbourhood does real harm. So the requirements
are: answers only from the checked data, every claim traceable to a source, no invented numbers,
no rankings of neighbourhoods, any language, and it must still work offline.

## How it works

1. **Documents from verified facts.** `app/rag/corpus.py` writes short, plain-English documents
   from the same facts as the bulletins: reported crime this month, year to date and last year;
   traffic collisions; yearly counts since 2014; month by month; 2021 Census languages; officers and
   local services; the counting rules; dataset notes; published bulletins. Each has an id, a title
   and the page it comes from. Neighbourhoods named in the question (or picked) decide which
   neighbourhood documents are included.
2. **Retrieval.** `app/rag/retrieve.py`: with Cohere, the question and documents are embedded
   (`embed-multilingual-v3.0`, document vectors cached on disk by text hash), the closest are
   shortlisted, then **reranked** (`rerank-v5.0-fast`). Without Cohere: BM25 keyword ranking with a
   small preference for current summary documents over older bulletins.
3. **Grounded answer.** `app/rag/answer.py`: with Cohere, **Command** (`command-a-03-2025`) gets the
   top documents through the v2 chat `documents` field and returns an answer with **native
   citations** (character spans and document ids). Without Cohere, the local model (gemma3:12b via
   Ollama) gets numbered documents and cites with [n]. If no model is available, the answer quotes
   the top sources.
4. **Guardrails.**
   - Ranking questions ("most dangerous neighbourhood", "rank by crime") are declined before any
     model call, with the reason.
   - **Number guard:** every number in the answer must appear in the retrieved documents (or the
     question). Otherwise the answer is held back and only the sources are shown.
   - The system prompt limits answers to the documents, asks for the period of every number, the
     question's language, no descriptions of people or groups, and "a report is not a charge".
5. **The page:** `/ask` shows the answer with citation markers linked to the sources, which engine
   answered, how documents were ranked, the time taken and the number-guard result. "Answer with"
   lets you compare engines on the same question.

## Turning Cohere on

Add the key to `.env` (never paste it into chat or code), then restart the app:

```
COHERE_API_KEY=<your key>
# optional overrides
COHERE_CHAT_MODEL=command-a-03-2025
COHERE_EMBED_MODEL=embed-multilingual-v3.0
COHERE_RERANK_MODEL=rerank-v5.0-fast
```

Check it in about 5 seconds (one small call each to embed, rerank and chat; plain advice if a key or
model name is wrong):

```bash
.venv/bin/python scripts/cohere_smoke.py
```

Then run the evaluation with all three engines to fill in the results below:

```bash
.venv/bin/python scripts/rag_eval.py --engines cohere,local,extractive --hoods 055,130,035
```

The Status page shows "Cohere (Ask): Key set". Offline mode never calls Cohere. `LLM_PROVIDER=cohere`
also makes Command the bulletin writer and translator (every bulletin check still applies).

## Evaluation

`scripts/rag_eval.py` generates questions from the live facts (so the right answer is known) for
several neighbourhoods: counts last month, year to date, direction of change, pedestrian
collisions, top home language, officers, the counting rules, two ranking questions that must be
declined, an out-of-scope question, and an Urdu question. Per engine it reports how many answers
are correct, how often the right source is cited, how often the number guard passes, how many
answers were held back, and the median time.

```bash
.venv/bin/python scripts/rag_eval.py --engines cohere,local,extractive --hoods 055,130,035
```

### Results

<!-- rag-eval:start -->
_Run 2026-10-24 01:28 on Thorncliffe Park, Milliken, Westminster-Branson (questions generated from the live facts)._

| Engine | Questions | Correct | Cited the right source | Number guard passed | Held back | Median s |
|---|---|---|---|---|---|---|
| local | 24 | 96% | 100% | 100% | 0 | 15.87 |
| extractive | 24 | 100% | 100% | 100% | 0 | 0.18 |

| Engine | count | ytd | direction | collisions | language | officers | urdu | rules | decline | out_of_scope |
|---|---|---|---|---|---|---|---|---|---|---|
| local | 3/3 | 3/3 | 2/3 | 3/3 | 3/3 | 1/1 | 3/3 | 2/2 | 2/2 | 1/1 |
| extractive | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1/1 | 3/3 | 2/2 | 2/2 | 1/1 |
<!-- rag-eval:end -->

## Language coverage (documented and measured)

Toronto's neighbourhoods need 21 languages besides English. What Cohere documents
([supported languages](https://docs.cohere.com/docs/supported-languages),
[Command A](https://docs.cohere.com/docs/command-a), checked Oct 24, 2026):

- **Retrieval (embed-multilingual-v3.0):** lists every NeighbourBrief language **except Pashto**.
  Rerank v3 is documented with the same coverage; I found no language list for the rerank v5 models.
- **Answers (Command A):** "trained to perform well in 23 languages". Of ours, that covers Spanish,
  Italian, Portuguese, Korean, Chinese, Arabic, Russian, Polish, Vietnamese, Ukrainian, Greek and
  Persian. **Not listed: Urdu, Pashto, Tamil, Punjabi, Bengali, Gujarati, Tagalog and Somali**,
  which are among the most common languages in the neighbourhoods this tool serves (Urdu is 20.8% of
  Thorncliffe Park; Tamil, Punjabi and Gujarati lead in several others).

So the evaluation asks the same question in every language (translated by Azure Translator; as residents
would, most translations write the neighbourhood's name in their own script, so the neighbourhood is
passed explicitly, like choosing it on the Ask page) and reports, per language, whether the
answer had the right number and came back in the question's language, next to what Cohere
documents. Every miss is listed word for word.

<!-- rag-lang:start -->
_Run 2026-10-24 01:28: "How many assaults were reported in Thorncliffe Park last month?" in every language (translated by Azure Translator)._

| Language | Cohere Embed documents it | Command A documents it | local | extractive |
|---|---|---|---|---|
| Urdu | yes | **no** | ✓ (via translation) | ✓ (via translation) |
| Pashto | **no** | **no** | ✓ (via translation) | ✓ (via translation) |
| Arabic | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Persian | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Chinese (Traditional) | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Chinese (Simplified) | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Tamil | yes | **no** | ✓ (via translation) | ✓ (via translation) |
| Russian | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Filipino (Tagalog) | yes | **no** | ✓ (via translation) | ✓ (via translation) |
| Italian | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Portuguese (Portugal) | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Spanish | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Korean | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Vietnamese | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Polish | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Bengali | yes | **no** | ✓ (via translation) | ✓ (via translation) |
| Greek | yes | yes | ✗ | ✓ (via translation) |
| Gujarati | yes | **no** | ✓ (via translation) | ✓ (via translation) |
| Ukrainian | yes | yes | ✓ (via translation) | ✓ (via translation) |
| Punjabi | yes | **no** | ✓ (via translation) | ✓ (via translation) |
| Somali | yes | **no** | ✓ (via translation) | ✓ (via translation) |

Correct: local: 20/21 · extractive: 21/21.

**Details of every miss**

- **local, Greek** (expected 6). Q: Πόσες επιθέσεις αναφέρθηκαν στο Thorncliffe Park τον περασμένο μήνα; · A: Six assaults were reported in Thorncliffe Park in September [1]. This was part of a total of 23 reports to police that month [1]. There were 95 assaults reported so far in 2026 [1]. A report to police does not mean anyon
<!-- rag-lang:end -->

What this means in the product:

- **A question in another language is also translated into English** (Azure) for searching and for the
  ranking rule. Without that, keyword search can't match an Urdu or Chinese question to English
  documents, and a ranking question asked in Urdu would slip past the English-only ranking rule (both
  seen in testing).
- **If the answer comes back in English, it's translated into the question's language** (Azure, numbers
  protected) and shown under the original, with its citations. It's shown only if every number matches
  the original exactly. The evaluation marks these "(via translation)", separate from the model
  answering in the language itself, so the table shows what each model can do on its own.
- **Measuring each model fairly:** the local model also gets the English version of the question, which
  makes it answer in English (so its column is "via translation" throughout). Cohere gets only the
  original question, so its column shows what Command A does in each language on its own: "✓" means it
  answered correctly in that language.
- **Every answer, in any language, goes through the number guard**, and models must write numbers
  with digits so the guard can see them (an answer that wrote "six" in Greek would otherwise have slipped
  past it).
- Bulletin translations don't depend on Cohere (Azure Translator, with NeighbourBrief's number,
  alphabet and meaning checks).

## What I'd do next with a real customer

- Questions from real users (with consent) to grow the evaluation set beyond generated questions.
- Rerank relevance threshold: below it, say "I don't have that" instead of answering.
- For languages Command A doesn't document (Urdu, Tamil, Punjabi, ...): answer in English plus a
  translated answer through the bulletin translation path (Azure, already checked), or test Aya
  models, measured with the same per-language evaluation.
- `command-a-translate` or Aya for the bulletin translations in the languages they cover, compared
  with Azure on the same number/alphabet/meaning checks already in NeighbourBrief.
- Private deployment of the models next to the data (the app already runs on one machine with no
  outside calls in offline mode).

## Files

`app/ai/cohere.py` (client) · `app/rag/corpus.py` (documents) · `app/rag/retrieve.py` (embed,
rerank, BM25) · `app/rag/answer.py` (engines, guardrails, citations) · `app/templates/ask.html` ·
`tests/test_cohere.py` and the Ask tests in `tests/test_app.py` (fake Cohere API) ·
`scripts/rag_eval.py`.
