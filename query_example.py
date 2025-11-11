from indexer import Indexer

def interactive():
    idx = Indexer("index.faiss", "index_meta.json")
    while True:
        q = input("Query (or 'exit'): ")
        if q.strip().lower() in ("exit", "quit"):
            break
        results = idx.query(q, topk=5)
        for r in results:
            print("----")
            print("Doc:", r.get("doc_id"))
            print(r.get("text")[:400].replace("\n", " "))
            print("Meta:", r.get("metadata"))

if __name__ == "__main__":
    interactive()