import logging
import re
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page

from web_agent import pick_link

logger = logging.getLogger(__name__)

ATS_DOMAINS = (
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "ashbyhq.com",
    "icims.com",
    "smartrecruiters.com",
    "dynamicsats.com",
    "paylocity.com",
    "recruiting.paylocity.com",
)

_PICK_JOB_GOAL = (
    "Pick ONE link whose text is a specific job title — for example "
    "'Software Engineer', 'Credit Analyst', 'Marketing Manager', 'Data Analyst'. "
    "The link text must read like an actual job role, NOT like 'Join Our Team', "
    "'View All Jobs', 'Apply Here', 'Careers', or any company/brand name. "
    "Reply -1 if no links with specific job titles are visible on this page."
)

_PICK_GATEWAY_GOAL = (
    "Pick the link that leads to a list of open job positions or a job application portal. "
    "This might say 'Join Our Team', 'View Open Positions', 'Apply Here', or link to an "
    "external jobs portal (Greenhouse, Lever, Workday, Paylocity, Dynamics ATS, etc.). "
    "Reply -1 if no such link exists."
)


_JOB_URL_SIGNALS = ("job", "career", "opening", "position", "apply", "vacancy", "requisition")


def _looks_like_job_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    netloc = urlparse(url).netloc.lower()
    return any(s in path or s in netloc for s in _JOB_URL_SIGNALS)


_LISTING_PAGE_SIGNALS = ("search", "results", "listing", "browse", "all-jobs")


def _looks_like_individual_job_url(url: str) -> bool:
    if not _looks_like_job_url(url):
        return False
    last_segment = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    if any(s in last_segment.lower() for s in _LISTING_PAGE_SIGNALS):
        return False
    return (
        len(last_segment) >= 8
        and bool(re.match(r"^[A-Za-z0-9_-]+$", last_segment))
        and (any(c.isdigit() for c in last_segment) or len(last_segment) > 12)
    )


def _is_blog_post_url(url: str) -> bool:
    return bool(re.search(r"/\d{4}/\d{2}/\d{2}/", urlparse(url).path))


def _origin(page: Page) -> str:
    parsed = urlparse(page.url)
    return f"{parsed.scheme}://{parsed.netloc}"


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


def _title_from_row_text(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip() and line.strip() != "\xa0"]
    candidates: list[str] = []
    for line in lines[:6]:
        lower = line.lower()
        if lower in {"full time", "part time", "contract"}:
            continue
        if "," in line and len(line) < 45:
            continue
        if 5 <= len(line) <= 80:
            candidates.append(line)
    if not candidates:
        return None
    return max(candidates, key=len)


def _extract_job_from_ats_table(page: Page) -> tuple[str, str] | None:
    for row in page.locator("tr").all():
        try:
            text = row.inner_text(timeout=1000)
            job_title = _title_from_row_text(text)
            if not job_title:
                continue
            link = row.locator("a[href]").first
            if link.count() == 0:
                continue
            href = link.get_attribute("href", timeout=1000)
            if href:
                return job_title, urljoin(page.url, href)
        except Exception:
            continue
    return None


def find_open_position(page: Page, career_page_url: str) -> dict:
    logger.info("Searching for open positions at: %s", career_page_url)

    page.goto(career_page_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    links = _scan_links(page, _origin(page))

    # Detect embedded ATS iframes — skip analytics/ad trackers by requiring job signals or known ATS domain
    embedded_urls = [
        f.url for f in page.frames[1:]
        if f.url and f.url.startswith("http") and f.url != career_page_url
        and (
            any(s in f.url.lower() for s in _JOB_URL_SIGNALS)
            or any(d in f.url.lower() for d in ATS_DOMAINS)
        )
    ]

    # Pass 1: look for a direct individual job title link on the career page
    job_url = pick_link(links, _PICK_JOB_GOAL)
    if job_url and _looks_like_job_url(job_url) and not _is_blog_post_url(job_url):
        text = next((l["text"] for l in links if l["href"] == job_url), "")
        logger.info("Found individual job via web agent: %s", job_url)
        return {"job_title": text, "job_url": job_url}

    # Pass 2: no direct job titles visible — try up to 3 gateway/portal links in order
    logger.info("No individual job titles found; looking for a jobs portal or listing link")
    career_links = links
    tried_gateways: set[str] = set()

    # Prepend any embedded iframe URLs so they are tried before AI-picked gateways
    for embed_url in embedded_urls:
        logger.info("Trying embedded frame as portal: %s", embed_url)
        tried_gateways.add(embed_url)
        page.goto(embed_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        portal_links = _scan_links(page, _origin(page))
        job_url = pick_link(portal_links, _PICK_JOB_GOAL)
        if job_url and _looks_like_job_url(job_url) and not _is_blog_post_url(job_url):
            text = next((l["text"] for l in portal_links if l["href"] == job_url), "")
            logger.info("Found individual job via embedded frame: %s", job_url)
            return {"job_title": text, "job_url": job_url}
        table_job = _extract_job_from_ats_table(page)
        if table_job:
            logger.info("Found job via ATS table in embedded frame: %s", table_job[1])
            return {"job_title": table_job[0], "job_url": table_job[1]}

    for _attempt in range(3):
        remaining = [l for l in career_links if l["href"] not in tried_gateways]
        gateway_url = pick_link(remaining, _PICK_GATEWAY_GOAL)

        if not gateway_url or gateway_url == career_page_url or gateway_url in tried_gateways:
            break

        tried_gateways.add(gateway_url)

        # If the gateway URL itself is a direct job link, return it immediately
        if _looks_like_individual_job_url(gateway_url):
            text = next((l["text"] for l in career_links if l["href"] == gateway_url), "")
            logger.info("Gateway URL is a direct job link: %s", gateway_url)
            return {"job_title": text, "job_url": gateway_url}

        logger.info("Following gateway link: %s", gateway_url)
        page.goto(gateway_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        portal_links = _scan_links(page, _origin(page))

        # Pass 3: pick one individual job from the portal/listing page
        job_url = pick_link(portal_links, _PICK_JOB_GOAL)
        if job_url and _looks_like_job_url(job_url) and not _is_blog_post_url(job_url):
            text = next((l["text"] for l in portal_links if l["href"] == job_url), "")
            logger.info("Found individual job via web agent: %s", job_url)
            return {"job_title": text, "job_url": job_url}

        # ATS table scan for portal-hosted job boards (run on any portal page)
        table_job = _extract_job_from_ats_table(page)
        if table_job:
            logger.info("Found job via ATS table: %s", table_job[1])
            return {"job_title": table_job[0], "job_url": table_job[1]}

        logger.info("No jobs found at %s, trying next gateway", gateway_url)

    raise RuntimeError(f"Could not find an open position on {career_page_url}")
