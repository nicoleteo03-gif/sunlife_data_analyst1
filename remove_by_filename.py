#!/usr/bin/env python3
"""
Remove rows from master_data.csv where source_file exactly matches the given filename.
Creates a timestamped backup before modifying the CSV.
Usage:
  python remove_by_filename.py --filename test_new_fields.txt
"""
import argparse
from pathlib import Path
import pandas as pd
import shutil
import datetime
import sys

MASTER = Path("master_data.csv")

def backup():
    if MASTER.exists():
        stamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
        bak = MASTER.with_name(f"{MASTER.stem}_backup_{stamp}{MASTER.suffix}")
        shutil.copy2(MASTER, bak)
        print("Backup created:", bak)
        return bak
    else:
        print("master_data.csv not found.")
        sys.exit(1)

def remove_by_filename(filename: str):
    bak = backup()
    df = pd.read_csv(MASTER, dtype=str).fillna("")
    before = len(df)
    df = df[~(df.get("source_file", "").astype(str) == filename)]
    after = len(df)
    df.to_csv(MASTER, index=False)
    print(f"Removed {before-after} rows for filename '{filename}'. Remaining rows: {after}")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--filename", "-f", required=True, help="Exact filename to remove from master_data.csv")
    args = p.parse_args()
    remove_by_filename(args.filename)