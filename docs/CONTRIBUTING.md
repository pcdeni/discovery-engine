# Contributing to Discovery Engine

## Prerequisites

- Python 3.10+
- An LLM API key (any of: Anthropic, OpenRouter, Gemini, OpenAI)
- Git + GitHub CLI (`gh`) for submitting PRs

## Setup

```bash
# Clone and install
git clone https://github.com/discovery-engine/discovery-engine
cd discovery-engine
pip install -e ".[anthropic]"   # or your preferred provider

# Configure
discovery config --provider anthropic --api-key sk-ant-YOUR_KEY
discovery config --github-user YOUR_GITHUB_USERNAME
```

## Running Extractions

### Basic usage (recommended for first-time contributors)

```bash
# Extract 5 papers to test your setup
discovery run --count 5

# Check results
discovery status

# Validate your batch
discovery validate ~/.discovery/data/batch/
```

### Autonomous mode (walk away and let it run)

```bash
# Run forever, processing open-access papers
discovery run

# Or with options:
discovery run --source arxiv --model claude-sonnet-4-20250514
```

### What happens during extraction

1. The CLI fetches an unclaimed paper from the index
2. Downloads full text (if available) or abstract
3. Sends it through the combined extraction prompt via your LLM
4. Normalizes the output (fixes snake_case, restructures if needed)
5. Validates against the schema
6. Saves to your local batch (~/.discovery/data/batch/)

## Submitting Results

When you have results ready:

```bash
# Preview what would be submitted
discovery submit --dry-run

# Submit as a PR
discovery submit
```

This creates a branch, commits your results to `submissions/`, and opens a PR.

## Quality Requirements

Every submission must pass:

- **Schema validation:** Required keys present, correct types
- **Non-empty core fields:** At least 1 entity, 1 bridge tag, 1 tension, 1 provides, 1 requires
- **Format compliance:** Provides/requires are dicts (not strings), operations are snake_case
- **Bridge tag quality:** No domain nouns (graphene, insulin), no statistical terms (p-value)
- **Mechanism depth:** Relation mechanisms are 20+ characters (not just "enables")

## Trust Levels

- **New contributors (first 10 PRs):** Manual review by maintainer
- **Established (10-50 PRs):** Auto-merge if CI passes, spot-checked weekly
- **Trusted (50+ PRs):** Full auto-merge

## Tips

- **Cheaper models work fine.** Gemini Flash and DeepSeek V3 produce valid schema at 1/10th the cost of Sonnet.
- **Full text > abstract.** Papers with full text produce richer extractions. PMC OA and arXiv have free full text.
- **Run overnight.** Set `discovery run --count 100` before bed, submit in the morning.
- **Check validation failures.** If a paper fails validation, it's usually a model issue (bad JSON structure). The normalizer fixes most common issues automatically.
