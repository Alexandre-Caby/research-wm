# Core Execution Engine

This package holds the ingestion, processing, and worker modules. Each stage is one module
with a single job and a `run_x()` entry point, invoked as `python -m core.<stage>`.
Operational state lives in a SQLite control plane; the chunk vectors and metadata live in a
LanceDB data plane. `run_pipeline.sh` orchestrates the stages.

## Pipeline order

```
bootstrap -> ingest_papers -> ingest_code -> extract -> chunk -> embed -> report -> purge
```

## Layout

- **`config.py`** — `TOPIC` (from the `research-<topic>` folder), storage paths,
  `EMAIL_CONTACT`/`GITHUB_TOKEN`/`HF_TOKEN`, chunk parameters, the embedding model, device
  selection, `WEIGHT_EXTENSIONS` (the checkpoint blacklist enforcing the no-weights rule),
  and `rel_path`/`abs_path` for the storage-relative paths the registry persists — both
  raise on a path that lies outside the lake rather than escaping it. All paths derive from
  `__file__`.
- **`log.py`** — logging setup (console + `storage/pipeline.log`).
- **`catalog.py`** — the hand-curated seed catalog: the four `catalog_status` constants
  (`catalog` | `undecided` | `watch` | `excluded`), `CATALOG_SEED` (models with their
  status, org, and provenance note), `WATCH_ONLY` and the `WATCH_SEED` rows derived from
  it, `SCAN_ORGS`, and `EXCLUDED_REPOS`. Seeded into `models` by `registry.seed_catalog`.
- **`registry.py`** — the SQLite control plane. `papers`, `repos`, `models`, and `cursors`
  tables, the status FSM, atomic upserts that preserve `created_at` and never overwrite a
  stored `content_hash` with a null one, the storage-relative path guard enforcing C3 at
  the write boundary, `checkpoint_wal` for the move to the server, dedup keys, and
  per-source pagination cursors stored as opaque JSON.
- **`bootstrap.py`** — creates the schema and seeds the catalog into a fresh lake; the
  first stage `run_pipeline.sh` runs.
- **`sources/`** — one collector per source, each normalizing into a shared
  `discover(block, query, cursor, per_page)` contract so a new source is a new file, not a
  new pipeline.
  - `openalex.py`, `arxiv.py`, `semanticscholar.py`: paper discovery. arXiv carries the
    field — it publishes as preprints, so it is not optional here.
  - `resolvers.py`: the PDF cascade — direct arXiv/OA links, then Unpaywall, then Semantic
    Scholar's `openAccessPdf`.
  - `github.py`: repository discovery (org scan, topic search, single repo) and the
    tarball collector — resolves the commit SHA, streams the archive, filters it to text,
    and deletes it in the same pass. Rejects weight artifacts, oversized and binary files,
    excluded trees, and tar members whose path escapes the extraction root.
- **`ingest_papers.py`** — discovery across sources × query blocks with per-pair cursors,
  an in-RAM `O(1)` skip before any network call, then resolve and fetch on a thread pool
  (network only — the sqlite connection stays on the main thread). Fetches a PDF, or
  scrapes HTML straight to Markdown, or records `paywalled`.
- **`ingest_code.py`** — drives the three GitHub discovery modes, skips known and excluded
  repositories before spending a call, and records `extracted` / `empty` / `duplicate` per
  repository. Pins the commit SHA except for HY-World, which tracks HEAD.
- **`extract.py`** — PDF → Markdown with fitz across a process pool, boilerplate stripping,
  heading detection and a quality score, with per-paper registry checkpoints. Repositories
  skip this stage: they are already text.
- **`chunk.py`** — both kinds. Papers use the front-matter parser and section-aware word
  windows; code is cut at `def`/`class` boundaries with a sliding line-window fallback, and
  every code chunk carries a `# <repo> / <file> / <symbol>` header so a retrieved chunk is
  self-describing. Writes one chunk JSON per entity into Tier 4.
- **`encoders.py`** — the `bge-m3` text encoder: lazy-loaded, batched, L2-normalized, CUDA
  when available.
- **`embed.py`** — batch-encodes every chunk in mini-batches and upserts one row per chunk
  into LanceDB, then removes the temporary chunk JSON files after successful insertion.
- **`store.py`** — LanceDB access: the chunk table schema, idempotent upsert by
  `chunk_id`, vector search (the RAG entry point), and Parquet export.
- **`report.py`** — QC statistics over the registry.
- **`purge.py`** — frees disk space by clearing the temporary Tier 1 staging area only.

**Rule**: all modules resolve paths from `__file__` (via `config.py`) so they stay
deployment-agnostic.
