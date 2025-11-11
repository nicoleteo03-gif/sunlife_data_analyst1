from flask import Flask, request, jsonify
from pathlib import Path
from extractors import extract_document
from normalizer import normalize_fields
from indexer import Indexer
import shutil
import datetime
import pandas as pd

app = Flask(__name__)
ROOT = Path(".")
INPUT_DIR = ROOT / "input"
RAW_DIR = ROOT / "raw"
MASTER_CSV = ROOT / "master_data.csv"
INPUT_DIR.mkdir(exist_ok=True)
RAW_DIR.mkdir(exist_ok=True)

indexer = Indexer("index.faiss", "index_meta.json")

def append_master(row: dict):
    if MASTER_CSV.exists():
        df = pd.read_csv(MASTER_CSV)
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])
    df.to_csv(MASTER_CSV, index=False)

@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "no file part"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "no selected file"}), 400
    save_path = INPUT_DIR / f.filename
    f.save(save_path)
    text, tables, metadata = extract_document(save_path)
    normalized = normalize_fields(text, tables, metadata)
    normalized["source_file"] = f.filename
    normalized["ingested_at"] = datetime.datetime.utcnow().isoformat()
    append_master(normalized)
    indexer.add_document(f.filename, text, metadata)
    indexer.save()
    shutil.move(str(save_path), str(RAW_DIR / f.filename))
    return jsonify({"status": "ok", "row": normalized})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765)