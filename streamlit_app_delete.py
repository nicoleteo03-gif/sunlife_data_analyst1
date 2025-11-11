r"""
Replace your existing streamlit_app_with_delete.py with this complete file.

What this file does (single unified script)
- Streamlit app to upload files, extract text, normalize fields (captures unknown keys)
- Option to save unknown keys into a JSON "extras" column (default) OR expand them into separate CSV columns
- Button to "Expand existing extras into columns" (one-click) to flatten previous rows
- Safe Delete-by-filename with backups and index rebuild (FAISS if available; fallback to meta-only)
- Works with your existing extractors.py and indexer.py if present; falls back to simple behavior otherwise

Save this file to your project folder:
C:\Users\khaiw\OneDrive\Sunlife_GenAI_Demo\copilot\streamlit_app_with_delete.py

Run:
.\venv\Scripts\Activate.ps1
streamlit run streamlit_app_with_delete.py
"""

import streamlit as st
from pathlib import Path
import shutil
import pandas as pd
import datetime
import json
import re
from typing import Dict, List, Optional
import os

# Try to import existing modules (if present in your project)
try:
    from extractors import extract_document  # should return (text, tables, metadata)
except Exception:
    extract_document = None

try:
    from indexer import Indexer
except Exception:
    Indexer = None

# Optional embeddings / faiss for rebuild (not required)
try:
    from sentence_transformers import SentenceTransformer
    st_model_available = True
except Exception:
    SentenceTransformer = None
    st_model_available = False

try:
    import faiss
    faiss_available = True
except Exception:
    faiss = None
    faiss_available = False

import numpy as np

# ----------------------------
# Paths / config
# ----------------------------
ROOT = Path(".")
INPUT_DIR = ROOT / "input"
RAW_DIR = ROOT / "raw"
MASTER_CSV = ROOT / "master_data.csv"
FAISS_PATH = "index.faiss"
META_PATH = "index_meta.json"

INPUT_DIR.mkdir(exist_ok=True)
RAW_DIR.mkdir(exist_ok=True)

# ----------------------------
# Fallback extractor if none provided
# ----------------------------
def fallback_extract_document(path: Path):
    text = ""
    tables = []
    metadata = {"file_ext": path.suffix.lower()}
    if path.suffix.lower() == ".txt":
        text = path.read_text(encoding="utf-8", errors="ignore")
    elif path.suffix.lower() == ".docx":
        try:
            from docx import Document
            doc = Document(path)
            paragraphs = [p.text for p in doc.paragraphs]
            text = "\n".join(paragraphs)
        except Exception:
            text = ""
    else:
        # PDF/image OCR not implemented here; rely on your extractors.py for those
        text = ""
    return text, tables, metadata

def call_extract_document(path: Path):
    if extract_document:
        try:
            return extract_document(path)
        except Exception:
            return fallback_extract_document(path)
    else:
        return fallback_extract_document(path)

# ----------------------------
# Normalizer: parse key:value and heuristics
# ----------------------------
try:
    import spacy
    nlp = spacy.load("en_core_web_sm")
except Exception:
    nlp = None

def parse_key_value_lines(text: str) -> Dict[str, str]:
    kv = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # match "Key: Value" or "Key - Value"
        m = re.match(r'^\s*([^:–\-]{1,120})\s*[:\-–]\s*(.+)$', line)
        if m:
            key = m.group(1).strip().lower()
            val = m.group(2).strip()
            key = re.sub(r'[\s/]+', '_', key)
            key = re.sub(r'[^\w_]', '', key)
            kv[key] = val
    return kv

def find_date(text: str, kv: dict) -> str:
    for k in ("date", "invoice_date", "date_of"):
        if k in kv:
            return kv[k]
    m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if m:
        return m.group(1)
    m = re.search(r'(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{4})', text)
    if m:
        return m.group(1)
    return ""

def find_amount(text: str, kv: dict) -> str:
    for k in ("amount", "total", "invoice_amount", "amt"):
        if k in kv:
            return kv[k]
    m = re.search(r'((?:RM|MYR|\$)\s?[0-9\.,]+)', text, re.I)
    if m:
        return m.group(1)
    m = re.search(r'([0-9]{1,3}(?:[,\.][0-9]{3})*(?:[.,][0-9]{2})?)', text)
    if m:
        return m.group(1)
    return ""

def find_client_name(text: str, kv: dict) -> str:
    for k in ("client_name", "client", "name", "customer_name", "customer"):
        if k in kv:
            return kv[k]
    for line in text.splitlines():
        if re.match(r'^\s*(client(?:\s+name)?|name)\s*[:\-–]', line, re.I):
            parts = re.split(r'[:\-–]', line, maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                return parts[1].strip()
    if nlp:
        doc = nlp(text[:2000])
        persons = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
        if persons:
            return persons[0]
    return ""

def find_department(text: str, kv: dict) -> str:
    for k in ("department", "dept", "division", "team"):
        if k in kv:
            return kv[k]
    low = text.lower()
    dept_map = {
        "finance": ["finance", "invoice", "payment", "amount due"],
        "marketing": ["marketing", "campaign", "social media"],
        "it": ["it ", "server", "system", "it department", "tech"],
        "chef": ["chef", "kitchen", "culinary"],
        "operations": ["operations", "ops", "logistics"],
        "hr": ["human resources", "hr "]
    }
    for canonical, keywords in dept_map.items():
        for kw in keywords:
            if kw in low:
                return canonical.title()
    return "Unknown"

def normalize_fields(text: str, tables, metadata: dict) -> dict:
    combined = text + "\n" + ("\n".join(tables) if tables else "")
    kv = parse_key_value_lines(combined)
    result = {}
    result["client_name"] = find_client_name(combined, kv)
    result["date"] = find_date(combined, kv)
    result["amount"] = find_amount(combined, kv)
    result["department"] = find_department(combined, kv)
    result["file_ext"] = metadata.get("file_ext", "")
    # include any other parsed kv pairs (age, gender, address, phone, hobby, etc.)
    for k, v in kv.items():
        if k in ("client_name", "date", "amount", "department", "file_ext"):
            continue
        result[k] = v
    return result

# ----------------------------
# Schema helper: append row with extras JSON OR expand into columns
# ----------------------------
def safe_col_name(k: str) -> str:
    k = str(k).strip().lower()
    k = re.sub(r"[\/\-\:\.]+", " ", k)
    k = re.sub(r"\s+", "_", k)
    k = re.sub(r"[^\w_]", "", k)
    return k

def append_master_with_options(master_csv: Path, row: Dict[str, str], use_extras_json: bool = True):
    """
    If use_extras_json=True: store unknown keys under 'extras' JSON column (recommended).
    If use_extras_json=False: add unknown keys as new CSV columns (schema-on-write).
    """
    canonical = ["client_name", "date", "amount", "department", "file_ext", "source_file", "ingested_at"]
    master_exists = master_csv.exists()
    # normalize incoming keys & values
    norm = {}
    for k, v in row.items():
        nk = safe_col_name(k)
        norm[nk] = "" if v is None else str(v)
    if master_exists:
        df = pd.read_csv(master_csv, dtype=str).fillna("")
        existing_cols = list(df.columns)
    else:
        if use_extras_json:
            existing_cols = canonical + ["extras"]
        else:
            existing_cols = canonical.copy()
        df = None
    extras = {}
    new_cols = []
    mapped = {}
    for nk, val in norm.items():
        if nk in existing_cols:
            mapped[nk] = val
        elif nk in canonical:
            mapped[nk] = val
        else:
            if use_extras_json:
                extras[nk] = val
            else:
                new_cols.append(nk)
                mapped[nk] = val
    # add new columns to df if needed
    if df is not None and new_cols and not use_extras_json:
        for nc in new_cols:
            if nc not in existing_cols:
                existing_cols.append(nc)
                df[nc] = ""
    # build final row
    final_row = {c: "" for c in existing_cols}
    for k, v in mapped.items():
        if k in final_row:
            final_row[k] = v
    for c in canonical:
        if c in norm and c in final_row:
            final_row[c] = norm[c]
    if use_extras_json:
        if extras:
            final_row["extras"] = json.dumps(extras, ensure_ascii=False)
        else:
            if "extras" not in final_row:
                final_row["extras"] = ""
    # append and save
    if df is not None:
        new_df = pd.DataFrame([final_row], dtype=str)
        combined = pd.concat([df, new_df], ignore_index=True, sort=False).fillna("")
        combined.to_csv(master_csv, index=False)
    else:
        pd.DataFrame([final_row], dtype=str).fillna("").to_csv(master_csv, index=False)

# ----------------------------
# Utility: expand existing extras column into separate CSV columns (in-place or to new file)
# ----------------------------
def expand_extras_in_csv(master_csv: Path, out_csv: Path = None, overwrite: bool = False):
    """
    Parse the 'extras' JSON column and create new columns for each key.
    - Creates a timestamped backup of master_csv.
    - Writes expanded CSV to out_csv (if provided) or master_data_expanded_TIMESTAMP.csv.
    - If overwrite=True, will replace master_csv with the expanded version.
    """
    if not master_csv.exists():
        return False, f"{master_csv} not found."
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    backup_path = master_csv.with_name(f"{master_csv.stem}_backup_{stamp}{master_csv.suffix}")
    shutil.copy2(master_csv, backup_path)
    df = pd.read_csv(master_csv, dtype=str).fillna("")
    if "extras" not in df.columns:
        return True, "No extras column found; nothing to expand."
    all_keys = set()
    parsed_extras = []
    for val in df["extras"].astype(str).tolist():
        parsed = {}
        if isinstance(val, str) and val.strip():
            try:
                parsed = json.loads(val)
            except Exception:
                # fallback: try Python literal
                try:
                    import ast
                    parsed = ast.literal_eval(val)
                except Exception:
                    # last-resort regex parsing
                    import re
                    pairs = re.findall(r'"?([A-Za-z0-9_\-\s]+)"?\s*:\s*"?([^",}]+)"?', val)
                    parsed = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in pairs}
        parsed_norm = {safe_col_name(k): v for k, v in parsed.items()}
        for k in parsed_norm.keys():
            all_keys.add(k)
        parsed_extras.append(parsed_norm)
    # Add columns
    for k in sorted(all_keys):
        if k not in df.columns:
            df[k] = ""
    # Fill values
    for idx, mapping in enumerate(parsed_extras):
        for k, v in mapping.items():
            df.at[idx, k] = v
    # Write out
    out_path = out_csv if out_csv else master_csv.with_name(f"{master_csv.stem}_expanded_{stamp}{master_csv.suffix}")
    df.to_csv(out_path, index=False)
    if overwrite:
        shutil.copy2(out_path, master_csv)
        return True, f"Expanded and overwrote {master_csv}. Backup: {backup_path.name}"
    return True, f"Expanded CSV written to {out_path}. Backup: {backup_path.name}"

# ----------------------------
# FAISS rebuild helper (best-effort)
# ----------------------------
def rebuild_faiss_from_meta(filtered_meta, faiss_path=FAISS_PATH, meta_path=META_PATH, model_name="all-MiniLM-L6-v2"):
    # backup meta & faiss
    try:
        if Path(meta_path).exists():
            shutil.copy2(meta_path, Path(meta_path).with_name(f"{Path(meta_path).stem}_backup_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}{Path(meta_path).suffix}"))
    except Exception:
        pass
    try:
        if Path(faiss_path).exists():
            shutil.copy2(faiss_path, Path(faiss_path).with_name(f"{Path(faiss_path).stem}_backup_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}{Path(faiss_path).suffix}"))
    except Exception:
        pass

    # If required libraries missing, just write meta back and return warning
    if not (st_model_available and faiss_available):
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(filtered_meta, f, ensure_ascii=False, indent=2)
        return False, "SentenceTransformer or FAISS not available. Meta updated but FAISS not rebuilt."

    model = SentenceTransformer(model_name)
    emb_dim = model.get_sentence_embedding_dimension()
    index = faiss.IndexFlatL2(emb_dim)

    texts = [m["text"] for m in filtered_meta]
    if not texts:
        # write empty meta and empty index
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        empty = faiss.IndexFlatL2(emb_dim)
        faiss.write_index(empty, faiss_path)
        return True, "Rebuilt empty index/meta."

    batch_size = 64
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        embs = model.encode(batch, convert_to_numpy=True, show_progress_bar=False)
        embs = np.array(embs, dtype="float32")
        if embs.ndim == 1:
            embs = np.expand_dims(embs, 0)
        index.add(embs)

    faiss.write_index(index, faiss_path)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(filtered_meta, f, ensure_ascii=False, indent=2)
    return True, "Rebuilt FAISS index and meta."

# ----------------------------
# Indexer initialization (use your Indexer if present)
# ----------------------------
if Indexer:
    indexer = Indexer(FAISS_PATH, META_PATH)
else:
    # simple stub: store meta entries in META_PATH and naive substring query
    class _StubIndexer:
        def add_document(self, doc_id, text, metadata):
            meta = []
            if Path(META_PATH).exists():
                try:
                    meta = json.load(open(META_PATH, "r", encoding="utf-8"))
                except Exception:
                    meta = []
            meta.append({"doc_id": doc_id, "text": text, "metadata": metadata})
            with open(META_PATH, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        def save(self):
            pass
        def query(self, q, topk=5):
            res = []
            meta = []
            if Path(META_PATH).exists():
                try:
                    meta = json.load(open(META_PATH, "r", encoding="utf-8"))
                except Exception:
                    meta = []
            for m in meta:
                if q.lower() in (m.get("text", "") or "").lower() or q.lower() in json.dumps(m.get("metadata", {})).lower():
                    res.append({"doc_id": m.get("doc_id"), "text": m.get("text"), "metadata": m.get("metadata")})
            return res[:topk]
    indexer = _StubIndexer()

# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="GenAI File Uploader (with delete + extras options)", layout="wide")
st.title("GenAI: Upload files — flexible schema + delete by filename")

uploaded = st.file_uploader("Upload a file (pdf, docx, txt, png, jpg)", accept_multiple_files=False)

st.sidebar.header("Save options")
use_extras = st.sidebar.checkbox("Save unknown keys inside 'extras' JSON (stable schema, recommended)", value=True)
auto_expand_on_save = st.sidebar.checkbox("When saving, expand unknown keys into separate CSV columns (schema-on-write)", value=False)
# If auto_expand_on_save is checked, we treat unknown keys as new columns even though use_extras may be checked.

st.sidebar.markdown("---")
if st.sidebar.button("Expand existing extras into columns (one-time)"):
    ok, msg = expand_extras_in_csv(MASTER_CSV, overwrite=False)
    if ok:
        st.sidebar.success(msg)
    else:
        st.sidebar.error(msg)

st.sidebar.markdown("---")
st.sidebar.subheader("Quick actions")
if st.sidebar.button("Show master CSV (head)"):
    if MASTER_CSV.exists():
        st.write(pd.read_csv(MASTER_CSV).head(50))
    else:
        st.write("master_data.csv not present yet.")

# Search area
st.sidebar.markdown("---")
st.sidebar.subheader("Search index (example)")
q = st.sidebar.text_input("Query text", value="Client")
if st.sidebar.button("Run search"):
    try:
        results = indexer.query(q, topk=5)
        st.write("Top results:")
        for r in results:
            st.markdown(f"**Doc:** {r.get('doc_id')}")
            st.write((r.get("text") or "")[:400].replace("\n", " "))
            st.write(r.get("metadata"))
    except Exception as e:
        st.error(f"Search failed: {e}")

# Delete UI
st.sidebar.markdown("---")
st.sidebar.subheader("Delete data by filename")
del_name = st.sidebar.text_input("Filename to delete (exact match)", value="")
if st.sidebar.button("Delete by filename"):
    if not del_name:
        st.sidebar.error("Enter a filename (exact match) to delete.")
    else:
        if st.sidebar.checkbox(f"Confirm deletion of all rows and index entries for '{del_name}'"):
            # backup CSV & meta
            bak_csv = None
            if MASTER_CSV.exists():
                stamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
                bak_csv = MASTER_CSV.with_name(f"{MASTER_CSV.stem}_backup_{stamp}{MASTER_CSV.suffix}")
                shutil.copy2(MASTER_CSV, bak_csv)
            bak_meta = None
            if Path(META_PATH).exists():
                stamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
                bak_meta = Path(META_PATH).with_name(f"{Path(META_PATH).stem}_backup_{stamp}{Path(META_PATH).suffix}")
                shutil.copy2(META_PATH, bak_meta)
            bak_faiss = None
            if Path(FAISS_PATH).exists():
                try:
                    stamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
                    bak_faiss = Path(FAISS_PATH).with_name(f"{Path(FAISS_PATH).stem}_backup_{stamp}{Path(FAISS_PATH).suffix}")
                    shutil.copy2(FAISS_PATH, bak_faiss)
                except Exception:
                    pass

            # remove rows from CSV
            if MASTER_CSV.exists():
                df = pd.read_csv(MASTER_CSV, dtype=str).fillna("")
                before = len(df)
                df = df[~(df.get("source_file", "").astype(str) == del_name)]
                after = len(df)
                df.to_csv(MASTER_CSV, index=False)
            else:
                before = after = 0

            # filter meta and rebuild index
            meta = []
            if Path(META_PATH).exists():
                try:
                    meta = json.load(open(META_PATH, "r", encoding="utf-8"))
                except Exception:
                    meta = []
            filtered_meta = [m for m in meta if m.get("doc_id") != del_name]
            ok, msg = rebuild_faiss_from_meta(filtered_meta, faiss_path=FAISS_PATH, meta_path=META_PATH)
            if not ok:
                st.sidebar.warning(msg)
            st.sidebar.success(f"Deleted {before-after} rows from master CSV for '{del_name}'. Backups: {bak_csv.name if bak_csv else 'none'}")
            if bak_meta:
                st.sidebar.info(f"Meta backup: {bak_meta.name}")
            if bak_faiss:
                st.sidebar.info(f"FAISS backup: {bak_faiss.name}")
            raw_file = RAW_DIR / del_name
            if raw_file.exists():
                try:
                    raw_file.unlink()
                    st.sidebar.info(f"Removed raw/{del_name}")
                except Exception:
                    st.sidebar.warning(f"Could not remove raw/{del_name}")

# ----------------------------
# Main upload & save flow
# ----------------------------
if uploaded:
    save_path = INPUT_DIR / uploaded.name
    with open(save_path, "wb") as f:
        f.write(uploaded.getbuffer())
    st.success(f"Saved file to: {save_path}")

    st.info("Running extraction...")
    text, tables, metadata = call_extract_document(save_path)
    metadata = metadata or {}
    metadata.setdefault("file_ext", save_path.suffix.lower())

    st.subheader("Raw extracted text (first 4000 chars)")
    st.text_area("Text", value=(text[:4000] + ("...\n\n(Truncated)" if len(text) > 4000 else "")), height=250)

    st.subheader("Detected tables (if any)")
    if tables:
        for i, t in enumerate(tables[:5], 1):
            st.markdown(f"Table {i}")
            st.text_area(f"table_{i}", value=t, height=120)
    else:
        st.write("No tables detected.")

    st.subheader("Normalized fields (heuristic)")
    normalized = normalize_fields(text, tables, metadata)
    normalized["source_file"] = uploaded.name
    normalized["ingested_at"] = datetime.datetime.utcnow().isoformat()

    # Show editable fields (including any extras parsed)
    cols = list(normalized.keys())
    edited = {}
    for c in cols:
        edited[c] = st.text_input(c, value=str(normalized.get(c, "")))

    if st.button("Save to master CSV and index"):
        row = {k: edited[k] for k in edited}
        # Decide save behavior based on sidebar options
        if auto_expand_on_save:
            append_master_with_options(MASTER_CSV, row, use_extras_json=False)
        else:
            append_master_with_options(MASTER_CSV, row, use_extras_json=use_extras)
        # update index meta
        try:
            indexer.add_document(uploaded.name, text, metadata)
            indexer.save()
        except Exception:
            meta = []
            if Path(META_PATH).exists():
                try:
                    meta = json.load(open(META_PATH, "r", encoding="utf-8"))
                except Exception:
                    meta = []
            meta.append({"doc_id": uploaded.name, "text": text, "metadata": metadata})
            with open(META_PATH, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        # move uploaded file to raw/ archive
        try:
            dest = RAW_DIR / uploaded.name
            if dest.exists():
                dest = RAW_DIR / f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uploaded.name}"
            shutil.move(str(save_path), str(dest))
        except Exception as e:
            st.warning(f"Could not move file to raw/: {e}")
        st.success("Saved row and updated index. File archived to raw/")

# Footer notes
# Add or replace at the end of your file (near the bottom, after the main UI code)
st.markdown("---")
st.caption("Notes: unknown fields are preserved. Use 'Expand existing extras into columns' to flatten previous rows. Backups are created automatically for delete/expand operations.")