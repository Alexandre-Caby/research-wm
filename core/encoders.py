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
