# Discovery Engine — System Design

> Complete architecture for distributed, autonomous scientific paper extraction.
> Last updated: 2026-03-11

---

## 1. What This Is

An open-source system where autonomous agents continuously extract structured knowledge from scientific papers and patents, building a public cross-domain discovery graph. Inspired by [Karpathy's autoresearch](https://github.com/karpathy/autoresearch) — a single agent loops forever doing useful work — but distributed across many contributors.

**Karpathy's loop:**
```
forever: modify code → train → evaluate → keep/discard
```

**Our loop:**
```
forever: fetch paper → extract → validate → submit → repeat
```

The difference: autoresearch is one agent, one GPU, one metric. We are many agents, many machines, one shared dataset.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                      PAPER UNIVERSE                              │
│  arXiv · PMC OA · OpenAlex · OSTI · Patents · Europe PMC · ...  │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                    GitHub Actions cron (weekly)
                    queries APIs for new papers
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                    PAPER INDEX (HuggingFace)                     │
│  paper_index.parquet — every known paper ID + metadata + status  │
│  Fields: paper_id, source, title, access_tier, status, ...       │
│  Status: new → claimed → extracted → validated                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
              Contributors pull unclaimed papers
              Each runs autonomous loop on own machine
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              CONTRIBUTOR MACHINE (the "miner")                   │
│                                                                  │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐    │
│  │ fetch paper  │ →  │ LLM extract  │ →  │ local validate   │    │
│  │ (text/abstract)   │ (own API key)│    │ (schema + tags)  │    │
│  └─────────────┘    └──────────────┘    └──────────────────┘    │
│                                                  │               │
│                                          batch results           │
│                                                  │               │
└──────────────────────────────────────────────────┼───────────────┘
                                                   │
                                           Submit PR to GitHub
                                                   │
                                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                     GITHUB REPOSITORY                            │
│                                                                  │
│  PR received → Actions CI validates → auto-merge if clean        │
│  Post-merge Action → push results to HuggingFace Dataset         │
│  Post-merge Action → update paper_index status                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              HUGGINGFACE DATASET (results store)                  │
│                                                                  │
│  discovery-results/                                              │
│    results.parquet  — all extraction results                     │
│    embeddings/      — sentence embeddings for matching           │
│    graph/           — bridge adjacency, clusters                 │
│    stats.json       — contributor leaderboard, progress          │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                    Periodic batch processing
                    (GitHub Actions cron, weekly)
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              DISCOVERY GRAPH (computed)                           │
│                                                                  │
│  Harmonization  → canonicalize entity/tag names                  │
│  Embedding      → encode provides/requires/bridge_tags           │
│  Matching       → find provides↔requires connections             │
│  Clustering     → UMAP + HDBSCAN on bridge tags                 │
│  Browsing       → HF Spaces Gradio app (or static site)         │
└──────────────────────────────────────────────────────────────────┘
```

**Total infrastructure cost: $0/month.** Contributors pay ~$0.01-0.03 per paper for their own LLM API calls.

---

## 3. The Two Layers

### Layer 1: Fact Extraction

Meticulous librarian. Extract every structured fact the paper states.

**Output:**
- **Entities**: Named materials, devices, processes, organisms — typed, aliased, with domains
- **Properties**: Every measurable characteristic with value, unit, conditions, confidence
- **Relations**: Causal links between entities (enables, inhibits, degrades, etc.) with mechanisms
- **Paper Analysis**: objective, method, key_findings, limitations, context

**Quality bar:** Zero inference. If the paper doesn't say it, don't extract it.

### Layer 2: Cross-Domain Abstraction

Connection detector. Strip the paper of its domain identity and expose its underlying mechanism.

**Output:**
- **Core Friction**: The problem in domain-free language (no field-specific nouns)
- **Mechanism**: How the solution works, using verb phrases and structural patterns
- **Unsolved Tensions**: Tradeoffs, assumptions, constraints — NEVER empty
- **Bridge Tags**: Abstract functional descriptors that pass two tests:
  - The "3 fields test": would this tag appear in 3+ unrelated fields?
  - The "specificity test": would fewer than 10,000 papers match?
- **Interface (provides/requires)**: Concrete operations in snake_case, descriptions emphasize VERBS over domain nouns. This is the primary discovery mechanism — papers connect when one's `provides` matches another's `requires`.

**Quality bar:** Inference from stated results IS expected. Every mechanism has tradeoffs; identifying them is the core skill.

### Why Both Layers in One Pass

The original design separated Phase 1 (facts, distributed) from Phase 2 (cross-domain, centralized + secret). Now that this is open-source, there is no reason to keep Phase 2 secret. The combined prompt (`v_combined.txt`) produces both layers in a single LLM call — simpler, cheaper, no intermediate storage.

The combined prompt is 444 lines and has been validated:
- 21 papers on ROG with 100% clean schema
- All results have 1-3 provides (dict), 2-3 requires (dict), 3-4 tensions (dict), 4-6 bridge tags
- Tested across 5 flagship models + 13 OpenClaw community models

---

## 4. Paper Discovery & Tracking

### The Problem

There is no central server. How do you know what papers exist and which ones still need extraction?

### Solution: Paper Index on HuggingFace

A single `paper_index.parquet` file, updated weekly by GitHub Actions, is the source of truth.

**Schema:**

| Column | Type | Description |
|--------|------|-------------|
| `paper_id` | string | Source-prefixed ID (`arxiv:2401.12345`, `pmc:12345678`, `osti:1234567`) |
| `source` | string | Origin database |
| `title` | string | Paper title (truncated to 200 chars) |
| `year` | int | Publication year |
| `access_tier` | string | `open` / `oa_link` / `institutional` / `abstract_only` |
| `abstract_len` | int | Abstract length in chars (skip if < 100) |
| `date_added` | date | When this paper entered our index |
| `status` | string | `new` / `extracted` / `validated` |
| `extracted_by` | string | GitHub username of extractor (null if new) |

**How papers enter the index (GitHub Actions cron, weekly):**

| Source | API | New papers/week | Access |
|--------|-----|-----------------|--------|
| arXiv | OAI-PMH with `from` date | ~15,000-20,000 | Open (PDF, LaTeX source) |
| PubMed/PMC | E-utilities with `mindate` | ~20,000 | Mixed (PMC OA = open, rest = abstract) |
| OpenAlex | API with `from_updated_date` | ~100,000+ | Metadata + abstract; full text via OA links |
| OSTI | API with `date_added` | ~1,000 | Open (DOE-funded research) |
| Semantic Scholar | API with `publicationDateOrYear` | ~50,000 | Abstract; S2ORC for OA subset |
| Google Patents | BigQuery with publication date | ~10,000 | Open (full text claims + description) |
| USPTO | Bulk XML weekly dumps | ~7,000 | Open |
| Europe PMC | OAI-PMH with `from` date | ~20,000 | Abstract; full text for OA subset |

**Update script** (`scripts/update_paper_index.py`):
- Runs as GitHub Actions cron (weekly, Sunday midnight)
- Queries each API for papers added in the last 7 days
- Deduplicates against existing index (by paper_id)
- Determines `access_tier` using Unpaywall API + source metadata
- Appends new rows to `paper_index.parquet`
- Pushes updated file to HuggingFace
- Logs stats: "Added 12,345 new papers (8,201 open, 2,100 oa_link, 1,800 institutional, 244 abstract_only)"

### Bootstrapping

The initial index will be seeded from our existing data sources:
- `v_combined.txt` sources already downloaded on ROG: OpenAlex (250M+), PMC OA (7.2M), arXiv (2.5M), OSTI (3.4M), patents
- First upload: a parquet file with 500K+ open-access paper IDs ready for extraction
- Contributors see an ocean of available work from day one

---

## 5. Content Sourcing & Paywall Handling

### The Problem

Scientific publishing is fragmented. Some papers are freely available, others sit behind $30/article paywalls. In a decentralized system, no central server can download papers. Contributors must source their own content.

### Access Tiers

| Tier | Description | Size | Who Can Extract |
|------|-------------|------|-----------------|
| **open** | Full text freely available (CC0, OA, government) | ~500M+ papers + patents | Anyone |
| **oa_link** | Paywalled journal but legal OA version exists (green OA, preprint, repository) | ~50M additional | Anyone (Unpaywall/CORE provides URL) |
| **institutional** | Behind publisher paywall, no legal OA version | ~100M+ | Contributors with university library access |
| **abstract_only** | No full text available to contributor | Fallback | Anyone (extract from abstract, lower quality) |

### How Contributors Get Full Text

**Tier: open**
- arXiv: LaTeX source via `arxiv.org/e-print/`, or PDF via `arxiv.org/pdf/`
- PMC OA: XML full text via `ncbi.nlm.nih.gov/pmc/oai/`
- OSTI: Full text via `osti.gov/api/` (DOE-funded papers)
- Patents: Full text via Google Patents Public Data, USPTO bulk XML, EPO OPS API
- OpenAlex OA: Follow `open_access.oa_url` field

**Tier: oa_link**
- Unpaywall API (`api.unpaywall.org/v2/DOI?email=you`): Returns legal OA URL for ~30% of paywalled papers
- CORE API (`core.ac.uk/api-v3/`): 200M+ repository copies
- Semantic Scholar S2ORC: Open access full text for millions of papers

**Tier: institutional**
- Contributor has university VPN/proxy → accesses publisher directly
- No central downloading — the contributor fetches on their own network
- The CLI supports: `discovery extract --doi 10.1234/example` → contributor's browser/proxy resolves it

**Tier: abstract_only**
- Fallback for papers with no text access
- Extract from abstract only — less rich but still useful for bridge tags and interface
- Results are tagged `text_source: abstract` so they can be upgraded later when someone with access processes the full text

### Priority Strategy

Start with **open tier**. 500M+ freely available papers and patents is years of work. Don't fight paywalls until open access is exhausted for a given topic.

The CLI default is `--tier open` — contributors who don't specify get only freely available papers. Contributors with institutional access can opt in: `--tier institutional --publisher elsevier,springer`.

---

## 6. The Contributor Loop (autoresearch-style)

### Program Directive

Like Karpathy's `program.md`, the contributor runs a script that loops forever:

```python
# run.py — the autonomous extraction loop
# Start this and walk away. It will process papers until interrupted.

while True:
    # 1. Fetch next unclaimed paper
    paper = fetch_next_paper(tier="open", source="round-robin")
    if paper is None:
        log("No unclaimed papers available. Sleeping 1 hour.")
        sleep(3600)
        continue

    # 2. Get paper text
    text = fetch_text(paper)
    if text is None:
        mark_failed(paper, "could not fetch text")
        continue

    # 3. Extract with LLM
    result = extract(text, prompt=COMBINED_PROMPT)

    # 4. Validate locally
    issues = validate(result)
    if issues:
        log(f"Validation failed: {issues}")
        mark_failed(paper, issues)
        continue

    # 5. Save to local batch
    save_to_batch(paper, result)
    papers_done += 1

    # 6. Submit batch every N papers
    if papers_done % BATCH_SIZE == 0:
        submit_batch()  # creates PR

    log(f"Done: {paper.id} ({papers_done} total)")
```

### Contributor Setup

```bash
# One-time setup
git clone https://github.com/discovery-engine/discovery-engine
cd discovery-engine
pip install -e .

# Configure
discovery config --api-key sk-ant-... --provider anthropic
# Or: --provider openrouter --api-key sk-or-...
# Or: --provider gemini --api-key AIza...

# Run forever (default: open-access papers, round-robin sources)
discovery run

# Or: run with options
discovery run --count 100          # stop after 100 papers
discovery run --tier institutional  # include paywalled papers
discovery run --source arxiv        # only arXiv papers
discovery run --model claude-sonnet-4-20250514    # specific model
```

### Submission Flow

When a batch is ready (default: every 25 papers):

1. CLI creates a branch: `contrib/<github-user>/<timestamp>`
2. Commits result JSONs to `submissions/`
3. Pushes and creates a PR
4. GitHub Actions CI:
   - JSON schema validation (required keys, types, value ranges)
   - Bridge tag quality check (blocklist of domain nouns, statistical terms)
   - Format enforcement (provides/requires are dicts not strings, tensions are dicts not strings)
   - Duplicate check (paper_id not already in results)
5. If CI passes → auto-merge (for established contributors) or maintainer review (for new contributors)
6. Post-merge Action:
   - Moves results from `submissions/` to HuggingFace dataset
   - Updates `paper_index.parquet` status to `extracted`
   - Updates contributor leaderboard
   - Deletes the submission files from the repo (keeps it clean)

---

## 7. Quality Assurance

### Automated (every PR)

| Check | What | Fail Condition |
|-------|------|----------------|
| Schema | Required keys exist, correct types | Missing `paper_analysis`, `entities`, `cross_domain` |
| Non-empty | Core fields have content | 0 entities, 0 bridge_tags, 0 tensions |
| Format | Correct structure | String instead of dict for provides/requires/tensions |
| Tag quality | Bridge tags are abstract | Domain nouns (`graphene`, `insulin`), statistical terms (`p-value`) |
| Provides/requires | Functional descriptions | Missing `operation` or `description` fields |
| Duplicate | Paper not already extracted | paper_id exists in results dataset |

### Periodic (weekly batch)

| Check | What | When |
|-------|------|------|
| Honeypot | Pre-extracted papers seeded into queue; compare contributor result with known-good | Every 50th paper is a honeypot |
| Grounding | Entity names appear in source text | Run on random sample of results |
| Consistency | Bridge tags cluster into coherent groups | After harmonization |
| Contributor scoring | Quality score per contributor | Updated weekly, feeds leaderboard |

### Trust Ramp

New contributors:
- First 10 submissions: manual review by maintainer
- Papers 11-50: auto-merge if CI passes, spot-checked weekly
- Papers 50+: full auto-merge, contributor marked "trusted"

---

## 8. Why Not Blockchain

### The Intuition

"Processing papers is computational work. Blockchain mining is computational work. Why not make extraction the proof-of-work?"

This is a natural thought, but the analogy breaks down on inspection:

| Property | Blockchain Mining | Paper Extraction |
|----------|------------------|------------------|
| **Purpose of work** | Intentionally wasteful — the waste IS the security | Intentionally useful — we want the results |
| **Verification** | Any node can verify a hash in microseconds | Verifying extraction quality requires LLM or human judgment |
| **Consensus** | Majority hashrate determines truth | Quality determines truth (one good extraction > 100 bad ones) |
| **Censorship resistance** | Essential — no authority can modify the chain | Undesirable — we WANT to remove bad extractions |
| **Decentralization** | Required — single points of failure defeat the purpose | Helpful but optional — HuggingFace + GitHub are trusted enough |
| **Token economics** | Miners earn tokens with market value | No tokenizable value to distribute |
| **Overhead** | Massive (consensus protocol, full nodes, network) | Our data is ~1KB per paper extraction |

### What You Actually Want (and Already Have)

The properties that make blockchain attractive are already provided by **git**:

- **Immutability**: Git commits are hash chains (Merkle trees). Every commit cryptographically references its parent. This IS a blockchain, structurally.
- **Attribution**: Signed commits. Every contribution is linked to a GitHub identity.
- **Auditability**: Full public commit history on GitHub. Anyone can verify when a result was added and by whom.
- **Tamper evidence**: If anyone modifies a past result, the hash chain breaks. Git detects this automatically.
- **Fork resistance**: Branch protection rules on `main`. Force-push disabled. Only CI-validated PRs can merge.

What blockchain adds that git doesn't: **decentralized consensus** (no central authority). But we WANT a central authority (the project maintainers + CI) to enforce quality. Decentralized consensus would mean "majority of contributors agree" — which for extraction quality is meaningless (bad extractions can outnumber good ones).

### What To Use Instead

```
Git (hash chain)          → immutable, attributed history
GitHub Actions (CI)       → automated quality consensus
HuggingFace Datasets      → versioned, public, auditable storage
Contributor leaderboard   → reputation/credit system
GPG-signed commits        → cryptographic attribution (optional)
```

This gives you every useful property of a blockchain with none of the overhead. The dataset is public, versioned, auditable, attributed, and tamper-evident — without running consensus nodes, burning electricity, or designing token economics.

### The "Proof of Useful Work" Concept

The core insight — that extraction IS valuable work and should be credited — is correct. The implementation is:

1. Every extraction is a signed git commit attributed to a contributor
2. Quality scores are computed automatically (schema compliance + periodic honeypot checks)
3. A public leaderboard tracks `papers_extracted × quality_score` per contributor
4. Co-authorship on publications for top contributors

This is "proof of useful work" without the blockchain overhead. The work is verified by CI (not by hash computation), and credit is tracked by git (not by token balances).

---

## 9. GitHub Repository Structure

```
discovery-engine/
├── README.md                           # Quick start for contributors
├── LICENSE                             # MIT
├── DESIGN.md                           # This document
├── pyproject.toml                      # Package: pip install discovery-engine
│
├── .github/
│   └── workflows/
│       ├── validate-submission.yml     # CI: validate PRs with new extractions
│       ├── sync-to-hf.yml             # Post-merge: push results to HuggingFace
│       ├── update-paper-index.yml      # Weekly cron: discover new papers
│       └── weekly-maintenance.yml      # Weekly: harmonization, stats, leaderboard
│
├── discovery/                          # Python package
│   ├── __init__.py
│   ├── cli.py                          # Entry point: `discovery run`, `discovery submit`
│   ├── run.py                          # The autonomous extraction loop
│   ├── extract.py                      # LLM extraction (multi-provider)
│   ├── validate.py                     # Schema + quality validation
│   ├── submit.py                       # Batch PR submission
│   ├── sources.py                      # Paper source adapters (arXiv, PMC, etc.)
│   ├── fetch.py                        # Full text fetching (open, oa_link, abstract)
│   ├── config.py                       # Configuration management
│   └── providers/
│       ├── anthropic.py                # Claude API
│       ├── openrouter.py               # OpenRouter (DeepSeek, Llama, etc.)
│       ├── gemini.py                   # Google Gemini
│       └── openai.py                   # OpenAI / compatible APIs
│
├── prompts/
│   └── v_combined.txt                  # The extraction prompt (444 lines)
│
├── schemas/
│   └── extraction.schema.json          # JSON Schema for validation
│
├── scripts/
│   ├── update_paper_index.py           # Paper discovery (used by GH Actions cron)
│   ├── harmonize.py                    # Entity/tag canonicalization
│   ├── compute_embeddings.py           # Generate sentence embeddings
│   ├── find_matches.py                 # provides↔requires matching
│   └── generate_stats.py              # Leaderboard + dataset stats
│
├── submissions/                        # PRs add files here; post-merge Action cleans
│   └── .gitkeep
│
└── docs/
    ├── CONTRIBUTING.md                 # Detailed contributor guide
    ├── PAPER_SOURCES.md                # Where to find papers, access tiers
    └── MODEL_COMPATIBILITY.md          # Which LLMs work (validated models table)
```

---

## 10. HuggingFace Datasets

### Dataset: `discovery-engine/paper-index`

The registry of all known papers. Updated weekly by GitHub Actions.

- `paper_index.parquet` — one row per paper
- Versioned by HuggingFace Dataset versioning (every push is a commit)
- Contributors pull this to know what papers are available

### Dataset: `discovery-engine/results`

All extraction results. Updated on every PR merge.

- `results/` — one JSON per paper (same format as ROG extractions)
- `embeddings/` — sentence embeddings for matching
- `graph/` — bridge adjacency matrix, cluster assignments
- `stats.json` — extraction counts, contributor leaderboard
- `canonical_entities.jsonl` — harmonized entity index
- `canonical_tags.jsonl` — harmonized bridge tag index

### Dataset: `discovery-engine/discoveries`

Computed cross-domain connections. Updated weekly.

- `matches.parquet` — provides↔requires matches above threshold
- `clusters.parquet` — UMAP + HDBSCAN bridge tag clusters
- `causal_chains.parquet` — future: Type 2 connections

---

## 11. Complete Data Flow (end to end)

### Step 1: Paper enters the index
```
[arXiv API] → GitHub Actions cron → paper_index.parquet → HuggingFace
```

### Step 2: Contributor extracts
```
discovery run → pull paper_index → pick unclaimed paper → fetch text →
LLM extraction → local validation → save to batch
```

### Step 3: Batch submitted
```
discovery submit → create branch → commit JSONs to submissions/ →
push → create PR → GitHub Actions CI validates → auto-merge
```

### Step 4: Results stored
```
Post-merge Action → read submissions/ → push to HF results dataset →
update paper_index status → delete submissions/ files → update stats
```

### Step 5: Graph computed (weekly)
```
Weekly Action → harmonize entities/tags → compute embeddings →
find provides↔requires matches → cluster bridge tags →
push to HF discoveries dataset → update Gradio browser
```

---

## 12. Model Compatibility

Validated models (100% JSON parse, 100% FK integrity on combined prompt):

| Provider | Model | Cost/paper | Quality |
|----------|-------|-----------|---------|
| Anthropic | Claude Sonnet 4 | ~$0.02 | Excellent |
| Anthropic | Claude Haiku 3.5 | ~$0.005 | Good |
| Google | Gemini 2.5 Flash | ~$0.003 | Good |
| OpenRouter | DeepSeek V3 | ~$0.002 | Good |
| OpenRouter | Llama 3.3 70B | ~$0.003 | Good |
| OpenRouter | Qwen3 235B | ~$0.004 | Excellent |
| OpenAI | GPT-4o | ~$0.02 | Good |

**Minimum requirement:** Model must produce valid JSON matching the schema. Recommended: 70B+ parameter models for consistent quality.

**Known incompatible:** Step 3.5 Flash, GPT-5 Nano (inconsistent JSON structure).

---

## 13. Scaling Roadmap

| Phase | Papers | Timeline | Who Does The Work |
|-------|--------|----------|-------------------|
| **Seed** | 0 → 1,000 | Month 1-2 | ROG machine (our own continuous extraction) |
| **Launch** | 1,000 → 5,000 | Month 2-4 | ROG + early contributors |
| **Growth** | 5,000 → 50,000 | Month 4-12 | Community + ROG |
| **Scale** | 50,000 → 500,000 | Year 2 | Community-driven |
| **Maturity** | 500,000+ | Year 2+ | Self-sustaining community |

### Seed Phase: Our Own ROG Machine

Before launching publicly, we seed the dataset with 1,000+ papers extracted on ROG using the combined prompt. This:
- Proves the pipeline works end-to-end
- Provides honeypot papers for quality checking
- Gives contributors something to browse and understand what good results look like
- Generates enough data for initial embedding matching experiments

ROG is currently extracting at ~36 papers/hour = ~860 papers/day. One week of continuous operation = 6,000 papers.

### What Triggers Each Phase

- **Launch**: Paper published on arXiv + public GitHub repo + 1,000 seed papers on HF
- **Growth**: First external contributor submits a PR
- **Scale**: 10+ regular contributors, GitHub Actions workflows battle-tested
- **Maturity**: Dataset cited in other papers, used as research tool

---

## 14. Contributor Incentives (Why Bother?)

No payment. Pure volunteer model. What makes people contribute to open science?

| Incentive | How |
|-----------|-----|
| **Leaderboard** | Public ranking by papers extracted × quality score |
| **Co-authorship** | Top contributors get authorship on dataset papers |
| **The tool** | Contributors want the discovery engine to exist for their own research |
| **Portfolio** | "I contributed to an open scientific knowledge graph" |
| **Intrinsic** | Same motivation as Wikipedia editors, Galaxy Zoo volunteers |
| **Low barrier** | $0.01-0.03 per paper, 2 minutes of setup, walk away |

---

## 15. Safety & Ethics

- **Blacklist ontology**: Bridge tag combinations that trigger review (pathogen + synthesis, fissile + enrichment, toxin + production)
- **Dual-use review**: Flagged extractions held for maintainer review before merge
- **Audit log**: Every extraction attributed to a contributor via git
- **No PII**: Papers are public; extractions contain no personal data
- **Model refusal handling**: Some papers trigger LLM safety filters (false positives on microbiology, weapons-adjacent chemistry). These are logged as `fail: safety_refusal` and skipped, not retried.

---

## 16. What We're NOT Building

To keep scope realistic:

- **No web frontend** (use HF Spaces Gradio or static site)
- **No user accounts** (GitHub identity is enough)
- **No payment system** (pure volunteer)
- **No central server** (GitHub + HF handle everything)
- **No blockchain** (git is already a hash chain)
- **No real-time processing** (batch/weekly is fine)
- **No LLM hosting** (contributors use their own API keys)

---

## Related Documents

| Document | What It Covers |
|----------|---------------|
| [STRATEGY.md](STRATEGY.md) | High-level strategy and competitive landscape |
| [COMMUNITY_MODEL.md](COMMUNITY_MODEL.md) | Detailed community processing design (historical, being revised) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Knowledge graph engine internals |
| [AI_MODELS.md](AI_MODELS.md) | Model selection, prompt design, validation results |
| [FUTURE_FEATURES.md](FUTURE_FEATURES.md) | Deferred features: Type 2/3 connections, cross-encoder, ASPIRE |
| [DATA_SOURCES.md](DATA_SOURCES.md) | 40+ data sources, download status |
| [paper_draft.md](paper_draft.md) | Academic paper draft |
