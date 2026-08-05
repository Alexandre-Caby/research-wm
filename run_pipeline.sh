#!/usr/bin/env bash
set -euo pipefail

source ~/.venv/bin/activate

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"

LIMIT=""
SOURCE=""
WORKERS=""
CODE_MODE="org"
REPO=""
SKIP_CODE=false
PURGE=false
YES=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --limit) LIMIT="$2"; shift 2 ;;
        --source) SOURCE="$2"; shift 2 ;;
        --workers) WORKERS="$2"; shift 2 ;;
        --code-mode) CODE_MODE="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --no-code) SKIP_CODE=true; shift ;;
        --purge) PURGE=true; shift ;;
        --yes) YES=true; shift ;;
        *) echo "Unknown flag: $1" >&2; exit 1 ;;
    esac
done

limit_args=()
[[ -n "$LIMIT" ]] && limit_args+=(--limit "$LIMIT")

banner() {
    echo "--------------------------------------------------"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1"
    echo "--------------------------------------------------"
}

banner "bootstrap registry"
python -m core.bootstrap

ingest_papers_args=("${limit_args[@]}")
[[ -n "$SOURCE" ]] && ingest_papers_args+=(--source "$SOURCE")

banner "ingest papers"
python -m core.ingest_papers "${ingest_papers_args[@]}"

if [[ "$SKIP_CODE" == false ]]; then
    ingest_code_args=(--mode "$CODE_MODE" "${limit_args[@]}")
    [[ -n "$REPO" ]] && ingest_code_args+=(--repo "$REPO")
    banner "ingest code"
    python -m core.ingest_code "${ingest_code_args[@]}"
fi

extract_args=("${limit_args[@]}")
[[ -n "$WORKERS" ]] && extract_args+=(--workers "$WORKERS")

banner "extract"
python -m core.extract "${extract_args[@]}"

banner "chunk"
python -m core.chunk "${limit_args[@]}"

banner "embed"
python -m core.embed "${limit_args[@]}"

banner "report"
python -m core.report

if [[ "$PURGE" == true ]]; then
    purge_args=()
    [[ "$YES" == true ]] && purge_args+=(--yes)
    banner "purge"
    python -m core.purge "${purge_args[@]}"
fi

banner "pipeline completed, cleaning up run artifacts"
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type f -name "*.pyc" -delete

echo "--------------------------------------------------"
echo "Pipeline completed successfully."
echo "Metrics:"
python -c "
from core import config
from core import registry as R
from core.report import run_report
conn = R.connect(config.DB_PATH)
report = run_report(conn)
print(f'- Papers total:   {report[\"papers_total\"]}')
print(f'- Repos total:    {report[\"repos_total\"]}')
print(f'- Papers status:  {report[\"papers_by_status\"]}')
print(f'- Repos status:   {report[\"repos_by_status\"]}')
print(f'- Chunks total:   {report[\"num_chunks_total\"]}')
# C3: fold the -wal back into the .db so copying storage/ to the server
# cannot lose committed rows.
R.checkpoint_wal(conn)
conn.close()
"
echo "--------------------------------------------------"
