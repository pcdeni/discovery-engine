# Contributing to Discovery Engine

## Prerequisites

- Python 3.10+
- An LLM API key (any of: Anthropic, OpenRouter, Gemini, OpenAI)
- Git + GitHub CLI (`gh`) for submitting PRs

## Setup

```bash
# Clone and install
git clone https://github.com/pcdeni/discovery-engine
cd discovery-engine
pip install -e ".[anthropic]"   # or your preferred provider

# Configure
discovery config --provider anthropic --api-key sk-ant-YOUR_KEY
discovery config --github-user YOUR_GITHUB_USERNAME
```

## Running Extractions

### Basic usage (recommended for first-time contributors)

```bash
# Preview what would be processed (no LLM calls)
discovery run --count 5 --dry-run

# Extract 5 papers to test your setup
discovery run --count 5

# Check results
discovery status

# Validate your batch
discovery validate ~/.discovery/data/batch/
```

### Autonomous mode (walk away and let it run)

```bash
# Run forever, auto-submit PRs when batch is full
discovery run --auto-submit

# Or with options:
discovery run --source arxiv --model claude-sonnet-4-20250514 --auto-submit
discovery run --count 100 --auto-submit   # stop after 100 papers
```

### What happens during extraction

1. The CLI queries scientific databases (arXiv, PMC, OpenAlex, OSTI) for recent papers
2. Checks `processed_papers.jsonl` on GitHub to skip already-processed papers
3. Downloads full text (if available) or abstract
4. Sends it through the combined extraction prompt via your LLM
5. Normalizes the output (fixes snake_case, restructures if needed)
6. Validates against the schema
7. Saves to your local batch (~/.discovery/data/batch/)
8. Auto-submits PR when batch is full (if `--auto-submit` is set)

### Using cheaper models

```bash
# OpenRouter: DeepSeek V3 at $0.002/paper
pip install -e ".[all]"
discovery config --provider openrouter --api-key YOUR_KEY
discovery config --model deepseek/deepseek-chat
discovery run --auto-submit

# Google: Gemini Flash at $0.003/paper
discovery config --provider gemini --api-key YOUR_KEY
discovery run --auto-submit
```

## Submitting Results

If not using `--auto-submit`:

```bash
# Preview what would be submitted
discovery submit --dry-run

# Submit as a PR
discovery submit
```

This creates a branch, commits your results to `submissions/`, and opens a PR.
If you don't have the repo cloned locally, it auto-clones to `~/.discovery/repo/`.

## Quality Requirements

Every submission goes through the **same automated checks**, every time.
No trust levels, no shortcuts. All checks pass → auto-merge. Any check fails → rejected.

### Layer 1: Schema Validation
- Required keys present, correct types
- At least 1 entity, 1 bridge tag, 1 tension, 1 provides, 1 requires
- Provides/requires are dicts (not strings), operations are snake_case
- No blocklisted bridge tags (domain nouns, statistical terms)
- Relation mechanisms are 20+ characters

### Layer 2: Quality Gate
- **Grounding check:** Extracted entities must actually appear in the paper text. Fabricated data gets caught here.
- **Honeypot check:** Some papers have reference extractions. If your output diverges significantly, the submission is blocked.
- **Statistical anomaly detection:** Catches suspiciously minimal outputs, hallucination dumps, uniform structure (copy-paste).
- **Template abuse detection:** Structural fingerprinting catches mass-generated fake extractions.
- **Cross-submission dedup:** Duplicate paper IDs or identical structures across results = blocked.

### Layer 3: Duplicate Prevention
- Papers already in `processed_papers.jsonl` are blocked
- Duplicate paper IDs within a single batch are blocked

## How Duplicate Prevention Works

The system uses `processed_papers.jsonl` in the repo root as a shared tracking file:
- When a PR is merged, CI auto-appends the paper IDs to this file
- When you run `discovery run`, it fetches this file from GitHub
- Papers already in the file are skipped
- This prevents contributors from wasting time on already-processed papers

## Tips

- **Cheaper models work fine.** Gemini Flash and DeepSeek V3 produce valid schema at 1/10th the cost of Sonnet.
- **Full text > abstract.** Papers with full text produce richer extractions. PMC OA and arXiv have free full text.
- **Run overnight.** Set `discovery run --count 100 --auto-submit` before bed. It'll process and submit automatically.
- **Check validation failures.** If a paper fails validation, it's usually a model issue (bad JSON structure). The normalizer fixes most common issues automatically.
- **Source-specific runs.** Use `--source arxiv` or `--source pmc` to focus on a single database.
