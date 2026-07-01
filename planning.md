# Provenance Guard — planning.md

> Design spec written before implementation. Author: Jaed Pizarro · CodePath AI201 · Project 4
> A backend any creative-sharing platform can plug in to classify submitted text as
> human- vs AI-written, score confidence honestly, surface a transparency label, and
> handle appeals — with rate limiting and a structured audit log.

---

## 1. Architecture narrative — the path one submission takes

A creator on a writing platform posts a poem. The platform forwards it to
`POST /submit` with the `text` and the `creator_id`. Inside Provenance Guard:

1. **Router (`app.py`)** receives the JSON, validates it, and mints a unique
   `content_id`. The `/submit` route is wrapped by the **rate limiter** first, so a
   flood of requests is rejected with `429` before any expensive work runs.
2. The text goes to the **detection pipeline (`signals.py`)**, which runs **two or
   more distinct signals**: a **Groq LLM** semantic judgement and **stylometric** +
   **lexical** heuristics. Each returns its own `p_ai` (probability the text is AI).
3. **Confidence scoring (`scoring.py`)** combines the signal scores with a documented
   weighting into a single calibrated `p_ai`, then maps it to a verdict —
   `likely_ai`, `uncertain`, or `likely_human` — using **asymmetric thresholds** that
   make it harder to accuse a human than to clear one.
4. **Label generation (`labels.py`)** turns that verdict + score into the exact
   plain-language transparency label the reader will see. If the creator holds a
   Verified-Human credential, the label is prefixed with a badge.
5. **Storage (`storage.py`)** saves the submission record and writes a structured
   entry to the **audit log** (timestamp, content_id, verdict, confidence, every
   signal's score). The response — `content_id`, `attribution`, `confidence`, `p_ai`,
   `label`, and the per-signal breakdown — goes back to the platform.

If that creator disputes the result, the platform calls `POST /appeal` with the
`content_id` and the creator's reasoning. The system flips the content's status to
`under_review`, logs the appeal next to the original decision, and returns a
confirmation. A human reviewer reads the queue via `GET /log`.

## 2. Architecture

```
                      ┌──────────────────────── SUBMISSION FLOW ────────────────────────┐

  creator/platform          raw text + creator_id
        │                          │
        ▼                          ▼
   POST /submit ──▶ [ rate limiter ] ──(under limit)──▶ mint content_id
        │                 │                                   │
        │              (over limit)                           ▼
        │                 │                        ┌───── detection pipeline ─────┐
        │                 ▼                        │  signal 1: Groq LLM   (p_ai) │
        │              429 Too Many                │  signal 2: stylometric(p_ai) │
        │                                          │  signal 3: lexical    (p_ai) │
        │                                          └──────────────┬───────────────┘
        │                                        combined score   │  weighted avg
        │                                                         ▼
        │                                         confidence scoring → p_ai + verdict
        │                                                         │ (label text)
        │                                                         ▼
        │                                            transparency label  ◀── verified? badge
        │                                                         │
        │                          submission record + log entry  ▼
        │                                              audit log (JSON)
        ▼                                                         │
   JSON response  ◀──── content_id, attribution, confidence, p_ai, label ◀┘


                      ┌────────────────────────── APPEAL FLOW ──────────────────────────┐

   POST /appeal ──▶ content_id + creator_reasoning ──▶ status = "under_review"
        │                                                       │  (append)
        │                                                       ▼
        │                                            audit log  (appeal beside decision)
        ▼                                                       │
   JSON confirmation ◀───────────────────────────────────────── ┘  ──▶ human reviewer via GET /log
```

**Narrative:** The submission flow rate-limits, then fans a piece of text out to
independent signals, merges them into one calibrated confidence score, and derives
both a verdict and the reader-facing label — logging everything. The appeal flow
takes a `content_id`, moves that item to `under_review`, records the creator's
reasoning alongside the original decision in the same audit log, and surfaces it to a
human reviewer. No automated re-classification.

## 3. Detection signals

The pipeline uses **three genuinely distinct signals** (2 are required; the third is
the *ensemble* stretch feature). "Distinct" here means each captures a different
*property* of the text, so together they are more informative than any one alone.

| # | Signal | Captures | Output | Blind spot |
|---|--------|----------|--------|------------|
| 1 | **Groq LLM** (`llama-3.3-70b-versatile`) | Semantic + holistic coherence — does it *read* like an assistant? | `p_ai` 0–1 parsed from a JSON reply | LLM detectors are themselves imperfect; lightly-edited AI can fool them |
| 2 | **Stylometric** (pure Python) | *Structure*: sentence-length burstiness, vocabulary evenness, punctuation/casing regularity. AI is uniform; humans are bursty and messy | `p_ai` 0–1 (weighted blend of components) | A careful human essayist writing uniformly looks AI-like |
| 3 | **Lexical** (pure Python) | *Surface markers*: density of LLM "tell" connectives (`furthermore`, `it is important to note`, `stakeholders`…) and contraction rate | `p_ai` 0–1 | Formal human academics use those connectives too — so it is capped and down-weighted |

**Why this set:** signal 1 is semantic, signal 2 is structural, signal 3 is
surface-lexical — three different axes. When the LLM disagrees with the heuristics,
that disagreement is itself a useful "this is hard, review it" flag.

**Combining them (ensemble weighting):** a weighted average of whichever signals are
available — LLM 0.50, stylometric 0.30, lexical 0.20. If the LLM signal is
unavailable (no key / network error), its weight is redistributed to the heuristics
and the result is flagged `degraded: true` so labels and logs stay honest. The image
path (multimodal stretch) swaps in a metadata signal instead.

## 4. Uncertainty representation

- The single reported number, **`p_ai`**, is the system's probability the content is
  **AI-generated** (0 = confidently human, 1 = confidently AI). This is the "0.51 vs
  1.0" number: **0.51 sits in the Uncertain band; 1.0 is a confident AI verdict.**
- **What 0.6 means:** "leaning AI, but not confidently." Deliberately, 0.6 does **not**
  produce an AI label — it is Uncertain. We would rather say "we're not sure" than
  brand a borderline piece.
- **Mapping raw signals → calibrated score:** weighted average (§3), clamped to 0–1.
- **Thresholds (asymmetric — the core false-positive safeguard):**

  | `p_ai` range | Verdict | Rationale |
  |---|---|---|
  | `p_ai ≥ 0.72` | **Likely AI** | Needs *strong* evidence to accuse |
  | `0.40 < p_ai < 0.72` | **Uncertain** | Wide benefit-of-the-doubt band |
  | `p_ai ≤ 0.40` | **Likely human** | Easier bar to clear a creator |

  On a creative platform, **wrongly branding a human's work as AI is worse than
  missing some AI**, so the AI bar (0.72) is higher than the human bar (0.40), and the
  midpoint 0.5 falls inside "Uncertain."

## 5. Transparency label design (the three variants, written now)

Plain language, no jargon; every label states it is an automated estimate the creator
can appeal. `{conf}` is a 0–100 confidence in the stated verdict.

- **High-confidence AI:**
  > ⚠️ Likely AI-generated. Our automated analysis found strong signals of machine
  > authorship in this text (confidence {conf}%). This is an estimate, not a certainty
  > — no AI detector is perfect. The creator can appeal this label.

- **High-confidence human:**
  > ✅ Likely human-written. Our automated analysis found little evidence of AI
  > generation (confidence {conf}%). Attribution estimates can be wrong; this label
  > reflects an automated check, not a guarantee.

- **Uncertain:**
  > ❓ Attribution uncertain. Our analysis could not confidently tell whether this was
  > written by a person or by AI (estimated {p_ai}% likelihood of AI). We're showing
  > this openly instead of guessing — treat authorship as unknown.

- **Verified-Human badge** (provenance-certificate stretch) is prefixed when the
  creator is credentialed:
  > 🔵 Verified Human Creator — this creator completed Provenance Guard identity attestation.

## 6. Appeals workflow

- **Who:** the creator of a classified piece (via the platform), using the
  `content_id` from their `/submit` response.
- **What they provide:** `content_id` + `creator_reasoning` (free text).
- **What the system does:** looks up the submission; sets its status to
  `under_review`; appends an `appeal` entry to the audit log next to the original
  decision (original verdict + confidence + the reasoning); returns a confirmation.
  **No automated re-classification** — a human decides.
- **What a reviewer sees** via `GET /log`: the appeal entry with the original verdict,
  original confidence, the creator's reasoning, and the shared `content_id` linking it
  back to the full submission record.

## 7. Anticipated edge cases (specific, not generic)

1. **Formal human writing scored as AI.** A monetary-policy essay with even sentence
   lengths, clean punctuation, and academic connectives (`fundamental tension`,
   `unintended consequences`) trips the stylometric *and* lexical signals. Our
   safeguard: the asymmetric threshold keeps it in **Uncertain** (measured `p_ai ≈
   0.61`) rather than Likely-AI, and the LLM signal usually pulls it toward human.
2. **Lightly-edited AI text.** AI output with a few human touches (a lowercase start,
   a dash, a contraction) raises its burstiness and hides its tells, landing around
   `p_ai ≈ 0.41` (Uncertain) on heuristics alone — a near-miss we surface honestly
   rather than a false "human" claim. This is where the LLM signal earns its weight.
3. **Very short posts** (< 8 words): stylometry/lexical are statistically meaningless,
   so those signals abstain to 0.5 rather than inventing a verdict.

## 8. AI Tool Plan

I use **Claude** to generate implementation code against this spec + the §2 diagram.

- **M3 (submission endpoint + first signal).** Provide: §3 detection-signals table +
  §2 diagram. Ask for: the Flask app skeleton with the `POST /submit` stub, and the
  first signal function (Groq LLM). Verify: call the signal function directly on a few
  inputs and confirm its return shape matches §3 (a `p_ai` float, not a bare label)
  before wiring it into the route.
- **M4 (second signal + confidence scoring).** Provide: §3 + §4 (uncertainty) + §2.
  Ask for: the stylometric signal function and the `combine()` scoring logic. Verify:
  that the generated thresholds exactly match §4 (0.72 / 0.40), and that clearly-AI vs
  clearly-human inputs produce noticeably different scores — print both signal scores
  separately to catch a misbehaving one.
- **M5 (production layer).** Provide: §5 label variants + §6 appeals + §2. Ask for:
  the label-generation function and the `POST /appeal` endpoint. Verify: all three
  label variants are reachable by feeding inputs at different confidence levels, and
  that an appeal actually flips status to `under_review` and logs correctly.

> Stretch features (ensemble 3rd signal, provenance certificate, analytics dashboard,
> multimodal image support) were each noted here before building and are documented in
> the README.
