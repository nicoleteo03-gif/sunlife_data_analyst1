def append_master_expand(master_csv: Path, row: Dict[str, str],
                         canonical_cols: Optional[List[str]] = None):
    """
    Append a row to master_csv and expand any extra keys into new CSV columns.
    This will mutate the CSV schema (add columns) as new keys appear.
    """
    canonical = canonical_cols or ["client_name", "date", "amount", "department", "file_ext", "source_file", "ingested_at"]
    master_exists = master_csv.exists()
    # normalize incoming row keys
    norm = {}
    for k, v in row.items():
        nk = str(k).strip().lower().replace(' ', '_')
        norm[nk] = v if v is not None else ""
    if master_exists:
        df = pd.read_csv(master_csv, dtype=str).fillna("")
        cols = list(df.columns)
    else:
        df = None
        cols = canonical.copy()
    # Determine new columns to add
    for k in norm.keys():
        if k not in cols:
            cols.append(k)
            if df is not None:
                df[k] = ""
    # Build final_row respecting cols order
    final_row = {c: "" for c in cols}
    for k, v in norm.items():
        final_row[k] = v
    # Ensure canonical fields exist and are set if present
    for c in canonical:
        if c in norm:
            final_row[c] = norm[c]
    # Save
    if master_exists:
        new_df = pd.DataFrame([final_row], dtype=str)
        combined = pd.concat([df, new_df], ignore_index=True, sort=False).fillna("")
        combined.to_csv(master_csv, index=False)
    else:
        pd.DataFrame([final_row], dtype=str).fillna("").to_csv(master_csv, index=False)