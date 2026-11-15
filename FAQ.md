# Capy Care FAQ

Answers to questions judges are likely to ask. Every number below was measured or verified while building Capy Care; anything we have not validated is marked as such.

## The product

**What problem are you solving?**
After a hospital stay, families have to reconcile discharge papers, new pill bottles and what relatives notice at home, and mistakes are common: one study found 65% of patients left the hospital with a discrepancy in their medication records (PubMed 18414970, 2008). There are 63 million family caregivers in the US (AARP & NAC, *Caregiving in the U.S. 2025*). Capy Care does the reconciliation for them.

**What is the "one real job"?**
Medication reconciliation for a family: keep one correct, dated medication list across every source, catch contradictions, get a person to confirm any change, and prepare the doctor visit. Nurses and pharmacists do this job professionally; families do it at the kitchen table.

**Why a team of agents instead of one chatbot?**
Each step needs different tools: reading (small SIE models), remembering over time (Cognee), checking the official label (Keenable) and talking with the family (Band). Splitting the work makes every step visible, because each capybara posts its reasoning and tool calls in the Band room, and it keeps the LLM out of steps that small models do better.

**How is this different from a pill-reminder app or asking ChatGPT?**
Reminder apps assume the medication list is already right; Capy Care builds and checks the list from the family's real documents. A chatbot works from one conversation; Capy Care keeps a dated memory across every source, notices when two sources disagree, and quotes the official label for every drug claim.

**What happens if an agent gets something wrong?**
Nothing changes on its own. A medication change needs a person to confirm it, and the confirmation is stored with who confirmed it and why. Drug facts are quoted from the label with their source, and the brief only contains facts already in the record. If Cognee's conflict review ever missed a dose mismatch, a deterministic reconciliation check would still flag it, labelled as the crew's check rather than Cognee's.

**Is it medical advice? Is it HIPAA-compliant?**
No to both, today. It is a hackathon prototype on synthetic data that flags problems and prepares questions; it never recommends changing a medication. A production version would keep data inside a compliant boundary: SIE can be self-hosted and Cognee is open source, so both could run on the family's or provider's own infrastructure.

**Who would use or pay for it?**
Families caring for a parent first. The same job happens at every transition of care, so home-care agencies, care-management programs and hospital discharge teams are natural channels. We have not validated pricing.

**Why capybaras?**
They are famously calm and get along with everyone, which is the tone a stressed family needs. Each capybara is one agent: Capy Reader (SIE), Elder Capy (Cognee), Capy Pharmacist (Keenable) and Capy Guardian (Band).

## Superlinked SIE

**Which SIE models do you use?**
Seven, all on SIE serverless:

| Primitive | Model | Job | Warm latency |
|---|---|---|---|
| `extract` | `docling` | Discharge PDF → markdown | 1.3 s |
| `extract` | `lightonai/LightOnOCR-2-1B` | Pill-bottle photo → text | 1.1–1.4 s |
| `extract` | `openai/whisper-large-v3-turbo` | Voice note → transcript | 1.0 s |
| `extract` | `fastino/gliner2-large-v1` | Entities and schema-mode medication facts, no LLM | 0.3–0.6 s per call |
| `score` | `Qwen/Qwen3-Reranker-4B` | Ranks drug-label passages | ~0.4 s |
| `encode` | `Qwen/Qwen3-Embedding-4B` | Cognee's vectors (2560 dimensions) | — |
| `generate` | `Qwen/Qwen3.8-27B-FP8` | Cognee's LLM, the visit brief, vision fallback for photos | 1–10 s |

**Why small models instead of one big LLM for everything?**
Reading and extraction are narrow tasks where specialist models are fast and predictable: OCR, speech-to-text and GLiNER2 each answer in about a second and cannot invent prose. We use the 27B LLM only where judgment helps: building Cognee's graph, reviewing conflicts and writing the brief.

**How do you get structured facts without an LLM?**
GLiNER2's schema mode. We send a JSON schema (medication, dose, frequency, status, prescriber) together with the one table row or label that mentions a drug, and get the fields back in about 0.3 s. Schema mode only accepts string fields, so we extract one medication per call instead of asking for an array.

**Does Cognee really run on SIE?**
Yes. Cognee's LLM points at SIE's OpenAI-compatible chat endpoint with Qwen3.8-27B (Cognee's `custom` provider with the `instructor` structured-output backend), and its embeddings point at SIE's `/v1/embeddings` with Qwen3-Embedding-4B. Every LLM and embedding call Cognee makes goes to Superlinked.

**Serverless or self-hosted? Did cold starts bite?**
Serverless, so there was nothing to host. The first OCR call after a long idle took about 200 s, so the app warms every model at startup, pings them every 8 minutes, retries 429/5xx responses with backoff, and falls back to Qwen3.8 vision (3.4 s) if OCR is slow.

**What did you learn about SIE's APIs?**
Strict `json_schema` output from Qwen worked when we called SIE directly, but inside Cognee the default structured-output backend sometimes produced invalid JSON; switching Cognee to `instructor` (tool calling) made extraction reliable. GLiNER2 relation extraction did not return relations with the parameters we tried, so relations come from Cognee's LLM extraction.

**What would you do next with SIE?**
Point Cognee's LLM-free GLiNER pipeline (its remote GLiNER worker over HTTP) at SIE's GLiNER2 so the graph can be built without an LLM, translate briefs for multilingual families with SIE's `madlad400` translation model, and fine-tune GLiNER2 on pharmacy labels.

## Cognee

**Why Cognee and not a vector database?**
The job is about facts that change over time and sometimes contradict each other. Plain retrieval would return both the "5 mg" and the "10 mg" chunks and leave the LLM to guess. Cognee stores them as dated graph facts, flags the disagreement as a `FactConflict`, keeps the family's decision, and answers before-and-after questions.

**How does conflict detection work?**
Each source is remembered as a `DataItem` with an `effective_date` (discharge summary Nov 8, pill bottle Nov 11). `improve(review_conflicts=True)` compares facts about the same entity and attribute across dated sources and stores a `FactConflict` such as "lisinopril `has_dose` 5 mg once daily vs 10 mg once daily", citing both source chunks. We read it with `list_conflicts()`.

**If Cognee already settles conflicts by date, why ask a person?**
For a time-varying fact, Cognee's review settles the conflict by date on its own (it marked ours resolved by review). For medications, a newer source is not proof, because the bottle could be a dispensing error. So the Guardian asks the family to confirm with the pharmacy, and we store the answer with `resolve_conflict()`, which records `resolution_source = "user"` and the reason. Later reviews keep that decision.

**How does the temporal question work?**
Dates become `Timestamp` nodes in the graph. When Sam says Mom has been "dizzy since Wednesday", the crew resolves that to Nov 11 and calls `recall(..., query_type=SearchType.TEMPORAL)` with "What changed in her medications between Nov 7 and Nov 11?". Cognee answered: "Lisinopril was increased from 5 mg once daily to 10 mg once daily."

**What's in the graph?**
The patient, medications, doses, prescriber, symptom, dates and `FactConflict` nodes, connected by `takes`, `has_dose`, `prescribed_by`, `reports_symptom` and `changed_on` relations from a custom extraction prompt. The dashboard draws the graph live from Cognee's graph store.

**Did you use Cognee Cloud?**
We were given a tenant, but we ran open-source Cognee 1.6.8 inside our container so that its LLM and embeddings could be served by SIE. The same operations map to Cognee Cloud's REST API (`/remember`, `/improve`, `/conflicts`, `/recall`).

**How reliable is the conflict detection?**
Cognee flagged the dose conflict in every test run after we fixed a name-cleaning bug in our own code (4 of 4). The bug was that the OCR fallback glued the patient's name onto the drug name, so Cognee saw two different drugs.

**How long does `remember()` take, and could it be faster?**
About 10–25 s per source while a 27B model does the extraction, so the first step takes about 45 s live. Next steps: send both sources in one `remember()` call, or build the graph with Cognee's LLM-free GLiNER pipeline on SIE.

**How would this work for many families?**
One Cognee dataset per family, so memories and permissions stay separate; Cognee supports datasets, ACLs and tenants. Siblings could share a family dataset.

## Keenable

**Where do the drug facts come from?**
Keenable search finds the official FDA label on DailyMed, and Keenable fetch with a prompt returns only the label sections that answer the question, in about 1.5 s. SIE's reranker then picks the two passages to quote, for example "Dizziness: High Dose 19%, Low Dose 12%".

**How do you avoid hallucinated medical facts?**
Capy Pharmacist never states a drug fact from a model's memory: every claim is a quote from the fetched label, with its source. The LLM only writes the brief from facts already in the record.

## Band

**Are these really separate agents?**
Yes. Four agents are registered in Band and each runs on `band-sdk`. They wake only when @mentioned and hand work to each other by @mention; their thoughts and tool calls are posted as events in the room, where the family member talks to them too.

**Why Band instead of function calls between agents?**
The family has to be in the loop. Band gives a shared room where people and agents talk and see each other's work, and where the Guardian asks a person before anything changes. It also means each agent could run anywhere, on any framework.

**What limits did you hit?**
Band's free plan has file transfer turned off, so documents stay in the app's vault and only the conversation goes through Band. The plan also allows 5 concurrent agents, which fits our crew of four.

## The demo

**Is the demo live?**
Yes. Every run calls SIE, Cognee, Keenable and Band for real, and the dashboard's "Under the hood" panel counts the calls. The video is a recording of a real run, sped up while Cognee is working (marked "sped up" on screen). The data is synthetic: a fictional patient, generated documents, and an AI-generated voice note.

**How long does a full case take?**
About 100–115 s live: roughly 45 s to read and remember both documents, a few seconds to record the family's decision, and about 30 s from the voice note to the finished brief.

**What was the hardest part?**
Making the LLM steps dependable: getting Cognee's structured extraction to work with Qwen on SIE (solved with the `instructor` backend), and keeping messy OCR text from splitting one drug into two entities.

**What's next?**
Pharmacy refill feeds instead of photos, translated briefs for multilingual families, a shared memory for every sibling, and an LLM-free graph-building path with GLiNER2 on SIE.

**Can I run it myself?**
Yes; see "Run it" in the README. You need SIE, Keenable and Band keys and four registered Band agents. Without keys, the video and the repo show the full flow.
