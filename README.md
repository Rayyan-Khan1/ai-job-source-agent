# AI Job Source Agent

Minimal CLI that takes a LinkedIn job URL, extracts company info, finds the careers page, and returns one open position.

## Workflow

```
LinkedIn Job URL
  → Extract company name (LinkedIn job page)
  → Find official company website (DuckDuckGo search + brand-match validation)
  → Find careers page (AI agent picks the link, falls back to common paths)
  → Find one open position (AI agent, with sanity checks on its picks)
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Usage

First run (LinkedIn login required once):

```bash
python main.py --url "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4412053174" --headed
```

Sign in to LinkedIn in the browser window. Session is saved to `storage_state.json`.

Normal run:

```bash
python main.py --url "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4412053174"
```

## Example output

```json
{
  "company_name": "MongoDB",
  "career_page_url": "https://www.mongodb.com/company/careers",
  "job_url": "https://www.mongodb.com/careers/job/?gh_jid=7310506"
}
```

## Notes

- Accepts LinkedIn URLs with `currentJobId=` or `/jobs/view/{id}`.
- Company website is discovered via DuckDuckGo search using the company name and LinkedIn slug.
- Career page discovery uses a Gemini AI agent to identify the careers link, with fallback to common paths (`/careers`, `careers.<domain>`, etc.).
- Job finding uses a 3-pass AI agent flow (direct listing → gateway/portal link → listing on portal page), with heuristic sanity checks on every AI pick to reject blog posts, listing pages, and non-job links mistaken for an individual posting.
- Sites protected by enterprise bot-detection (Akamai, Cloudflare WAF, etc.) will block headless browser access entirely — these are a known limitation and cannot be worked around without bypassing security measures.
