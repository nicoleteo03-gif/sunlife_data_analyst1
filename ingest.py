import os
import json
from pathlib import Path
from extractors import extract_document, extract_tables_from_pdf
from indexer import Indexer
from normalizer import normalize_fields
import pandas as pd
from tqdm import tqdm

INPUT_DIR = Path("input")
RAW_DIR = Path("raw")
RAW_DIR.mkdir(exist_ok=True)
MASTER_CSV = Path("master_data.csv")

def append_master(row):
    if MASTER_CSV.exists():
        df = pd.read_csv(MASTER_CSV)
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])
    df.to_csv(MASTER_CSV, index=False)

def main():
    indexer = Indexer("index.faiss", "index_meta.json")
    files = list(INPUT_DIR.glob("*"))
    if not files:
        print("No files in input/ — put some PDFs, DOCX, or images there.")
        return

    for file in tqdm(files):
        print("Processing", file)
        text, tables, metadata = extract_document(file)
        # normalize fields from text + tables
        normalized = normalize_fields(text, tables, metadata)
        # add file metadata
        normalized["source_file"] = str(file.name)
        append_master(normalized)

        # add to vector index: chunk text simply
        indexer.add_document(str(file.name), text, metadata)

        # move file to raw storage
        dest = RAW_DIR / file.name
        file.rename(dest)

    # persist index
    indexer.save()
    print("Done. Master CSV and index updated.")

if __name__ == "__main__":
    main()