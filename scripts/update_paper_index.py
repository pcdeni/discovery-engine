"""
Paper Index Updater — discovers new papers from scientific databases.

Run by GitHub Actions weekly cron, or manually:
    python scripts/update_paper_index.py

Queries:
- arXiv (OAI-PMH, last 7 days)
- PubMed/PMC (E-utilities, last 7 days)
- OpenAlex (API, last 7 days)
- OSTI (API, last 7 days)

Appends new paper IDs to the paper_index on HuggingFace.
"""

import json
import os
import re
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("update_paper_index")

# HuggingFace settings
HF_REPO = os.environ.get("HF_INDEX_REPO", "discovery-engine/paper-index")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# How many days back to look
LOOKBACK_DAYS = 8  # slight overlap to avoid missing papers

# API delay between requests
API_DELAY = 1.0


def main():
    """Main entry point."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")

    logger.info(f"Discovering papers added since {cutoff_str}")

    new_papers = []

    # Fetch from each source
    sources = [
        ("arxiv", fetch_arxiv_new),
        ("pmc", fetch_pmc_new),
        ("openalex", fetch_openalex_new),
        ("osti", fetch_osti_new),
    ]

    for source_name, fetcher in sources:
        try:
            papers = fetcher(cutoff_str)
            logger.info(f"  {source_name}: {len(papers)} new papers")
            new_papers.extend(papers)
        except Exception as e:
            logger.error(f"  {source_name}: FAILED — {e}")

    logger.info(f"Total new papers discovered: {len(new_papers)}")

    if not new_papers:
        logger.info("No new papers found. Nothing to update.")
        return

    # Deduplicate
    seen = set()
    unique_papers = []
    for p in new_papers:
        if p["paper_id"] not in seen:
            seen.add(p["paper_id"])
            unique_papers.append(p)

    logger.info(f"After dedup: {len(unique_papers)} unique papers")

    # Save locally (JSONL)
    output_path = "paper_index_update.jsonl"
    with open(output_path, "w", encoding="utf-8") as f:
        for p in unique_papers:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    logger.info(f"Saved to {output_path}")

    # Push to HuggingFace if token available
    if HF_TOKEN:
        try:
            push_to_huggingface(unique_papers)
        except Exception as e:
            logger.error(f"HuggingFace push failed: {e}")
    else:
        logger.info("No HF_TOKEN set. Skipping HuggingFace push. Results saved locally.")


def fetch_arxiv_new(cutoff_date: str, max_results: int = 5000) -> list[dict]:
    """Fetch recent arXiv papers via the API (search by date)."""
    papers = []
    # arXiv API search by submittedDate
    url = "http://export.arxiv.org/api/query"
    params = {
        "search_query": f"submittedDate:[{cutoff_date.replace('-', '')} TO *]",
        "start": 0,
        "max_results": min(max_results, 2000),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    resp = httpx.get(url, params=params, timeout=60)
    resp.raise_for_status()

    # Parse XML entries
    entries = re.findall(r"<entry>(.*?)</entry>", resp.text, re.DOTALL)

    for entry in entries:
        arxiv_id_match = re.search(r"<id>http://arxiv.org/abs/(.+?)</id>", entry)
        title_match = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)

        if arxiv_id_match:
            arxiv_id = arxiv_id_match.group(1).strip()
            title = title_match.group(1).strip() if title_match else ""
            title = re.sub(r"\s+", " ", title)

            papers.append({
                "paper_id": f"arxiv:{arxiv_id}",
                "source": "arxiv",
                "title": title[:200],
                "access_tier": "open",
                "status": "new",
                "date_added": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            })

    time.sleep(API_DELAY * 3)  # arXiv asks for 3s between requests
    return papers


def fetch_pmc_new(cutoff_date: str, max_results: int = 5000) -> list[dict]:
    """Fetch recent PubMed Central OA papers via E-utilities."""
    papers = []

    # Search for recent PMC OA papers
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "pmc",
        "term": f'"open access"[filter] AND ("{cutoff_date}"[PDAT] : "3000"[PDAT])',
        "retmax": min(max_results, 10000),
        "retmode": "json",
    }

    resp = httpx.get(search_url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    id_list = data.get("esearchresult", {}).get("idlist", [])

    # Fetch metadata in batches
    for i in range(0, len(id_list), 200):
        batch = id_list[i : i + 200]
        summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        params = {
            "db": "pmc",
            "id": ",".join(batch),
            "retmode": "json",
        }
        resp = httpx.get(summary_url, params=params, timeout=60)
        resp.raise_for_status()
        results = resp.json().get("result", {})

        for pmc_id in batch:
            info = results.get(str(pmc_id), {})
            title = info.get("title", "")

            papers.append({
                "paper_id": f"pmc:{pmc_id}",
                "source": "pmc",
                "title": title[:200],
                "access_tier": "open",
                "status": "new",
                "date_added": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            })

        time.sleep(API_DELAY)

    return papers


def fetch_openalex_new(cutoff_date: str, max_results: int = 5000) -> list[dict]:
    """Fetch recent OpenAlex works (OA only)."""
    papers = []
    cursor = "*"

    while len(papers) < max_results:
        url = "https://api.openalex.org/works"
        params = {
            "filter": f"from_updated_date:{cutoff_date},is_oa:true",
            "per_page": 200,
            "cursor": cursor,
            "select": "id,title,publication_year,open_access",
        }

        resp = httpx.get(url, params=params,
                        headers={"User-Agent": "discovery-engine/0.1"},
                        timeout=60)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            break

        for work in results:
            work_id = work.get("id", "").split("/")[-1]  # extract W-id
            if not work_id:
                continue

            title = work.get("title", "") or ""
            is_oa = work.get("open_access", {}).get("is_oa", False)

            papers.append({
                "paper_id": f"openalex:{work_id}",
                "source": "openalex",
                "title": title[:200],
                "access_tier": "open" if is_oa else "abstract_only",
                "status": "new",
                "date_added": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            })

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

        time.sleep(API_DELAY)

    return papers[:max_results]


def fetch_osti_new(cutoff_date: str, max_results: int = 2000) -> list[dict]:
    """Fetch recent OSTI records."""
    papers = []

    url = "https://www.osti.gov/api/v1/records"
    params = {
        "date_added": f"[{cutoff_date} TO *]",
        "rows": min(max_results, 1000),
        "page": 0,
    }

    resp = httpx.get(url, params=params,
                    headers={"Accept": "application/json"},
                    timeout=60)
    resp.raise_for_status()
    records = resp.json()

    if not isinstance(records, list):
        return papers

    for record in records:
        osti_id = record.get("osti_id", "")
        if not osti_id:
            continue

        title = record.get("title", "") or ""

        papers.append({
            "paper_id": f"osti:{osti_id}",
            "source": "osti",
            "title": title[:200],
            "access_tier": "open",
            "status": "new",
            "date_added": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        })

    return papers


def push_to_huggingface(papers: list[dict]):
    """Push new paper entries to HuggingFace dataset."""
    from huggingface_hub import HfApi

    api = HfApi(token=HF_TOKEN)

    # Create JSONL content
    content = "\n".join(json.dumps(p, ensure_ascii=False) for p in papers) + "\n"

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    api.upload_file(
        path_or_fileobj=content.encode("utf-8"),
        path_in_repo=f"updates/{timestamp}_new_papers.jsonl",
        repo_id=HF_REPO,
        repo_type="dataset",
        commit_message=f"Add {len(papers)} new papers ({timestamp})",
    )

    logger.info(f"Pushed {len(papers)} papers to {HF_REPO}")


if __name__ == "__main__":
    main()
