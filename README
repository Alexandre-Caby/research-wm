# World Models Research Data Pipeline

A dynamic data center built from the world-models literature and from the open source code
that implements it. The pipeline discovers papers and repositories across open sources,
extracts and chunks their text, and embeds those chunks into a vector store — serving both
a semantic-search / RAG index and a versioned dataset export. It is designed to keep
growing as the field evolves and as new sources are added.

## Scope Doctrine

Three rules constrain every design decision in this repository. They are not preferences;
they are the reason the corpus is a data lake and not a folder of clones.

1. **Code and papers, never weights.** No checkpoint is ever downloaded, stored, or
   exported. `config.WEIGHT_EXTENSIONS` rejects checkpoint artifacts at ingestion.
2. **Built off-server, consulted on demand.** The corpus is a knowledge base queried
   through the vector index, not a set of repositories cloned and kept in sync locally.
   The target machine has no CUDA (Vulkan/RADV only), so these models cannot be executed
   there; the volumetry does not suit local replication; and the actual need is
   *consultation*, not *execution*.
3. **Consultation, not execution.** Nothing in this pipeline runs a world model. It reads
   what world models are made of.

## Directory Structure

```text
research-<topic>/
├── README                    # Project documentation
├── run_pipeline.sh           # bootstrap -> ingest -> extract -> chunk -> embed -> report
├── requirements.txt          # Python dependencies
├── core/                     # Pipeline package (one module per stage)
│   ├── config.py             # TOPIC autodetect, paths, tokens, model, query blocks,
│   │                         #   code filter, WEIGHT_EXTENSIONS
│   ├── registry.py           # SQLite control plane (status FSM, dedup, cursors)
│   ├── catalog.py            # Seed catalog: hand-curated models, provenance, exclusions
│   ├── bootstrap.py          # Schema init + catalog seeding
│   ├── log.py                # Logging setup
│   ├── report.py             # QC statistics
│   ├── purge.py              # Tier 1 cleanup
│   ├── sources/              # openalex.py, arxiv.py, semanticscholar.py, resolvers.py,
│   │                         #   github.py
│   ├── ingest_papers.py      # Paper discovery -> resolve -> PDF/HTML -> Tier 1/3
│   ├── ingest_code.py        # Repository tarball -> filtered text -> Tier 3
│   ├── extract.py            # PDF -> Markdown (fitz, quality-scored)
│   ├── chunk.py              # Papers and code -> chunk JSON in Tier 4
│   ├── encoders.py           # Text encoder (bge-m3), batched, device auto
│   ├── embed.py              # Chunks -> embeddings -> LanceDB               (phase 4)
│   └── store.py              # LanceDB chunk table: search + parquet export  (phase 4)
└── storage/                  # Multi-tier data lake
    ├── 1_raw_data/           # Temporary landing zone
    ├── 2_register_data/      # SQLite registry (<topic>_registry.db)
    ├── 3_exploitable_data/   # Clean normalized text (papers/ and code/)
    └── 4_vector_data/        # Chunk JSON + LanceDB table (vectors + metadata)
```

## Naming & Directory Conventions

- **Root directory rule**: the root must follow `research-xxx` (here `research-wm`).
- **Dynamic registry mapping**: the `xxx` suffix is extracted at runtime to instantiate the
  ledger at `storage/2_register_data/xxx_registry.db`. No manual configuration when cloning
  this architecture for a new topic.
- **Fixed data-lake hierarchy**: the folder names inside `storage/` are locked and
  immutable.

## Corpus Catalog

The catalog is the seed list of code repositories. It is transcribed here as recorded; the
provenance column keeps the audit trail rather than restating a claim as verified.

| Repository | Org | Provenance note |
|---|---|---|
| ABot-World | `amap-cvlab` | existence confirmed, pass of 2026-07-18 |
| AlayaWorld | `AlayaLab` | existence confirmed, pass of 2026-07-18 |
| DreamX-World | `AMAP-ML` | existence confirmed, pass of 2026-07-18 |
| HY-World | `Tencent-Hunyuan` | **do not pin a version** — already superseded by releases > 2.0 on the org |
| Gamma-World | `nv-tlabs` | — |
| PiD | `nv-tlabs` | — |
| PhysForge | `HKU-MMLab` | ICML 2026 |
| Cosmos (+ cookbook) | `nvidia-cosmos` | — |
| V-JEPA 2 | Meta / FAIR | added 2026-08-01, **never audited individually** (stars / licence / activity) |
| DreamerV3 | `danijar` | added 2026-08-01, carried over from the original CDC, **never re-evaluated** |
| DreamerV3 (PyTorch port) | `NM512` | from the unformalized training-sourcing backlog |
| commaVQ | `commaai` | from the unformalized training-sourcing backlog |

### Excluded by name

- **ReactiveGWM** (`INV-WZQ`) — CC BY-NC 4.0 plus a Capcom ROM dependency. Licence never
  gates ingestion under open discovery, but this exclusion predates the pipeline and is
  carried forward: `models.catalog_status = 'excluded'`, reason recorded in `core/catalog.py`.

### Undecided

`Genesis-Embodied-AI/Genesis` and `Farama-Foundation/Gymnasium` were in the original CDC
and disappeared from the live list **with no recorded reason**. They are seeded as
`models.catalog_status = 'undecided'` — recorded, but not endorsed as catalog entries —
until the decision is explicitly made in either direction. Both orgs are in `SCAN_ORGS`, so
open discovery collects their code regardless of that flag.

### Closed or not self-hostable — passive watch only

Google Genie, Panoworld, Arbord 3D, Tri-splat, Perception DLM, AlphaEvolve, Seedance 2.5,
Scope, Omni Contact. Tracked for literature and announcements; no code ingestion is
possible. Seeded as `models.catalog_status = 'watch'` rows with no `repo_full_name`: the
row is the provenance record that the system was surveyed and left uncollectable.

### Unformalized backlog — the "training" sourcing track

A second list survives from the *building* world models track, never formalized as a track
of its own. Its figures date from June 2026 and are stale by construction. Its three
repositories **are** in the catalog (`catalog_status = 'catalog'`, note
`unformalized training-sourcing backlog`) so their provenance is recorded; the environments
and tooling below have no catalog entry and reach the corpus only through open discovery.

- Repositories: `NM512/dreamerv3-torch`, `nvidia-cosmos/cosmos-predict2.5`,
  `commaai/commavq`
- Environments: Minigrid, Craftax, dm_control
- Tooling: lightning, torchrl, lerobot

## Data Lifecycle

The SQLite registry tracks each entity through an explicit status machine, in the same
shape as the sibling pipelines:

```
discovered -> fetched -> extracted -> chunked -> embedded
    └-> paywalled | empty | duplicate | failed   (terminal)
```

Licence never gates ingestion. ReactiveGWM stays out by name via
`models.catalog_status = 'excluded'`, with its reason recorded in `core/catalog.py`.

## Architecture

Two ingestion paths with different mechanics converge on one shared text-processing path.

### Sources

| Source | Role |
|---|---|
| OpenAlex | Primary paper discovery, polite pool via `WM_CONTACT_EMAIL` |
| arXiv API | Second paper discovery pass over `cs.LG`, `cs.CV`, `cs.RO`, `cs.AI` |
| Semantic Scholar | Third paper discovery pass and citation-graph expansion from catalog papers |
| Unpaywall / S2 `openAccessPdf` | PDF resolution cascade after direct arXiv candidates |
| GitHub | Repository discovery: org scan over the catalog orgs, topic/keyword search, and links harvested from ingested paper Markdown |

Europe PMC is excluded: it is biomedical and returns nothing relevant here.

### Code acquisition

A repository is never cloned. Its tarball at the pinned commit is downloaded to Tier 1,
walked and filtered into text, written to `storage/3_exploitable_data/code/<org>__<name>/`,
and the archive is deleted in the same pass — one API call per repository. `pin_policy` is
`sha` for every repository except HY-World, which is `head`, since its releases already
move past the pinned baseline.

### Corpus scope

Discovery is open on both sides of the corpus, not limited to the catalog list. Licence is
recorded on every repository (`repos.licence`) so a training-corpus export can filter by it
later, but it never blocks collection — the sole exception is ReactiveGWM, excluded by name
for the reasons in `core/catalog.py`.

### Code chunk grain

Code chunks are symbol-level: `core/chunk.py` cuts Python at `def`/`class` boundaries so a
chunk is a whole symbol, and falls back to a sliding line window for every other language.
No parser library is carried — a regex on definition lines is enough at this grain and
keeps the dependency set to what `requirements.txt` already pins. Every code chunk opens
with a `# <repo> / <file> / <symbol>` header so a retrieved chunk is self-describing. The
filter drops `tests/`, vendored and generated trees, binaries and oversized files before
anything reaches Tier 3, which is what makes open-ended repository discovery affordable.
Papers use the section-aware Markdown chunker in the same module.

### Chunk storage

One LanceDB table, `chunks`, keyed by `chunk_id`, holds both kinds — shared columns
(`kind`, `entity_id`, `title`, `text`, `vector`, …) plus paper-only and code-only columns.
Papers and repositories share no dedup key and little metadata, so they stay two registry
tables; everything after extraction is identical text processing, so it shares one chunk
table.

## How to Run

```bash
pip install -r requirements.txt
export WM_CONTACT_EMAIL=you@example.org   # polite pool for the discovery APIs
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

Stages run as package modules from the project root, in this order:

```bash
python -m core.bootstrap                     # schema + catalog seed
python -m core.ingest_papers --limit 50      # discover, resolve, fetch  (--source openalex,arxiv,semanticscholar)
python -m core.ingest_code --mode org        # org scan  (--mode search | --mode repo --repo org/name)
python -m core.extract                       # PDF -> Markdown          (--workers N)
python -m core.chunk                         # both kinds -> chunk JSON (--kind paper|code|all)
python -m core.embed                         # chunks -> LanceDB        (--batch-size N)
python -m core.report                        # QC stats
python -m core.store --export out.parquet    # dataset export
python -m core.purge --dry-run               # then --yes / --min-quality 0.5
```

### All in One

```bash
./run_pipeline.sh          # --limit N, --source, --workers N, --code-mode, --repo,
                           # --no-code, --purge, --yes
```

Embedding runs on CPU here (`bge-m3`, 1024-dim), so it is the slow stage — roughly a few
hundred chunks per minute. Ingestion and extraction are network- and IO-bound and finish
far sooner; run `embed` on its own when back-filling a large corpus.
