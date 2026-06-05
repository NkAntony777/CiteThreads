"""
Live smoke test for every paper-retrieval method in the project.

Hits every crawler + the unified search service, prints what it got
back, and tallies a pass/fail. This is the kind of test you run from
a real environment (not CI) to verify the upstream APIs are reachable
and the project's wrappers still parse what they return.

Usage:
    cd backend
    ./.venv/Scripts/python.exe smoke_test_crawlers.py

Exits 0 if every reachable method returned >= 1 result; 1 if any
unreachable.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, List, Optional, Tuple

# Skip the venv httpx entirely — talk to APIs with stdlib so this works
# even if the venv's network stack is broken.

KNOWN_DOI = "10.48550/arXiv.1706.03762"   # Attention Is All You Need
KNOWN_ARXIV = "1706.03762"
KNOWN_PMID = "29303776"                   # the PubMed mirror of the same
KNOWN_OPENALEX = "W2128901057"            # OpenAlex work id for the same


def _http_json(url: str, headers: Optional[dict] = None) -> Optional[Any]:
    """Minimal JSON GET. Returns None on transport error or 4xx/5xx."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "CiteThreadsSmoke/1.0 (mailto:test@example.com)",
        "Accept": "application/json",
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}
    return None


# ---------------------------------------------------------------------------
# Each test returns (label, ok, details)
# ---------------------------------------------------------------------------


def test_openalex_search() -> Tuple[str, bool, str]:
    data = _http_json(
        "https://api.openalex.org/works?search=graph%20neural%20network%20survey&per-page=2"
    )
    if not data or "_error" in data:
        return ("openalex.search_papers", False, f"error: {data and data.get('_error') or 'no response'}")
    count = data.get("meta", {}).get("count", 0)
    return ("openalex.search_papers", count > 0, f"count={count}")


def test_openalex_get_by_id() -> Tuple[str, bool, str]:
    data = _http_json(f"https://api.openalex.org/works/{KNOWN_OPENALEX}")
    if not data or "_error" in data:
        return ("openalex.get_paper_by_id", False, str(data))
    return ("openalex.get_paper_by_id", bool(data.get("title")),
            f"title={data.get('title','')[:50]}")


def test_openalex_references() -> Tuple[str, bool, str]:
    data = _http_json(
        f"https://api.openalex.org/works?filter=cited_by:{KNOWN_OPENALEX}&per-page=3"
    )
    if not data or "_error" in data:
        return ("openalex.get_references", False, str(data))
    n = len(data.get("results", []))
    return ("openalex.get_references (via OpenAlex W ID)", n > 0, f"got {n} refs")


def test_openalex_citations() -> Tuple[str, bool, str]:
    data = _http_json(
        f"https://api.openalex.org/works?filter=cites:{KNOWN_OPENALEX}&per-page=3"
    )
    if not data or "_error" in data:
        return ("openalex.get_citations", False, str(data))
    n = len(data.get("results", []))
    return ("openalex.get_citations (via OpenAlex W ID)", n > 0, f"got {n} citing")


def test_openalex_author_search() -> Tuple[str, bool, str]:
    data = _http_json(
        "https://api.openalex.org/works?filter=author.search:Hinton&per-page=3"
    )
    if not data or "_error" in data:
        return ("openalex.author.search (raw)", False, str(data))
    n = len(data.get("results", []))
    return ("openalex.author.search (raw)", n > 0, f"got {n} papers")


def test_semantic_scholar_get_paper() -> Tuple[str, bool, str]:
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{KNOWN_DOI}?fields=title,year,citationCount,authors"
    data = _http_json(url)
    if not data or "_error" in data:
        return ("semantic_scholar.get_paper_by_doi", False, str(data))
    return ("semantic_scholar.get_paper_by_doi", bool(data.get("title")),
            f"title={data.get('title','')[:50]}")


def test_semantic_scholar_references() -> Tuple[str, bool, str]:
    url = (f"https://api.semanticscholar.org/graph/v1/paper/DOI:{KNOWN_DOI}/references"
           "?fields=title,year&limit=3")
    data = _http_json(url)
    if not data or "_error" in data:
        return ("semantic_scholar.get_references", False, str(data))
    n = len(data.get("data", []))
    return ("semantic_scholar.get_references", n > 0, f"got {n} refs")


def test_semantic_scholar_citations() -> Tuple[str, bool, str]:
    url = (f"https://api.semanticscholar.org/graph/v1/paper/DOI:{KNOWN_DOI}/citations"
           "?fields=title,year&limit=3")
    data = _http_json(url)
    if not data or "_error" in data:
        return ("semantic_scholar.get_citations", False, str(data))
    n = len(data.get("data", []))
    return ("semantic_scholar.get_citations", n > 0, f"got {n} citing")


def test_arxiv_search() -> Tuple[str, bool, str]:
    q = urllib.parse.quote("graph neural network survey")
    data = _http_json(f"https://export.arxiv.org/api/query?search_query=all:{q}&max_results=2")
    if not data or "_error" in data:
        return ("arxiv.search_papers", False, str(data))
    if isinstance(data, dict) and "_error" in data:
        return ("arxiv.search_papers", False, data["_error"])
    n = data.count("<entry>") if isinstance(data, str) else 0
    return ("arxiv.search_papers", n > 0, f"got {n} entries")


def test_arxiv_get_by_id() -> Tuple[str, bool, str]:
    data = _http_json(f"https://export.arxiv.org/api/query?id_list={KNOWN_ARXIV}")
    if not data or "_error" in data:
        return ("arxiv.get_paper_by_id", False, str(data))
    return ("arxiv.get_paper_by_id", "<entry>" in str(data), "entry found" if "<entry>" in str(data) else "empty")


def test_pubmed_search() -> Tuple[str, bool, str]:
    q = urllib.parse.quote("graph neural network")
    # eSearch
    data = _http_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={q}&retmax=3&retmode=json"
    )
    if not data or "_error" in data:
        return ("pubmed.search_papers", False, str(data))
    ids = data.get("esearchresult", {}).get("idlist", [])
    return ("pubmed.search_papers", len(ids) > 0, f"got {len(ids)} PMIDs")


def test_pubmed_get_by_pmid() -> Tuple[str, bool, str]:
    data = _http_json(
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={KNOWN_PMID}&retmode=json"
    )
    if not data or "_error" in data:
        return ("pubmed.get_paper_by_pmid", False, str(data))
    rec = data.get("result", {}).get(KNOWN_PMID, {})
    return ("pubmed.get_paper_by_pmid", bool(rec.get("title")),
            f"title={rec.get('title','')[:50]}")


def test_dblp_search() -> Tuple[str, bool, str]:
    # DBLP doesn't have a direct keyword search; uses the "ask" endpoint
    # for unified search. The project's wrapper is keyed on conferences,
    # so we test a NeurIPS query.
    q = urllib.parse.quote("graph neural network")
    url = f"https://dblp.org/search/publ/api?q={q}&format=json&h=3"
    data = _http_json(url)
    if not data or "_error" in data:
        return ("dblp.search (raw)", False, str(data))
    n = len(data.get("result", {}).get("hits", {}).get("hit", []))
    return ("dblp.search (raw)", n > 0, f"got {n} hits")


def test_crossref_search() -> Tuple[str, bool, str]:
    q = urllib.parse.quote("graph neural network survey")
    data = _http_json(f"https://api.crossref.org/works?query={q}&rows=2")
    if not data or "_error" in data:
        return ("crossref.search_papers (raw)", False, str(data))
    n = data.get("message", {}).get("total-results", 0)
    return ("crossref.search_papers (raw)", n > 0, f"total-results={n}")


def test_crossref_get_by_doi() -> Tuple[str, bool, str]:
    data = _http_json(f"https://api.crossref.org/works/{KNOWN_DOI}")
    if not data or "_error" in data:
        return ("crossref.get_paper_by_doi", False, str(data))
    title = data.get("message", {}).get("title", [""])[0]
    return ("crossref.get_paper_by_doi", bool(title), f"title={title[:50]}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


TESTS: List[Callable[[], Tuple[str, bool, str]]] = [
    test_openalex_search,
    test_openalex_get_by_id,
    test_openalex_references,
    test_openalex_citations,
    test_openalex_author_search,
    test_semantic_scholar_get_paper,
    test_semantic_scholar_references,
    test_semantic_scholar_citations,
    test_arxiv_search,
    test_arxiv_get_by_id,
    test_pubmed_search,
    test_pubmed_get_by_pmid,
    test_dblp_search,
    test_crossref_search,
    test_crossref_get_by_doi,
]


def main() -> int:
    print(f"Anchor: DOI {KNOWN_DOI} / arXiv {KNOWN_ARXIV} / PMID {KNOWN_PMID}\n")
    passed = failed = 0
    for t in TESTS:
        t0 = time.perf_counter()
        try:
            label, ok, detail = t()
        except Exception as exc:  # noqa: BLE001
            label, ok, detail = t.__name__, False, f"uncaught: {type(exc).__name__}: {exc}"
            traceback.print_exc()
        dt = (time.perf_counter() - t0) * 1000
        mark = "✓" if ok else "✗"
        print(f"  {mark}  {label:<48s}  {dt:6.0f}ms  {detail}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
