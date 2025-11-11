import json
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np
import os

class Indexer:
    def __init__(self, faiss_path="index.faiss", meta_path="index_meta.json", model_name="all-MiniLM-L6-v2"):
        self.faiss_path = faiss_path
        self.meta_path = meta_path
        self.model = SentenceTransformer(model_name)
        self.emb_dim = self.model.get_sentence_embedding_dimension()
        self.index = faiss.IndexFlatL2(self.emb_dim)
        self.meta = []  # list of dicts: {doc_id, text, other_metadata}
        # load if exists
        if os.path.exists(self.faiss_path) and os.path.exists(self.meta_path):
            self.load()

    def add_document(self, doc_id, text, metadata=None):
        metadata = metadata or {}
        # simple chunking: split by 500 chars
        chunks = [text[i:i+500] for i in range(0, len(text), 500)] if text else []
        for c in chunks:
            emb = self.model.encode(c)
            emb = np.array([emb]).astype("float32")
            self.index.add(emb)
            self.meta.append({"doc_id": doc_id, "text": c, "metadata": metadata})

    def save(self):
        faiss.write_index(self.index, self.faiss_path)
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=2)

    def load(self):
        self.index = faiss.read_index(self.faiss_path)
        with open(self.meta_path, "r", encoding="utf-8") as f:
            self.meta = json.load(f)

    def query(self, q, topk=5):
        emb = self.model.encode(q).astype("float32")
        D, I = self.index.search(np.array([emb]), topk)
        results = []
        for idx in I[0]:
            if idx < len(self.meta):
                results.append(self.meta[idx])
        return results