import argparse
import json
import logging
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from career_finder import find_career_page
from job_finder import find_open_position
from linkedin_extractor import extract_company_info

STORAGE_STATE_PATH = Path("storage_state.json")


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def validate_linkedin_url(url: str) -> None:
    if not url or "linkedin.com" not in url:
        raise ValueError("URL must be a linkedin.com job link")
    if "currentJobId=" not in url and "/jobs/view/" not in url:
        raise ValueError("URL must contain currentJobId= or /jobs/view/{id}")


def run_pipeline(linkedin_job_url: str, headed: bool) -> dict:
    logger = logging.getLogger(__name__)
    logger.info("Starting pipeline for: %s", linkedin_job_url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context_kwargs = {}
        if STORAGE_STATE_PATH.exists():
            context_kwargs["storage_state"] = str(STORAGE_STATE_PATH)

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        try:
            company_info = extract_company_info(page, linkedin_job_url, headed)
            career_page_url = find_career_page(page, company_info["company_website"])
            job_info = find_open_position(page, career_page_url)

            result = {
                "company_name": company_info["company_name"],
                "career_page_url": career_page_url,
                "job_url": job_info["job_url"],
            }
            logger.info("Pipeline complete")
            return result
        finally:
            context.storage_state(path=str(STORAGE_STATE_PATH))
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Job Source Agent")
    parser.add_argument("--url", required=True, help="LinkedIn job URL")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser visibly (required for first-time LinkedIn login)",
    )
    args = parser.parse_args()

    configure_logging(args.verbose)
    logger = logging.getLogger(__name__)

    try:
        validate_linkedin_url(args.url)
        result = run_pipeline(args.url, args.headed)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        logger.error("%s", exc)
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
