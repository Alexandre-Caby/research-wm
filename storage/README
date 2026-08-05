# Multi-Tier Data Lake

This directory houses all data assets partitioned into four strict logical tiers based on
their processing maturity. The tier names are locked and identical to the other
`research-xxx` pipelines.

## Storage Tiers

- **`1_raw_data/` (Bronze Tier)**: Temporary landing zone for source PDFs and repository
  archives. Content here is volatile and subject to automated purges once its extraction
  has been validated.
- **`2_register_data/` (Silver Tier)**: The SQLite control plane (`wm_registry.db`)
  tracking global system state — the entity tables, the status FSM, and the dedup keys
  (canonical DOI, normalized title, content hash, commit SHA).
  - **CRITICAL RULE**: This folder and its contents must not be deleted or manually
    altered. It is the single source of truth for deduplication and pipeline state.
- **`3_exploitable_data/` (Gold Tier)**: Normalized, ultra-light text assets optimized for
  direct agent exploitation, split by entity kind:
  - `papers/` — Markdown with front-matter, one file per paper.
  - `code/` — normalized source and documentation text extracted from the catalog
    repositories, one directory per repository snapshot.
  - **CRITICAL RULE**: This folder is permanent and must never be purged. Unlike
    `1_raw_data`, it is not regenerable without re-running extraction, and it is the
    reusable knowledge base the rest of the pipeline exists to build.
- **`4_vector_data/` (Platinum Tier)**: Section-aware chunk JSON derived from the Gold
  tier, and the LanceDB table that holds one embedded row per chunk (vector + metadata).
  This tier serves both the semantic-search / RAG index and a versioned dataset export to
  Parquet.

  **Note on JSON files**: The chunk JSON files generated during the chunking phase are
  temporary. They are automatically deleted as soon as they have been successfully
  inserted into LanceDB to avoid inode-table pressure and disk-space saturation.

## Weights Are Never Stored

No tier ever holds model weights. Checkpoint artifacts are rejected at ingestion by
`config.WEIGHT_EXTENSIONS`, so a `.safetensors` or `.ckpt` file can never reach Tier 1,
let alone Tier 3. The lake stores what a repository *says* and *does* — its code, its
configuration, its documentation, its paper — never what it *has learned*.
