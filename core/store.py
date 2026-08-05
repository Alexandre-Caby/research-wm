"""LanceDB data plane: vector search over the unified paper/code chunk table."""
import argparse

import lancedb
import numpy as np
import pyarrow as pa

from core import config

# chunk.py emits one schema for both kind="paper" and kind="code" rows.
_CHUNK_SCHEMA_FIELDS: list[tuple[str, pa.DataType]] = [
    ("chunk_id", pa.string()),
    ("kind", pa.string()),
    ("entity_id", pa.string()),
    ("model_id", pa.string()),
    ("title", pa.string()),
    ("source", pa.string()),
    ("query_block", pa.string()),
    ("section", pa.string()),
    ("section_index", pa.int64()),
    ("chunk_index", pa.int64()),
    ("word_count", pa.int64()),
    ("text", pa.string()),
    ("doi", pa.string()),
    ("year", pa.int64()),
    ("extraction_quality", pa.float64()),
    ("repo_full_name", pa.string()),
    ("file_path", pa.string()),
    ("symbol", pa.string()),
    ("symbol_kind", pa.string()),
    ("start_line", pa.int64()),
    ("end_line", pa.int64()),
    ("language", pa.string()),
    ("commit_sha", pa.string()),
    ("licence", pa.string()),
]


class LanceStore:
    def __init__(self, lance_dir: str | None = None, dim: int | None = None) -> None:
        self.lance_dir = lance_dir or config.LANCE_DIR
        self.dim = dim
        self.table_name = "chunks"

    def _schema(self, dim: int) -> pa.Schema:
        fields = [pa.field(name, dtype) for name, dtype in _CHUNK_SCHEMA_FIELDS]
        fields.append(pa.field("vector", pa.list_(pa.float32(), dim)))
        return pa.schema(fields)

    def open_table(self):
        db = lancedb.connect(self.lance_dir)
        if self.table_name in db.table_names():
            return db.open_table(self.table_name)
        if self.dim is None:
            raise RuntimeError(
                "cannot open table before dim is known: run upsert() first or pass dim"
            )
        return db.create_table(self.table_name, schema=self._schema(self.dim))

    def upsert(self, rows: list[dict]) -> None:
        if not rows:
            return
        if self.dim is None:
            self.dim = len(rows[0]["vector"])

        table = self.open_table()
        data = [self._to_arrow_row(row) for row in rows]
        (
            table.merge_insert("chunk_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(data)
        )

    def _to_arrow_row(self, row: dict) -> dict:
        out = dict(row)
        if isinstance(out["vector"], np.ndarray):
            out["vector"] = out["vector"].tolist()
        return out

    def search(self, vector, k: int = 10) -> list[dict]:
        try:
            table = self.open_table()
        except RuntimeError:
            return []
        return table.search(vector, vector_column_name="vector").limit(k).to_list()

    def export_parquet(self, out_path: str) -> None:
        table = self.open_table()
        table.to_pandas().to_parquet(out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", required=True)
    args = parser.parse_args()
    LanceStore().export_parquet(args.export)
