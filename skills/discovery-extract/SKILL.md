---
name: discovery-extract
description: Extract structured scientific knowledge from papers. Queries arXiv, PubMed, OpenAlex, OSTI for papers, extracts entities/relations/cross-domain connections via LLM, validates, and submits PRs to the Discovery Engine dataset.
user-invocable: true
metadata: {"openclaw": {"requires": {"env": ["ANTHROPIC_API_KEY"], "bins": ["git", "python3"]}, "primaryEnv": "ANTHROPIC_API_KEY"}}
---

# Discovery Engine — Paper Extraction Skill

You are an autonomous scientific paper extraction agent. Your job is to continuously
discover scientific papers, extract structured knowledge from them, and submit results
to the Discovery Engine project.

## What This Skill Does

1. **Discovers** recent papers from arXiv, PubMed Central, OpenAlex, and OSTI
2. **Checks** which papers are already processed (via GitHub tracking file)
3. **Fetches** paper text (full text or abstract)
4. **Extracts** structured knowledge using your LLM:
   - Layer 1 (Facts): entities, properties, relations
   - Layer 2 (Connections): bridge tags, provides/requires interface, unsolved tensions
5. **Validates** output against the schema
6. **Submits** results as a PR to the Discovery Engine GitHub repo

## Setup (One-Time)

Run these commands to set up:

```bash
# Clone and install
git clone https://github.com/pcdeni/discovery-engine
cd discovery-engine
pip install -e ".[anthropic]"

# Configure (use your own API key)
discovery config --provider anthropic --api-key $ANTHROPIC_API_KEY
discovery config --github-user $(git config user.name)
```

## Running Extractions

### Quick test (5 papers)
```bash
discovery run --count 5 --dry-run    # preview what would be processed
discovery run --count 5              # actually extract 5 papers
```

### Autonomous mode (run forever, auto-submit PRs)
```bash
discovery run --auto-submit
```

### Source-specific extraction
```bash
discovery run --source arxiv --count 50
discovery run --source pmc --count 50
discovery run --source openalex --count 50
discovery run --source osti --count 50
```

### With other models
```bash
# OpenRouter (DeepSeek, Llama, Qwen, etc.)
pip install -e ".[all]"
discovery config --provider openrouter --api-key $OPENROUTER_API_KEY
discovery config --model deepseek/deepseek-chat
discovery run --auto-submit

# Local LLM (no API key needed)
discovery config --provider local
discovery config --model llama3.1
discovery run --auto-submit
```

## Submitting Results

Results accumulate in `~/.discovery/data/batch/`. Submit them:

```bash
discovery submit --dry-run    # preview
discovery submit              # create PR
```

Or use `--auto-submit` with `discovery run` for fully autonomous operation.

## Checking Progress

```bash
discovery status    # shows local + global progress
```

## How It Works

The system is fully decentralized:
- Each contributor runs their own LLM with their own API key
- Papers are discovered from public APIs (no central queue needed)
- A tracking file on GitHub (`processed_papers.jsonl`) prevents duplicate work
- Results are submitted as PRs, validated by CI, and merged into the dataset
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
