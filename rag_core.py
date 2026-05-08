"""
rag_core.py — LuminaContextEngine: ChromaDB-backed persistent context engine for Lumina-LY.

Provides:
  - memorize()              — incremental hash-fingerprint chunk-and-store into vector DB
  - recall()                — query the closest memory, with data integrity guard
  - audit()                 — style consistency audit against historical corpus
  - generate_explanation()  — use DeepSeek to generate natural-language explanation
  - count_memories()        — how many vectors are stored
"""

import hashlib
import time
import os
from typing import Any

import chromadb
from chromadb import PersistentClient, Collection
from openai import OpenAI
from dotenv import load_dotenv

# ── Safety Tolerances ────────────────────────────────────────────────
_MAX_FILE_BYTES: int = 5 * 1024 * 1024          # 5 MB — reject oversized
_MAX_CHUNKS_PER_FILE: int = 1000                  # cap chunk explosion
_INTEGRITY_THRESHOLD: float = 1.8                 # 阈值放宽至 1.8：兼容中英跨语言的自然语言查询 (如中文 Query 检索英文 Code)
_CHUNK_LINES: int = 50                             # lines per chunk

# ── ChromaDB Path ────────────────────────────────────────────────────
_MEMORY_DB_PATH: str = "./.sentinel_memory"

# ── LLM Config ───────────────────────────────────────────────────────
load_dotenv()
_DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
_DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
_DEEPSEEK_MODEL: str = "deepseek-chat"


# ── Fingerprint Helper ───────────────────────────────────────────────

def _chunk_hash(text: str) -> str:
    """Return the MD5 hexdigest of *text*.

    Used by :meth:`LuminaContextEngine.memorize` to detect whether a chunk
    has changed since the last index cycle. MD5 is deliberately **not**
    used for crypto here — only for fast identity dedup.
    """
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class LuminaContextEngine:
    """Persistent context engine for Lumina-LY.

    Wraps a ChromaDB ``PersistentClient`` + a single ``Collection``
    (``code_style``). All context is stored as chunked documents with
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
        and upserted. Deleted chunks are purged. Everything else is
        skipped — zero-cost.

        Safety guards
        -------------
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

        # ── Size check ───────────────────────────────────────────────
        raw_size: int = len(content.encode("utf-8"))
        if raw_size > _MAX_FILE_BYTES:
            print(f"[LuminaContextEngine] ⚠️ '{filename}' 体积 "
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
                print(f"[LuminaContextEngine] ⚠️ '{filename}' 超过 "
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

        # ── Index report ─────────────────────────────────────────────
        print(
            f"🚀 [增量引擎] {filename} 扫描完毕："
            f"跳过 {skip_count} 块，更新 {len(upsert_ids)} 块，"
            f"删除 {len(delete_ids)} 块，耗时 {elapsed:.2f} 秒。"
        )

    # ── Query ────────────────────────────────────────────────────────

    def recall(self, question: str, n_results: int = 1) -> dict[str, list]:
        """Query the vector DB for the *n_results* closest context entries.

        Data integrity filter
        ---------------------
        If the highest-ranked match has a distance ≥ ``_INTEGRITY_THRESHOLD``
        (1.8 by default), the result is considered noise and an empty
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
            ``distances``, ``ids``. When the filter triggers, all lists
            are empty.
        """
        results: dict[str, list] = self.collection.query(
            query_texts=[question],
            n_results=n_results,
        )

        # ── Data integrity filter ──────────────────────────────
        distances = results.get("distances", [])
        if distances and distances[0]:
            best_distance: float = distances[0][0]
            if best_distance >= _INTEGRITY_THRESHOLD:
                print(f"[LuminaContextEngine] 🚫 查询与上下文距离 "
                      f"{best_distance:.2f} ≥ 阈值 "
                      f"{_INTEGRITY_THRESHOLD}，判定为无相关上下文。")
                return {"documents": [], "metadatas": [],
                        "distances": [], "ids": []}

        return results

    # ── Audit / Style Consistency ────────────────────────────────────

    def audit(self, new_code: str) -> tuple[bool, float]:
        """Compare *new_code* against the historical corpus for style consistency.

        Parameters
        ----------
        new_code : str
            Source code text to evaluate (first 500 chars are used).

        Returns
        -------
        tuple[bool, float]
            ``(is_consistent, distance)`` where *is_consistent* is ``True`` when the
            closest match has a distance < 1.2 (stylistically familiar).
        """
        if self.count_memories() == 0:
            return True, 0.0  # empty engine → safe by default

        results = self.collection.query(
            query_texts=[new_code[:500]],
            n_results=1,
        )

        distances = results.get("distances")
        if distances and distances[0]:
            distance: float = distances[0][0]
            is_consistent: bool = distance < 1.2
            return is_consistent, distance

        return True, 0.0

    # ── Generation ───────────────────────────────────────────────────

    def generate_explanation(self, query: str, context_chunks: list[str]) -> str:
        """Use DeepSeek to generate a natural-language explanation of the code context.

        Parameters
        ----------
        query : str
            The original user query.
        context_chunks : list[str]
            Code snippet chunks retrieved from the vector DB.

        Returns
        -------
        str
            Natural-language explanation of the relevant code logic.
        """
        if not _DEEPSEEK_API_KEY:
            return "⚠️ DeepSeek API 密钥未配置，请在 .env 文件中设置 DEEPSEEK_API_KEY。"

        if not context_chunks:
            return "🤖 未找到相关的代码上下文，无法生成解释。"

        system_prompt = (
            "你是一名资深架构师。请根据以下提供的代码切片，用通俗、简明的中文"
            "向用户解释这段代码的业务逻辑和设计意图。\n"
            "要求：\n"
            "1. 不要复读原始代码，而是解释其作用和原因。\n"
            "2. 保持专业但易懂，避免过多技术术语。\n"
            "3. 如果代码涉及特定的模式或架构决策，请指出。\n"
            "4. 必须严格使用中华人民共和国国家标准《标点符号用法》"
            "（GB/T 15834-2011）中规定的学术出版标点符号规范，"
            "禁止使用不规范的符号或风格。\n"
        )

        context_text = "\n\n---\n\n".join(context_chunks)
        user_prompt = (
            f"用户问题：{query}\n\n"
            f"相关代码上下文：\n```\n{context_text}\n```"
        )

        try:
            client = OpenAI(
                api_key=_DEEPSEEK_API_KEY,
                base_url=_DEEPSEEK_BASE_URL,
            )

            response = client.chat.completions.create(
                model=_DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
            )

            explanation = response.choices[0].message.content.strip()
            return explanation

        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "Unauthorized" in error_msg:
                return "⚠️ DeepSeek API 鉴权失败，请检查 API Key 是否有效。"
            elif "429" in error_msg or "Rate limit" in error_msg:
                return "⚠️ API 请求过于频繁，请稍后再试。"
            else:
                return f"⚠️ 生成解释时发生错误：{error_msg}"

    # ── Utility ──────────────────────────────────────────────────────

    def count_memories(self) -> int:
        """Return the total number of stored vectors."""
        return self.collection.count()
