import logging
import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page

from website_search import search_company_website

logger = logging.getLogger(__name__)

COMPANY_NAME_SELECTORS = [
    ".job-details-jobs-unified-top-card__company-name a",
    ".jobs-unified-top-card__company-name a",
    "a[data-tracking-control-name='public_jobs_topcard-org-name']",
    ".topcard__org-name-link",
]

INDUSTRY_SELECTORS = [
    ".description__job-criteria-list .description__job-criteria-item",
    ".job-details-jobs-unified-top-card__job-insight",
]


def parse_job_id(linkedin_job_url: str) -> str:
    parsed = urlparse(linkedin_job_url)
    query = parse_qs(parsed.query)
    if "currentJobId" in query:
        return query["currentJobId"][0]

    match = re.search(r"/jobs/view/(\d+)", parsed.path)
    if match:
        return match.group(1)

    raise ValueError(f"Could not parse job ID from URL: {linkedin_job_url}")


def _needs_login(page: Page) -> bool:
    url = page.url.lower()
    return "authwall" in url or "/login" in url


def _wait_for_login(page: Page, headed: bool) -> None:
    if not _needs_login(page):
        return
    if not headed:
        raise RuntimeError(
            "LinkedIn requires login. Re-run with --headed and sign in."
        )
    logger.info("Waiting for LinkedIn sign-in in the browser window...")
    page.wait_for_url(
        lambda url: "authwall" not in url.lower() and "/login" not in url.lower(),
        timeout=120000,
    )
    page.wait_for_timeout(2000)


def _first_text(page: Page, selectors: list[str]) -> str | None:
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.count() > 0:
            text = locator.inner_text(timeout=2000).strip()
            if text:
                return text
    return None


def _company_slug(page: Page) -> str | None:
    for selector in COMPANY_NAME_SELECTORS:
        locator = page.locator(selector).first
        if locator.count() == 0:
            continue
        href = locator.get_attribute("href", timeout=2000)
        if not href or "/company/" not in href:
            continue
        match = re.search(r"/company/([^/?#]+)", urlparse(href).path)
        if match:
            return match.group(1)
    return None


def _extract_industry(page: Page) -> str | None:
    for item in page.locator(".description__job-criteria-list .description__job-criteria-item").all():
        try:
            label = item.locator(".description__job-criteria-subheader").inner_text(timeout=1000).strip().lower()
            if "industr" not in label:
                continue
            value = item.locator(".description__job-criteria-text").inner_text(timeout=1000).strip()
            if value:
                return value
        except Exception:
            continue
    return None


def extract_company_info(page: Page, linkedin_job_url: str, headed: bool = False) -> dict:
    job_id = parse_job_id(linkedin_job_url)
    job_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    logger.info("Navigating to job page: %s", job_url)

    page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    _wait_for_login(page, headed)

    company_name = _first_text(page, COMPANY_NAME_SELECTORS)
    if not company_name:
        raise RuntimeError("Could not extract company name from LinkedIn job page")

    logger.info("Found company name: %s", company_name)

    company_slug = _company_slug(page)
    industry = _extract_industry(page)
    if industry:
        logger.info("Found company industry: %s", industry)

    company_website = search_company_website(
        page, company_name, company_slug, industry=industry
    )

    return {
        "company_name": company_name,
        "company_website": company_website,
    }
