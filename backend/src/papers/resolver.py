"""DOI and identifier metadata resolver.

Fetches bibliographic metadata and abstracts when Europe PMC does not
return content:
- DOI fallback: OpenAlex -> Semantic Scholar.
- PMCID fallback: NCBI PMC direct fetch.
"""

import logging
from typing import Any

from backend.src.common.http_client import HttpClientManager

logger = logging.getLogger(__name__)


# Source Adapters


async def fetch_doi_abstract(doi: str) -> dict[str, Any] | None:
    """Fetch metadata for a DOI via OpenAlex and Semantic Scholar.

    Prefers an entry with an abstract. If neither provider has an
    abstract, returns OpenAlex metadata if available, falling back to
    Semantic Scholar.
    """
    openalex_data = await fetch_from_openalex(doi)
    if openalex_data:
        # Require an abstract; otherwise query downstream providers.
        if openalex_data.get("abstract"):
            return openalex_data
    # Downstream fallback provider when OpenAlex lacks an abstract.
    return await fetch_from_semantic_scholar(doi)


async def fetch_from_openalex(doi: str) -> dict[str, Any] | None:
    """Fetch metadata from the OpenAlex API."""
    url = f"https://api.openalex.org/works/doi:{doi}"
    try:
        client = await HttpClientManager.get_client()
        response = await client.get(url, timeout=15.0)
        if response.status_code != 200:
            return None
        data = response.json()
        title = data.get("title", "")
        if not title:
            return None
        abstract = data.get("abstract_inverted_index")
        abstract_text = _reconstruct_abstract(abstract)
        return {
            "doi": doi,
            "title": title,
            "abstract": abstract_text,
            "authors": [
                a.get("author", {}).get("display_name", "")
                for a in data.get("authorships", [])
                if a.get("author", {}).get("display_name")
            ][:10],
            "year": data.get("publication_year"),
            "journal": data.get("primary_location", {}).get("source", {}).get("display_name", ""),
            "source": "OpenAlex",
            "url": data.get("doi"),
            "isOpenAccess": bool((data.get("open_access") or {}).get("is_oa")),
            "pdfUrl": (data.get("best_oa_location") or {}).get("pdf_url"),
        }
    except Exception as e:
        logger.error(f"OpenAlex fetch failed for {doi}: {e}")
        return None


def _reconstruct_abstract(inverted_index: dict[str, list] | None) -> str:
    """Reconstruct abstract from OpenAlex inverted index mapping."""
    if not inverted_index:
        return ""
    # Map token position to word text, retaining the first occurrence.
    position_to_word: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            if pos not in position_to_word:
                position_to_word[pos] = word
    if not position_to_word:
        return ""
    # Order tokens sequentially by index offset.
    max_pos = max(position_to_word.keys())
    words = [position_to_word.get(i, "") for i in range(max_pos + 1)]
    return " ".join(words).strip()


async def fetch_from_semantic_scholar(doi: str) -> dict[str, Any] | None:
    """Fetch paper metadata from the Semantic Scholar API."""
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    params = {"fields": "title,abstract,authors,year,venue,externalIds,openAccessPdf,isOpenAccess"}
    try:
        client = await HttpClientManager.get_client()
        response = await client.get(url, params=params, timeout=15.0)
        if response.status_code != 200:
            return None
        data = response.json()
        title = data.get("title", "")
        if not title:
            return None
        return {
            "doi": doi,
            "title": title,
            "abstract": data.get("abstract", ""),
            "authors": [a.get("name", "") for a in data.get("authors", []) if a.get("name")][:10],
            "year": data.get("year"),
            "journal": data.get("venue", ""),
            "source": "Semantic Scholar",
            "url": f"https://www.semanticscholar.org/paper/{doi}",
            "openAccessPdf": data.get("openAccessPdf"),
            "isOpenAccess": bool(data.get("isOpenAccess")),
        }
    except Exception as e:
        logger.error(f"Semantic Scholar fetch failed for {doi}: {e}")
        return None


async def fetch_pmc_by_pmcid(pmcid: str) -> dict[str, Any] | None:
    """Fetch metadata and full-text XML from NCBI PMC by PMCID."""
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": "pmc",
        "id": pmcid.replace("PMC", ""),
        "rettype": "xml",
        "retmode": "xml",
    }
    try:
        client = await HttpClientManager.get_client()
        resp = await client.get(url, params=params, timeout=15.0)
        if resp.status_code != 200:
            return None

        from lxml import etree as ET

        root = ET.fromstring(resp.content)

        title = ""
        title_node = root.find(".//article-title")
        if title_node is not None:
            title = "".join(title_node.itertext()).strip()

        abstract = ""
        abstract_node = root.find(".//abstract")
        if abstract_node is not None:
            abstract = "".join(abstract_node.itertext()).strip()

        authors = []
        contribs = root.findall(".//contrib-group/contrib[@contrib-type='author']")
        for contrib in contribs:
            surname = contrib.findtext("name/surname", "")
            given = contrib.findtext("name/given-names", "")
            name = f"{surname} {given}".strip()
            if name:
                authors.append(name)
        authors = authors[:10]

        journal = root.findtext(".//journal-title", "")
        if not journal:
            journal = root.findtext(".//abbrev-journal-title", "")

        year = None
        pub_date = root.find(".//pub-date/year")
        if pub_date is not None and pub_date.text:
            try:
                year = int(pub_date.text)
            except ValueError:
                pass

        resolved_doi = ""
        article_ids = root.findall(".//article-id")
        for article_id in article_ids:
            if article_id.get("pub-id-type") == "doi" and article_id.text:
                resolved_doi = article_id.text.strip()
                break

        return {
            "doi": resolved_doi,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "year": year,
            "journal": journal,
            "source": "PMC",
            "url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/",
            "full_text_xml": resp.text,
            "pmcid": pmcid,
        }
    except Exception as e:
        logger.error(f"PMC full text fetch failed for {pmcid}: {e}")
        return None
