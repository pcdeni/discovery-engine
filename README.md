# Discovery Engine

Distributed scientific paper extraction for cross-domain discovery.

Autonomous agents continuously extract structured knowledge from scientific papers and patents, building a public cross-domain discovery graph. Each paper is decomposed into:

- **Layer 1 (Facts):** Entities, properties, relations — what the paper found
- **Layer 2 (Connections):** Bridge tags, provides/requires interface, unsolved tensions — how it connects to other fields

Papers connect when one's `provides` matches another's `requires`, enabling cross-domain discovery.

## Quick Start

```bash
# Install
pip install -e ".[anthropic]"   # or: .[openai], .[gemini], .[all]

# Configure
discovery config --provider anthropic --api-key sk-ant-...
discovery config --github-user your-username

# Run — discovers papers automatically, no setup needed
discovery run --count 5           # test with 5 papers
discovery run --auto-submit       # run forever, auto-submit PRs
```

That's it. The agent will:
1. Query arXiv, PubMed, OpenAlex, OSTI for recent papers
2. Check which ones are already processed (via shared tracking file)
3. Fetch text, extract with your LLM, validate, save
4. Submit PRs automatically when batch is full

## How It Works

```
discover papers → fetch text → LLM extraction → validate → save → submit PR → merge → HuggingFace
      ↑                                                                              |
      └── processed_papers.jsonl ←── CI auto-updates on merge ←──────────────────────┘
```

1. **You run the loop** on your machine with your own LLM API key (~$0.002-0.03/paper)
2. **Papers discovered** in real-time from public APIs (no pre-built queue needed)
3. **Duplicates avoided** via shared `processed_papers.jsonl` on GitHub
4. **Results validate** locally against the schema
5. **Submit a PR** — GitHub Actions CI checks quality
6. **Merge pushes to HuggingFace** — results join the public dataset
7. **Tracking file auto-updates** — next contributor sees your papers as done

## Commands

| Command | What it does |
|---------|-------------|
| `discovery run` | Run the autonomous extraction loop |
| `discovery run --count 50` | Stop after 50 papers |
| `discovery run --source arxiv` | Only arXiv papers |
| `discovery run --auto-submit` | Auto-create PRs when batch is full |
| `discovery run --dry-run` | Preview papers without extracting |
| `discovery submit` | Submit pending results as a PR |
| `discovery submit --dry-run` | Preview what would be submitted |
| `discovery validate path.json` | Validate an extraction result |
| `discovery status` | Show local + global progress |
| `discovery config --show` | Show current configuration |

## Supported LLM Providers

| Provider | Models | Cost/paper |
|----------|--------|-----------|
| Anthropic | Claude Sonnet 4 | ~$0.02 |
| Google | Gemini 2.5 Flash | ~$0.003 |
| OpenRouter | DeepSeek V3, Llama 3.3 70B, Qwen3 235B | ~$0.002-0.004 |
| OpenAI | GPT-4o | ~$0.02 |

## Paper Sources

| Source | Papers | Full Text | Access |
|--------|--------|-----------|--------|
| arXiv | 2.5M | LaTeX/PDF | Open |
| PMC OA | 7.2M | XML | Open |
| OSTI | 3.4M | Mixed | Open |
| OpenAlex | 250M+ | Abstract | Mixed |

## Architecture

See [DESIGN.md](DESIGN.md) for the complete system design, including:
- Two-layer extraction (facts + cross-domain abstraction)
- Paper discovery and tracking
- Quality assurance (schema validation, honeypots, consensus)
- Why not blockchain (and what we use instead)
- Scaling roadmap

## OpenClaw Skill

This project includes an [OpenClaw skill](skills/discovery-extract/SKILL.md) for automated extraction. Install it to let your OpenClaw agent process papers autonomously.

## Contributing

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for detailed contributor guide.

**Short version:** `pip install` → `discovery config` → `discovery run --auto-submit` → walk away.

## License

MIT
