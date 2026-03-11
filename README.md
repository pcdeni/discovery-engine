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

# Run the autonomous extraction loop
discovery run                     # run forever, open-access papers
discovery run --count 10          # stop after 10 papers
discovery run --source arxiv      # only arXiv papers
discovery run --dry-run           # preview without extracting

# Submit results
discovery submit

# Check progress
discovery status
```

## How It Works

```
fetch paper → LLM extraction → validate → save → submit PR → merge → HuggingFace
```

1. **You run the loop** on your machine with your own LLM API key (~$0.01-0.03/paper)
2. **Results validate** locally against the schema (entities, bridge tags, provides/requires)
3. **Submit a PR** — GitHub Actions CI checks quality
4. **Merge pushes to HuggingFace** — results join the public dataset

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
| Google Patents | 14.8M | Full text | Open |

## Architecture

See [DESIGN.md](DESIGN.md) for the complete system design, including:
- Two-layer extraction (facts + cross-domain abstraction)
- Paper discovery and tracking
- Quality assurance (schema validation, honeypots, consensus)
- Why not blockchain (and what we use instead)
- Scaling roadmap

## Contributing

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for detailed contributor guide.

**Short version:** Fork → `discovery run --count 10` → `discovery submit` → PR reviewed → merged.

## License

MIT
