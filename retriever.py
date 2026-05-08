from __future__ import annotations

import os
import pickle
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

import faiss
import numpy as np
import torch
from sentence_transformers import CrossEncoder, SentenceTransformer

from config import (
    KB_DIR,
    INDEX_DIR,
    EMBED_MODEL,
    RERANK_MODEL,
    TOP_K,
    MAX_CHARS_PER_CHUNK,
)


@dataclass
class KBChunk:
    source: str
    heading: str
    text: str


_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_EMBED_MODEL_CACHE = None
_RERANK_MODEL_CACHE = None


def get_retriever_device() -> str:
    return _DEVICE


def _read_markdown_files(root: str) -> List[str]:
    out = []
    for base, _, files in os.walk(root):
        for f in files:
            if f.endswith(".md"):
                out.append(os.path.join(base, f))
    return sorted(out)


def _chunk_markdown(path: str) -> List[KBChunk]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    chunks: List[KBChunk] = []
    current_heading = "root"
    buffer: List[str] = []

    def flush():
        nonlocal buffer
        txt = "\n".join(buffer).strip()
        if txt:
            while len(txt) > MAX_CHARS_PER_CHUNK:
                split_at = txt.rfind("\n", 0, MAX_CHARS_PER_CHUNK)
                if split_at == -1 or split_at < MAX_CHARS_PER_CHUNK // 2:
                    split_at = MAX_CHARS_PER_CHUNK

                part = txt[:split_at].strip()
                if part:
                    chunks.append(
                        KBChunk(
                            source=path,
                            heading=current_heading,
                            text=part,
                        )
                    )

                txt = txt[split_at:].strip()

            if txt:
                chunks.append(
                    KBChunk(
                        source=path,
                        heading=current_heading,
                        text=txt,
                    )
                )

        buffer = []

    for line in lines:
        if line.startswith("#"):
            flush()
            current_heading = line.strip()
            buffer.append(line)
        else:
            buffer.append(line)

    flush()
    return chunks


def load_kb_chunks() -> List[KBChunk]:
    files = _read_markdown_files(KB_DIR)
    chunks: List[KBChunk] = []

    for path in files:
        chunks.extend(_chunk_markdown(path))

    return chunks


def _index_paths() -> Tuple[str, str]:
    return (
        os.path.join(INDEX_DIR, "kb.index"),
        os.path.join(INDEX_DIR, "kb_chunks.pkl"),
    )


def _load_embed_model() -> SentenceTransformer:
    global _EMBED_MODEL_CACHE

    if _EMBED_MODEL_CACHE is None:
        _EMBED_MODEL_CACHE = SentenceTransformer(EMBED_MODEL, device=_DEVICE)
        print(f"[retriever] SentenceTransformer device: {_DEVICE}")

    return _EMBED_MODEL_CACHE


def _load_reranker() -> CrossEncoder:
    global _RERANK_MODEL_CACHE

    if _RERANK_MODEL_CACHE is None:
        _RERANK_MODEL_CACHE = CrossEncoder(RERANK_MODEL, device=_DEVICE)
        print(f"[retriever] CrossEncoder device: {_DEVICE}")

    return _RERANK_MODEL_CACHE


def _router_ids(query: str) -> List[str]:
    ids = re.findall(r"\b(r\d+)\b", query.lower())
    seen = set()
    out = []

    for x in ids:
        if x not in seen:
            seen.add(x)
            out.append(x)

    return out


def _query_expansions(query: str) -> List[str]:
    q = query.strip()
    lower = q.lower()
    expansions = [q]

    routers = _router_ids(lower)
    router_mentions = len(routers)

    if "bastion" in lower:
        expansions.extend(
            [
                "bastion host public ssh jump host",
                "bastion private host ssh only from bastion security group",
            ]
        )

    if "needs internet" in lower or "needs internet access" in lower or "outbound internet" in lower:
        expansions.extend(
            [
                "private subnet outbound internet nat gateway",
                "private host internet access through nat",
                "nat gateway public private subnet",
            ]
        )

    if "public" in lower and "private" in lower:
        expansions.extend(
            [
                "public private subnet split nat gateway",
                "split one lan into public and private subnets",
            ]
        )

    if router_mentions == 2:
        expansions.extend(
            [
                "two router topology vpc peering",
                "r1 r2 directly connected peering",
                "two routed domains peering",
            ]
        )

    if router_mentions >= 3 or ("r1 is connected to r2" in lower and "r2 is connected to r3" in lower):
        expansions.extend(
            [
                "three router chain transit gateway",
                "multi router tgw",
                "non transitive peering problem tgw",
            ]
        )

    if router_mentions <= 1:
        expansions.extend(
            [
                "single router one vpc multiple subnets",
            ]
        )

    if "firewall" in lower or "sg" in lower or "security group" in lower:
        expansions.extend(
            [
                "firewall security group default",
                "firewall mode security group",
            ]
        )

    seen = set()
    out = []

    for item in expansions:
        if item not in seen:
            seen.add(item)
            out.append(item)

    return out


def _category_flags(query: str) -> Dict[str, bool]:
    q = query.lower()
    routers = _router_ids(q)
    router_mentions = len(routers)

    has_chain = (
        ("r1 is connected to r2" in q and "r2 is connected to r3" in q)
        or "chain" in q
    )
    has_two_router_direct = (
        ("r1 is connected to r2" in q and "r2 is connected to r3" not in q)
        or "two routers" in q
    )

    wants_bastion = "bastion" in q or "jump host" in q
    wants_nat = (
        "nat" in q
        or "needs internet" in q
        or "needs internet access" in q
        or "outbound internet" in q
        or "private but needs internet" in q
    )
    wants_public_private = (
        ("public" in q and "private" in q)
        or wants_bastion
        or wants_nat
    )

    wants_tgw = (
        "tgw" in q
        or "transit gateway" in q
        or router_mentions >= 3
        or has_chain
    )

    wants_peering = (
        not wants_tgw
        and (
            "peering" in q
            or has_two_router_direct
            or router_mentions == 2
        )
    )

    wants_single_router = (
        router_mentions <= 1
        and not wants_peering
        and not wants_tgw
    )

    return {
        "wants_bastion": wants_bastion,
        "wants_nat": wants_nat,
        "wants_public_private": wants_public_private,
        "wants_peering": wants_peering,
        "wants_tgw": wants_tgw,
        "wants_single_router": wants_single_router,
        "wants_firewall": ("firewall" in q or "sg" in q or "security group" in q),
        "has_chain": has_chain,
        "has_two_router_direct": has_two_router_direct,
        "router_mentions": router_mentions,
    }


def _metadata_boost(query: str, chunk: KBChunk) -> float:
    flags = _category_flags(query)
    source_name = os.path.basename(chunk.source).lower()
    heading = chunk.heading.lower()
    text = chunk.text.lower()
    blob = f"{source_name}\n{heading}\n{text}"

    boost = 0.0

    def has(*terms: str) -> bool:
        return any(term.lower() in blob for term in terms)

    if flags["wants_single_router"]:
        if has("single_router.md", "single router", "one vpc"):
            boost += 0.70
        if has("peering.md", "vpc peering"):
            boost -= 0.80
        if has("tgw.md", "transit gateway"):
            boost -= 0.95

    if flags["wants_bastion"]:
        if has("bastion.md", "bastion host", "ssh to s1 only from bastion security group"):
            boost += 1.05
        if has("security_patterns.md", "bastion receives ssh from admin cidr", "ssh only from bastion sg"):
            boost += 0.45

    if flags["wants_nat"]:
        if has("nat.md", "nat gateway", "private subnet default route to nat", "private host internet access"):
            boost += 1.00
        if has("aws_network_patterns.md", "private outbound internet requires nat gateway"):
            boost += 0.45

    if flags["wants_public_private"]:
        if has("nat.md", "split into public/private", "public subnet", "private subnet"):
            boost += 0.55
        if has("aws_network_patterns.md", "public subnet", "private subnet"):
            boost += 0.40

    if flags["wants_peering"]:
        if has("peering.md", "vpc peering", "two routers"):
            boost += 1.20
        if has("aws_network_patterns.md", "## peering", "vpc peering is non-transitive"):
            boost += 0.35
        if has("tgw.md", "transit gateway", "three router chain"):
            boost -= 1.10
        if has("single_router.md", "single router", "one vpc"):
            boost -= 0.45

    if flags["wants_tgw"]:
        if has("tgw.md", "transit gateway", "three router chain"):
            boost += 1.25
        if has("aws_network_patterns.md", "## transit gateway", "better for 3+ routed domains"):
            boost += 0.60
        if has("peering.md", "vpc peering"):
            boost -= 1.00
        if has("single_router.md", "single router", "one vpc"):
            boost -= 0.70

    if flags["has_chain"]:
        if has("tgw.md", "three router chain", "transit gateway"):
            boost += 0.45
        if has("peering.md", "vpc peering"):
            boost -= 0.55

    if flags["has_two_router_direct"]:
        if has("peering.md", "two routers", "vpc peering"):
            boost += 0.55
        if has("tgw.md", "transit gateway"):
            boost -= 0.45

    if flags["wants_firewall"]:
        if has("security_patterns.md", "firewall mode defaults to security group", "security group"):
            boost += 0.40
        if has("mapping_rules.md", "firewall -> security group"):
            boost += 0.20

    if has("mapping_rules.md", "core interpretation", "mapping rules"):
        boost += 0.05

    return boost


def _build_fresh_index():
    os.makedirs(INDEX_DIR, exist_ok=True)
    index_path, meta_path = _index_paths()

    chunks = load_kb_chunks()
    if not chunks:
        raise ValueError(f"No KB chunks found under {KB_DIR}")

    model = _load_embed_model()

    texts = [f"{c.heading}\n{c.text}" for c in chunks]
    emb = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
        batch_size=32,
    )
    emb = np.asarray(emb, dtype="float32")

    if emb.ndim != 2 or emb.shape[0] == 0 or emb.shape[1] == 0:
        raise ValueError(f"Invalid embedding matrix shape: {emb.shape}")

    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)

    faiss.write_index(index, index_path)

    with open(meta_path, "wb") as f:
        pickle.dump(chunks, f)

    return model, index, chunks


def build_or_load_index():
    os.makedirs(INDEX_DIR, exist_ok=True)
    index_path, meta_path = _index_paths()

    if os.path.exists(index_path) and os.path.exists(meta_path):
        try:
            index = faiss.read_index(index_path)

            with open(meta_path, "rb") as f:
                chunks = pickle.load(f)

            model = _load_embed_model()

            if len(chunks) == 0:
                raise ValueError("Cached chunk list is empty.")

            if index.ntotal != len(chunks):
                raise ValueError(
                    f"Index/chunk mismatch: index={index.ntotal}, chunks={len(chunks)}"
                )

            return model, index, chunks

        except Exception:
            try:
                os.remove(index_path)
            except OSError:
                pass

            try:
                os.remove(meta_path)
            except OSError:
                pass

    return _build_fresh_index()


def _faiss_recall(query: str, recall_k: int = 14) -> List[Tuple[int, float]]:
    model, index, _ = build_or_load_index()
    candidates: Dict[int, float] = {}

    for expanded_query in _query_expansions(query):
        q = model.encode(
            [expanded_query],
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
            batch_size=1,
        )
        q = np.asarray(q, dtype="float32")

        scores, idxs = index.search(q, recall_k)

        for score, idx in zip(scores[0], idxs[0]):
            if idx < 0:
                continue

            score = float(score)

            if idx not in candidates or score > candidates[idx]:
                candidates[idx] = score

    ranked = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
    return ranked[:recall_k]


def retrieve_context(query: str, top_k: int = TOP_K) -> List[Dict[str, str]]:
    _, _, chunks = build_or_load_index()
    reranker = _load_reranker()

    recalled = _faiss_recall(query, recall_k=max(top_k * 3, 14))
    if not recalled:
        return []

    candidate_pairs = []
    candidate_meta = []

    for idx, vec_score in recalled:
        chunk = chunks[idx]
        candidate_pairs.append([query, f"{chunk.heading}\n{chunk.text}"])
        candidate_meta.append((idx, vec_score, chunk))

    rerank_scores = reranker.predict(
        candidate_pairs,
        batch_size=16,
        show_progress_bar=False,
    )

    rescored = []

    for (idx, vec_score, chunk), rerank_score in zip(candidate_meta, rerank_scores):
        meta_boost = _metadata_boost(query, chunk)
        final_score = (
            0.30 * float(vec_score)
            + 0.25 * float(rerank_score)
            + 1.60 * float(meta_boost)
        )
        rescored.append(
            (final_score, vec_score, float(rerank_score), meta_boost, idx, chunk)
        )

    rescored.sort(key=lambda x: x[0], reverse=True)

    results: List[Dict[str, str]] = []

    for final_score, vec_score, rerank_score, meta_boost, _, c in rescored[:top_k]:
        results.append(
            {
                "source": c.source,
                "heading": c.heading,
                "text": c.text,
                "score": float(final_score),
                "vector_score": float(vec_score),
                "rerank_score": float(rerank_score),
                "metadata_boost": float(meta_boost),
            }
        )

    return results
