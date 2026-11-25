from collections.abc import Callable
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
from threading import Lock
from uuid import UUID

from claimpilot.config import Settings
from claimpilot.schemas.knowledge import Document, Evidence, Operator, SearchRequest, Section
from claimpilot.storage.intake import IntakeError
from claimpilot.storage.knowledge import IndexWork, KnowledgeStore

logger = logging.getLogger(__name__)
Embedding = Callable[[list[str]], list]
MODEL = "all-MiniLM-L6-v2/onnx-chroma"


class KnowledgeIndex:
    def __init__(
        self, settings: Settings, store: KnowledgeStore, *,
        embed: Embedding | None = None, embedding_id: str = MODEL,
    ) -> None:
        self.settings = settings
        self.store = store
        self._embed = embed
        self._client = None
        self._collection = None
        self._lock = Lock()
        self.config = {
            "embedding": embedding_id, "distance": "cosine", "splitter": "recursive-v1",
            "chunk_size": settings.claimpilot_kb_chunk_size,
            "chunk_overlap": settings.claimpilot_kb_chunk_overlap,
        }
        self.prefix = "kb_" + sha256(str(settings.claimpilot_intake_db.resolve()).encode()).hexdigest()[:16] + "_"
        signature = sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()[:16]
        self.name = self.prefix + signature

    def _chroma(self):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=str(self.settings.claimpilot_kb_chroma.resolve()),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def _vector_collection(self):
        with self._lock:
            if self._collection is None:
                self._collection = self._chroma().get_or_create_collection(
                    self.name, embedding_function=None, configuration={"hnsw": {"space": "cosine"}},
                    metadata={"index_config": json.dumps(self.config, sort_keys=True)},
                )
                if self._collection.metadata["index_config"] != json.dumps(self.config, sort_keys=True):
                    raise RuntimeError("Incompatible knowledge index configuration")
            return self._collection

    def embed(self, texts: list[str]) -> list:
        with self._lock:
            if self._embed is None:
                from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

                embedder = ONNXMiniLM_L6_V2(preferred_providers=["CPUExecutionProvider"])
                embedder.DOWNLOAD_PATH = Path(self.settings.claimpilot_kb_model_cache).resolve() / "all-MiniLM-L6-v2"
                self._embed = embedder
            return self._embed(texts)

    def _split(self, work: IndexWork) -> list[tuple[str, Section]]:
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config["chunk_size"], chunk_overlap=self.config["chunk_overlap"],
        )
        chunks = []
        for block in work.sections:
            for text in splitter.split_text(block.text):
                key = f"{work.doc.id}:{work.doc.generation}:{len(chunks)}"
                chunks.append((key, block.model_copy(update={"text": text})))
        return chunks

    def build(self, actor: Operator, doc_id: UUID, key: str, *, force: bool = False) -> Document:
        work = self.store.reserve_index(actor, doc_id, key, self.config, force=force)
        if isinstance(work, Document):
            return work
        doc = work.doc
        collection = None
        error = None
        chunks = []
        try:
            chunks = self._split(work)
            collection = self._vector_collection()
            for start in range(0, len(chunks), self.settings.claimpilot_kb_batch_size):
                batch = chunks[start:start + self.settings.claimpilot_kb_batch_size]
                texts = [f"{part.section}\n{part.text}" if part.section else part.text for _, part in batch]
                collection.upsert(
                    ids=[chunk_id for chunk_id, _ in batch], embeddings=self.embed(texts),
                    metadatas=[{
                        "workspace_id": actor.tenant_id, "document_id": str(doc.id),
                        "document_type": doc.document_type, "policy_id": doc.policy_id or "",
                        "policy_version": doc.policy_version or "", "subject": doc.subject_claimant_id or "",
                        "generation": doc.generation, "generation_id": f"{doc.id}:{doc.generation}",
                        "document_version": doc.document_version, "content_hash": doc.content_hash,
                        "chunk_id": chunk_id, "page": part.page or 0, "section": part.section or "",
                    } for chunk_id, part in batch],
                )
        except Exception:
            logger.warning("knowledge_index_failed document_id=%s", doc.id)
            error = "indexing_failed"
        published = self.store.finish_index(actor, doc, chunks, error)
        if collection is not None:
            if published is None or published.generation != doc.generation or published.status == "failed":
                self._cleanup(collection, actor.tenant_id, doc.id, {"$eq": doc.generation})
            elif published.status == "ready":
                self._cleanup(collection, actor.tenant_id, doc.id, {"$lt": doc.generation})
        if published is None:
            raise IntakeError(404, "document_not_found", "Document was deleted during indexing.")
        return published

    @staticmethod
    def _cleanup(collection, tenant: str, doc_id: UUID, generation: dict | None = None) -> None:
        where = [{"workspace_id": tenant}, {"document_id": str(doc_id)}]
        if generation:
            where.append({"generation": generation})
        try:
            collection.delete(where={"$and": where})
        except Exception:
            logger.warning("knowledge_vector_cleanup_failed document_id=%s", doc_id)

    def delete(self, actor: Operator, doc_id: UUID, key: str) -> None:
        self.store.delete(actor, doc_id, key)
        try:
            client = self._chroma()
            for collection in client.list_collections():
                if collection.name.startswith(self.prefix):
                    self._cleanup(collection, actor.tenant_id, doc_id)
        except Exception:
            logger.warning("knowledge_vector_cleanup_failed document_id=%s", doc_id)

    def purge_workspace(self, tenant: str) -> None:
        if not (self.settings.claimpilot_kb_chroma / "chroma.sqlite3").is_file():
            return
        with self._lock:
            client = self._chroma()
        for collection in client.list_collections():
            if collection.name.startswith(self.prefix):
                client.get_collection(collection.name, embedding_function=None).delete(
                    where={"workspace_id": tenant},
                )

    def search(self, tenant: str, request: SearchRequest, *, subject: str = "") -> list[Evidence]:
        document_id = str(request.document_id) if request.document_id else None
        allowed = self.store.scoped_generations(tenant, request, self.config, subject, document_id=document_id)
        if not allowed:
            return []
        count = request.top_k or self.settings.claimpilot_kb_top_k
        filters = [
            {"workspace_id": tenant}, {"document_type": request.document_type},
            {"policy_id": request.policy_id or ""}, {"policy_version": request.policy_version or ""},
            {"generation_id": {"$in": allowed}},
        ]
        try:
            found = self._vector_collection().query(
                query_embeddings=self.embed([request.query]), n_results=count * 6,
                where={"$and": filters}, include=["distances"],
            )
        except Exception:
            raise IntakeError(503, "retrieval_unavailable", "Knowledge retrieval is temporarily unavailable.") from None
        keys = found["ids"][0]
        rows = self.store.scoped_chunks(tenant, request, self.config, subject, keys, document_id=document_id)
        result = []
        word_sets = []
        for key, distance in zip(keys, found["distances"][0], strict=True):
            row = rows.get(key)
            if row is None:
                continue
            words = set(re.findall(r"\w+", row["text"].casefold()))
            if any(previous and len(previous & words) >= 0.75 * min(len(previous), len(words))
                   and section == row["section"] and source == row["document_id"]
                   for previous, section, source in word_sets):
                continue
            result.append(Evidence(
                chunk_id=key, document_id=row["document_id"], filename=row["filename"], title=row["title"],
                document_version=row["document_version"], content_hash=row["content_hash"],
                document_type=row["document_type"], policy_id=row["policy_id"] or None,
                policy_version=row["policy_version"] or None, page=row["page"],
                section=row["section"], text=row["text"], distance=distance,
            ))
            word_sets.append((words, row["section"], row["document_id"]))
            if len(result) >= count:
                break
        return result

    def revalidate(self, tenant: str, scope: SearchRequest, evidence: list[Evidence], subject: str) -> list[Evidence]:
        document_id = str(scope.document_id) if scope.document_id else None
        valid = self.store.scoped_chunks(tenant, scope, self.config, subject, [e.chunk_id for e in evidence],
                                         document_id=document_id)
        return [e for e in evidence if e.chunk_id in valid]
