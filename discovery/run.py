"""
Autonomous extraction loop — the "autoresearch" agent.

Start this and walk away. It will continuously:
1. Fetch unclaimed papers from the paper index
2. Download text (full text or abstract)
3. Extract with LLM (using your API key)
4. Validate and normalize
5. Save to local batch
6. Submit PR when batch is full

Usage:
    discovery run                       # run forever, open-access papers
    discovery run --count 50            # stop after 50 papers
    discovery run --tier institutional   # include paywalled papers
    discovery run --source arxiv         # only arXiv papers
    discovery run --dry-run             # fetch + show papers, don't extract
"""

import json
import time
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config
from .extract import extract_paper, ExtractionError
from .normalize import normalize_result
from .validate import validate_result
from .sources import fetch_paper, Paper

logger = logging.getLogger("discovery.run")


def run_loop(
    count: Optional[int] = None,
    tier: str = "open",
    source: Optional[str] = None,
    dry_run: bool = False,
    batch_size: Optional[int] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
):
    """
    Main extraction loop. Processes papers until interrupted or count reached.

    Args:
        count: Stop after this many papers (None = forever)
        tier: Access tier filter ("open", "oa_link", "institutional", "all")
        source: Only papers from this source (None = round-robin)
        dry_run: If True, fetch and display papers but don't extract
        batch_size: Papers per submission batch (default from config)
        provider: LLM provider override
        model: Model override
    """
    config.ensure_dirs()
    batch_size = batch_size or config.get_batch_size()
    batch_dir = config.BATCH_DIR
    progress_file = config.PROGRESS_FILE

    # Load or create progress tracker
    processed_ids = _load_processed_ids(progress_file)

    papers_done = 0
    papers_failed = 0
    batch_count = 0

    logger.info("=" * 60)
    logger.info("Discovery Engine — Autonomous Extraction Loop")
    logger.info(f"Provider: {provider or config.get_provider()}")
    logger.info(f"Model: {model or config.get_model()}")
    logger.info(f"Tier: {tier}")
    logger.info(f"Source: {source or 'round-robin'}")
    logger.info(f"Batch size: {batch_size}")
    logger.info(f"Already processed: {len(processed_ids)}")
    if count:
        logger.info(f"Target: {count} papers")
    else:
        logger.info("Target: run forever (Ctrl+C to stop)")
    logger.info("=" * 60)

    # Get the list of papers to process
    paper_queue = _build_paper_queue(tier=tier, source=source, exclude=processed_ids)

    if not paper_queue:
        logger.warning("No unclaimed papers found. Try a different --tier or --source.")
        return

    logger.info(f"Papers in queue: {len(paper_queue)}")

    for paper_entry in paper_queue:
        if count and papers_done >= count:
            logger.info(f"Reached target of {count} papers. Stopping.")
            break

        paper_id = paper_entry["paper_id"]

        if paper_id in processed_ids:
            continue

        try:
            # Step 1: Fetch paper text
            logger.info(f"[{papers_done + 1}{'/' + str(count) if count else ''}] Fetching: {paper_id}")

            paper = fetch_paper(paper_id)

            if not paper.text:
                logger.warning(f"  No text available for {paper_id}. Skipping.")
                _log_progress(progress_file, paper_id, "skip", "no_text")
                processed_ids.add(paper_id)
                continue

            if len(paper.text) < 100:
                logger.warning(f"  Text too short ({len(paper.text)} chars) for {paper_id}. Skipping.")
                _log_progress(progress_file, paper_id, "skip", "text_too_short")
                processed_ids.add(paper_id)
                continue

            logger.info(f"  Title: {paper.title[:80]}")
            logger.info(f"  Text: {len(paper.text)} chars ({paper.text_source})")

            if dry_run:
                logger.info(f"  [DRY RUN] Would extract {paper_id}")
                processed_ids.add(paper_id)
                papers_done += 1
                continue

            # Step 2: Extract with LLM
            logger.info(f"  Extracting with LLM...")
            t0 = time.time()

            result = extract_paper(
                paper.text,
                provider=provider,
                model=model,
            )

            elapsed = time.time() - t0
            logger.info(f"  Extraction completed in {elapsed:.1f}s")

            # Step 3: Normalize
            result = normalize_result(result)

            # Add metadata
            result["_meta"] = result.get("_meta", {})
            result["_meta"]["paper_id"] = paper_id
            result["_meta"]["source"] = paper.source
            result["_meta"]["text_source"] = paper.text_source
            result["_meta"]["extracted_by"] = config.get_github_user() or "unknown"
            result["_meta"]["extracted_at"] = datetime.now(timezone.utc).isoformat()
            result["_meta"]["title"] = paper.title[:200]

            # Step 4: Validate
            issues = validate_result(result)
            if issues:
                logger.warning(f"  Validation issues ({len(issues)}):")
                for issue in issues[:5]:
                    logger.warning(f"    - {issue}")
                _log_progress(progress_file, paper_id, "fail", f"validation: {issues[0]}")
                processed_ids.add(paper_id)
                papers_failed += 1
                continue

            # Step 5: Save to batch
            safe_filename = paper_id.replace(":", "__").replace("/", "_") + ".json"
            result_path = batch_dir / safe_filename
            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            _log_progress(progress_file, paper_id, "ok", f"{elapsed:.1f}s")
            processed_ids.add(paper_id)
            papers_done += 1
            batch_count += 1

            logger.info(f"  Saved: {safe_filename} (batch: {batch_count}/{batch_size})")

            # Step 6: Submit batch if full
            if batch_count >= batch_size:
                logger.info(f"Batch full ({batch_count} papers). Ready for submission.")
                logger.info(f"Run: discovery submit")
                batch_count = 0
                # In fully autonomous mode, we'd auto-submit here.
                # For safety, require manual submission until contributor is trusted.

        except ExtractionError as e:
            logger.error(f"  Extraction error: {e}")
            _log_progress(progress_file, paper_id, "fail", str(e)[:200])
            processed_ids.add(paper_id)
            papers_failed += 1

        except KeyboardInterrupt:
            logger.info("\nInterrupted by user.")
            break

        except Exception as e:
            logger.error(f"  Unexpected error: {type(e).__name__}: {e}")
            _log_progress(progress_file, paper_id, "fail", f"{type(e).__name__}: {str(e)[:150]}")
            processed_ids.add(paper_id)
            papers_failed += 1

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"Session complete:")
    logger.info(f"  Processed: {papers_done}")
    logger.info(f"  Failed: {papers_failed}")
    logger.info(f"  Pending in batch: {batch_count}")
    logger.info(f"  Total lifetime: {len(processed_ids)}")
    if batch_count > 0:
        logger.info(f"  Run 'discovery submit' to submit {batch_count} pending results.")
    logger.info("=" * 60)


def _build_paper_queue(
    tier: str = "open",
    source: Optional[str] = None,
    exclude: Optional[set] = None,
) -> list[dict]:
    """
    Build a queue of papers to process.

    In the full system, this pulls from the HuggingFace paper index.
    For now, supports:
    - Local JSONL file (paper_index.jsonl) if present
    - Built-in sample papers for testing
    """
    exclude = exclude or set()
    queue = []

    # Try loading from local paper index
    local_index = config.DATA_DIR / "paper_index.jsonl"
    if local_index.exists():
        with open(local_index, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                pid = entry.get("paper_id", "")
                if pid in exclude:
                    continue
                if source and entry.get("source") != source:
                    continue
                if tier != "all":
                    entry_tier = entry.get("access_tier", "open")
                    if tier == "open" and entry_tier not in ("open", "oa_link"):
                        continue
                    if tier == "institutional" and entry_tier == "abstract_only":
                        continue
                queue.append(entry)
        return queue

    # Try loading from HuggingFace
    try:
        queue = _fetch_hf_paper_index(tier=tier, source=source, exclude=exclude)
        if queue:
            return queue
    except Exception as e:
        logger.warning(f"Could not fetch HuggingFace paper index: {e}")

    # Fallback: built-in sample papers for testing
    logger.info("No paper index found. Using built-in sample papers for testing.")
    sample_papers = [
        {"paper_id": "arxiv:2401.12345", "source": "arxiv", "access_tier": "open"},
        {"paper_id": "pmc:41780551", "source": "pmc", "access_tier": "open"},
        {"paper_id": "pmc:40987604", "source": "pmc", "access_tier": "open"},
        {"paper_id": "osti:1961631", "source": "osti", "access_tier": "open"},
    ]
    return [p for p in sample_papers
            if p["paper_id"] not in exclude
            and (not source or p["source"] == source)]


def _fetch_hf_paper_index(
    tier: str = "open",
    source: Optional[str] = None,
    exclude: Optional[set] = None,
    limit: int = 1000,
) -> list[dict]:
    """
    Fetch paper index from HuggingFace dataset.

    Requires: pip install huggingface_hub datasets
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("Install datasets: pip install datasets")

    dataset = load_dataset(
        config.DEFAULT_HF_INDEX,
        split="train",
        streaming=True,
    )

    exclude = exclude or set()
    queue = []

    for entry in dataset:
        if len(queue) >= limit:
            break

        pid = entry.get("paper_id", "")
        if pid in exclude:
            continue
        if entry.get("status") != "new":
            continue
        if source and entry.get("source") != source:
            continue

        entry_tier = entry.get("access_tier", "open")
        if tier == "open" and entry_tier not in ("open", "oa_link"):
            continue

        queue.append(dict(entry))

    return queue


def _load_processed_ids(progress_file: Path) -> set:
    """Load set of already-processed paper IDs from progress file."""
    ids = set()
    if progress_file.exists():
        with open(progress_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ids.add(entry.get("paper_id", ""))
                except json.JSONDecodeError:
                    continue
    return ids


def _log_progress(progress_file: Path, paper_id: str, status: str, detail: str = ""):
    """Append a progress entry."""
    entry = {
        "paper_id": paper_id,
        "status": status,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(progress_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
