"""
Paper discovery — query scientific databases for papers to process.

This module queries real APIs in real-time to find papers. No pre-built
index needed. Each call returns a fresh batch of paper IDs from:
- arXiv (via API)
- PubMed Central (via E-utilities)
- OpenAlex (via API)
- OSTI (via API)

Usage:
    from discovery.discover import discover_papers
    papers = discover_papers(source="arxiv", max_per_source=100)
"""

import json
import re
import time
import random
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger("discovery.discover")

# Polite delay between API requests
API_DELAY = 1.0

# GitHub raw URL for tracking already-processed papers
PROCESSED_URL = "https://raw.githubusercontent.com/pcdeni/discovery-engine/main/processed_papers.jsonl"


def discover_papers(
    source: Optional[str] = None,
    max_per_source: int = 200,
    lookback_days: int = 30,
    exclude: Optional[set] = None,
    shuffle: bool = True,
) -> list[dict]:
    """
    Discover papers to process by querying scientific databases.

    Args:
        source: Specific source ("arxiv", "pmc", "openalex", "osti") or None for all
        max_per_source: Maximum papers to fetch per source
        lookback_days: How many days back to search
        exclude: Set of paper_ids to skip (already processed)
        shuffle: Randomize order (avoids contributors colliding on same papers)

    Returns:
        List of paper entries: [{"paper_id": "arxiv:...", "source": "arxiv", "title": "..."}]
    """
    exclude = exclude or set()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    all_sources = {
        "arxiv": _discover_arxiv,
        "pmc": _discover_pmc,
        "openalex": _discover_openalex,
        "osti": _discover_osti,
    }

    if source:
        sources = {source: all_sources[source]} if source in all_sources else {}
        if not sources:
            logger.error(f"Unknown source: {source}. Available: {list(all_sources.keys())}")
            return []
    else:
        sources = all_sources

    papers = []
    for name, fetcher in sources.items():
        try:
            logger.info(f"Discovering from {name}...")
            batch = fetcher(cutoff, max_results=max_per_source)
            # Filter out already-processed
            batch = [p for p in batch if p["paper_id"] not in exclude]
            logger.info(f"  {name}: {len(batch)} new papers")
            papers.extend(batch)
        except Exception as e:
            logger.warning(f"  {name}: failed — {e}")

    if shuffle:
        random.shuffle(papers)

    logger.info(f"Total papers discovered: {len(papers)}")
    return papers


def fetch_processed_ids() -> set:
    """
    Fetch the set of already-processed paper IDs from the GitHub repo.

    This is the shared state that prevents duplicate work across contributors.
    Falls back to empty set if GitHub is unreachable.
    """
    try:
        resp = httpx.get(PROCESSED_URL, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            ids = set()
            for line in resp.text.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ids.add(entry.get("paper_id", ""))
                except json.JSONDecodeError:
                    # Plain text line = just the paper_id
                    ids.add(line)
            ids.discard("")
            logger.info(f"Fetched {len(ids)} processed paper IDs from GitHub")
            return ids
        elif resp.status_code == 404:
            logger.info("No processed_papers.jsonl found on GitHub (fresh start)")
            return set()
        else:
            logger.warning(f"GitHub returned {resp.status_code}, assuming fresh start")
            return set()
    except Exception as e:
        logger.warning(f"Could not fetch processed IDs from GitHub: {e}")
        return set()


# ── Source-specific discovery ────────────────────────────────────────


def _discover_arxiv(cutoff_date: str, max_results: int = 200) -> list[dict]:
    """Discover recent arXiv papers via the API."""
    papers = []

    # arXiv API: search across diverse categories to get recent papers.
    # We search each category separately for reliability.
    categories = [
        "cs.AI", "cs.LG", "cs.CL", "q-bio", "cond-mat",
        "physics.chem-ph", "stat.ML", "math.OC", "eess.SP",
        "astro-ph", "hep-th", "quant-ph", "cs.CV", "cs.NE",
    ]

    # Pick a random subset of categories each time for diversity
    selected = random.sample(categories, min(4, len(categories)))
    per_cat = max(max_results // len(selected), 10)

    for cat in selected:
        if len(papers) >= max_results:
            break

        url = "https://export.arxiv.org/api/query"
        params = {
            "search_query": f"cat:{cat}",
            "start": 0,
            "max_results": per_cat,
            "sortBy": "lastUpdatedDate",
            "sortOrder": "descending",
        }

        try:
            resp = httpx.get(url, params=params, timeout=60, follow_redirects=True)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"  arXiv category {cat} failed: {e}")
            time.sleep(API_DELAY * 3)
            continue

        entries = re.findall(r"<entry>(.*?)</entry>", resp.text, re.DOTALL)

        for entry in entries:
            arxiv_id_match = re.search(r"<id>http://arxiv.org/abs/(.+?)</id>", entry)
            title_match = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)

            if arxiv_id_match:
                arxiv_id = arxiv_id_match.group(1).strip()
                # Remove version suffix for dedup
                clean_id = re.sub(r"v\d+$", "", arxiv_id)
                title = title_match.group(1).strip() if title_match else ""
                title = re.sub(r"\s+", " ", title)

                papers.append({
                    "paper_id": f"arxiv:{clean_id}",
                    "source": "arxiv",
                    "title": title[:200],
                    "access_tier": "open",
                })

        time.sleep(API_DELAY * 3)  # arXiv rate limit: 3s between requests

    return papers


def _discover_pmc(cutoff_date: str, max_results: int = 200) -> list[dict]:
    """Discover recent PubMed Central OA papers."""
    papers = []

    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "pmc",
        "term": f'"open access"[filter] AND ("{cutoff_date}"[PDAT] : "3000"[PDAT])',
        "retmax": min(max_results, 1000),
        "retmode": "json",
    }

    resp = httpx.get(search_url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    id_list = data.get("esearchresult", {}).get("idlist", [])

    # Fetch titles in batches
    for i in range(0, len(id_list), 200):
        batch = id_list[i:i + 200]
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
            })

        time.sleep(API_DELAY)

    return papers


def _discover_openalex(cutoff_date: str, max_results: int = 200) -> list[dict]:
    """Discover recent OpenAlex works (OA only)."""
    papers = []
    cursor = "*"

    while len(papers) < max_results:
        url = "https://api.openalex.org/works"
        params = {
            "filter": f"from_updated_date:{cutoff_date},is_oa:true",
            "per_page": min(max_results - len(papers), 200),
            "cursor": cursor,
            "select": "id,title",
            "mailto": "discovery-engine@proton.me",  # polite pool (no rate limit)
        }

        # Retry with backoff for rate limits
        for attempt in range(3):
            resp = httpx.get(
                url, params=params,
                headers={"User-Agent": "discovery-engine/0.1 (mailto:discovery-engine@proton.me)"},
                timeout=60,
            )
            if resp.status_code == 429:
                wait = (attempt + 1) * 5
                logger.warning(f"  OpenAlex rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            logger.warning("  OpenAlex: rate limited after 3 retries")
            break

        data = resp.json()

        results = data.get("results", [])
        if not results:
            break

        for work in results:
            work_id = work.get("id", "").split("/")[-1]
            if not work_id:
                continue
            title = work.get("title", "") or ""

            papers.append({
                "paper_id": f"openalex:{work_id}",
                "source": "openalex",
                "title": title[:200],
                "access_tier": "open",
            })

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

        time.sleep(API_DELAY)

    return papers[:max_results]


def _discover_osti(cutoff_date: str, max_results: int = 200) -> list[dict]:
    """Discover recent OSTI records."""
    papers = []

    url = "https://www.osti.gov/api/v1/records"
    params = {
        "date_added": f"[{cutoff_date} TO *]",
        "rows": min(max_results, 1000),
        "page": 0,
    }

    resp = httpx.get(
        url, params=params,
        headers={"Accept": "application/json"},
        timeout=60,
    )
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
        })

    return papers
