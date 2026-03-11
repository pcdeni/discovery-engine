---
name: discovery-extract
description: Extract structured scientific knowledge from papers. Discovers papers from arXiv, PubMed, OpenAlex, OSTI, extracts entities/relations/cross-domain connections via LLM, validates, and submits results to the Discovery Engine dataset.
user-invocable: true
metadata: {"openclaw": {"requires": {"env": [], "bins": ["python3", "gh"]}, "optionalEnv": ["ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"], "files": ["scripts/*", "references/*"]}}
---

# Discovery Engine — Paper Extraction Skill

You are a scientific paper extraction agent. Your job is to discover scientific
papers, extract structured knowledge from them, validate the output, and submit
results to the Discovery Engine project.

## What This Skill Does

1. **Discovers** recent papers from arXiv, PubMed Central, OpenAlex, and OSTI
2. **Checks** which papers are already processed (via GitHub tracking file)
3. **Fetches** paper text (full text or abstract)
4. **Extracts** structured knowledge using an LLM:
   - Layer 1 (Facts): entities, properties, relations
   - Layer 2 (Connections): bridge tags, provides/requires interface, unsolved tensions
5. **Validates** output against the schema
6. **Saves** results locally for review before submission

Everything runs through a bundled Python script — no pip install, no external dependencies.

## Prerequisites

- **Python 3.10+** (stdlib only — no packages to install)
- **GitHub CLI** (`gh`) — authenticated with `gh auth login`
- **An LLM** — one of: cloud API key (Anthropic, OpenRouter, Gemini, OpenAI) OR a local model (ollama, vllm, llama.cpp)

## Running Extractions

The bundled script handles everything: paper discovery, text fetching, LLM extraction, validation, and saving.

### Quick test (recommended first)
```bash
# Preview available papers (no LLM calls)
python scripts/extract.py discover --source arxiv --count 10

# Extract 5 papers (uses Anthropic by default)
python scripts/extract.py --provider anthropic --api-key $ANTHROPIC_API_KEY --count 5

# Or with other providers:
python scripts/extract.py --provider openrouter --api-key $OPENROUTER_API_KEY --count 5
python scripts/extract.py --provider gemini --api-key $GOOGLE_API_KEY --count 5
python scripts/extract.py --provider local --count 3   # requires ollama/vllm running

# Validate saved results
python scripts/extract.py validate ~/.discovery/data/batch/
```

### Source-specific extraction
```bash
python scripts/extract.py --provider anthropic --api-key $ANTHROPIC_API_KEY --source arxiv --count 20
python scripts/extract.py --provider anthropic --api-key $ANTHROPIC_API_KEY --source pmc --count 20
python scripts/extract.py --provider anthropic --api-key $ANTHROPIC_API_KEY --source osti --count 20
```

### Custom output directory
```bash
python scripts/extract.py --provider local --count 10 --output ./my-results/
```

Results are saved to `~/.discovery/data/batch/` by default (one JSON file per paper).

## Submitting Results

After extraction, submit results as a PR to the Discovery Engine repository.

### Step 1: Fork the repo (first time only)
```bash
gh repo fork pcdeni/discovery-engine --clone=false
```

### Step 2: Clone your fork
```bash
gh repo clone pcdeni/discovery-engine discovery-engine-submit
cd discovery-engine-submit
```

### Step 3: Create a branch and copy results
```bash
BRANCH="contrib/$(gh api user --jq .login)/$(date +%Y%m%d-%H%M%S)"
git checkout -b "$BRANCH"
cp ~/.discovery/data/batch/*.json submissions/
git add submissions/
git commit -m "Add extraction results"
git push -u origin "$BRANCH"
```

### Step 4: Create the PR
```bash
gh pr create --title "extraction: $(ls submissions/*.json | wc -l) papers" \
  --body "Extraction results from discovery-extract skill" \
  --repo pcdeni/discovery-engine
```

GitHub Actions CI will validate the submission (schema, quality, dedup).
If all checks pass, the PR is auto-merged.

## How It Works

- The `scripts/extract.py` script queries public paper APIs directly (no database needed)
- Papers already in `processed_papers.jsonl` on GitHub are automatically skipped
- The extraction prompt is bundled at `references/prompt.txt` (444 lines)
- The JSON schema is bundled at `references/schema.json`
- Each contributor runs their own LLM (cloud or local)
- Results are submitted as PRs — validated by CI and auto-merged

Papers connect when one's `provides` matches another's `requires`, enabling
cross-domain scientific discovery at scale.

## Bundled Files

| File | Purpose |
|------|---------|
| `scripts/extract.py` | Self-contained extraction script (Python stdlib only) |
| `references/prompt.txt` | The 444-line extraction prompt |
| `references/schema.json` | JSON schema for validation |

## Supported Models

| Provider | Model | Quality |
|----------|-------|---------|
| Anthropic | Claude Sonnet 4 | Excellent |
| Google | Gemini 2.5 Flash | Good |
| OpenRouter | DeepSeek V3 | Good |
| OpenAI | GPT-4o | Good |
| Local | Any 70B+ via ollama/vllm | Varies |
