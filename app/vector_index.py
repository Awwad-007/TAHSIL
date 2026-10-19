import hashlib
import math
import re
from threading import RLock
from typing import Dict, List

try:
    import faiss
    import numpy as np
except ImportError:  # pragma: no cover - exercised only on minimal installs
    faiss = None
    np = None


class HistoryIndex:
    def __init__(self, dim: int = 128):
        self.dim = dim
        self.docs: List[Dict[str, str]] = []
        self.lock = RLock()
        self.idx = faiss.IndexFlatIP(dim) if faiss else None

    def _vec(self, text: str):
        vals = [0.0] * self.dim
        for tok in re.findall(r"[a-z0-9_]+", text.lower()):
            pos = int(hashlib.sha256(tok.encode()).hexdigest(), 16) % self.dim
            vals[pos] += 1.0
        mag = math.sqrt(sum(v * v for v in vals)) or 1.0
        vals = [v / mag for v in vals]
        return np.array([vals], dtype="float32") if np else vals

    def clear(self) -> None:
        with self.lock:
            self.docs.clear()
            if self.idx:
                self.idx.reset()

    def add(self, customer_id: str, text: str) -> None:
        doc = {"customer_id": customer_id, "text": text}
        with self.lock:
            self.docs.append(doc)
            if self.idx:
                self.idx.add(self._vec(f"{customer_id} {text}"))

    def query(self, customer_id: str, text: str = "", k: int = 3) -> List[str]:
        with self.lock:
            pool = [d for d in self.docs if d["customer_id"] == customer_id]
            if not pool:
                return []
            q = self._vec(f"{customer_id} {text}")
            if self.idx:
                scores, ids = self.idx.search(q, len(self.docs))
                order = [int(i) for i in ids[0] if int(i) >= 0]
                pool_ids = {id(d) for d in pool}
                ranked = [self.docs[i] for i in order if id(self.docs[i]) in pool_ids]
            else:
                ranked = pool
            return [d["text"] for d in ranked[:k]]


history_index = HistoryIndex()


def seed_history(rows: List[Dict[str, str]]) -> None:
    history_index.clear()
    for row in rows:
        history_index.add(row["customer_id"], row["text"])
