"""Embedding generation module for semantic vector search.

This module provides utilities for generating embeddings from message content
using sentence-transformers models. Embeddings enable semantic similarity search
that complements keyword-based FTS5 search.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from sentence_transformers import SentenceTransformer

# Embedding dimension for the default model (all-MiniLM-L6-v2)
EMBEDDING_DIMENSION = 384

# Default model for embeddings
DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class EmbeddingGenerator:
    """Generate embeddings for text content using sentence-transformers.
    
    This class manages loading and caching of the embedding model,
    and provides methods to generate embeddings for individual texts
    or batches of texts.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME):
        """Initialize the embedding generator.
        
        Args:
            model_name: Name of the sentence-transformers model to use.
                       Defaults to all-MiniLM-L6-v2 (384 dimensions).
        """
        self.model_name = model_name
        self._model: SentenceTransformer | None = None

    def _load_model(self) -> SentenceTransformer:
        """Load the sentence-transformers model (lazy loading).
        
        Returns:
            The loaded SentenceTransformer model.
            
        Raises:
            ImportError: If sentence-transformers is not installed.
        """
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                msg = (
                    "sentence-transformers is required for vector search. "
                    "Install with: pip install copilot-session-tools[vector]"
                )
                raise ImportError(msg) from e

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text.
        
        Args:
            text: The text to embed.
            
        Returns:
            List of floats representing the embedding vector.
        """
        model = self._load_model()
        # encode() returns numpy array, convert to list for SQLite storage
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()  # type: ignore[no-any-return]

    def embed_batch(self, texts: list[str], batch_size: int = 32, show_progress: bool = False) -> list[list[float]]:
        """Generate embeddings for multiple texts in batches.
        
        Args:
            texts: List of texts to embed.
            batch_size: Number of texts to process in each batch.
            show_progress: Whether to show a progress bar.
            
        Returns:
            List of embedding vectors, one per input text.
        """
        model = self._load_model()
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
        )
        # Convert numpy array to list of lists
        return embeddings.tolist()  # type: ignore[no-any-return]


def is_vector_search_available() -> bool:
    """Check if vector search dependencies are available.
    
    Returns:
        True if both sqlite-vec and sentence-transformers are installed.
    """
    try:
        import sqlite_vec  # noqa: F401
        from sentence_transformers import SentenceTransformer  # noqa: F401
        return True
    except ImportError:
        return False
