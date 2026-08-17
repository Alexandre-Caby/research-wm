"""Lazy sentence-transformers wrapper: loads bge-m3 on first use, not at import time."""
import os

import numpy as np

from core import config
from core.log import get_logger

logger = get_logger(__name__)

class TextEncoder:
    def __init__(self, model: str = config.TEXT_MODEL, device: str | None = None) -> None:
        self.model_name = model
        self.device = device or config.device()
        self._model = None
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        return self._dim if self._dim is not None else config.TEXT_DIM

    def _load(self) -> None:
        if self._model is None:
            if not (os.environ.get("HF_HOME") or os.environ.get("SENTENCE_TRANSFORMERS_HOME")):
                logger.warning(
                    "No HF_HOME/SENTENCE_TRANSFORMERS_HOME set: %s will re-download "
                    "on every run instead of using a persistent cache.", self.model_name,
                )
            if not config.HF_TOKEN:
                logger.warning(
                    "No HF_TOKEN set: downloads are rate-limited as an anonymous request."
                )

            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self.model_name, device=self.device,
                token=config.HF_TOKEN or None,
            )
            self._dim = self._model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str], batch_size: int = config.EMBED_BATCH) -> np.ndarray:
        self._load()
        return self._model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).astype("float32")

class OllamaTextEncoder:
    """bge-m3 served by Ollama (Vulkan on this host). Same 1024-dim space as the
    sentence-transformers model in name only: vectors from the two are NOT
    interchangeable — a lake must be embedded end to end by one of them."""
    MAX_CHARS = 3500   # bge-m3 serves a 2048-token context here; code tokenizes at
                   # roughly 2 chars/token, so 8000 chars overflowed it. Measured,
                   # not assumed: 'x'*40000 passed only because a repeated char
                   # compresses — real text does not.

    def __init__(self, url: str | None = None, model: str = "bge-m3") -> None:
        self.url = (url or os.environ.get("OLLAMA_URL", "http://172.28.0.1:11434")).rstrip("/")
        self.model_name = model
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        return self._dim if self._dim is not None else config.TEXT_DIM

    def encode(self, texts: list[str], batch_size: int = config.EMBED_BATCH) -> np.ndarray:
        import requests
        out = []
        for i in range(0, len(texts), batch_size):
            batch = [t[:self.MAX_CHARS] for t in texts[i:i + batch_size]]
            res = requests.post(f"{self.url}/api/embed",
                                json={"model": self.model_name, "input": batch},
                                timeout=600)
            if res.status_code != 200:
                raise RuntimeError(
                    f"Ollama {res.status_code}: {res.text[:500]} | "
                    f"batch={len(batch)} longest={max(len(t) for t in batch)} chars"
                )
            vectors = res.json()["embeddings"]
            if len(vectors) != len(batch):
                raise RuntimeError(
                    f"Ollama returned {len(vectors)} vectors for {len(batch)} inputs"
                )
            out.extend(vectors)
        arr = np.asarray(out, dtype="float32")
        # sentence-transformers normalizes; Ollama does not guarantee it, and the
        # store's cosine search assumes unit vectors.
        arr /= np.linalg.norm(arr, axis=1, keepdims=True).clip(min=1e-12)
        self._dim = arr.shape[1]
        return arr
