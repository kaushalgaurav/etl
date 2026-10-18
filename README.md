# Health Data Ingestion & Analytics Platform

Ingests Indian government health data, merges it across sources, and computes
the Key Performance Indicators behind the General Population report template.

Not a "web scraper": ingestion runs through a connector framework that prefers
official APIs and downloadable files over HTML, and falls back to browser
automation only when nothing else is available.

```
URL / Upload -> SOURCE DETECTION -> RAW -> CLEAN -> MASTER (per connector)
             -> MERGE on shared geography -> dashboard_master
             -> KPIs (fixed catalogues, or Claude-proposed + engine-executed)
             -> Dashboard / CSV / PDF
```

## Run

```bash
pip install -r requirements.txt
cp .env.example .env          # then set PLATFORM_DATABASE_URL
alembic upgrade head          # migrations are a deploy step, not a startup side effect
uvicorn app.main:app --reload
```

Swagger is at `/docs`. Without `PLATFORM_DATABASE_URL` it falls back to SQLite.

## The pipeline

Every stage is scoped by `survey_id`, which is what links the sources: census
and HMIS fetched under the same survey describe the same places, so merging
them gives a dashboard a denominator beside a measure.

| Step | Endpoint |
| --- | --- |
| 1. Fetch | `POST /ingest/fetch` — Census NADA API, HMIS Standard Reports, CSV/Excel, HTML |
| 2. Build layers | `POST /warehouse/{connector}/rebuild` — raw → clean → master |
| 3. Merge | `POST /datasets/merge` — joins each source's master into `dashboard_master` |
| 4. Read | `GET /datasets/merge`, `GET /warehouse/{connector}/rows` |
| 5. Export | `POST /exports/csv` — a whole layer, or one file per dataset |

Steps 1 and 3 run as background jobs; poll `GET /jobs/{job_id}`.

## Code layout

One module per section under `app/modules/`, with shared layers around them:

```
app/
  main.py                    app factory only — no business logic
  core/config.py             settings
  db/                        base, session, init_db
  models/                    one file per table (dataset, source, job, kpi)
  crud/                      dataset persistence, warehouse row reader
  constants/                 canonical_fields, kpi_catalog, hmis_indicators,
                             dashboard_kpis, district_crosswalk
  helpers/dataframe.py       snake_case, json_safe_records
  routes/__init__.py         aggregates every module router
  modules/
    ingestion/               connectors/, detector.py, cleanup.py, service.py
    census_warehouse/        census raw -> clean -> master
    hmis_warehouse/          hmis raw -> clean -> master
    warehouse/               one router serving every connector's layers
    merging/                 semantic.py, survey.py, run.py -> dashboard_master
    kpi/                     catalog, dashboard, dynamic (Claude), aggregation
    cleaning/                deterministic cleaning operations
    profiling/               column stats
    analytics/               run_computation — the fixed computation vocabulary
    datasets/                browse datasets and rows
    reports/                 CSV / PDF export
    sources/                 registry + seed_sources.json
    jobs/                    background worker pool
```

Each module holds its own `router.py`, `service.py` and `schemas.py`, so a
section can be read, changed or replaced on its own.

### Connectors (`app/modules/ingestion/connectors/`)

| Connector | Handles | Status |
| --- | --- | --- |
| `census_india` | Census of India NADA API — PCA indicators, long→wide | Working |
| `hmis_reports` | HMIS Standard Reports tree (F.1 by default, latest 2 years) | Working |
| `rest_api` | Generic JSON REST endpoints | Working |
| `data_gov_in` | data.gov.in resource API (`DATA_GOV_IN_API_KEY`) | Working |
| `csv_excel` | CSV/TSV/XLSX uploads and direct links | Working |
| `html_table` | Static pages — picks the largest `<table>` | Working |
| `dynamic_site` | JS-rendered pages via Playwright | Interface only, not installed |

### Source detection and the priority ladder (`ingestion/detector.py`)

API access, file download and HTML parsing are separate code paths that never
mix. The detector only chooses between them, and never starts with a browser:

```
1. Official API           -> rest_api / data_gov_in / census_india
2. Downloadable CSV/Excel -> csv_excel
3. Raw JSON/XML payload   -> rest_api
4. HTML table on the page -> html_table
5. JS-rendered page       -> dynamic_site
```

A page that merely *links* to a CSV is a file source, not a scraping target,
so the probe looks for download links before considering table parsing.

`POST /ingest/probe` returns the full capability report — content type, download
links found, table count, whether the markup is unrendered JS templates, whether
TLS verification failed — plus which connector it recommends and why.

### Semantic layer (`app/modules/merging/semantic.py`)

Canonical field dictionary + fuzzy matching, so `District`, `district_name`
and `DISTRICT_NM` resolve to the same field and datasets merge correctly.
Matching is deterministic (difflib, no LLM call) in three tiers: exact alias
(confidence 1.0) → fuzzy canonical (ratio) → raw-name fuzzy (capped at 0.8).

Census and HMIS additionally share `state_id` / `district_id` / `unique_id`
from `constants/district_crosswalk.tsv`, so the merge joins on ids rather than
on place names.

### Background jobs (`app/modules/jobs/`)

A national Census pull is ~33 paginated calls and an HMIS crawl runs to
thousands of files, so ingestion never blocks an HTTP request. The endpoint
returns a job id immediately; poll `GET /jobs/{id}` for live progress. Work
runs on a small thread pool — capped at 2 workers, which is also politeness
toward the source portals. Jobs persist in the database, so status survives a
restart, and any left `running` by a crash are reaped on the next startup.

### Source registry (`app/modules/sources/`)

Data for a real report never comes from one URL. The registry makes each
source a row — connector, config, geography, publisher, licence — so many
different portals run through one pipeline:

```
POST /sources          register a source
POST /sources/seed     load seed_sources.json (version-controlled)
POST /sources/{id}/run enqueue ingest + auto-clean -> returns a job id
POST /sources/run-all  enqueue every enabled source, collecting failures
```

Adding a source is a JSON entry, not code — see
`app/modules/sources/seed_sources.json`.

## KPIs

Three surfaces, deliberately separate:

| Endpoint | What |
| --- | --- |
| `GET /kpi/dashboard?survey_id=1` | The 28 fixed dashboard cards (KPI-01…KPI-28) |
| `GET /kpi/catalog` + `POST /kpi/compute` | The template's 227 indicators |
| `POST /kpi/generate` | Claude proposes ~10 KPIs for whatever columns the data has |

The first two are deterministic and need no API key.

### Claude's role

Claude **never** touches the database, writes SQL, or produces a number. It
receives the column profile and a row sample and returns a structured proposal
naming *which* computations to run; pandas executes them from the declared
formula. Two safeguards are enforced in code rather than asked for in the
prompt: every column a proposed KPI names is checked against the real frame, so
one that does not exist is rejected with the reason, and a zero denominator
returns null rather than infinity.

Each indicator reports whether it is `computed` (from the source the template
names), `proxy` (HMIS has a service-based equivalent for an NFHS indicator not
ingested), or `unavailable` with the reason. A blank cell and a wrong cell are
not the same kind of wrong.

`app/constants/kpi_catalog.tsv` holds the template's 227 indicators — theme,
name, definition and named source — extracted from the workbook. It is the
system's copy of that specification.

## Aggregation rule

Census describes a district once; HMIS describes it once per reporting period,
so the merge repeats census values across a district's HMIS rows. Summing a
census column over the merged frame therefore counts each district several
times — total population came out near 4.4 billion against India's actual
1,210,854,977. The rule, stated once in `app/modules/kpi/aggregation.py` and
used by every KPI surface: **sum each column over the frame that column belongs
in** — census over one row per district, HMIS over every row. A ratio of two
columns from the same source is immune either way, which is what makes the bug
easy to miss: the percentages look right while the counts are billions out.

## Storage, lineage and CSV output

Warehouse layers are shared tables (`census_raw`, `hmis_master`,
`dashboard_master`, …). Every row carries `_dataset_id`, `_ingested_at` and
`survey_id`, so a table holding many ingests can always be read back as the
one that produced it.

`POST /exports/csv` has two modes:

```
?layer=dashboard_master&survey_id=1    one CSV for the whole layer — the data view
?survey_id=1                           one CSV per dataset — the provenance view
```

The per-dataset mode folders by how the data was acquired, and names each file
`<category>__<source>__<name>__<id>.csv`:

```
data/exports/
  api/     api__census__census_2011_pca_state_35_district__9e8fc733.csv
           api__hmis__hmis_standard_reports_a_n_islands__38416881.csv
  web/     web__html_table__list_of_states_by_population__3bf4c7ab.csv
  file/    file__csv_excel__pop_csv__4438fb08.csv
  merged/  merged__merge__dashboard_master_survey_1__f92ff4fd.csv
```

The category alone cannot say *which* source a file came from — census and
HMIS are both API pulls — so the connector is in the name too. The layer mode
accepts the same column filters as `GET /warehouse/{connector}/rows`.

Downloaded source files are deleted once their rows are committed
(`PLATFORM_DELETE_SOURCE_FILES_AFTER_INGEST`); each survey caches into its own
directory, so one survey's cleanup never removes another's files. The
`.meta.json` sidecars are kept as the record of which file version produced
which rows.

## extract.py — one URL in, one CSV out

A standalone CLI for when you just want data from a URL, without running the
server. Each acquisition method is a **separate function**:
`extract_from_api`, `extract_from_file`, `extract_from_web`,
`extract_from_census` — nothing shared between them beyond the result shape.

```bash
python extract.py https://example.gov.in/data.json         # auto-detect
python extract.py https://example.gov.in/report --type web # force an extractor
python extract.py ./downloads/districts.csv                # a local file
python extract.py census://districts                       # all 640 districts
python extract.py census://districts?state=10              # one state
python extract.py <target> --clean                         # normalize first
```

Flags: `--type`, `--out`, `--name`, `--table-index`, `--clean` (snake_case
headers, trim, numeric coercion, drop empties/duplicates — off by default so
the CSV mirrors the source).

## Other scripts

```bash
python rebuild_hmis_raw.py --survey-id 1 --last 5   # rebuild hmis_raw from cached reports
python purge_source_files.py --dry-run              # clear downloaded source files
python migrate_sqlite_to_postgres.py                # one-off SQLite -> PostgreSQL
```

## Registered sources

| Source | Theme | Coverage | Status |
| --- | --- | --- | --- |
| Census 2011 PCA (all India) | Demographic | 640 districts x 85 indicators | **Verified end-to-end** — national total matches the official 1,210,854,977 |
| HMIS Standard Reports F.1 | Service delivery | District level, by reporting period | **Working** — one row per record, ~360 indicator columns |
| NFHS-5 district factsheets | RMNCH/Nutrition/NCDs | 707 districts x 109 fields | Schema verified; rows blocked on API key |
| NFHS-5 state/UT factsheets | RMNCH/Nutrition/NCDs | State level | Resource id confirmed; blocked on API key |
| India TB Report 2023 | Communicable diseases | 32 resources | Catalog verified; blocked on API key |
| NACO HIV prevalence / PLHIV / deaths | Communicable diseases | State/UT | Catalog verified; blocked on API key |
| AYUSH hospitals/beds/dispensaries | Infrastructure | State/UT | Catalog verified; blocked on API key |
| Blood Bank Directory | Infrastructure | Facility level | Catalog verified; blocked on API key |
| Road Accidents in India 2022 | NCDs | 58 resources | Catalog verified; **disabled** — MoRTH 2022, template cites NCRB 2021 |

"Blocked on API key" means the resource ids and schemas are confirmed real, but
`api.data.gov.in` refuses bulk reads on the shared sample key. Set
`DATA_GOV_IN_API_KEY` and re-run.

### Sources NOT found on data.gov.in

Scanning all 1,976 health-sector catalogs turned up nothing for these, so they
need their own connector (their portal, or a published PDF/Excel):

- **RHS (Rural Health Statistics)** — SHC/PHC/CHC/SDH/DH/HWC counts, i.e. most
  of the Infrastructure sheet. Published by MoHFW, not in the OGD catalog.
- **MoTA / tribal** — tribal PHCs, tribal facility counts, tribal HRH.
- **ABDM / ABHA** — ABHA IDs, registered professionals, registered facilities.
- **e-Rakthkosh** — the blood-bank source the template actually cites.
- **Anganwadi (AWC) counts**, **government medical college counts**.

This is why 187 of the template's 227 indicators report `unavailable`: the
NCDs, Infrastructure and HRH sheets have no ingested source yet.

## Not built yet

Multi-user auth/projects, scheduled + incremental ingestion, dataset
versioning, source licence/compliance capture, fuzzy *value* matching
(`New Delhi` vs `New Delhi District`), and the React frontend.
