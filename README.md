# careerscraper

Scrapes company career pages for open roles, parses requirements out of each job
description, filters against your criteria, and tracks what is new since the last run.

Same idea as `../linkedinscraper`, different source. Instead of one search API it reads a
curated registry of companies and talks to whatever backend actually serves each careers
page — Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Eightfold, Oracle Recruiting, or
a config-described custom API.

## Quick start

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt

./venv/bin/python main.py --list-companies       # what is in the registry
./venv/bin/python main.py --company gitlab       # one company
./venv/bin/python main.py                        # every enabled company
```

Results land in `data/jobs.db`, plus `out/new_jobs_<date>.csv` and `out/open_jobs.csv`.

## CLI

| Command | What it does |
|---|---|
| `main.py` | Every enabled company. Stores new jobs, exports CSV. |
| `main.py -c gitlab` | One company. `-c a,b` or repeated `-c` for several. Matches on substring. |
| `main.py -c gitlab --dry-run` | Fetch, parse, print. No database writes. |
| `main.py -c gitlab --no-filter` | Skip all filtering — see everything the board returns. |
| `main.py -c gitlab --limit 5` | Cap jobs processed. Keeps description fetches cheap while testing. |
| `main.py -c gitlab --raw` | Dump the raw upstream payload. For debugging an adapter. |
| `main.py --list-companies` | Registry with adapter, open-job count, last run. |
| `main.py --close-stale 30` | Close open jobs not seen in 30 days. `--dry-run` previews. No scraping. |
| `main.py -v` | Adds a filtered-out-reason breakdown and per-job errors. |

## Web UI

```bash
./venv/bin/python app.py                              # http://127.0.0.1:5000
PORT=5055 ./venv/bin/python app.py                    # different port
CS_CONFIG=config.sde.json ./venv/bin/python app.py    # a different config, so a different db
```

`CS_CONFIG` decides which config — and therefore which `db_path` — the UI opens; it prints
the file it read on startup. Without it the UI reads `config.json`, so a scrape run with
`--config config.sde.json` (a different `db_path`) looks like it changed nothing here.

Read-only over the same `data/jobs.db` the scraper writes — it opens SQLite in `mode=ro`, so
it can't interfere with a scrape running in another terminal.

- Full-text search across title, company, skills and description
- Filters: company, skill (with occurrence counts), location, max minimum-experience,
  work mode, first-seen date, include-closed
- **Location matching is spelling-tolerant.** Boards write one city several ways, so
  `bengaluru`, `bangalore` and `blr` all return the same 162 jobs, and a typed `karnataka`
  also matches `Karnātaka` (accents are folded). Alias groups live in `CITY_ALIASES` in
  `core/store.py`; `--show --location` uses the same rule
- Sort by newest found, lowest experience, company, title or posted date
- **Clicking a job title opens that company's own posting** in a new tab (`/apply/<id>`
  redirects to the stored `apply_url`, falling back to `job_url`)
- `details` opens the parsed view: every field, the skill chips, the `exp_snippet` evidence
  behind the experience number, and the full description text
- **★ saves a job** so you can come back to it — click the star in the list or on the detail
  page, click it again to unsave. `★ N saved` in the header (or the "★ saved only" checkbox)
  narrows to what you kept, and it composes with every other filter
- Saved jobs live in their own `data/favorites.db` (override with `favorites_path` in
  `config.json`), because the UI opens `jobs.db` **read-only** and must stay that way. They
  are keyed by the job's scrape key, not its row id, so they survive re-scrapes, a rebuilt
  database, and jobs closing and reopening
- Light and dark theme follow the OS setting

## Viewing results from the terminal

A scrape only prints the jobs that were new *that run*, so after the first run the terminal
goes quiet. To browse everything already stored — no network calls:

```bash
main.py --show                                    # every open job
main.py --show --location bengaluru --max-exp 4    # narrow it
main.py --show --skill Go --company hpe,gitlab
main.py --show --since 2026-08-01                  # only found this month
main.py --show --include-closed                    # include roles that have closed
main.py --show --skill Go --export out/go.csv      # same view, written to CSV
main.py --job 73                                   # one posting in full
```

`--show` prints one row per job with its ID, parsed experience, work mode and skills.
`--job <ID>` prints the full posting: every parsed field, the `exp_snippet` that produced the
experience number, and the description text. IDs come from the `--show` listing.

Also on disk after each run:

- `out/new_jobs_<date>.csv` — what that run found
- `out/open_jobs.csv` — full current snapshot
- `data/jobs.db` — `jobs` and `filtered_jobs` tables, queryable with any SQLite client
  (`sqlite3 data/jobs.db "select title, min_years_exp from jobs where is_open = 1"`)

## How "new" works

Career pages mostly do not publish posting dates, so the database owns the notion of new
rather than a `days_to_scrape` window:

- Identity is `(company, external_id)`, falling back to `(company, title, location)`.
- `first_seen` is stamped on insert; `last_seen` is refreshed every run.
- A job that disappears from a **successful** fetch is marked `is_open = 0` with a
  `closed_at`, not deleted. If it comes back, it reopens.
- Jobs rejected by filters go to the `filtered_jobs` table with the reason, so they are
  never re-fetched or re-evaluated.

## Requirement parsing

Every surviving description is parsed into structured columns (rule-based, no LLM):

| Column | Notes |
|---|---|
| `min_years_exp`, `max_years_exp` | Handles `5+ years`, `4 to 5 years`, `5-7 years`, `minimum 5years`, `2yrs`, `7+ yrs`. |
| `exp_snippet` | The surrounding text the number came from — so a wrong number is visible, not mysterious. |
| `skills_required`, `skills_preferred` | Vocabulary match from `config.json`, split by section heading. |
| `work_mode` | Remote / Hybrid / Onsite, from board metadata when given, else the text. |
| `education` | Degree mentions found in the required section. |

Year counts are only accepted near words like *experience*, *background*, *hands-on*, and
are rejected next to company-history phrasing (`for more than 25 years`, `in the past 3
years`). Section headings decide required vs preferred: `Nice to have` / `Bonus` /
`Preferred` go to preferred, everything else to required. Lever and SmartRecruiters hand us
real section titles; other boards get headings sniffed out of the text.

This replaces the `desc_words` string list the LinkedIn scraper used for experience. Instead
of 25 literal phrases that miss `5 - 8 yrs`, set one number:

```json
"max_min_experience": 4
```

## config.json

| Key | Meaning |
|---|---|
| `locations`, `location_exclude`, `keep_unknown_location` | Substring match on the job location. |
| `title_include`, `title_exclude` | Same semantics as the LinkedIn scraper. |
| `company_exclude` | Drop by company name. |
| `desc_words` | Literal phrases that disqualify a description. |
| `max_min_experience`, `min_min_experience` | Bounds on the parsed minimum. |
| `drop_if_experience_unknown` | Default `false` — keep jobs that never state years. |
| `require_any_skill`, `drop_if_no_skills_found` | Require at least one skill from a list. |
| `languages` | Empty = off. Non-empty needs `pip install langdetect`. |
| `skills` | Canonical skill → patterns. `re:` prefix for raw regex, `cs:` for case-sensitive. |

`Go` is matched case-sensitively on purpose — a lowercase `go` is the English verb, so
`"we go to market"` must not read as a Go job.

## companies.json

```json
{
  "name": "GitLab",
  "careers_url": "https://about.gitlab.com/jobs/all-jobs/",
  "adapter": "greenhouse",
  "params": { "board_token": "gitlab" },
  "enabled": true
}
```

**Adding a company:** ask Claude to "add \<company\>". The careers page gets inspected once,
the backend identified, the endpoint verified to return jobs, and a working entry appended.
That one-time detective work is why entries carry an adapter instead of being sniffed at
runtime — a company either works or is explicitly marked as not working.

**125 companies enabled**, every one probed live before being added (job count recorded in
each entry's `_verified`):

| Backend | Companies |
|---|---|
| Greenhouse (71) | Coinbase, Rubrik, Stripe, Datadog, MongoDB, Cloudflare, Zscaler, Elastic, Twilio, DoorDash, Airbnb, Reddit, Robinhood, Figma, Discord, Instacart, Toast, Affirm, Chime, Grafana Labs, ClickHouse, Vercel, Airtable, Coursera, Udemy, Postman, GitLab, Databricks, Harness, DevRev, Tekion, Netradyne, PhonePe, Razorpay, Groww, Jane Street, Tower Research, Pure Storage, Okta, Samsara, Dropbox, Commvault, Druva, Cockroach Labs, Neo4j, Yugabyte, Starburst, Sumo Logic, Anthropic, Scale AI, Block, Brex, Adyen, Marqeta, SoFi, Pinterest, Lyft, Asana, Flexport, Duolingo, IMC Trading, Databento, Tenable, Wiz, Netskope, Abnormal Security, Chainguard, Tailscale, Cribl, Axonius, Orca Security |
| Ashby (14) | Snowflake, Confluent, Notion, Plaid, Sentry, Temporal, Linear, Atlan, Sarvam AI, OpenAI, Ramp, Vanta, Supabase, Modal |
| Workday (19) | NVIDIA, Palo Alto Networks, CrowdStrike, Adobe, HPE (incl. Juniper), BrowserStack, Fractal, Cloudera, Visa, Cohesity, Mastercard, PayPal, Intel, Micron, Broadcom (incl. VMware), Cisco, Proofpoint, Qualys, F5 |
| SmartRecruiters (7) | Bosch, Freshworks, Canva, Arista Networks, Wise, Swiggy, Graviton |
| Eightfold (2) | NetApp, Netflix |
| Lever (4) | Meesho, CRED, Sophos, Sysdig |
| Oracle Recruiting (3) | American Express, Fortinet, Uber |
| custom_json (2) | Atlassian, Amazon |
| custom_html (4) | Google, Nutanix, Intuit, Marqeta |

The big-enterprise entries took the most digging, and the notes in each entry say why:
Cisco's careers site is a Phenom front end, but every posting's `applyUrl` points at Cisco's
own Workday tenant, so the `workday` adapter reads it directly (Splunk roles appear there
too); Fortinet's careers page redirects straight into Oracle Recruiting; F5's Workday tenant
is `ffive`, not `f5`; Wiz's Greenhouse token is `wizinc`, not `wiz`. Intuit runs a Radancy board whose
results endpoint wraps HTML inside JSON, so `custom_html` reads its server-rendered pages
instead — 15 jobs a page, 31 pages for the whole board.
Visa's careers page is Akamai-protected but the jobs sit in Workday site `Visa`;
Cohesity renders client-side over Workday site `Cohesity_Careers`; Nutanix's careers site is
a marketing shell in front of a Jobvite board; American Express looks like Eightfold but its
Eightfold tenant answers `count=0`, with the real listing on Oracle Recruiting.

Amazon comes through `amazon.jobs/search.json`, filtered to Indian cities by exact
`normalized_location` strings, with `basic_qualifications` / `preferred_qualifications`
mapped onto the required/preferred buckets. Google's results page is server-rendered, so
`custom_html` reads it without a browser — but its class names (`lLd3Je`, `QJPWVe`,
`r0wTof`) are build hashes and will break on a redesign; if Google reports 0 jobs, re-check
those selectors first. Uber has since moved to Oracle Recruiting — its old
`loadSearchJobsResults` API 404s — which turned 18 description-less titles into the full
516-job board with descriptions.

**Disabled, with the reason recorded in the entry:**

| Company | Why |
|---|---|
| IBM | List renders client-side; `careers.ibm.com` and `ibmglobal.avature.net` answer 202 with an empty body (Akamai bot protection). |
| Goldman Sachs | `higher.gs.com` ships no job data or API reference in its HTML. |
| D. E. Shaw | Bespoke portal, no marker. (The Lever board `dexterity` is an unrelated robotics company — do not use it.) |
| Qualcomm | Eightfold tenant exists but its jobs API answers `403 {"message": "Not authorized for PCSX"}` — the config is right, the board is gated. |
| Microsoft | `gcsservices.careers.microsoft.com` resolves through Azure Front Door but every request fails to connect, with and without browser headers. Likely TLS-fingerprint filtering. |
| Apple | `jobs.apple.com/api/role/search` answers 301 → `apple.com/pagenotfound` for non-browser callers. |

All of these need either a browser session or a network-tab capture of the real search call.

### Partial fetches never close jobs

Workday and SmartRecruiters entries often narrow server-side (`search_text`, `q`, `country`)
or stop at `max_jobs`. That means the run saw a slice of the board, not all of it, so absence
does not imply a job closed. Adapters flag those fetches and `mark_closed` is skipped —
otherwise every run would close and reopen jobs outside the current slice.

The cost is that those companies never close anything, and neither does a company whose
board fails outright (a renamed Greenhouse token 404s, and a failed fetch must never be read
as "everything closed"). Their rows stay open indefinitely and their landing pages rot.
`--close-stale DAYS` is the backstop:

```bash
main.py --close-stale 30 --dry-run    # what would close, grouped by company
main.py --close-stale 30              # close it
```

It closes by age alone, so it only tells the truth if you scrape regularly — a 7-day window
right after a month of not running would close jobs that are still perfectly open. Closing
stays reversible either way: if a board lists the job again, the next run reopens it.

## Adapters

| Adapter | Endpoint | Descriptions |
|---|---|---|
| `greenhouse` | `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` | In list |
| `lever` | `api.lever.co/v0/postings/<token>?mode=json` | In list, with section titles |
| `ashby` | `api.ashbyhq.com/posting-api/job-board/<token>` | In list |
| `workday` | `POST <host>/wday/cxs/<tenant>/<site>/jobs` | Detail call per job |
| `smartrecruiters` | `api.smartrecruiters.com/v1/companies/<token>/postings` | Detail call per job |
| `eightfold` | `<host>/api/apply/v2/jobs?domain=<domain>` | Detail call per job |
| `oracle_ce` | `<pod>/hcmRestApi/resources/latest/recruitingCEJobRequisitions` | Detail call per job, as sections |
| `custom_json` | Any JSON API, described entirely in `params` | In list, or optional detail call |
| `custom_html` | Server-rendered HTML + CSS selectors | Optional detail call |

Because four adapters need a detail call per job, cheap filters (title, location, company)
run **before** descriptions are fetched — typically cutting detail requests by ~90%.

Enterprise Workday tenants list thousands of roles 20 at a time, so narrow them with
`search_text` (string or list), `applied_facets`, and `max_jobs`. SmartRecruiters supports
`q` and `country` server-side.

`eightfold` keys off `domain` (the tenant's own domain — `netapp.eightfold.ai` answers only
for `domain=netapp.com`), and takes `location` (string or list) plus `max_jobs`. It ignores
any `num` above 10, so paging steps by what a page actually returned.

`oracle_ce` needs the Oracle pod host, not the public careers host: `careers.<company>.com`
is a skin over `<pod>.fa.<region>.oraclecloud.com`, which the careers page HTML reveals. Two
quirks are baked into the adapter — `expand=requisitionList.secondaryLocations` is mandatory
(without it the response reports a job count but carries no jobs), and paging lives inside
the `finder` string with a 200-per-page cap.

`custom_json` covers a company that serves its own API — Atlassian is the worked example.
Where a board splits the description across fields, map them with `sections`:

```json
"sections": { "Overview": "overview", "Responsibilities": "responsibilities",
              "Qualifications": "qualifications" }
```

Section headings then feed required-vs-preferred bucketing instead of being one flat blob.

There is no headless browser. A JavaScript-only careers page yields 0 jobs and gets flagged
in the run summary as possibly stale.

## Layout

```
main.py                  CLI + orchestration
app.py                   Flask web UI (read-only over the same database)
templates/               base.html, jobs.html, job.html
companies.json           curated company registry
config.json              filters, skill vocabulary, paths
adapters/                one module per backend + two config-driven generics
core/http.py             retry, backoff, rate limiting
core/text.py             HTML → text, dotted-path lookup
core/requirements.py     experience / skills / work mode / education parsing
core/filters.py          cheap and deep filters, dedupe
core/store.py            SQLite schema, new/closed tracking, CSV export
core/favorites.py        starred jobs, in their own writable database
```

## Failure handling

One bad board never ends a run. Per company: the error is caught, recorded, and the run
summary prints `fetched / seen / new / filtered / closed` per company followed by failures.
A company returning 0 jobs is called out explicitly — that is how a silently changed API
gets noticed. Exit code is non-zero only when every company failed.
