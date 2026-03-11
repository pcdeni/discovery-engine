# Honeypot Reference Extractions

This directory contains reference extractions for known-answer papers.
These are used by the automated quality gate to verify contributor submissions.

## How it works

1. Certain papers are pre-extracted by the maintainer and stored here
2. When a contributor submits an extraction for one of these papers, CI
   compares their output against the reference
3. Significant divergence flags the submission for review
4. This catches:
   - Fabricated data (made-up entities that don't match the paper)
   - Template abuse (same extraction pasted across different papers)
   - Low-quality models that produce garbage

## File format

Files are named `{source}__{id}.json` (same as regular extractions).
They contain the standard extraction schema with verified-correct data.

## Adding honeypots

Run a reference extraction and verify it manually:

```bash
discovery run --count 1 --source arxiv  # extract one paper
# Manually review the output
cp ~/.discovery/data/batch/arxiv__XXXX.json honeypots/
```

Aim for 5-10% of the paper queue to be honeypots. Contributors don't
know which papers are honeypots.
