"""The Chroma index: build it, query it, rebuild it without duplicating it.

Chroma ships embedding functions that would have made this module a few lines shorter.
They are not used: an embedding is a model call, and a model call that does not go
through :class:`~m2x.adapter.ModelAdapter` leaves the run log unable to say which model
built the index or what it cost — the same rule that keeps the extractor wrapped rather
than replaced.

**Idempotence comes from the ids, not from bookkeeping.** Chunk ids are content
addresses (see :mod:`m2x.indexing`), so upserting the same corpus twice overwrites in
place. The one case ids alone do not cover is a source that *shrank* — an edited
document with a section removed leaves its old tail chunk orphaned in the index, still
retrievable, quoting text that no longer exists. So a source is written as a unit:
upsert what it has now, then delete the ids it used to have and no longer does.

**One index, one embedding model.** Vectors from two models are numerically comparable
and semantically unrelated, so mixing them returns confident nonsense rather than an
error. The model's repo id is recorded on the collection and checked on open.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings
from pydantic import BaseModel, ConfigDict, Field

from m2x.adapter import ModelAdapter
from m2x.errors import ConfigError
from m2x.indexing import Chunk, SourceType, chunk_transcript, load_document
from m2x.run_log import RunContext
from m2x.types import Provider, Transcript

PHASE = "phase-2"
"""Run-log phase these calls are attributed to — Day 4's RAG work, per the handbook."""

DEFAULT_INDEX_DIR = Path("data/index")
"""Where the Chroma database lives. Under git-ignored ``data/``, created on demand."""

DEFAULT_COLLECTION = "m2x"
"""Collection name. One collection: meetings and documents are separated by metadata."""

DEFAULT_EMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5"
"""Hugging Face repo id of the embedding model. Routed to Ollama by the registry."""

DEFAULT_DOCUMENTS = (
    Path("README.md"),
    Path("docs/m2x-week1-handbook.md"),
    Path("docs/corpus.md"),
)
"""Project docs indexed alongside the meetings.

Tracked files, so a fresh clone indexes the same corpus. They are the three that answer
questions a meeting cannot: what the system is meant to do, what the week's plan was,
and where the recordings came from. Missing ones are reported and skipped rather than
failing the build, so the list can name a doc that has not been written yet.
"""

EMBED_BATCH_SIZE = 32
"""Chunks per embedding request.

Large enough that a corpus rebuild is a handful of round trips, small enough that one
failure does not discard a whole meeting's work — and that a local model is not asked
to hold the entire corpus in memory at once.
"""

DISTANCE_METRIC = "cosine"
"""Cosine distance: ``1 - similarity``, so smaller is nearer.

Chosen over the default L2 because embedding models are trained for cosine similarity,
and because a normalised range makes any later threshold discussable in the abstract
rather than per-corpus.
"""

_MODEL_METADATA_KEY = "embed_model_repo_id"


class Hit(BaseModel):
    """One retrieved chunk, with its distance and everything needed to cite it."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    text: str
    distance: float = Field(ge=0.0)
    """Cosine distance from the query. Smaller is nearer; 0.0 is identical."""

    metadata: dict[str, str | int | float | bool]

    @property
    def similarity(self) -> float:
        """``1 - distance``. Convenient for reading, not a confidence."""
        return 1.0 - self.distance

    @property
    def citation(self) -> str:
        """Human-readable source reference built from the stored metadata."""
        source = str(self.metadata.get("source_id", "unknown"))
        if self.metadata.get("source_type") == SourceType.MEETING.value:
            start, end = self.metadata.get("t_start"), self.metadata.get("t_end")
            if start is not None and end is not None:
                return f"{source} {_timestamp(float(start))}–{_timestamp(float(end))}"
        heading = self.metadata.get("heading")
        return f"{source} § {heading}" if heading else source


def _timestamp(seconds: float) -> str:
    """Render seconds as ``mm:ss`` (or ``h:mm:ss`` past an hour)."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


class VectorStore:
    """A persistent Chroma collection pinned to one embedding model."""

    def __init__(
        self,
        path: Path = DEFAULT_INDEX_DIR,
        *,
        collection: str = DEFAULT_COLLECTION,
        embed_model_repo_id: str = DEFAULT_EMBED_MODEL,
    ) -> None:
        """Open or create the index.

        Args:
            path: Directory holding the Chroma database. Created if absent, because
                ``data/`` does not exist on a fresh clone.
            collection: Collection name.
            embed_model_repo_id: Model whose vectors this index holds.

        Raises:
            ConfigError: The collection already exists and was built with a different
                embedding model. Failing here is the point — the alternative is a query
                embedded by one model searching vectors from another, which returns
                plausible results and no error at all.
        """
        path.mkdir(parents=True, exist_ok=True)
        self._embed_model_repo_id = embed_model_repo_id
        # Telemetry off: this project's tests assert no network happens, and an index
        # build should not phone home about a private meeting corpus either.
        self._client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._collection: Collection = self._client.get_or_create_collection(
            name=collection,
            metadata={"hnsw:space": DISTANCE_METRIC, _MODEL_METADATA_KEY: embed_model_repo_id},
        )

        existing = (self._collection.metadata or {}).get(_MODEL_METADATA_KEY)
        if existing and existing != embed_model_repo_id:
            raise ConfigError(
                f"Index at {path} was built with {existing!r}, not {embed_model_repo_id!r}. "
                "Vectors from two models are comparable but unrelated — rebuild the index "
                "or query it with the model that built it."
            )

    @property
    def embed_model_repo_id(self) -> str:
        """Model this index was built with."""
        return self._embed_model_repo_id

    def count(self) -> int:
        """How many chunks are stored."""
        return self._collection.count()

    def source_ids(self) -> set[str]:
        """Distinct ``source_id`` values currently in the index."""
        stored = self._collection.get(include=["metadatas"])
        return {str(item["source_id"]) for item in stored["metadatas"] or []}

    def write_source(self, source_id: str, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> int:
        """Replace everything stored for one source.

        Upserts the given chunks, then deletes any id previously stored under this
        source that is no longer present. Without the delete, an edited document that
        lost a section would keep serving the removed text forever — retrievable,
        confident, and wrong.

        Args:
            source_id: Source being written.
            chunks: Its chunks, in order.
            vectors: One vector per chunk, positionally aligned.

        Returns:
            Number of chunks written.

        Raises:
            ValueError: ``chunks`` and ``vectors`` are different lengths — a mismatch
                would attach vectors to the wrong text with no way to notice later.
        """
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors for {source_id}")

        keep = {chunk.chunk_id for chunk in chunks}
        if chunks:
            self._collection.upsert(
                ids=[chunk.chunk_id for chunk in chunks],
                embeddings=[list(vector) for vector in vectors],
                documents=[chunk.text for chunk in chunks],
                metadatas=[chunk.to_metadata() for chunk in chunks],
            )

        stored = self._collection.get(where={"source_id": source_id}, include=[])
        stale = [stored_id for stored_id in stored["ids"] if stored_id not in keep]
        if stale:
            self._collection.delete(ids=stale)

        return len(chunks)

    def query(
        self,
        vector: Sequence[float],
        *,
        k: int = 5,
        source_type: SourceType | None = None,
    ) -> list[Hit]:
        """Return the ``k`` nearest chunks.

        Args:
            vector: Query embedding, from the same model that built the index.
            k: How many hits to return.
            source_type: Restrict to meetings or documents. ``None`` searches both.

        Returns:
            Hits ordered nearest first. Fewer than ``k`` when the index is smaller.

        Raises:
            ValueError: ``k`` is not positive.
        """
        if k <= 0:
            raise ValueError(f"k must be positive, got {k}")
        if self.count() == 0:
            return []

        result = self._collection.query(
            query_embeddings=[list(vector)],
            n_results=min(k, self.count()),
            where={"source_type": source_type.value} if source_type else None,
        )

        hits: list[Hit] = []
        for position, chunk_identifier in enumerate(result["ids"][0]):
            hits.append(
                Hit(
                    chunk_id=chunk_identifier,
                    text=(result["documents"] or [[]])[0][position],
                    distance=float((result["distances"] or [[]])[0][position]),
                    metadata=dict((result["metadatas"] or [[]])[0][position] or {}),
                )
            )
        return hits


def embed_texts(
    adapter: ModelAdapter,
    texts: Sequence[str],
    *,
    model_repo_id: str = DEFAULT_EMBED_MODEL,
    provider: Provider | None = None,
    batch_size: int = EMBED_BATCH_SIZE,
    context: RunContext | None = None,
) -> list[list[float]]:
    """Embed texts in batches, preserving order.

    Args:
        adapter: Adapter performing the calls.
        texts: Texts to embed.
        model_repo_id: Embedding model repo id.
        provider: Force a backend. ``None`` uses the model's default route.
        batch_size: Texts per request.
        context: Provenance for the run log.

    Returns:
        One vector per text, in input order.

    Raises:
        ValueError: ``batch_size`` is not positive.
        M2XError: Any routing or provider failure.
    """
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size}")

    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = list(texts[start : start + batch_size])
        result = adapter.embed(batch, model_repo_id, provider=provider, context=context)
        vectors.extend(result.vectors)
    return vectors


class IndexOutcome(BaseModel):
    """What one index build did, per source and in total."""

    model_config = ConfigDict(frozen=True)

    sources: dict[str, int]
    """Chunks written, keyed by source id."""

    total_chunks: int
    """Chunks written across every source this run."""

    index_size: int
    """Chunks in the collection afterwards. Equal to ``total_chunks`` only when the
    build covered every source already present — a partial rebuild leaves the rest."""

    embed_model_repo_id: str
    skipped: dict[str, str] = Field(default_factory=dict)
    """Sources that produced nothing, and why. Empty files are reported, not ignored."""


def build_index(
    store: VectorStore,
    adapter: ModelAdapter,
    *,
    transcripts: Iterable[tuple[str, Transcript]] = (),
    documents: Iterable[Path] = (),
    provider: Provider | None = None,
    batch_size: int = EMBED_BATCH_SIZE,
    command: str = "m2x index build",
) -> IndexOutcome:
    """Chunk, embed and store every supplied source.

    Args:
        store: Destination index.
        adapter: Adapter performing the embedding calls.
        transcripts: ``(meeting_id, Transcript)`` pairs.
        documents: Markdown files to index as ``source_type=doc``.
        provider: Force an embedding backend.
        batch_size: Texts per embedding request.
        command: Run-log command label.

    Returns:
        Per-source counts and the resulting index size.

    Raises:
        OSError: A document could not be read.
        M2XError: Any routing or provider failure.
    """
    written: dict[str, int] = {}
    skipped: dict[str, str] = {}

    for meeting_id, transcript in transcripts:
        chunks = chunk_transcript(transcript, meeting_id)
        if not chunks:
            skipped[meeting_id] = "transcript has no segments"
            continue
        context = RunContext(phase=PHASE, command=command, meeting_id=meeting_id)
        vectors = embed_texts(
            adapter,
            [chunk.text for chunk in chunks],
            model_repo_id=store.embed_model_repo_id,
            provider=provider,
            batch_size=batch_size,
            context=context,
        )
        written[meeting_id] = store.write_source(meeting_id, chunks, vectors)

    for path in documents:
        chunks = load_document(path)
        if not chunks:
            skipped[path.stem] = "document is empty"
            continue
        context = RunContext(phase=PHASE, command=command)
        vectors = embed_texts(
            adapter,
            [chunk.text for chunk in chunks],
            model_repo_id=store.embed_model_repo_id,
            provider=provider,
            batch_size=batch_size,
            context=context,
        )
        written[path.stem] = store.write_source(path.stem, chunks, vectors)

    return IndexOutcome(
        sources=written,
        total_chunks=sum(written.values()),
        index_size=store.count(),
        embed_model_repo_id=store.embed_model_repo_id,
        skipped=skipped,
    )


def query_index(
    store: VectorStore,
    adapter: ModelAdapter,
    question: str,
    *,
    k: int = 5,
    provider: Provider | None = None,
    source_type: SourceType | None = None,
    command: str = "m2x index query",
    context: RunContext | None = None,
) -> list[Hit]:
    """Embed a question and return the nearest chunks.

    The query is embedded with the model recorded on the collection, not with a
    default — a same-dimension mismatch does not fail, it just returns nonsense.

    Args:
        store: Index to search.
        adapter: Adapter performing the embedding call.
        question: Natural-language query.
        k: How many hits to return.
        provider: Force an embedding backend.
        source_type: Restrict to meetings or documents.
        command: Run-log command label.
        context: Provenance override. ``m2x ask`` passes one carrying its prompt version,
            so the retrieval leg of a question is attributable to the same prompt as the
            answering leg rather than showing up as an unversioned call.

    Returns:
        Hits ordered nearest first.

    Raises:
        ValueError: ``question`` is blank.
        M2XError: Any routing or provider failure.
    """
    if not question.strip():
        raise ValueError("query needs a non-empty question")

    result = adapter.embed(
        [question],
        store.embed_model_repo_id,
        provider=provider,
        context=context or RunContext(phase=PHASE, command=command),
    )
    return store.query(result.vectors[0], k=k, source_type=source_type)
