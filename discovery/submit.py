"""
Batch submission — create a PR with extraction results.

After running `discovery run`, results accumulate in ~/.discovery/data/batch/.
This module creates a GitHub PR to submit them.

Usage:
    discovery submit              # submit all pending results
    discovery submit --dry-run    # show what would be submitted
"""

import json
import subprocess
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config

logger = logging.getLogger("discovery.submit")


def submit_batch(dry_run: bool = False, repo_path: Optional[str] = None) -> dict:
    """
    Submit pending extraction results as a GitHub PR.

    Steps:
    1. Collect all JSON files from ~/.discovery/data/batch/
    2. Copy them to the repo's submissions/ directory
    3. Create a branch, commit, push, create PR

    Returns:
        {"submitted": int, "pr_url": str} or {"submitted": 0} if nothing to submit
    """
    batch_dir = config.BATCH_DIR
    result_files = list(batch_dir.glob("*.json"))

    if not result_files:
        logger.info("No pending results to submit.")
        return {"submitted": 0}

    logger.info(f"Found {len(result_files)} result(s) to submit.")

    # Validate all files first
    from .validate import validate_file
    invalid = []
    for f in result_files:
        is_valid, issues = validate_file(f)
        if not is_valid:
            invalid.append((f.name, issues))

    if invalid:
        logger.error(f"{len(invalid)} file(s) failed validation:")
        for fname, issues in invalid:
            logger.error(f"  {fname}: {issues[0]}")
        logger.error("Fix validation issues before submitting.")
        return {"submitted": 0, "invalid": len(invalid)}

    if dry_run:
        logger.info("DRY RUN — would submit:")
        for f in result_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            meta = data.get("_meta", {})
            logger.info(f"  {f.name}: {meta.get('paper_id', '?')} ({meta.get('source', '?')})")
        return {"submitted": 0, "would_submit": len(result_files)}

    # Determine repo path
    repo_path = Path(repo_path) if repo_path else _find_repo_root()
    if not repo_path:
        logger.error(
            "Could not find discovery-engine repo. "
            "Either run from within the repo or specify --repo-path."
        )
        return {"submitted": 0}

    submissions_dir = repo_path / "submissions"
    submissions_dir.mkdir(exist_ok=True)

    # Create branch name
    user = config.get_github_user() or "anonymous"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch_name = f"contrib/{user}/{timestamp}"

    try:
        # Create branch
        _git(repo_path, "checkout", "-b", branch_name)

        # Copy files to submissions/
        for f in result_files:
            dest = submissions_dir / f.name
            shutil.copy2(f, dest)
            _git(repo_path, "add", str(dest.relative_to(repo_path)))

        # Commit
        msg = f"Add {len(result_files)} extraction result(s)\n\nContributor: {user}\nModel: {config.get_model()}"
        _git(repo_path, "commit", "-m", msg)

        # Push
        _git(repo_path, "push", "-u", "origin", branch_name)

        # Create PR using gh CLI
        pr_title = f"[extraction] {len(result_files)} papers by {user}"
        pr_body = _build_pr_body(result_files, user)

        result = subprocess.run(
            ["gh", "pr", "create",
             "--title", pr_title,
             "--body", pr_body,
             "--base", "main"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )

        pr_url = result.stdout.strip() if result.returncode == 0 else ""

        if pr_url:
            logger.info(f"PR created: {pr_url}")

            # Clean up: remove submitted files from batch
            for f in result_files:
                f.unlink()
            logger.info(f"Cleaned up {len(result_files)} files from batch directory.")
        else:
            logger.warning(f"PR creation may have failed: {result.stderr}")
            logger.info("Files kept in batch directory. Run 'discovery submit' to retry.")

        # Switch back to main
        _git(repo_path, "checkout", "main")

        return {"submitted": len(result_files), "pr_url": pr_url, "branch": branch_name}

    except Exception as e:
        logger.error(f"Submission failed: {e}")
        # Try to get back to main
        try:
            _git(repo_path, "checkout", "main")
        except Exception:
            pass
        return {"submitted": 0, "error": str(e)}


def _git(repo_path: Path, *args):
    """Run a git command in the repo."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def _find_repo_root() -> Optional[Path]:
    """Find the discovery-engine repo root (look for prompts/v_combined.txt)."""
    # Check common locations
    candidates = [
        Path.cwd(),
        Path.cwd().parent,
        Path.home() / "discovery-engine",
        Path.home() / "projects" / "discovery-engine",
    ]

    for candidate in candidates:
        if (candidate / "prompts" / "v_combined.txt").exists():
            return candidate
        if (candidate / "discovery" / "__init__.py").exists():
            return candidate

    return None


def _build_pr_body(result_files: list[Path], user: str) -> str:
    """Build a PR description summarizing the submission."""
    sources = {}
    models = set()
    text_sources = {"full_text": 0, "abstract": 0}

    for f in result_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        meta = data.get("_meta", {})
        src = meta.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
        models.add(meta.get("model", "unknown"))
        ts = meta.get("text_source", "unknown")
        if ts in text_sources:
            text_sources[ts] += 1

    lines = [
        f"## Extraction Submission",
        f"",
        f"**Contributor:** {user}",
        f"**Papers:** {len(result_files)}",
        f"**Model(s):** {', '.join(sorted(models))}",
        f"",
        f"### Sources",
    ]

    for src, cnt in sorted(sources.items()):
        lines.append(f"- {src}: {cnt}")

    lines.extend([
        f"",
        f"### Text Coverage",
        f"- Full text: {text_sources['full_text']}",
        f"- Abstract only: {text_sources['abstract']}",
        f"",
        f"---",
        f"*Submitted via `discovery submit`*",
    ])

    return "\n".join(lines)
