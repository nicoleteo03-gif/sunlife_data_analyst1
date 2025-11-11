# Deduplicate master_data.csv by source_file (keep last), drop rows with empty client_name,
# fix header if there is a stray empty column, and normalize amount a little.
import pandas as pd
from pathlib import Path
import shutil
import datetime
import re

MASTER = Path("master_data.csv")
if not MASTER.exists():
    print("master_data.csv not found.")
    exit(1)

# backup
stamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
shutil.copy2(MASTER, MASTER.with_name(f"master_data_backup_{stamp}.csv"))
print("Backup created")

# load (allow for bad columns by reading all columns)
df = pd.read_csv(MASTER, dtype=str)

# Fix stray empty column name if present (detect '')
if "" in df.columns:
    cols = [c for c in df.columns if c != ""]
    df = df[cols]
    print("Removed stray empty column")

# Ensure canonical columns exist
canonical = ["client_name", "date", "amount", "department", "file_ext", "source_file", "ingested_at"]
for c in canonical:
    if c not in df.columns:
        df[c] = ""

# Trim whitespace
df = df.applymap(lambda v: v.strip() if isinstance(v, str) else v)

# Drop rows with empty client_name and empty date and empty amount (optional)
df = df[~((df["client_name"]== "") & (df["date"]=="") & (df["amount"]==""))]

# Deduplicate by source_file keeping last occurrence
if "source_file" in df.columns:
    df = df.sort_values("ingested_at", na_position="first")
    df = df.drop_duplicates(subset=["source_file"], keep="last")

# Normalize amount: strip non-digit/decimal characters but keep dot and comma, then convert comma thousand separators
def normalize_amount(a):
    if not isinstance(a, str) or a.strip()=="":
        return ""
    s = a.strip()
    # replace common currency letters
    s = s.replace("MYR", "").replace("RM", "").replace("R", "").replace("M", "")
    # remove spaces
    s = s.replace(" ", "")
    # if comma used as decimal like 2,5 -> convert to 2.5 if needed (not safe to auto-decide)
    # remove thousands comma: 1,000 -> 1000
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    s = s.replace(",", "")
    # keep only digits and dot
    s = re.sub(r"[^0-9\.]", "", s)
    return s

df["amount"] = df["amount"].apply(normalize_amount)

df.to_csv(MASTER, index=False)
print("Cleaned and deduplicated. New row count:", len(df))