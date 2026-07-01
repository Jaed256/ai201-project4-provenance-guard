# Provenance Guard — Portfolio Walkthrough (2–3 min)

Keep it short and unpolished — show it working, talk through a couple of decisions.
Have two terminals open: **left** running `python app.py`, **right** for curl. (Record with `Win+G`.)

---

**0:00 — What it is (~20s)**
> "This is Provenance Guard — a backend a writing platform plugs in to check whether
> submitted text is human-written or AI-generated. The point isn't to police people;
> it's to be honest about uncertainty and give creators a way to appeal."

**0:20 — Submit AI-looking text (~30s)** — run in the right terminal:
```bash
curl -s -X POST http://localhost:5000/submit -H "Content-Type: application/json" \
 -d '{"text":"Artificial intelligence represents a transformative paradigm shift. It is important to note that stakeholders across sectors must collaborate to ensure responsible deployment.","creator_id":"alice"}'
```
> "Three independent signals run — a Groq LLM, plus stylometric and lexical heuristics.
> They combine into one score. This one comes back **Likely AI, ~0.82**, and notice the
> **label** is plain-language and says it's an estimate the creator can appeal."

**0:50 — Submit human-looking text (~20s)**
```bash
curl -s -X POST http://localhost:5000/submit -H "Content-Type: application/json" \
 -d '{"text":"ok so i finally tried that ramen place downtown and honestly? underwhelming. broth was fine but WAY too salty. probably wont go back.","creator_id":"bob"}'
```
> "Casual, bursty, messy — **Likely human, ~0.15**. The score genuinely moves."

**1:10 — The design decision (~30s)**
> "The key decision is **asymmetric thresholds**. On a creative platform, wrongly
> branding a *human* as AI is worse than missing some AI — so it takes 0.72 to say
> 'Likely AI' but only 0.40 to clear someone. Everything between is shown honestly as
> **Uncertain** instead of guessing. A formal human essay lands in Uncertain, not
> 'AI' — that's the false-positive safeguard working."

**1:40 — Appeal (~25s)** — paste a `content_id` from earlier:
```bash
curl -s -X POST http://localhost:5000/appeal -H "Content-Type: application/json" \
 -d '{"content_id":"PASTE_ID","creator_reasoning":"I wrote this myself."}'
curl -s http://localhost:5000/log
```
> "A creator can contest a call. The item flips to **under_review** and the appeal is
> logged right next to the original decision — here's the structured audit log."

**2:05 — Quick tour of the extras (~25s)**
> "Rate limiting stops floods with 429s; `/verify` lets a creator earn a **Verified
> Human** badge; `/dashboard` shows detection patterns and appeal rate; and `/submit`
> also handles **image metadata** for multimodal content. The detailed evidence — audit
> log, rate-limit output, all three label variants — is in the README."

**2:30 — Wrap (~10s)**
> "So: multi-signal detection, honest confidence, a real appeals path, and production
> safety. Thanks!"

---

### Checklist
- [ ] Show the system working end-to-end (submit → label → appeal → log)
- [ ] Talk through 1–2 design decisions (asymmetric thresholds; honest uncertainty)
- [ ] Keep it a couple of minutes, unpolished
