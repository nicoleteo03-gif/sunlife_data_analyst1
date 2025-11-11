from pathlib import Path
import json
import shutil
import datetime

def safe_rebuild_faiss_meta(filtered_meta, faiss_path: str, meta_path: str, rebuild_func):
    """
    Call rebuild_func(filtered_meta, faiss_path=..., meta_path=...). If it fails for any reason,
    fallback to writing meta_path with filtered_meta and return (ok, message).
    - rebuild_func should be your existing rebuild_faiss_from_meta function.
    """
    # backup existing meta & faiss
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

    # Attempt rebuild, but if any exception occurs, write meta json and return warning
    try:
        ok, msg = rebuild_func(filtered_meta, faiss_path=faiss_path, meta_path=meta_path)
        return ok, msg
    except Exception as e:
        # fallback: write meta file only
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(filtered_meta, f, ensure_ascii=False, indent=2)
            return False, f"FAISS rebuild failed: {e}. Meta written to {meta_path}."
        except Exception as e2:
            return False, f"Failed to write meta file after FAISS rebuild error: {e2}. Original error: {e}"