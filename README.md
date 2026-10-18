# Health Data Ingestion & Analytics Platform

Ingests Indian government health data, merges it across sources, and computes
the Key Performance Indicators behind the General Population report template.

Everything lives in `platform/` — a FastAPI backend over PostgreSQL.

## Run

```bash
cd platform/backend
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
| 3. Merge | `POST /datasets/merge` — joins each source's master on shared geography → `dashboard_master` |
| 4. Read | `GET /datasets/merge`, `GET /warehouse/{connector}/rows` |
| 5. Export | `POST /exports/csv` — a whole layer, or one file per dataset |

Steps 1 and 3 run as background jobs; poll `GET /jobs/{job_id}`.

## KPIs

Three surfaces, deliberately separate:

| Endpoint | What |
| --- | --- |
| `GET /kpi/dashboard?survey_id=1` | The 28 fixed dashboard cards (KPI-01…KPI-28) |
| `GET /kpi/catalog` + `POST /kpi/compute` | The template's 227 indicators |
| `POST /kpi/generate` | Claude proposes ~10 KPIs for whatever columns the data has |

The first two are deterministic and need no API key. In the third, Claude
proposes *which* computations to run and pandas executes them — it never
produces a number, and a KPI naming a column that does not exist is rejected
rather than guessed.

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
times. The rule, stated once in `app/modules/kpi/aggregation.py` and used by
every KPI surface: **sum each column over the frame that column belongs in** —
census over one row per district, HMIS over every row.

## Data sources

| Source | Connector | Notes |
| --- | --- | --- |
| Census 2011 | `census_india` | NADA API, district level up |
| HMIS | `hmis_reports` | Standard Reports F.1, latest 2 years by default |
| data.gov.in | `data_gov_in` | Needs `DATA_GOV_IN_API_KEY` |
| Any CSV/Excel/HTML | `csv_excel`, `html_table` | By URL or upload |

Downloaded reports are deleted once their rows are committed
(`PLATFORM_DELETE_SOURCE_FILES_AFTER_INGEST`); each survey caches into its own
directory, so one survey's cleanup never removes another's files. The
`.meta.json` sidecars are kept as the record of which file version produced
which rows.

## Scripts

```bash
python rebuild_hmis_raw.py --survey-id 1 --last 5   # rebuild hmis_raw from cached reports
python purge_source_files.py --dry-run              # clear downloaded source files
python migrate_sqlite_to_postgres.py                # one-off SQLite → PostgreSQL
python extract.py <url>                             # ad-hoc extraction from one URL
```
