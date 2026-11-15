# Presenting Capy Care (3 minutes)

**Open:** the live app (Claude app → this session → the "Capy Care" preview), or play `demo/capy-care-demo.mp4`.
**Reset between runs:** the **↺ New case** button, top right.

## Talk track

**0:00 · The problem (15 s).** "When Mom comes home from the hospital, her discharge papers say lisinopril 5 mg. Three days later the new bottle says 10 mg. Which is right? In one study, two out of three patients left the hospital with a medication-record discrepancy. Capy Care is a crew of four AI agents that does one job: medication reconciliation for the family."

**0:15 · Click 📎 "Share Mom's papers + new bottle".** While it runs:
- "Capy Reader reads both with **Superlinked SIE**: docling for the PDF, LightOnOCR for the photo, then GLiNER2 pulls out each drug, dose and status. That's small models and no LLM, each in about a second." *(Point at the purple SIE badges.)*
- "Elder Capy stores each source in **Cognee** with the date it was true. Cognee itself runs on SIE too: its LLM is Qwen3.8 and its embeddings are Qwen3-Embedding, both served by Superlinked."
- When the red card appears: "**Cognee's conflict review** caught it: two dated sources disagree on lisinopril's dose. That's a `FactConflict` in the graph." *(Point at the red diamond in the graph.)*
- "Capy Pharmacist reads the real FDA label live with **Keenable**. Both doses are normal, so it can't decide, and Capy Guardian asks a human in the **Band** room."

**1:15 · Tap the 💬 suggested reply → Send.** "Lily called the pharmacy. GLiNER2 reads her answer, and Cognee's `resolve_conflict` records the family's decision, so it holds on every future review." *(The card turns green.)*

**1:40 · Click 🎙️ "Forward Sam's voice note".**
- "Whisper on SIE transcribes it in a second: dizzy since Wednesday."
- "Elder Capy asks Cognee a **time** question: what changed right before Wednesday? The dose went up the day before."
- "The label lists dizziness as a common side effect, and Qwen on SIE writes a one-page brief with three questions for the doctor."

**2:30 · Close.** "SIE senses, Cognee remembers. It flags and never advises; a person makes every call."

## Likely questions

- **Why SIE instead of one big LLM?** Reading and extraction run on small specialist models (OCR, ASR, GLiNER2), which are fast and cheap per call. The LLM is used only where judgment helps: Cognee's graph building, conflict review, and the brief. Every SIE call and its latency shows in "Under the hood".
- **How does Cognee know the facts conflict?** Each source is remembered with an `effective_date`. `improve(review_conflicts=True)` compares facts about the same entity and attribute across dated sources and stores a `FactConflict` citing both chunks. The family's choice is saved with `resolve_conflict()` (`resolution_source = user`).
- **Cognee Cloud?** We ran open-source Cognee so its LLM and embeddings could run on SIE. The same calls map to Cognee Cloud's REST API.
- **What if a model gets it wrong?** It never changes anything on its own: medication changes need a person to confirm, label claims are quoted with their source, and a labelled reconciliation check backs up the conflict review.
- **Is the data real?** No. It's a fictional patient with synthetic documents and voices.
