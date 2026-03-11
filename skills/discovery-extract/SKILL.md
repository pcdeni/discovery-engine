---
name: discovery-extract
description: Extract structured scientific knowledge from papers. Queries arXiv, PubMed, OpenAlex, OSTI for papers, extracts entities/relations/cross-domain connections via LLM, validates, and submits PRs to the Discovery Engine dataset.
user-invocable: true
metadata: {"openclaw": {"requires": {"env": [], "bins": ["git", "gh", "python3"]}, "optionalEnv": ["ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"]}}
---

# Discovery Engine — Paper Extraction Skill

You are a scientific paper extraction agent. Your job is to discover scientific
papers, extract structured knowledge from them, validate the output, and submit
results to the Discovery Engine project.

## What This Skill Does

1. **Discovers** recent papers from arXiv, PubMed Central, OpenAlex, and OSTI
2. **Checks** which papers are already processed (via GitHub tracking file)
3. **Fetches** paper text (full text or abstract)
4. **Extracts** structured knowledge using your LLM:
   - Layer 1 (Facts): entities, properties, relations
   - Layer 2 (Connections): bridge tags, provides/requires interface, unsolved tensions
5. **Validates** output against the schema
6. **Saves** results locally for review before submission

## Prerequisites

- **Git** + **GitHub CLI** (`gh`) — authenticated with `gh auth login`
- **Python 3.10+**
- **An LLM** — one of: cloud API key (Anthropic, OpenRouter, Gemini, OpenAI) OR a local model (ollama, vllm, llama.cpp)

## Setup (One-Time)

```bash
# Clone and install
git clone https://github.com/pcdeni/discovery-engine
cd discovery-engine
pip install -e ".[all]"

# Configure — pick ONE provider:

# Cloud (Anthropic)
discovery config --provider anthropic --api-key YOUR_KEY

# Cloud (OpenRouter — DeepSeek, Llama, Qwen)
discovery config --provider openrouter --api-key YOUR_KEY
discovery config --model deepseek/deepseek-chat

# Cloud (Google Gemini)
discovery config --provider gemini --api-key YOUR_KEY

# Local (no API key needed — requires ollama/vllm/llama.cpp running)
discovery config --provider local
discovery config --model llama3.1

# Set your GitHub username (for PR attribution)
discovery config --github-user $(git config user.name)
```

## Running Extractions

### Quick test (recommended first)
```bash
discovery run --count 5 --dry-run    # preview what would be processed (no LLM calls)
discovery run --count 5              # extract 5 papers
discovery validate ~/.discovery/data/batch/   # check results
```

### Batch extraction
```bash
discovery run --count 50             # extract 50 papers, review before submitting
discovery submit --dry-run           # preview what would be submitted
discovery submit                     # create PR (requires gh auth)
```

### Source-specific extraction
```bash
discovery run --source arxiv --count 50
discovery run --source pmc --count 50
discovery run --source osti --count 50
```

## Submitting Results

Results accumulate in `~/.discovery/data/batch/`. Submit them as a PR:

```bash
discovery submit --dry-run    # preview
discovery submit              # create PR (requires authenticated gh CLI)
```

Submission uses the GitHub CLI (`gh`) to create PRs. Make sure you've run
`gh auth login` beforehand.

## Checking Progress

```bash
discovery status    # shows local + global progress
```

## How It Works

- Each contributor runs their own LLM (cloud or local)
- Papers are discovered from public APIs (no central queue needed)
- A tracking file on GitHub (`processed_papers.jsonl`) prevents duplicate work
- Results are submitted as PRs, validated by CI, and auto-merged if checks pass
- Merged results are stored in the GitHub repo under `results/`

Papers connect when one's `provides` matches another's `requires`, enabling
cross-domain scientific discovery at scale.

## Supported Models

| Provider | Model | Quality |
|----------|-------|---------|
| Anthropic | Claude Sonnet 4 | Excellent |
| Google | Gemini 2.5 Flash | Good |
| OpenRouter | DeepSeek V3 | Good |
| OpenAI | GPT-4o | Good |
| Local | Any 70B+ via ollama/vllm | Varies |
