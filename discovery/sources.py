"""
Paper source adapters — fetch paper text from different databases.

Each source provides:
- fetch_text(paper_id) → full text or abstract
- fetch_metadata(paper_id) → title, authors, year, doi, etc.

Sources supported:
- arXiv: LaTeX source or PDF (open access)
- PMC: XML full text (PMC OA subset)
- Europe PMC: Abstract (full text for OA subset)
- OSTI: Full text for DOE papers
- OpenAlex: Abstract + metadata
- Semantic Scholar: Abstract + S2ORC for OA
- Google Patents: Full text (claims + description)
"""

import re
import time
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("discovery.sources")

# Delay between API requests (be polite to public APIs)
API_DELAY = 1.0


@dataclass
class Paper:
    """A paper to be extracted."""
    paper_id: str           # source-prefixed: "arxiv:2401.12345"
    source: str             # arxiv, pmc, openalex, osti, etc.
    title: str = ""
    abstract: str = ""
    full_text: str = ""     # Full text if available, empty if not
    year: int = 0
    doi: str = ""
    authors: list[str] = field(default_factory=list)
    access_tier: str = "open"   # open, oa_link, institutional, abstract_only
    text_source: str = ""       # "full_text" or "abstract"

    @property
    def text(self) -> str:
        """Best available text for extraction."""
        if self.full_text:
            self.text_source = "full_text"
            return self.full_text
        if self.abstract:
            self.text_source = "abstract"
            return self.abstract
        return ""

    @property
    def raw_id(self) -> str:
        """ID without source prefix."""
        if ":" in self.paper_id:
            return self.paper_id.split(":", 1)[1]
        return self.paper_id


def fetch_paper(paper_id: str) -> Paper:
    """
    Fetch a paper by its source-prefixed ID.

    Args:
        paper_id: e.g., "arxiv:2401.12345", "pmc:12345678", "osti:1234567"

    Returns:
        Paper object with text and metadata
    """
    if ":" not in paper_id:
        raise ValueError(f"paper_id must be source-prefixed (e.g., 'arxiv:2401.12345'), got: {paper_id}")

    source, raw_id = paper_id.split(":", 1)

    fetchers = {
        "arxiv": _fetch_arxiv,
        "pmc": _fetch_pmc,
        "europepmc": _fetch_europepmc,
        "openalex": _fetch_openalex,
        "osti": _fetch_osti,
        "semanticscholar": _fetch_semanticscholar,
    }

    fetcher = fetchers.get(source)
    if not fetcher:
        raise ValueError(f"Unknown source: {source}. Supported: {list(fetchers.keys())}")

    return fetcher(raw_id)


# ── arXiv ──────────────────────────────────────────────────────────────


def _fetch_arxiv(arxiv_id: str) -> Paper:
    """Fetch from arXiv. Tries abstract via API (full text would need PDF parsing)."""
    import httpx

    # Fetch metadata via arXiv API
    url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()

    # Parse XML response — extract from within <entry> block
    text = resp.text
    entry_match = re.search(r"<entry>(.*?)</entry>", text, re.DOTALL)
    entry_text = entry_match.group(1) if entry_match else text
    title = _xml_extract(entry_text, "title") or ""
    abstract = _xml_extract(entry_text, "summary") or ""
    # Clean up arXiv formatting
    title = re.sub(r"\s+", " ", title).strip()
    abstract = re.sub(r"\s+", " ", abstract).strip()

    authors = re.findall(r"<name>(.*?)</name>", text)
    doi_match = re.search(r'doi="(.*?)"', text) or re.search(r"<arxiv:doi.*?>(.*?)</arxiv:doi>", text)
    doi = doi_match.group(1) if doi_match else ""

    year_match = re.search(r"<published>(\d{4})", text)
    year = int(year_match.group(1)) if year_match else 0

    paper = Paper(
        paper_id=f"arxiv:{arxiv_id}",
        source="arxiv",
        title=title,
        abstract=abstract,
        year=year,
        doi=doi,
        authors=authors[:10],  # cap at 10
        access_tier="open",
    )

    # For full text, we'd need to download and parse the PDF or LaTeX source.
    # For now, use abstract. Full text support is a future enhancement.
    # arXiv LaTeX source: https://arxiv.org/e-print/{arxiv_id}

    time.sleep(API_DELAY)
    return paper


# ── PMC (PubMed Central) ──────────────────────────────────────────────


def _fetch_pmc(pmc_id: str) -> Paper:
    """Fetch from PubMed Central. Full text available for OA subset."""
    import httpx

    # Try full text first (PMC OA via BioC)
    full_text = ""
    ft_url = f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{pmc_id}/unicode"
    resp = httpx.get(ft_url, timeout=30)

    if resp.status_code == 200:
        try:
            # BioC returns HTML error page on non-OA papers (status 200 but not JSON)
            data = resp.json()
            passages = []
            for doc in data.get("documents", []):
                for passage in doc.get("passages", []):
                    passages.append(passage.get("text", ""))
            full_text = "\n\n".join(p for p in passages if p)
        except (ValueError, KeyError):
            pass  # Not JSON = not available in OA subset

    # Get metadata via E-utilities (esummary)
    meta_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pmc&id={pmc_id}&retmode=json"
    meta_resp = httpx.get(meta_url, timeout=30)

    title, abstract, year, doi, authors, pmid = "", "", 0, "", [], ""
    if meta_resp.status_code == 200:
        try:
            meta = meta_resp.json()
            result = meta.get("result", {}).get(str(pmc_id), {})
            title = result.get("title", "")
            year_str = result.get("pubdate", "")
            year_match = re.search(r"(\d{4})", year_str)
            year = int(year_match.group(1)) if year_match else 0
            authors = [a.get("name", "") for a in result.get("authors", [])[:10]]
            # Extract DOI and PMID from articleids
            for aid in result.get("articleids", []):
                if aid.get("idtype") == "doi" and not doi:
                    doi = aid.get("value", "")
                elif aid.get("idtype") == "pmid" and not pmid:
                    pmid = aid.get("value", "")
            # Fallback DOI from top-level
            if not doi:
                doi = result.get("doi", "")
        except Exception:
            pass

    # If no full text and no abstract, try PubMed BioC (needs PMID, not PMC ID)
    if not full_text and not abstract and pmid:
        try:
            pubmed_url = f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{pmid}/unicode"
            pubmed_resp = httpx.get(pubmed_url, timeout=30)
            if pubmed_resp.status_code == 200:
                pdata = pubmed_resp.json()
                for doc in pdata.get("documents", []):
                    for passage in doc.get("passages", []):
                        ptype = passage.get("infons", {}).get("type", "")
                        ptext = passage.get("text", "")
                        if "abstract" in ptype.lower() and ptext:
                            abstract = (abstract + " " + ptext).strip() if abstract else ptext
                        elif "title" in ptype.lower() and ptext and not title:
                            title = ptext
        except Exception:
            pass

    # Fallback: EFetch from PubMed (uses PMID) — returns XML, parse <AbstractText>
    if not full_text and not abstract and pmid:
        try:
            efetch_url = (
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                f"?db=pubmed&id={pmid}&rettype=abstract&retmode=xml"
            )
            efetch_resp = httpx.get(efetch_url, timeout=30)
            if efetch_resp.status_code == 200:
                # Extract <AbstractText> elements from PubMed XML
                abs_parts = re.findall(
                    r"<AbstractText[^>]*>(.*?)</AbstractText>",
                    efetch_resp.text, re.DOTALL
                )
                if abs_parts:
                    abstract = " ".join(
                        re.sub(r"<[^>]+>", "", part).strip() for part in abs_parts
                    )
        except Exception:
            pass

    # Last resort: EFetch from PMC — returns JATS XML, parse <abstract>
    if not full_text and not abstract:
        try:
            efetch_url = (
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                f"?db=pmc&id={pmc_id}&rettype=xml"
            )
            efetch_resp = httpx.get(efetch_url, timeout=30)
            if efetch_resp.status_code == 200:
                # Extract <abstract> block and strip XML tags
                abs_match = re.search(
                    r"<abstract[^>]*>(.*?)</abstract>",
                    efetch_resp.text, re.DOTALL
                )
                if abs_match:
                    abstract = re.sub(r"<[^>]+>", " ", abs_match.group(1))
                    abstract = re.sub(r"\s+", " ", abstract).strip()
        except Exception:
            pass

    time.sleep(API_DELAY)

    return Paper(
        paper_id=f"pmc:{pmc_id}",
        source="pmc",
        title=title,
        abstract=abstract,
        full_text=full_text,
        year=year,
        doi=doi,
        authors=authors,
        access_tier="open" if full_text else ("abstract_only" if abstract else "no_text"),
    )


# ── Europe PMC ─────────────────────────────────────────────────────────


def _fetch_europepmc(epmc_id: str) -> Paper:
    """Fetch from Europe PMC. Usually abstract only."""
    import httpx

    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/article/MED/{epmc_id}?resultType=core&format=json"
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    result = data.get("result", data)

    abstract = result.get("abstractText", "")
    title = result.get("title", "")
    doi = result.get("doi", "")
    year = result.get("pubYear")
    year = int(year) if year else 0
    authors = [f"{a.get('lastName', '')} {a.get('initials', '')}".strip()
               for a in result.get("authorList", {}).get("author", [])[:10]]

    # Check if full text is available
    full_text = ""
    has_ft = result.get("hasTextMinedTerms") == "Y" or result.get("isOpenAccess") == "Y"
    if has_ft:
        ft_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{epmc_id}/fullTextXML"
        ft_resp = httpx.get(ft_url, timeout=30)
        if ft_resp.status_code == 200:
            # Strip XML tags for plain text
            full_text = re.sub(r"<[^>]+>", " ", ft_resp.text)
            full_text = re.sub(r"\s+", " ", full_text).strip()

    time.sleep(API_DELAY)

    return Paper(
        paper_id=f"europepmc:{epmc_id}",
        source="europepmc",
        title=title,
        abstract=abstract,
        full_text=full_text,
        year=year,
        doi=doi,
        authors=authors,
        access_tier="open" if full_text else "abstract_only",
    )


# ── OpenAlex ───────────────────────────────────────────────────────────


def _fetch_openalex(work_id: str) -> Paper:
    """Fetch from OpenAlex. Abstract + metadata."""
    import httpx

    # OpenAlex IDs can be full URLs or short IDs
    if not work_id.startswith("W"):
        work_id = f"W{work_id}"

    url = f"https://api.openalex.org/works/{work_id}"
    resp = httpx.get(url, headers={"User-Agent": "discovery-engine/0.1"}, timeout=30)
    resp.raise_for_status()

    data = resp.json()

    # Reconstruct abstract from inverted index
    abstract = ""
    inv_index = data.get("abstract_inverted_index")
    if inv_index:
        word_positions = []
        for word, positions in inv_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        word_positions.sort()
        abstract = " ".join(w for _, w in word_positions)

    title = data.get("title", "")
    doi = data.get("doi", "")
    if doi and doi.startswith("https://doi.org/"):
        doi = doi[16:]  # strip URL prefix
    year = data.get("publication_year", 0) or 0

    authors = []
    for auth in data.get("authorships", [])[:10]:
        name = auth.get("author", {}).get("display_name", "")
        if name:
            authors.append(name)

    # Check for OA full text URL
    full_text = ""
    oa_url = data.get("open_access", {}).get("oa_url")
    access_tier = "abstract_only"
    if data.get("open_access", {}).get("is_oa"):
        access_tier = "oa_link"

    time.sleep(API_DELAY)

    return Paper(
        paper_id=f"openalex:{work_id}",
        source="openalex",
        title=title,
        abstract=abstract,
        full_text=full_text,
        year=year,
        doi=doi,
        authors=authors,
        access_tier=access_tier,
    )


# ── OSTI ───────────────────────────────────────────────────────────────


def _fetch_osti(osti_id: str) -> Paper:
    """Fetch from OSTI (DOE Office of Scientific and Technical Information)."""
    import httpx

    url = f"https://www.osti.gov/api/v1/records/{osti_id}"
    resp = httpx.get(url, headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()

    records = resp.json()
    if not records:
        raise ValueError(f"OSTI record {osti_id} not found")

    data = records[0] if isinstance(records, list) else records

    abstract = data.get("description", "") or data.get("abstract", "") or ""
    title = data.get("title", "")
    doi = data.get("doi", "")
    year_str = data.get("publication_date", "")
    year_match = re.search(r"(\d{4})", year_str)
    year = int(year_match.group(1)) if year_match else 0
    authors = data.get("authors", [])
    if isinstance(authors, str):
        authors = [a.strip() for a in authors.split(";")]
    authors = authors[:10]

    time.sleep(API_DELAY)

    return Paper(
        paper_id=f"osti:{osti_id}",
        source="osti",
        title=title,
        abstract=abstract,
        year=year,
        doi=doi,
        authors=authors,
        access_tier="open",
    )


# ── Semantic Scholar ───────────────────────────────────────────────────


def _fetch_semanticscholar(s2_id: str) -> Paper:
    """Fetch from Semantic Scholar API."""
    import httpx

    url = f"https://api.semanticscholar.org/graph/v1/paper/{s2_id}"
    params = {"fields": "title,abstract,year,doi,authors,openAccessPdf"}
    resp = httpx.get(url, params=params, timeout=30)
    resp.raise_for_status()

    data = resp.json()

    abstract = data.get("abstract", "") or ""
    title = data.get("title", "")
    doi = data.get("doi", "")
    year = data.get("year", 0) or 0
    authors = [a.get("name", "") for a in data.get("authors", [])[:10]]

    access_tier = "abstract_only"
    if data.get("openAccessPdf"):
        access_tier = "oa_link"

    time.sleep(API_DELAY)

    return Paper(
        paper_id=f"semanticscholar:{s2_id}",
        source="semanticscholar",
        title=title,
        abstract=abstract,
        year=year,
        doi=doi,
        authors=authors,
        access_tier=access_tier,
    )


# ── Utilities ──────────────────────────────────────────────────────────


def _xml_extract(xml_text: str, tag: str) -> Optional[str]:
    """Extract text content from an XML tag (simple, no lxml dependency)."""
    pattern = f"<{tag}[^>]*>(.*?)</{tag}>"
    match = re.search(pattern, xml_text, re.DOTALL)
    return match.group(1).strip() if match else None
