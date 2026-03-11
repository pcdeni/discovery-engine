"""
Discovery Engine CLI — command-line interface.

Commands:
    discovery run          Run the autonomous extraction loop
    discovery submit       Submit pending results as a PR
    discovery validate     Validate extraction result files
    discovery config       Configure API keys and preferences
    discovery status       Show current progress and pending batch
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__, config


def main():
    parser = argparse.ArgumentParser(
        prog="discovery",
        description="Discovery Engine — distributed scientific paper extraction",
    )
    parser.add_argument("--version", action="version", version=f"discovery {__version__}")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── run ─────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser("run", help="Run autonomous extraction loop")
    run_parser.add_argument("--count", type=int, help="Stop after N papers (default: run forever)")
    run_parser.add_argument("--tier", default="open", choices=["open", "institutional", "all"],
                           help="Access tier filter (default: open)")
    run_parser.add_argument("--source", help="Only papers from this source (arxiv, pmc, openalex, osti)")
    run_parser.add_argument("--batch-size", type=int, help="Papers per submission batch")
    run_parser.add_argument("--provider", help="LLM provider (anthropic, openrouter, gemini, openai)")
    run_parser.add_argument("--model", help="LLM model name")
    run_parser.add_argument("--dry-run", action="store_true", help="Discover papers but don't extract")
    run_parser.add_argument("--auto-submit", action="store_true",
                           help="Auto-create PR when batch is full")
    run_parser.add_argument("--lookback-days", type=int, default=30,
                           help="How far back to search for papers (default: 30)")

    # ── submit ──────────────────────────────────────────────────────
    submit_parser = subparsers.add_parser("submit", help="Submit pending results as PR")
    submit_parser.add_argument("--dry-run", action="store_true", help="Show what would be submitted")
    submit_parser.add_argument("--repo-path", help="Path to discovery-engine repo")

    # ── validate ────────────────────────────────────────────────────
    validate_parser = subparsers.add_parser("validate", help="Validate extraction results")
    validate_parser.add_argument("path", help="JSON file or directory to validate")
    validate_parser.add_argument("--strict", action="store_true", help="Enable quality checks")
    validate_parser.add_argument("--normalize", action="store_true",
                                help="Auto-fix common issues before validating")

    # ── config ──────────────────────────────────────────────────────
    config_parser = subparsers.add_parser("config", help="Configure API keys and preferences")
    config_parser.add_argument("--provider", choices=["anthropic", "openrouter", "gemini", "openai", "local"],
                              help="LLM provider ('local' for ollama/vllm/llama.cpp)")
    config_parser.add_argument("--api-key", help="API key for the provider (use 'none' for local)")
    config_parser.add_argument("--model", help="Model name (provider-specific)")
    config_parser.add_argument("--base-url", help="Custom API base URL (for local LLMs, e.g. http://localhost:11434/v1)")
    config_parser.add_argument("--github-user", help="Your GitHub username")
    config_parser.add_argument("--batch-size", type=int, help="Papers per submission batch")
    config_parser.add_argument("--show", action="store_true", help="Show current config")

    # ── status ──────────────────────────────────────────────────────
    status_parser = subparsers.add_parser("status", help="Show progress and pending batch")

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.command:
        parser.print_help()
        return

    # Dispatch
    if args.command == "run":
        cmd_run(args)
    elif args.command == "submit":
        cmd_submit(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "config":
        cmd_config(args)
    elif args.command == "status":
        cmd_status(args)


def cmd_run(args):
    from .run import run_loop
    run_loop(
        count=args.count,
        tier=args.tier,
        source=args.source,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        provider=args.provider,
        model=args.model,
        auto_submit=args.auto_submit,
        lookback_days=args.lookback_days,
    )


def cmd_submit(args):
    from .submit import submit_batch
    result = submit_batch(dry_run=args.dry_run, repo_path=args.repo_path)
    if result.get("pr_url"):
        print(f"\nPR created: {result['pr_url']}")
    elif result.get("submitted", 0) == 0 and not result.get("would_submit"):
        print("Nothing to submit.")


def cmd_validate(args):
    from .validate import validate_file, validate_batch
    from .normalize import normalize_file_inplace

    target = Path(args.path)

    if args.normalize and target.is_file():
        changes = normalize_file_inplace(target)
        if changes:
            print(f"Normalized: {len(changes)} auto-fix(es) applied")
            for c in changes:
                print(f"  {c}")

    if target.is_file():
        is_valid, issues = validate_file(target, strict=args.strict)
        if is_valid:
            print(f"PASS: {target.name}")
        else:
            print(f"FAIL: {target.name}: {len(issues)} issue(s)")
            for issue in issues:
                print(f"  - {issue}")
            sys.exit(1)

    elif target.is_dir():
        if args.normalize:
            for fp in target.glob("*.json"):
                normalize_file_inplace(fp)

        results = validate_batch(target, strict=args.strict)
        print(f"Validated {results['total']} files: {results['valid']} valid, {results['invalid']} invalid")
        if results["files"]:
            for fname, issues in results["files"].items():
                print(f"  FAIL: {fname}: {issues[0]}")
        sys.exit(0 if results["invalid"] == 0 else 1)

    else:
        print(f"Error: {target} not found")
        sys.exit(1)


def cmd_config(args):
    if args.show:
        cfg = config.load_config()
        if not cfg:
            print("No configuration found. Run: discovery config --provider <provider> --api-key <key>")
            return
        for k, v in cfg.items():
            # Mask API keys
            if "key" in k.lower() and v:
                v = v[:8] + "..." + v[-4:] if len(v) > 12 else "***"
            print(f"  {k}: {v}")
        return

    cfg = config.load_config()

    if args.provider:
        # 'local' is sugar for 'openai' with a local base URL
        if args.provider == "local":
            cfg["provider"] = "openai"
            if not args.base_url:
                cfg["base_url"] = "http://localhost:11434/v1"  # ollama default
            if not args.api_key:
                cfg["openai_api_key"] = "not-needed"
            if not args.model:
                cfg["model"] = "llama3.1"  # sensible default
        else:
            cfg["provider"] = args.provider
    if args.api_key:
        provider = args.provider or cfg.get("provider", "anthropic")
        if provider == "local":
            provider = "openai"
        cfg[f"{provider}_api_key"] = args.api_key
    if args.model:
        cfg["model"] = args.model
    if args.base_url:
        cfg["base_url"] = args.base_url
    if args.github_user:
        cfg["github_user"] = args.github_user
    if args.batch_size:
        cfg["batch_size"] = args.batch_size

    config.save_config(cfg)
    print(f"Config saved to {config.CONFIG_FILE}")

    # Show summary
    provider = cfg.get("provider", "anthropic")
    key = cfg.get(f"{provider}_api_key", "")
    key_display = (key[:8] + "...") if key else "NOT SET"
    base_url = cfg.get("base_url", "")
    print(f"  Provider: {provider}")
    print(f"  API key: {key_display}")
    print(f"  Model: {cfg.get('model', config.get_model())}")
    if base_url:
        print(f"  Base URL: {base_url}")
    print(f"  GitHub user: {cfg.get('github_user', 'NOT SET')}")


def cmd_status(args):
    config.ensure_dirs()

    # Global processed (from GitHub)
    print("Checking global state...")
    from .discover import fetch_processed_ids
    global_ids = fetch_processed_ids()
    print(f"Papers processed globally: {len(global_ids)}")

    # Batch status
    batch_files = list(config.BATCH_DIR.glob("*.json"))
    print(f"\nPending in batch: {len(batch_files)}")
    if batch_files:
        sources = {}
        for f in batch_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            src = data.get("_meta", {}).get("source", "?")
            sources[src] = sources.get(src, 0) + 1
        for src, cnt in sorted(sources.items()):
            print(f"  {src}: {cnt}")

    # Local progress
    if config.PROGRESS_FILE.exists():
        with open(config.PROGRESS_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        statuses = {}
        for line in lines:
            try:
                entry = json.loads(line.strip())
                s = entry.get("status", "?")
                statuses[s] = statuses.get(s, 0) + 1
            except json.JSONDecodeError:
                continue
        print(f"\nLocal progress: {len(lines)} papers")
        for s, cnt in sorted(statuses.items()):
            print(f"  {s}: {cnt}")
    else:
        print("\nNo local extraction history yet.")

    # Config
    cfg = config.load_config()
    if cfg:
        print(f"\nConfig:")
        print(f"  Provider: {cfg.get('provider', 'not set')}")
        print(f"  Model: {cfg.get('model', 'default')}")
        print(f"  Batch size: {cfg.get('batch_size', config.get_batch_size())}")
    else:
        print("\nNo config found. Run: discovery config --provider anthropic --api-key YOUR_KEY")


if __name__ == "__main__":
    main()
