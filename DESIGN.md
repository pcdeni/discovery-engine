# Discovery Engine — System Design

> Complete architecture for distributed, autonomous scientific paper extraction.
> Last updated: 2026-03-11

---

## 1. What This Is

An open-source system where autonomous agents continuously extract structured knowledge from scientific papers and patents, building a public cross-domain discovery graph.

**The loop:**
```
forever: fetch paper → extract → validate → submit → repeat
```

Many agents, many machines, one shared dataset.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                      PAPER UNIVERSE                              │
│  arXiv · PMC OA · OpenAlex · OSTI · Patents · Europe PMC · ...  │
└────────────────────────────┬─────────────────────────────────────┘
                             │
              Contributors run autonomous loop
              on their own machine, any LLM
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                   CONTRIBUTOR MACHINE                             │
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
│  Post-merge Action → archive results, update tracking            │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              DISCOVERY GRAPH (computed)                           │
│                                                                  │
│  Harmonization  → canonicalize entity/tag names                  │
│  Embedding      → encode provides/requires/bridge_tags           │
│  Matching       → find provides↔requires connections             │
│  Clustering     → UMAP + HDBSCAN on bridge tags                 │
│  Browsing       → web app or static site                         │
└──────────────────────────────────────────────────────────────────┘
```

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

The combined prompt (`v_combined.txt`) produces both layers in a single LLM call — simpler, cheaper, no intermediate storage.

The combined prompt is 444 lines and has been validated across multiple flagship models and community models.

---

## 4. Paper Discovery & Tracking

### The Problem

There is no central server. How do you know which papers still need extraction?

### Solution: Shared Tracking File

A `processed_papers.jsonl` file on GitHub is the source of truth. Contributors check it before extracting to avoid duplicates.

**How papers are discovered:**

The CLI queries public APIs in real-time to find recent papers:

| Source | API | Access |
|--------|-----|--------|
| arXiv | OAI-PMH with `from` date | Open (PDF, LaTeX source) |
| PubMed/PMC | E-utilities with `mindate` | Mixed (PMC OA = open, rest = abstract) |
| OpenAlex | API with `from_updated_date` | Metadata + abstract; full text via OA links |
| OSTI | API with `date_added` | Open (DOE-funded research) |

Papers already in `processed_papers.jsonl` are skipped automatically. No central queue needed — each contributor discovers and deduplicates independently.

---

## 5. Content Sourcing & Paywall Handling

### Access Tiers

| Tier | Description | Who Can Extract |
|------|-------------|-----------------|
| **open** | Full text freely available (CC0, OA, government) | Anyone |
| **oa_link** | Paywalled journal but legal OA version exists (green OA, preprint) | Anyone (Unpaywall/CORE provides URL) |
| **institutional** | Behind publisher paywall, no legal OA version | Contributors with university library access |
| **abstract_only** | No full text available to contributor | Anyone (extract from abstract, lower quality) |

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
- The CLI supports: `discovery extract --doi 10.1234/example` → contributor's browser/proxy resolves it

**Tier: abstract_only**
- Fallback for papers with no text access
- Results are tagged `text_source: abstract` so they can be upgraded later when someone with access processes the full text

### Priority Strategy

Start with **open tier**. There are hundreds of millions of freely available papers and patents — years of work. Don't fight paywalls until open access is exhausted for a given topic.

The CLI default is `--tier open`. Contributors with institutional access can opt in: `--tier institutional --publisher elsevier,springer`.

---

## 6. The Contributor Loop

### The Autonomous Agent

The contributor runs a script that loops forever:

```python
# Simplified view of the extraction loop
while True:
    paper = fetch_next_paper(tier="open", source="round-robin")
    if paper is None:
        sleep(3600)
        continue

    text = fetch_text(paper)
    if text is None:
        mark_failed(paper, "could not fetch text")
        continue

    result = extract(text, prompt=COMBINED_PROMPT)

    issues = validate(result)
    if issues:
        mark_failed(paper, issues)
        continue

    save_to_batch(paper, result)
    papers_done += 1

    if papers_done % BATCH_SIZE == 0:
        submit_batch()  # creates PR
```

### Contributor Setup

```bash
# One-time setup
git clone https://github.com/pcdeni/discovery-engine
cd discovery-engine
pip install -e ".[all]"

# Configure
discovery config --provider anthropic --api-key sk-ant-...
# Or: --provider openrouter --api-key sk-or-...
# Or: --provider gemini --api-key AIza...
# Or: --provider local  (for ollama/vllm/llama.cpp)

# Run forever (default: open-access papers, round-robin sources)
discovery run

# Or: run with options
discovery run --count 100          # stop after 100 papers
discovery run --source arxiv       # only arXiv papers
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
5. Auto-merge if CI passes
6. Post-merge Action:
   - Moves results from `submissions/` to `results/`
   - Updates `processed_papers.jsonl` tracking
   - Cleans up submission files

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

### Periodic (batch)

| Check | What | When |
|-------|------|------|
| Honeypot | Pre-extracted papers seeded into queue; compare contributor result with known-good | Every 50th paper is a honeypot |
| Grounding | Entity names appear in source text | Run on random sample of results |
| Consistency | Bridge tags cluster into coherent groups | After harmonization |

---

## 8. GitHub Repository Structure

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
│       └── post-merge.yml             # Post-merge: archive results, update tracking
│
├── discovery/                          # Python package
│   ├── __init__.py
│   ├── cli.py                          # Entry point: `discovery run`, `discovery submit`
│   ├── run.py                          # The autonomous extraction loop
│   ├── extract.py                      # LLM extraction (multi-provider)
│   ├── validate.py                     # Schema + quality validation
│   ├── submit.py                       # Batch PR submission
│   ├── sources.py                      # Paper source adapters (arXiv, PMC, etc.)
│   ├── config.py                       # Configuration management
│   └── normalize.py                    # Result normalization
│
├── prompts/
│   └── v_combined.txt                  # The extraction prompt (444 lines)
│
├── schemas/
│   └── extraction.schema.json          # JSON Schema for validation
│
├── scripts/
│   ├── harmonize.py                    # Entity/tag canonicalization
│   ├── compute_embeddings.py           # Generate sentence embeddings
│   └── find_matches.py                 # provides↔requires matching
│
├── submissions/                        # PRs add files here; post-merge Action cleans
│   └── .gitkeep
│
├── results/                            # Archived extraction results
│   └── *.json
│
├── processed_papers.jsonl              # Tracking: which papers are done
│
└── docs/
    ├── CONTRIBUTING.md                 # Detailed contributor guide
    └── MODEL_COMPATIBILITY.md          # Which LLMs work (validated models table)
```

---

## 9. Complete Data Flow (end to end)

### Step 1: Contributor discovers papers
```
discovery run → query public APIs → filter out already-processed → pick paper
```

### Step 2: Contributor extracts
```
fetch text → LLM extraction → local validation → save to batch
```

### Step 3: Batch submitted
```
discovery submit → create branch → commit JSONs to submissions/ →
push → create PR → GitHub Actions CI validates → auto-merge
```

### Step 4: Results archived
```
Post-merge Action → move submissions/ to results/ →
update processed_papers.jsonl → clean up
```

### Step 5: Graph computed (periodic)
```
harmonize entities/tags → compute embeddings →
find provides↔requires matches → cluster bridge tags
```

---

## 10. Model Compatibility

Validated models (100% JSON parse, 100% FK integrity on combined prompt):

| Provider | Model | Quality |
|----------|-------|---------|
| Anthropic | Claude Sonnet 4 | Excellent |
| Anthropic | Claude Haiku 3.5 | Good |
| Google | Gemini 2.5 Flash | Good |
| OpenRouter | DeepSeek V3 | Good |
| OpenRouter | Llama 3.3 70B | Good |
| OpenRouter | Qwen3 235B | Excellent |
| OpenAI | GPT-4o | Good |
| Local | Any 70B+ model via ollama/vllm | Varies |

**Minimum requirement:** Model must produce valid JSON matching the schema. Recommended: 70B+ parameter models for consistent quality.

---

## 11. Contributor Incentives

| Incentive | How |
|-----------|-----|
| **Leaderboard** | Public ranking by papers extracted × quality score |
| **Co-authorship** | Top contributors get authorship on dataset papers |
| **The tool** | Contributors want the discovery engine to exist for their own research |
| **Portfolio** | "I contributed to an open scientific knowledge graph" |
| **Intrinsic** | Same motivation as Wikipedia editors, Galaxy Zoo volunteers |
| **Low barrier** | Minutes of setup, walk away |

---

## 12. Safety & Ethics

- **Blacklist ontology**: Bridge tag combinations that trigger review (pathogen + synthesis, fissile + enrichment, toxin + production)
- **Dual-use review**: Flagged extractions held for maintainer review before merge
- **Audit log**: Every extraction attributed to a contributor via git
- **No PII**: Papers are public; extractions contain no personal data
- **Model refusal handling**: Some papers trigger LLM safety filters (false positives on microbiology, weapons-adjacent chemistry). These are logged as `fail: safety_refusal` and skipped, not retried.

---

## 13. What We're NOT Building

To keep scope realistic:

- **No web frontend** (static site or Gradio app when needed)
- **No user accounts** (GitHub identity is enough)
- **No central server** (GitHub handles everything)
- **No real-time processing** (batch is fine)
- **No LLM hosting** (contributors use their own API keys or local models)
