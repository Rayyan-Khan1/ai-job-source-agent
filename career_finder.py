import logging
import re
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page

from web_agent import pick_link

logger = logging.getLogger(__name__)

CAREER_KEYWORDS = re.compile(
    r"career|careers|jobs|join us|work with us|opportunities|openings|hiring",
    re.IGNORECASE,
)

FALLBACK_PATHS = ("/careers", "/jobs", "/about/careers", "/company/careers", "/join-us")


def _normalize_origin(company_website: str) -> str:
    parsed = urlparse(company_website)
    if not parsed.scheme:
        parsed = urlparse(f"https://{company_website}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _registered_domain(host: str) -> str:
    host = host.lower().split(":")[0]
    labels = host.split(".")
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host


def _looks_like_careers(page: Page) -> bool:
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return False
    if len(body_text.strip()) <= 100:
        return False

    if CAREER_KEYWORDS.search(page.url.lower()):
        return True

    # A page that merely links to a careers page (e.g. in its nav/footer)
    # mentions it once; an actual careers page repeats job/career terms.
    return len(CAREER_KEYWORDS.findall(body_text.lower())) >= 2


def _scan_links(page: Page, origin: str) -> list[dict]:
    raw_links = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => ({ href: e.getAttribute('href') || '', text: (e.textContent || '').trim() }))",
    )

    seen: set[str] = set()
    links: list[dict] = []
    for link in raw_links:
        href = link["href"]
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(origin, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append({"href": absolute, "text": link["text"]})

    return links


def _validate_url(page: Page, url: str) -> bool:
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)
        if response and not response.ok:
            return False
        return _looks_like_careers(page)
    except Exception:
        return False


def find_career_page(page: Page, company_website: str) -> str:
    origin = _normalize_origin(company_website)
    logger.info("Searching for careers page starting at: %s", origin)

    page.goto(origin, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    links = _scan_links(page, origin)
    candidate = pick_link(
        links,
        "Find the link that leads to this company's careers, jobs, or open positions page.",
    )
    if candidate and _validate_url(page, candidate):
        logger.info("Found careers page via web agent: %s", candidate)
        return candidate

    logger.info("Web agent did not find a careers link; trying common paths and subdomains")
    registered = _registered_domain(urlparse(origin).netloc)
    fallback_urls = [f"https://careers.{registered}", f"https://jobs.{registered}"]
    fallback_urls.extend(urljoin(origin, path) for path in FALLBACK_PATHS)

    for url in fallback_urls:
        if _validate_url(page, url):
            logger.info("Found careers page via fallback: %s", url)
            return url

    raise RuntimeError(f"Could not find careers page for {origin}")
