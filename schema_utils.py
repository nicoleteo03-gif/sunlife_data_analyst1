import json
import re
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd

# optional fast fuzzy matching
try:
    from rapidfuzz import process, fuzz
    RAPIDFUZZ_AVAILABLE = True
except Exception:
    RAPIDFUZZ_AVAILABLE = False

# optional phone normalization
try:
    import phonenumbers
    PHONENUM_AVAILABLE = True
except Exception:
    PHONENUM_AVAILABLE = False

def normalize_key_name(k: str) -> str:
    """Normalize a key name to a stable form: lowercase, underscores, remove punctuation."""
    k = k.strip().lower()
    k = re.sub(r"[\/\-\:\.]+", " ", k)      # replace separators with space
    k = re.sub(r"\s+", "_", k)             # spaces -> underscore
    k = re.sub(r"[^\w_]", "", k)           # remove other non-word chars
    return k

def try_normalize_phone(value: str) -> str:
    """If phonenumbers is available, try to canonicalize phone numbers; otherwise return original."""
    if not PHONENUM_AVAILABLE or not isinstance(value, str):
        return value
    try:
        # Try parse in generic way (no region) then format E164 fallback to original
        p = phonenumbers.parse(value, None)
        if phonenumbers.is_valid_number(p):
            return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        pass
    return value

def fuzzy_map_key(key: str, existing: List[str], threshold: int = 85) -> Optional[str]:
    """
    If RAPIDFUZZ_AVAILABLE, return a best match from 'existing' if similarity >= threshold.
    Otherwise return None.
    """
    if not RAPIDFUZZ_AVAILABLE or not existing:
        return None
    # process.extractOne returns (match, score, index)
    match = process.extractOne(key, existing, scorer=fuzz.token_set_ratio)
    if match and match[1] >= threshold:
        return match[0]
    return None

def append_master_auto(master_csv: Path, row: Dict[str, str],
                       canonical_cols: Optional[List[str]] = None,
                       fuzzy_threshold: int = 85,
                       use_extras_json: bool = True):
    """
    Append `row` to master_csv. Behavior:
    - Normalize keys in row.
    - If a normalized key matches a canonical column (or fuzzy-maps to one), put it into that column.
    - Unknown keys are either:
      - added as new CSV columns (if use_extras_json=False), OR
      - put into a JSON string under column 'extras' (if use_extras_json=True).
    The function preserves existing CSV columns and order.
    """
    canonical = canonical_cols or ["client_name", "date", "amount", "department", "file_ext", "source_file", "ingested_at"]
    master_exists = master_csv.exists()

    # Normalize incoming keys and values
    norm_row = {}
    extras = {}
    for k, v in row.items():
        nk = normalize_key_name(str(k))
        val = v if v is not None else ""
        # attempt basic phone normalization for likely phone keys
        if nk in ("phone", "telephone", "tel", "mobile", "phone_number") and PHONENUM_AVAILABLE:
            val = try_normalize_phone(str(val))
        norm_row[nk] = val

    # Prepare frame for writing/appending
    if master_exists:
        df = pd.read_csv(master_csv, dtype=str).fillna("")
        existing_cols = list(df.columns)
    else:
        # create empty with canonical plus extras if chosen
        if use_extras_json:
            existing_cols = canonical + ["extras"]
        else:
            existing_cols = canonical.copy()

    # Map normalized row keys into canonical or extras/new columns
    mapped = {}
    new_columns = []
    for nk, val in norm_row.items():
        if nk in existing_cols:
            mapped[nk] = val
        else:
            # try fuzzy mapping to canonical or existing cols
            mapped_to = None
            # try map to canonical cols first
            mapped_to = fuzzy_map_key(nk, canonical, threshold=fuzzy_threshold)
            # if none, try mapping to existing_cols
            if mapped_to is None:
                mapped_to = fuzzy_map_key(nk, existing_cols, threshold=fuzzy_threshold)
            if mapped_to:
                mapped[mapped_to] = val
            else:
                # unknown key
                if use_extras_json:
                    extras[nk] = val
                else:
                    new_columns.append(nk)
                    mapped[nk] = val

    # Build row dict with full columns
    if master_exists:
        # ensure we add new columns if use_extras_json is False
        if not use_extras_json and new_columns:
            for nc in new_columns:
                if nc not in existing_cols:
                    existing_cols.append(nc)
                    df[nc] = ""  # add new blank column
    else:
        # build a fresh DataFrame columns
        if not use_extras_json:
            existing_cols = canonical + new_columns
        else:
            existing_cols = canonical + ["extras"]

    # Final row to append (respect existing_cols order)
    final_row = {c: "" for c in existing_cols}
    for k, v in mapped.items():
        if k in final_row:
            final_row[k] = v
    # Put canonical values if present in norm_row (guarantee)
    for c in canonical:
        if c in norm_row and c in final_row:
            final_row[c] = norm_row[c]
    # Attach extras JSON if enabled
    if use_extras_json:
        if extras:
            final_row["extras"] = json.dumps(extras, ensure_ascii=False)
        else:
            # ensure the column exists
            if "extras" not in final_row:
                final_row["extras"] = ""
    # Save to CSV (append)
    if master_exists:
        new_df = pd.DataFrame([final_row], dtype=str)
        combined = pd.concat([df, new_df], ignore_index=True, sort=False).fillna("")
        combined.to_csv(master_csv, index=False)
    else:
        pd.DataFrame([final_row], dtype=str).fillna("").to_csv(master_csv, index=False)