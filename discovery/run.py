"""
Autonomous extraction loop — the "autoresearch" agent.

Start this and walk away. It will continuously:
1. Query scientific databases for recent papers (no pre-built index needed)
2. Check which papers are already processed (via GitHub tracking file)
3. Download text (full text or abstract)
4. Extract with LLM (using your API key)
5. Validate and normalize
6. Save to local batch
7. Auto-submit PR when batch is full
8. Loop forever (or until --count is reached)

Zero manual steps after initial config.

Usage:
    discovery run                       # run forever, open-access papers
    discovery run --count 50            # stop after 50 papers
    discovery run --source arxiv        # only arXiv papers
    discovery run --dry-run             # fetch + show papers, don't extract
    discovery run --auto-submit         # auto-create PRs when batch is full
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
from .discover import discover_papers, fetch_processed_ids

logger = logging.getLogger("discovery.run")


def run_loop(
    count: Optional[int] = None,
    tier: str = "open",
    source: Optional[str] = None,
    dry_run: bool = False,
    batch_size: Optional[int] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    auto_submit: bool = False,
    lookback_days: int = 30,
):
    """
    Main extraction loop. Discovers and processes papers until interrupted or count reached.

    Args:
        count: Stop after this many papers (None = forever)
        tier: Access tier filter ("open", "oa_link", "institutional", "all")
        source: Only papers from this source (None = round-robin all sources)
        dry_run: If True, discover and display papers but don't extract
        batch_size: Papers per submission batch (default from config)
        provider: LLM provider override
        model: Model override
        auto_submit: If True, auto-create PR when batch is full
        lookback_days: How far back to search for papers (default 30 days)
    """
    config.ensure_dirs()
    batch_size = batch_size or config.get_batch_size()
    batch_dir = config.BATCH_DIR
    progress_file = config.PROGRESS_FILE

    # Load local progress (papers this machine has already tried)
    local_processed = _load_processed_ids(progress_file)

    # Fetch global processed list from GitHub (papers anyone has already done)
    logger.info("Fetching global processed paper list from GitHub...")
    global_processed = fetch_processed_ids()

    # Merge: skip anything done locally OR globally
    all_processed = local_processed | global_processed

    papers_done = 0
    papers_failed = 0
    batch_count = len(list(batch_dir.glob("*.json")))  # resume pending batch

    logger.info("=" * 60)
    logger.info("Discovery Engine — Autonomous Extraction Loop")
    logger.info(f"Provider: {provider or config.get_provider()}")
    logger.info(f"Model: {model or config.get_model()}")
    logger.info(f"Source: {source or 'all sources'}")
    logger.info(f"Batch size: {batch_size}")
    logger.info(f"Already processed (local): {len(local_processed)}")
    logger.info(f"Already processed (global): {len(global_processed)}")
    logger.info(f"Pending in batch: {batch_count}")
    logger.info(f"Auto-submit: {'yes' if auto_submit else 'no'}")
    if count:
        logger.info(f"Target: {count} papers")
    else:
        logger.info("Target: run forever (Ctrl+C to stop)")
    logger.info("=" * 60)

    # Main loop: discover → process → submit → repeat
    round_num = 0
    while True:
        round_num += 1

        if count and papers_done >= count:
            logger.info(f"Reached target of {count} papers. Stopping.")
            break

        # Discover fresh papers from APIs
        remaining = (count - papers_done) if count else 200
        logger.info(f"\n--- Round {round_num}: Discovering papers ---")

        paper_queue = discover_papers(
            source=source,
            max_per_source=min(remaining, 200),
            lookback_days=lookback_days,
            exclude=all_processed,
            shuffle=True,
        )

        if not paper_queue:
            logger.info("No new papers found. Waiting 5 minutes before retry...")
            time.sleep(300)
            # Refresh global processed list
            global_processed = fetch_processed_ids()
            all_processed = local_processed | global_processed
            continue

        logger.info(f"Papers to process this round: {len(paper_queue)}")

        for paper_entry in paper_queue:
            if count and papers_done >= count:
                break

            paper_id = paper_entry["paper_id"]

            if paper_id in all_processed:
                continue

            try:
                # Step 1: Fetch paper text
                logger.info(f"\n[{papers_done + 1}{'/' + str(count) if count else ''}] {paper_id}")

                paper = fetch_paper(paper_id)

                if not paper.text:
                    logger.warning(f"  No text available. Skipping.")
                    _log_progress(progress_file, paper_id, "skip", "no_text")
                    all_processed.add(paper_id)
                    local_processed.add(paper_id)
                    continue

                if len(paper.text) < 500:
                    logger.warning(f"  Text too short ({len(paper.text)} chars, need 500+). Skipping.")
                    _log_progress(progress_file, paper_id, "skip", "text_too_short")
                    all_processed.add(paper_id)
                    local_processed.add(paper_id)
                    continue

                logger.info(f"  Title: {paper.title[:80]}")
                logger.info(f"  Text: {len(paper.text)} chars ({paper.text_source})")

                if dry_run:
                    logger.info(f"  [DRY RUN] Would extract {paper_id}")
                    all_processed.add(paper_id)
                    local_processed.add(paper_id)
                    papers_done += 1
                    continue

                # Step 2: Extract with LLM (retry on validation failure)
                max_attempts = 3
                result = None
                issues = None

                for attempt in range(1, max_attempts + 1):
                    logger.info(f"  Extracting with LLM...{f' (attempt {attempt})' if attempt > 1 else ''}")
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
                    result["_meta"]["extracted_by"] = config.get_github_user() or "anonymous"
                    result["_meta"]["extracted_at"] = datetime.now(timezone.utc).isoformat()
                    result["_meta"]["title"] = paper.title[:200]

                    # Step 4: Validate
                    issues = validate_result(result)
                    if not issues:
                        break  # Success!

                    if attempt < max_attempts:
                        logger.warning(f"  Validation failed (attempt {attempt}), retrying...")
                        for issue in issues[:3]:
                            logger.warning(f"    - {issue}")
                        time.sleep(2)

                if issues:
                    logger.warning(f"  Validation issues after {max_attempts} attempts ({len(issues)}):")
                    for issue in issues[:5]:
                        logger.warning(f"    - {issue}")
                    _log_progress(progress_file, paper_id, "fail", f"validation: {issues[0]}")
                    all_processed.add(paper_id)
                    local_processed.add(paper_id)
                    papers_failed += 1
                    continue

                # Step 5: Save to batch
                safe_filename = paper_id.replace(":", "__").replace("/", "_") + ".json"
                result_path = batch_dir / safe_filename
                with open(result_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)

                _log_progress(progress_file, paper_id, "ok", f"{elapsed:.1f}s")
                all_processed.add(paper_id)
                local_processed.add(paper_id)
                papers_done += 1
                batch_count += 1

                logger.info(f"  Saved: {safe_filename} (batch: {batch_count}/{batch_size})")

                # Brief pause between papers to respect API rate limits
                time.sleep(5)

                # Step 6: Auto-submit batch if full
                if batch_count >= batch_size:
                    logger.info(f"\nBatch full ({batch_count} papers).")

                    if auto_submit:
                        logger.info("Auto-submitting PR...")
                        try:
                            from .submit import submit_batch
                            submit_result = submit_batch()
                            if submit_result.get("pr_url"):
                                logger.info(f"PR created: {submit_result['pr_url']}")
                                batch_count = 0
                            else:
                                logger.warning("Auto-submit failed. Will retry next batch.")
                        except Exception as e:
                            logger.error(f"Auto-submit error: {e}")
                    else:
                        logger.info("Run 'discovery submit' to create a PR.")
                        logger.info("Or use --auto-submit to do this automatically.")

            except ExtractionError as e:
                logger.error(f"  Extraction error: {e}")
                _log_progress(progress_file, paper_id, "fail", str(e)[:200])
                all_processed.add(paper_id)
                local_processed.add(paper_id)
                papers_failed += 1

            except KeyboardInterrupt:
                logger.info("\nInterrupted by user.")
                break

            except Exception as e:
                logger.error(f"  Unexpected error: {type(e).__name__}: {e}")
                _log_progress(progress_file, paper_id, "fail", f"{type(e).__name__}: {str(e)[:150]}")
                all_processed.add(paper_id)
                local_processed.add(paper_id)
                papers_failed += 1

        else:
            # Queue exhausted without break — continue to next discovery round
            if not count:
                logger.info("Queue exhausted. Discovering more papers...")
                # Refresh global processed
                global_processed = fetch_processed_ids()
                all_processed = local_processed | global_processed
                continue

        # If we got here via break (KeyboardInterrupt or count reached), stop
        break

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"Session complete:")
    logger.info(f"  Extracted: {papers_done}")
    logger.info(f"  Failed: {papers_failed}")
    logger.info(f"  Pending in batch: {batch_count}")
    logger.info(f"  Total processed (local): {len(local_processed)}")
    if batch_count > 0:
        logger.info(f"  Run 'discovery submit' to submit {batch_count} pending results.")
    logger.info("=" * 60)


def _load_processed_ids(progress_file: Path) -> set:
    """Load set of already-processed paper IDs from local progress file."""
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
    ids.discard("")
    return ids


def _log_progress(progress_file: Path, paper_id: str, status: str, detail: str = ""):
    """Append a progress entry to the local progress file."""
    entry = {
        "paper_id": paper_id,
        "status": status,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(progress_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
