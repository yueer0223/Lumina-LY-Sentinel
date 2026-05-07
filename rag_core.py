"""
rag_core.py — SentinelBrain: ChromaDB-backed persistent memory for Lumina-LY.

Provides:
  - memorize()   — incremental hash-fingerprint chunk-and-store into vector DB
  - recall()     — query the closest memory, with gibberish guard
  - audit()      — style/anomaly detection against historical corpus
  - count_memories() — how many vectors are sleeping in cold storage
"""

import hashlib
import time
from typing import Any

import chromadb
from chromadb import PersistentClient, Collection

# ── Safety Tolerances ────────────────────────────────────────────────
_MAX_FILE_BYTES: int = 5 * 1024 * 1024          # 5 MB — veto oversized
_MAX_CHUNKS_PER_FILE: int = 1000                  # cap chunk explosion
_GIBBERISH_DISTANCE: float = 1.5                  # ≥ this → no relevant memory
_CHUNK_LINES: int = 50                             # lines per chunk

# ── ChromaDB Path ────────────────────────────────────────────────────
_MEMORY_DB_PATH: str = "./.sentinel_memory"


# ── Fingerprint Helper ───────────────────────────────────────────────

def _chunk_hash(text: str) -> str:
    """Return the MD5 hexdigest of *text*.

    Used by :meth:`SentinelBrain.memorize`  to detect whether a chunk
    has changed since the last index cycle.  MD5 is deliberately **not**
    used for crypto here — only for fast identity dedup.
    """
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class SentinelBrain:
    """Persistent vector brain for Lumina-LY.

    Wraps a ChromaDB ``PersistentClient`` + a single ``Collection``
    (``code_style``).  All memories are stored as chunked documents with
    metadata carrying the source filename, the zero-indexed chunk part,
    and an **MD5 hash fingerprint** for incremental diff.

    Incremental indexing
    --------------------
    When :meth:`memorize` is called, the method:

    1. Chunks the new content and fingerprints every chunk.
    2. Pulls the previous index state for the same filename.
    3. Compares old vs. new hashes → only ``upsert`` delta and
       ``delete`` vanished chunks, skipping everything that hasn't
       changed.
    """

    def __init__(self) -> None:
        """Initialise the ChromaDB client and the code_style collection."""
        self.client: PersistentClient = chromadb.PersistentClient(path=_MEMORY_DB_PATH)
        self.collection: Collection = self.client.get_or_create_collection(name="code_style")

    # ── Ingestion ────────────────────────────────────────────────────

    def memorize(self, filename: str, content: str) -> None:
        """Incrementally index *content* using MD5 hash fingerprints.

        Only chunks whose content has **actually changed** are re-embedded
        and upserted.  Deleted chunks are purged.  Everything else is
        skipped — zero-cost.

        Safety guards (retained from v1)
        ---------------------------------
        - Files larger than ``_MAX_FILE_BYTES`` (5 MB) are rejected.
        - At most ``_MAX_CHUNKS_PER_FILE`` (1000) chunks are produced;
          remaining lines are truncated.

        Parameters
        ----------
        filename : str
            Logical name or path of the source file (used as metadata).
        content : str
            Full text content to be chunked, fingerprinted, and indexed.
        """
        t0: float = time.perf_counter()

        # ── Size check (v1 carry-over) ───────────────────────────────
        raw_size: int = len(content.encode("utf-8"))
        if raw_size > _MAX_FILE_BYTES:
            print(f"[SentinelBrain] ⚠️  '{filename}' 体积 "
                  f"{raw_size / 1024 / 1024:.1f} MB "
                  f"超出上限 {_MAX_FILE_BYTES / 1024 / 1024:.0f} MB，"
                  f"已跳过索引。")
            return

        lines: list[str] = content.splitlines()
        chunk_size: int = _CHUNK_LINES

        # ── Build new chunks + fingerprints ──────────────────────────
        new_ids: list[str] = []
        new_texts: list[str] = []
        new_metadatas: list[dict[str, Any]] = []
        new_hash_map: dict[str, str] = {}  # chunk_id → md5 hexdigest

        total_chunks: int = (len(lines) + chunk_size - 1) // chunk_size
        max_chunks: int = min(_MAX_CHUNKS_PER_FILE, total_chunks)
        chunk_count: int = 0

        for i in range(0, len(lines), chunk_size):
            if chunk_count >= max_chunks:
                print(f"[SentinelBrain] ⚠️  '{filename}' 超过 "
                      f"{_MAX_CHUNKS_PER_FILE} 个分块上限，"
                      f"剩余 {len(lines) - i} 行截断未索引。")
                break

            chunk_text: str = "\n".join(lines[i:i + chunk_size])
            chunk_id: str = f"{filename}_part_{i // chunk_size}"
            fp: str = _chunk_hash(chunk_text)

            new_ids.append(chunk_id)
            new_texts.append(chunk_text)
            new_metadatas.append({
                "source": filename,
                "part": i // chunk_size,
                "hash": fp,
            })
            new_hash_map[chunk_id] = fp
            chunk_count += 1

        # ── Fetch old index state ────────────────────────────────────
        old_hash_map: dict[str, str] = {}
        try:
            existing = self.collection.get(where={"source": filename})
            if existing and existing["ids"]:
                for eid, emeta in zip(existing["ids"], existing["metadatas"]):
                    # Pre-upgrade chunks won't have a "hash" key → treat as ""
                    old_hash_map[eid] = emeta.get("hash", "")
        except Exception:
            pass  # no prior state → all chunks are "new"

        # ── Diff: skip / upsert / delete ─────────────────────────────
        old_ids_set: set[str] = set(old_hash_map.keys())
        new_ids_set: set[str] = set(new_ids)

        skip_count: int = 0
        upsert_ids: list[str] = []
        upsert_texts: list[str] = []
        upsert_metadatas: list[dict[str, Any]] = []

        for cid, ctext, cmeta in zip(new_ids, new_texts, new_metadatas):
            old_fp: str = old_hash_map.get(cid, "")
            if old_fp != "" and old_fp == cmeta["hash"]:
                skip_count += 1
            else:
                upsert_ids.append(cid)
                upsert_texts.append(ctext)
                upsert_metadatas.append(cmeta)

        delete_ids: list[str] = list(old_ids_set - new_ids_set)

        # ── Execute delta ────────────────────────────────────────────
        if delete_ids:
            self.collection.delete(ids=delete_ids)
        if upsert_ids:
            self.collection.upsert(
                ids=upsert_ids,
                documents=upsert_texts,
                metadatas=upsert_metadatas,
            )

        elapsed: float = time.perf_counter() - t0

        # ── Battle report ────────────────────────────────────────────
        print(
            f"🚀 [增量引擎] {filename} 扫描完毕："
            f"跳过 {skip_count} 块，更新 {len(upsert_ids)} 块，"
            f"删除 {len(delete_ids)} 块，耗时 {elapsed:.2f} 秒。"
        )

    # ── Query ────────────────────────────────────────────────────────

    def recall(self, question: str, n_results: int = 1) -> dict[str, list]:
        """Query the vector DB for the *n_results* closest memories.

        Gibberish / out-of-domain guard (v1 carry-over)
        -------------------------------------------------
        If the highest-ranked match has a distance ≥ ``_GIBBERISH_DISTANCE``
        (1.5 by default), the result is considered noise and an empty
        record is returned instead.

        Parameters
        ----------
        question : str
            Natural-language query string.
        n_results : int, optional
            How many neighbours to return (default 1).

        Returns
        -------
        dict[str, list]
            ChromaDB query result shape — keys ``documents``, ``metadatas``,
            ``distances``, ``ids``.  When the guard triggers, all lists
            are empty.
        """
        results: dict[str, list] = self.collection.query(
            query_texts=[question],
            n_results=n_results,
        )

        # ── Gibberish firewall ─────────────────────────────────
        distances = results.get("distances", [])
        if distances and distances[0]:
            best_distance: float = distances[0][0]
            if best_distance >= _GIBBERISH_DISTANCE:
                print(f"[SentinelBrain] 🚫 查询与记忆距离 "
                      f"{best_distance:.2f} ≥ 阈值 "
                      f"{_GIBBERISH_DISTANCE}，判定为无相关记忆。")
                return {"documents": [], "metadatas": [],
                        "distances": [], "ids": []}

        return results

    # ── Audit / Anomaly───────────────────────────────────────────────

    def audit(self, new_code: str) -> tuple[bool, float]:
        """Compare *new_code* against the historical corpus to detect style drift.

        Parameters
        ----------
        new_code : str
            Source code text to evaluate (first 500 chars are used).

        Returns
        -------
        tuple[bool, float]
            ``(is_safe, distance)`` where *is_safe* is ``True`` when the
            closest match has a distance < 1.2 (stylistically familiar).
        """
        if self.count_memories() == 0:
            return True, 0.0  # empty brain → safe by default

        results = self.collection.query(
            query_texts=[new_code[:500]],
            n_results=1,
        )

        distances = results.get("distances")
        if distances and distances[0]:
            distance: float = distances[0][0]
            is_safe: bool = distance < 1.2
            return is_safe, distance

        return True, 0.0

    # ── Utility ──────────────────────────────────────────────────────

    def count_memories(self) -> int:
        """Return the total number of stored vectors."""
        return self.collection.count()
