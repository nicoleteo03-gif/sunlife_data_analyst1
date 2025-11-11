import re
import datetime
from typing import Tuple, Dict

# Try to load spaCy if available (optional, used for PERSON fallback)
try:
    import spacy
    nlp = spacy.load("en_core_web_sm")
except Exception:
    nlp = None

def parse_key_value_lines(text: str) -> Dict[str, str]:
    """
    Parse lines like "Key: Value" into a dict with normalized keys (lowercase, underscores).
    Returns a dict where keys are normalized (e.g., 'client_name', 'department', 'age', 'gender').
    """
    kv = {}
    # split into lines and try to capture "key: value" or "key - value"
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^\s*([^:–\-]{1,40})\s*[:\-–]\s*(.+)$', line)
        if m:
            k = m.group(1).strip().lower()
            v = m.group(2).strip()
            # normalize key name (remove spaces, convert to underscore)
            k = re.sub(r'[\s/]+', '_', k)
            k = re.sub(r'[^\w_]', '', k)
            kv[k] = v
    return kv

def find_date(text: str, kv: dict) -> str:
    # Prefer explicit key-value date if present
    for k in ("date", "date_of", "invoice_date"):
        if k in kv:
            return kv[k]
    # Try common formats
    # 1) yyyy-mm-dd
    m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if m:
        return m.group(1)
    # 2) dd/mm/yyyy or d/m/yyyy
    m = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', text)
    if m:
        return m.group(1)
    # 3) mm/dd/yyyy (US)
    m = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', text)
    if m:
        return m.group(1)
    # 4) d.m.y or d.mm.yyyy
    m = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4})', text)
    if m:
        return m.group(1)
    return ""

def find_amount(text: str, kv: dict) -> str:
    # Prefer explicit key value
    for k in ("amount", "total", "invoice_amount", "amt"):
        if k in kv:
            return kv[k]
    # Currency-aware search (RM, MYR, $, etc.)
    m = re.search(r'((?:RM|MYR|\$)\s?[0-9\.,]+)', text, re.I)
    if m:
        return m.group(1)
    # fallback to numbers with commas/dots
    m = re.search(r'([0-9]{1,3}(?:[,\.][0-9]{3})*(?:[.,][0-9]{2})?)', text)
    if m:
        return m.group(1)
    return ""

def find_client_name(text: str, kv: dict) -> str:
    # Check common kv keys
    for k in ("client_name", "client", "name", "customer_name", "customer"):
        if k in kv:
            return kv[k]
    # Look for lines that start with "Client" or "Name"
    for line in text.splitlines():
        if re.match(r'^\s*(client(?:\s+name)?|name)\s*[:\-–]', line, re.I):
            parts = re.split(r'[:\-–]', line, maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                return parts[1].strip()
    # spaCy PERSON fallback
    if nlp:
        doc = nlp(text[:2000])
        persons = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
        if persons:
            return persons[0]
    return ""

def find_department(text: str, kv: dict) -> str:
    # explicit key
    for k in ("department", "dept", "division", "team"):
        if k in kv:
            return kv[k]
    # keyword heuristics (expand as needed)
    low = text.lower()
    dept_map = {
        "finance": ["finance", "invoice", "payment", "amount due"],
        "marketing": ["marketing", "campaign", "social media", "campaign"],
        "it": ["it ", "server", "system", "it department", "tech", "it,"],
        "chef": ["chef", "kitchen", "culinary", "cook"],
        "operations": ["operations", "ops", "logistics"],
        "hr": ["human resources", "hr "]
    }
    for canonical, keywords in dept_map.items():
        for kw in keywords:
            if kw in low:
                # for "chef" return Chef (preserve original capitalization)
                return canonical.title() if canonical not in ("finance","marketing","it") else canonical.capitalize()
    return "Unknown"

def normalize_fields(text: str, tables, metadata: dict) -> dict:
    """
    Return a dictionary of normalized fields.
    This also extracts any extra key:value pairs found in the document and returns them as additional keys.
    """
    combined = text + "\n" + ("\n".join(tables) if tables else "")
    kv = parse_key_value_lines(combined)

    result = {}

    # core canonical fields
    result["client_name"] = find_client_name(combined, kv)
    result["date"] = find_date(combined, kv)
    result["amount"] = find_amount(combined, kv)
    result["department"] = find_department(combined, kv)
    result["file_ext"] = metadata.get("file_ext", "")

    # add any extra parsed kv pairs (age, gender, invoice, campaign, notes, etc.)
    # Normalise key names: lowercase and replace spaces with underscore
    for k, v in kv.items():
        if k in ("client_name", "date", "amount", "department", "file_ext"):
            continue
        # avoid overwriting core fields
        result[k] = v

    return result