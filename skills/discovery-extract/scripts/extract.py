#!/usr/bin/env python3
"""
Discovery Engine — Self-contained paper extraction script.

No pip install needed. Uses only Python stdlib (urllib, json, xml).
Discovers papers, fetches text, calls LLM APIs, validates, saves results.

Usage:
    python extract.py --provider anthropic --api-key sk-ant-... --count 5
    python extract.py --provider openrouter --api-key sk-or-... --source arxiv --count 10
    python extract.py --provider local --base-url http://localhost:11434/v1 --count 3
    python extract.py validate results/paper.json
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────

TRACKING_URL = "https://raw.githubusercontent.com/pcdeni/discovery-engine/master/processed_papers.jsonl"
SCRIPT_DIR = Path(__file__).parent
PROMPT_FILE = SCRIPT_DIR.parent / "references" / "prompt.txt"
OUTPUT_DIR = Path.home() / ".discovery" / "data" / "batch"

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openrouter": "deepseek/deepseek-chat",
    "openai": "gpt-4o",
    "gemini": "gemini-2.5-flash",
    "local": "llama3.1",
}

# SSL context that works on most systems
try:
    SSL_CTX = ssl.create_default_context()
except Exception:
    SSL_CTX = ssl._create_unverified_context()


# ── HTTP helpers ─────────────────────────────────────────────────────

def http_get(url, headers=None, timeout=30):
    """GET request using urllib. Returns (status, body_text)."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def http_post(url, data, headers=None, timeout=300):
    """POST JSON request using urllib. Returns (status, body_text)."""
    body = json.dumps(data).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


# ── Paper Discovery ──────────────────────────────────────────────────

def discover_arxiv(count=10, lookback_days=30):
    """Discover recent papers from arXiv via the Atom API."""
    papers = []
    query = "cat:cond-mat+OR+cat:physics+OR+cat:cs+OR+cat:q-bio+OR+cat:math"
    url = f"http://export.arxiv.org/api/query?search_query={query}&sortBy=submittedDate&sortOrder=descending&max_results={count}"
    status, body = http_get(url, timeout=30)
    if status != 200:
        print(f"  [warn] arXiv API returned {status}", file=sys.stderr)
        return papers

    try:
        root = ET.fromstring(body)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns):
            arxiv_id = (entry.findtext("a:id", "", ns) or "").split("/abs/")[-1].strip()
            if not arxiv_id:
                continue
            title = (entry.findtext("a:title", "", ns) or "").strip().replace("\n", " ")
            abstract = (entry.findtext("a:summary", "", ns) or "").strip()
            papers.append({
                "id": f"arxiv:{arxiv_id}",
                "source": "arxiv",
                "title": title,
                "abstract": abstract,
            })
    except ET.ParseError:
        print("  [warn] Failed to parse arXiv XML", file=sys.stderr)
    return papers[:count]


def discover_pmc(count=10, lookback_days=30):
    """Discover recent open-access papers from PubMed Central."""
    papers = []
    url = (
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pmc&retmax={count}&sort=relevance&retmode=json"
        f"&term=open+access[filter]+AND+hasabstract[text]"
    )
    status, body = http_get(url)
    if status != 200:
        return papers

    try:
        data = json.loads(body)
        ids = data.get("esearchresult", {}).get("idlist", [])
    except (json.JSONDecodeError, KeyError):
        return papers

    if not ids:
        return papers

    # Fetch summaries
    id_str = ",".join(ids[:count])
    sum_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pmc&id={id_str}&retmode=json"
    status, body = http_get(sum_url)
    if status != 200:
        return papers

    try:
        data = json.loads(body)
        results = data.get("result", {})
        for pmc_id in ids[:count]:
            info = results.get(str(pmc_id), {})
            title = info.get("title", "")
            # Get abstract via EFetch
            abstract = _fetch_pmc_abstract(pmc_id, info)
            if title:
                papers.append({
                    "id": f"pmc:{pmc_id}",
                    "source": "pmc",
                    "title": title,
                    "abstract": abstract,
                })
    except (json.JSONDecodeError, KeyError):
        pass
    return papers


def _fetch_pmc_abstract(pmc_id, info=None):
    """Fetch abstract for a PMC paper via multiple fallback methods."""
    # Try to get PMID from article IDs
    pmid = ""
    if info:
        for aid in info.get("articleids", []):
            if aid.get("idtype") == "pmid":
                pmid = aid.get("value", "")
                break

    # Try PubMed EFetch with PMID (returns cleaner XML)
    if pmid:
        url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&rettype=abstract&retmode=xml"
        status, body = http_get(url)
        if status == 200 and "<AbstractText" in body:
            try:
                root = ET.fromstring(body)
                parts = []
                for at in root.iter("AbstractText"):
                    label = at.get("Label", "")
                    text = "".join(at.itertext()).strip()
                    if label:
                        parts.append(f"{label}: {text}")
                    elif text:
                        parts.append(text)
                if parts:
                    return " ".join(parts)
            except ET.ParseError:
                pass

    # Fallback: PMC EFetch
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmc_id}&rettype=abstract&retmode=xml"
    status, body = http_get(url)
    if status == 200 and "<abstract" in body.lower():
        try:
            root = ET.fromstring(body)
            for ab in root.iter():
                if ab.tag.endswith("abstract") or ab.tag == "abstract":
                    text = "".join(ab.itertext()).strip()
                    # Strip XML tag remnants
                    text = re.sub(r"<[^>]+>", "", text)
                    if len(text) > 100:
                        return text
        except ET.ParseError:
            pass
    return ""


def discover_openalex(count=10, lookback_days=30):
    """Discover recent papers from OpenAlex."""
    papers = []
    url = (
        f"https://api.openalex.org/works"
        f"?filter=is_oa:true,type:article,has_abstract:true"
        f"&sort=publication_date:desc&per_page={count}"
        f"&mailto=discovery-engine@proton.me"
    )
    status, body = http_get(url)
    if status != 200:
        return papers

    try:
        data = json.loads(body)
        for work in data.get("results", [])[:count]:
            oa_id = work.get("id", "").split("/")[-1]
            title = work.get("title", "")
            # OpenAlex stores abstract as inverted index
            abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))
            if title and oa_id:
                papers.append({
                    "id": f"openalex:{oa_id}",
                    "source": "openalex",
                    "title": title,
                    "abstract": abstract or "",
                })
    except (json.JSONDecodeError, KeyError):
        pass
    return papers


def _reconstruct_abstract(inverted_index):
    """Reconstruct abstract text from OpenAlex inverted index format."""
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""
    positions = []
    for word, indices in inverted_index.items():
        for idx in indices:
            positions.append((idx, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def discover_osti(count=10, lookback_days=30):
    """Discover recent papers from OSTI (DOE research)."""
    papers = []
    url = (
        f"https://www.osti.gov/api/v1/records"
        f"?sort=publication_date+desc&rows={count}"
    )
    status, body = http_get(url, headers={"Accept": "application/json"})
    if status != 200:
        return papers

    try:
        records = json.loads(body)
        if isinstance(records, dict):
            records = records.get("records", records.get("results", []))
        for rec in records[:count]:
            osti_id = str(rec.get("osti_id", ""))
            title = rec.get("title", "")
            abstract = rec.get("description", "") or rec.get("abstract", "")
            if title and osti_id:
                papers.append({
                    "id": f"osti:{osti_id}",
                    "source": "osti",
                    "title": title,
                    "abstract": abstract or "",
                })
    except (json.JSONDecodeError, KeyError):
        pass
    return papers


def discover_papers(source=None, count=10, lookback_days=30):
    """Discover papers from one or all sources."""
    discoverers = {
        "arxiv": discover_arxiv,
        "pmc": discover_pmc,
        "openalex": discover_openalex,
        "osti": discover_osti,
    }

    if source:
        fn = discoverers.get(source)
        if not fn:
            print(f"Unknown source: {source}. Options: {list(discoverers.keys())}", file=sys.stderr)
            return []
        return fn(count=count, lookback_days=lookback_days)

    # Round-robin across all sources
    per_source = max(count // len(discoverers), 3)
    papers = []
    for name, fn in discoverers.items():
        print(f"  Querying {name}...", file=sys.stderr)
        try:
            found = fn(count=per_source, lookback_days=lookback_days)
            papers.extend(found)
            print(f"    Found {len(found)} papers", file=sys.stderr)
        except Exception as e:
            print(f"    [warn] {name} failed: {e}", file=sys.stderr)
        time.sleep(1)  # Be polite to APIs
    return papers


# ── Deduplication ────────────────────────────────────────────────────

def fetch_processed_ids():
    """Fetch already-processed paper IDs from GitHub tracking file."""
    status, body = http_get(TRACKING_URL, timeout=15)
    if status != 200:
        return set()

    ids = set()
    for line in body.strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            pid = entry.get("paper_id", "")
            if pid:
                ids.add(pid)
        except json.JSONDecodeError:
            continue
    return ids


# ── LLM API Calls ───────────────────────────────────────────────────

def call_anthropic(prompt, model, api_key):
    """Call Anthropic Claude API."""
    status, body = http_post(
        "https://api.anthropic.com/v1/messages",
        data={
            "model": model,
            "max_tokens": 16384,
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    if status != 200:
        raise RuntimeError(f"Anthropic API error {status}: {body[:200]}")
    data = json.loads(body)
    return data["content"][0]["text"]


def call_openai_compatible(prompt, model, api_key, base_url="https://api.openai.com/v1"):
    """Call OpenAI-compatible API (also works for OpenRouter, local LLMs)."""
    status, body = http_post(
        f"{base_url.rstrip('/')}/chat/completions",
        data={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16384,
            "temperature": 0.1,
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if status != 200:
        raise RuntimeError(f"API error {status}: {body[:200]}")
    data = json.loads(body)
    if "error" in data:
        raise RuntimeError(f"API error: {data['error']}")
    return data["choices"][0]["message"]["content"]


def call_gemini(prompt, model, api_key):
    """Call Google Gemini API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    status, body = http_post(
        url,
        data={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 16384, "temperature": 0.1},
        },
    )
    if status != 200:
        raise RuntimeError(f"Gemini API error {status}: {body[:200]}")
    data = json.loads(body)
    return data["candidates"][0]["content"]["parts"][0]["text"]


def call_llm(prompt, provider, api_key, model, base_url=None):
    """Dispatch to the right LLM provider."""
    if provider == "anthropic":
        return call_anthropic(prompt, model, api_key)
    elif provider == "openrouter":
        return call_openai_compatible(prompt, model, api_key, "https://openrouter.ai/api/v1")
    elif provider == "openai":
        url = base_url or "https://api.openai.com/v1"
        return call_openai_compatible(prompt, model, api_key, url)
    elif provider == "gemini":
        return call_gemini(prompt, model, api_key)
    elif provider == "local":
        url = base_url or "http://localhost:11434/v1"
        return call_openai_compatible(prompt, model, api_key or "not-needed", url)
    else:
        raise ValueError(f"Unknown provider: {provider}")


# ── JSON Parsing ─────────────────────────────────────────────────────

def parse_json_response(text):
    """Extract JSON from LLM response, handling markdown blocks and commentary."""
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try markdown code block
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding outermost { ... }
    first = text.find("{")
    if first >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(first, len(text)):
            c = text[i]
            if esc:
                esc = False
                continue
            if c == "\\":
                esc = True
                continue
            if c == '"' and not esc:
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[first:i + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError("Could not extract valid JSON from LLM response")


# ── Validation ───────────────────────────────────────────────────────

def validate_result(data):
    """Validate extraction result. Returns list of issues (empty = valid)."""
    issues = []

    # Top-level keys
    if "paper_analysis" not in data:
        if "analysis" in data:
            issues.append("Wrong key: 'analysis' should be 'paper_analysis'")
        else:
            issues.append("Missing required key: paper_analysis")

    if "entities" not in data:
        issues.append("Missing required key: entities")
    elif not isinstance(data["entities"], list) or len(data["entities"]) == 0:
        issues.append("entities must be a non-empty array")

    if "cross_domain" not in data:
        issues.append("Missing required key: cross_domain")
    else:
        cd = data["cross_domain"]
        if "bridge_tags" not in cd or not cd["bridge_tags"]:
            issues.append("Missing or empty: cross_domain.bridge_tags")
        if "unsolved_tensions" not in cd or not cd["unsolved_tensions"]:
            issues.append("Missing or empty: cross_domain.unsolved_tensions")
        if "interface" not in cd:
            issues.append("Missing: cross_domain.interface")
        elif isinstance(cd["interface"], dict):
            if not cd["interface"].get("provides"):
                issues.append("Missing or empty: interface.provides")
            if not cd["interface"].get("requires"):
                issues.append("Missing or empty: interface.requires")
            # Check provides/requires are dicts not strings
            for p in cd["interface"].get("provides", []):
                if isinstance(p, str):
                    issues.append("provides entries must be objects, not strings")
                    break
            for r in cd["interface"].get("requires", []):
                if isinstance(r, str):
                    issues.append("requires entries must be objects, not strings")
                    break
        else:
            issues.append("cross_domain.interface must be an object")

        # Check tensions are dicts not strings
        for t in cd.get("unsolved_tensions", []):
            if isinstance(t, str):
                issues.append("unsolved_tensions entries must be objects, not strings")
                break

    return issues


# ── Main Extraction Loop ────────────────────────────────────────────

def extract_one(paper, prompt_text, provider, api_key, model, base_url=None):
    """Extract structured data from a single paper. Returns (result_dict, issues)."""
    text = paper.get("abstract", "")
    if len(text.strip()) < 50:
        return None, ["Paper text too short (< 50 chars)"]

    full_prompt = prompt_text + "\n\n---\n\n# PAPER TEXT\n\n" + text

    t0 = time.time()
    raw = call_llm(full_prompt, provider, api_key, model, base_url)
    elapsed = time.time() - t0

    result = parse_json_response(raw)
    issues = validate_result(result)

    # Add metadata
    result["_meta"] = result.get("_meta", {})
    result["_meta"]["paper_id"] = paper["id"]
    result["_meta"]["source"] = paper["source"]
    result["_meta"]["title"] = paper.get("title", "")
    result["_meta"]["text_source"] = "abstract"
    result["_meta"]["model"] = model
    result["_meta"]["provider"] = provider
    result["_meta"]["prompt_version"] = "v_combined"
    result["_meta"]["extraction_seconds"] = round(elapsed, 1)
    result["_meta"]["extracted_at"] = datetime.now(timezone.utc).isoformat()

    return result, issues


def run_batch(args):
    """Discover papers and extract them."""
    # Load prompt
    if not PROMPT_FILE.exists():
        print(f"Error: Prompt file not found at {PROMPT_FILE}", file=sys.stderr)
        sys.exit(1)
    prompt_text = PROMPT_FILE.read_text(encoding="utf-8")

    # Resolve provider settings
    provider = args.provider
    api_key = args.api_key or os.environ.get({
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "local": "",
    }.get(provider, ""), "")
    model = args.model or DEFAULT_MODELS.get(provider, "")
    base_url = args.base_url

    if not api_key and provider not in ("local",):
        print(f"Error: No API key. Use --api-key or set env var.", file=sys.stderr)
        sys.exit(1)

    # Create output directory
    output_dir = Path(args.output) if args.output else OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Discover papers
    count = args.count or 5
    print(f"Discovering {count} papers...", file=sys.stderr)
    papers = discover_papers(source=args.source, count=count * 3)

    if not papers:
        print("No papers found.", file=sys.stderr)
        return

    # Dedup against tracking file
    print("Checking for already-processed papers...", file=sys.stderr)
    processed = fetch_processed_ids()
    # Also check local output directory
    for f in output_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            pid = d.get("_meta", {}).get("paper_id", "")
            if pid:
                processed.add(pid)
        except Exception:
            pass

    papers = [p for p in papers if p["id"] not in processed]
    print(f"  {len(papers)} new papers after dedup", file=sys.stderr)

    if not papers:
        print("All discovered papers already processed.", file=sys.stderr)
        return

    # Extract
    done = 0
    for paper in papers[:count]:
        print(f"\n[{done + 1}/{count}] {paper['id']}: {paper['title'][:60]}...", file=sys.stderr)

        if len(paper.get("abstract", "").strip()) < 50:
            print("  Skipping: abstract too short", file=sys.stderr)
            continue

        # Retry loop
        for attempt in range(1, 4):
            try:
                result, issues = extract_one(paper, prompt_text, provider, api_key, model, base_url)
                if not issues:
                    break
                print(f"  Validation issues (attempt {attempt}): {issues[0]}", file=sys.stderr)
                if attempt < 3:
                    time.sleep(2)
            except Exception as e:
                print(f"  Error (attempt {attempt}): {e}", file=sys.stderr)
                result, issues = None, [str(e)]
                if "429" in str(e) or "rate" in str(e).lower():
                    time.sleep(30 * attempt)
                elif attempt < 3:
                    time.sleep(3 * attempt)

        if result and not issues:
            # Save
            safe_id = paper["id"].replace(":", "__").replace("/", "_")
            outfile = output_dir / f"{safe_id}.json"
            outfile.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  Saved: {outfile.name} ({result['_meta'].get('extraction_seconds', '?')}s)", file=sys.stderr)
            done += 1
        else:
            print(f"  FAILED: {issues[0] if issues else 'unknown'}", file=sys.stderr)

        time.sleep(5)  # Rate limit courtesy

    print(f"\nDone: {done}/{count} papers extracted to {output_dir}", file=sys.stderr)


def run_validate(args):
    """Validate one or more result files."""
    target = Path(args.path)
    files = list(target.glob("*.json")) if target.is_dir() else [target]

    total, valid, invalid = 0, 0, 0
    for f in files:
        total += 1
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            issues = validate_result(data)
            if issues:
                invalid += 1
                print(f"FAIL: {f.name}: {issues[0]}")
            else:
                valid += 1
                print(f"PASS: {f.name}")
        except Exception as e:
            invalid += 1
            print(f"ERROR: {f.name}: {e}")

    print(f"\nValidated {total}: {valid} pass, {invalid} fail")
    sys.exit(0 if invalid == 0 else 1)


def run_discover(args):
    """Discover papers without extracting (dry run)."""
    count = args.count or 10
    papers = discover_papers(source=args.source, count=count)
    processed = fetch_processed_ids()

    new_papers = [p for p in papers if p["id"] not in processed]
    print(f"Found {len(papers)} papers ({len(new_papers)} new):\n")
    for p in new_papers[:count]:
        has_text = "yes" if len(p.get("abstract", "")) > 100 else "no"
        print(f"  {p['id']}: {p['title'][:70]}... [abstract: {has_text}]")


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Discovery Engine — self-contained paper extraction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python extract.py --provider anthropic --api-key sk-ant-... --count 5\n"
               "  python extract.py --provider local --count 3\n"
               "  python extract.py discover --source arxiv --count 20\n"
               "  python extract.py validate ./results/\n",
    )
    sub = parser.add_subparsers(dest="command")

    # Default: batch extraction
    parser.add_argument("--provider", default="anthropic",
                        choices=["anthropic", "openrouter", "openai", "gemini", "local"],
                        help="LLM provider")
    parser.add_argument("--api-key", help="API key (or use env var)")
    parser.add_argument("--model", help="Model name (provider-specific)")
    parser.add_argument("--base-url", help="Custom API base URL (for local LLMs)")
    parser.add_argument("--source", help="Paper source (arxiv, pmc, openalex, osti)")
    parser.add_argument("--count", type=int, default=5, help="Number of papers (default: 5)")
    parser.add_argument("--output", help="Output directory (default: ~/.discovery/data/batch/)")

    # discover subcommand
    disc = sub.add_parser("discover", help="Preview papers without extracting")
    disc.add_argument("--source", help="Paper source")
    disc.add_argument("--count", type=int, default=10)

    # validate subcommand
    val = sub.add_parser("validate", help="Validate result files")
    val.add_argument("path", help="JSON file or directory")

    args = parser.parse_args()

    if args.command == "discover":
        run_discover(args)
    elif args.command == "validate":
        run_validate(args)
    else:
        run_batch(args)


if __name__ == "__main__":
    main()
