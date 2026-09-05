"""Load the index, rank by cosine similarity, and expand along the graph.

Vectors/graph come from the cache; titles/bodies/links are read fresh from source
at load time, so edits show up without re-embedding unchanged docs.
"""
from __future__ import annotations

import numpy as np

from .config import CONTEXT_CHAR_BUDGET
from .corpus import Doc, hydrate, load_docs, read_index_cache
from .providers import embed


def _normalize(mat: np.ndarray) -> np.ndarray:
    return mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)


class Index:
    def __init__(self, docs: list[Doc]):
        self.docs = [d for d in docs if d.vector]
        self.matrix = _normalize(np.array([d.vector for d in self.docs], dtype=np.float32))

    @classmethod
    def load(cls) -> "Index":
        docs = load_docs()
        missing = hydrate(docs, read_index_cache())
        if missing:  # new/changed docs since the last enrich — embed them on the fly
            for d, vector in zip(missing, embed([m.body for m in missing], task="document")):
                d.vector = vector
        return cls(docs)

    def rank(self, qvec: list[float], k: int) -> list[tuple[Doc, float]]:
        q = _normalize(np.array([qvec], dtype=np.float32))[0]
        sims = self.matrix @ q
        return [(self.docs[i], float(sims[i])) for i in np.argsort(sims)[::-1][:k]]

    def search(self, query: str, k: int) -> list[tuple[Doc, float]]:
        return self.rank(embed([query], task="query")[0], k)

    def expand(self, seeds: list[Doc], hops: int) -> list[Doc]:
        """Add neighbours of the seeds — linked docs and docs sharing an entity —
        up to `hops` away. Entity overlap carries the graph where wikilinks are sparse."""
        selected = {d.path: d for d in seeds}
        frontier = list(seeds)
        for _ in range(hops):
            targets = {t for s in frontier for t in s.links}
            ents = {e.lower() for s in frontier for e in s.entities}
            nxt = []
            for d in self.docs:
                if d.path in selected:
                    continue
                linked = d.path in targets or any(link in selected for link in d.links)
                shares_entity = bool(ents & {e.lower() for e in d.entities})
                if linked or shares_entity:
                    selected[d.path] = d
                    nxt.append(d)
            if not nxt:
                break
            frontier = nxt
        return list(selected.values())


def build_context(docs: list[Doc], char_budget: int = CONTEXT_CHAR_BUDGET) -> str:
    blocks, used = [], 0
    for d in docs:
        head = [f"## {d.title}  ({d.path})"]
        if d.entities:
            head.append("Entities: " + ", ".join(d.entities))
        if d.relations:
            head.append("Relations:\n" + "\n".join(f"  - {r}" for r in d.relations))
        block = "\n".join(head) + "\n\n" + d.body.strip()
        if used + len(block) > char_budget and blocks:
            break
        blocks.append(block[:char_budget])
        used += len(block)
    return "\n\n---\n\n".join(blocks)
