import logging
import re
from urllib.parse import urlparse

from ddgs import DDGS
from playwright.sync_api import Page

logger = logging.getLogger(__name__)

BLOCKLIST_DOMAINS = (
    "linkedin.com",
    "facebook.com",
    "wikipedia.org",
    "glassdoor.com",
    "indeed.com",
    "crunchbase.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "instagram.com",
    "bing.com",
    "duckduckgo.com",
    "zoominfo.com",
    "bbb.org",
    "britannica.com",
    "bloomberg.com",
    "dnb.com",
    "play.google.com",
    "apps.apple.com",
    "apps.microsoft.com",
    "f6s.com",
)

GENERIC_WORDS = frozenset(
    {"group", "groups", "inc", "incorporated", "llc", "ltd", "co", "company",
     "corp", "corporation", "holdings", "the", "and"}
)


def _is_blocklisted(url: str, brand_tokens: list[str]) -> bool:
    hostname = urlparse(url).netloc.lower()
    host_root = hostname.removeprefix("www.").split(".")[0].replace("-", "")
    if brand_tokens and host_root == brand_tokens[0]:
        return False
    return any(domain in hostname for domain in BLOCKLIST_DOMAINS)


def _significant_words(company_name: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+", company_name.lower())
    return [w for w in words if w not in GENERIC_WORDS]


def _brand_tokens(company_name: str, company_slug: str | None) -> list[str]:
    tokens: list[str] = []

    all_words = re.findall(r"[A-Za-z0-9]+", company_name.lower())
    if len(all_words) > 1:
        tokens.append("".join(all_words))
    if company_slug:
        slug_words = [w for w in re.split(r"[-_]", company_slug.lower()) if w]
        if len(slug_words) > 1:
            tokens.append("".join(slug_words))

    for word in _significant_words(company_name):
        if len(word) >= 3:
            tokens.append(word)

    return list(dict.fromkeys(tokens))


def _search_queries(
    company_name: str, company_slug: str | None, industry: str | None
) -> list[str]:
    if company_slug:
        name = company_slug.replace("-", " ").replace("_", " ")
    else:
        name = company_name

    queries: list[str] = []
    if industry:
        queries.append(f"{name} {industry} official website")
    queries.append(f"{name} official website")
    queries.append(name)
    return list(dict.fromkeys(queries))


def _ddg_search(query: str, brand_tokens: list[str], max_results: int = 8) -> list[str]:
    try:
        results = DDGS().text(query, max_results=max_results)
    except Exception:
        logger.warning("Search failed for query: %s", query, exc_info=True)
        return []

    seen: set[str] = set()
    links: list[str] = []
    for result in results:
        href = result.get("href", "")
        if not href.startswith("http") or _is_blocklisted(href, brand_tokens) or href in seen:
            continue
        seen.add(href)
        links.append(href)

    logger.info("Found %d search result links", len(links))
    return links


COMMON_TLDS = (".com", ".net", ".org", ".io", ".co", ".ai")


def _score_host(href: str, brand_tokens: list[str]) -> int:
    host = urlparse(href).netloc.lower().removeprefix("www.")
    host_root = host.split(".")[0].replace("-", "")
    if not host_root:
        return -1

    score = 1 if href.startswith("https://") else 0
    if any(host.endswith(tld) for tld in COMMON_TLDS):
        score += 1

    best_token = 0
    for token in brand_tokens:
        if host_root == token:
            best_token = max(best_token, 50 + len(token))
        elif host_root.startswith(token):
            extra = len(host_root) - len(token)
            best_token = max(best_token, 25 + len(token) - min(extra, 15))
        elif token in host_root:
            extra = len(host_root) - len(token)
            best_token = max(best_token, 8 - min(extra, 7))

    return score + best_token


def _rank_candidates(links: list[str], brand_tokens: list[str]) -> list[str]:
    scored = [(href, _score_host(href, brand_tokens)) for href in links]
    scored = [item for item in scored if item[1] > 0]
    scored.sort(key=lambda item: item[1], reverse=True)
    return [href for href, _ in scored]


def _host_strongly_matches(url: str, brand_tokens: list[str]) -> bool:
    host_root = urlparse(url).netloc.lower().removeprefix("www.").split(".")[0].replace("-", "")
    return any(host_root == token or host_root.startswith(token) for token in brand_tokens)


def _validate_candidate(
    page: Page, url: str, brand_tokens: list[str], industry: str | None
) -> bool:
    if _host_strongly_matches(url, brand_tokens):
        return True

    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if response and response.status >= 400:
            return False
        page.wait_for_timeout(1500)
        haystack = f"{page.title()} {page.url}".lower()
    except Exception:
        return False

    if any(len(token) >= 6 and token in haystack for token in brand_tokens):
        return True
    if industry and industry.lower() in haystack and any(token in haystack for token in brand_tokens):
        return True
    return False


def search_company_website(
    page: Page,
    company_name: str,
    company_slug: str | None = None,
    industry: str | None = None,
) -> str:
    brand_tokens = _brand_tokens(company_name, company_slug)
    logger.info("Searching for company website: %s", company_name)

    tried: set[str] = set()

    for query in _search_queries(company_name, company_slug, industry):
        links = _ddg_search(query, brand_tokens)
        for candidate in _rank_candidates(links, brand_tokens)[:3]:
            if candidate in tried:
                continue
            tried.add(candidate)
            logger.info("Validating candidate: %s", candidate)
            if _validate_candidate(page, candidate, brand_tokens, industry):
                logger.info("Found company website: %s", candidate)
                return candidate

    raise RuntimeError(f"Could not find an official website for {company_name!r}")
