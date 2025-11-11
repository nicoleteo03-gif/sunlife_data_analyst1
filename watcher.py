import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pathlib import Path
import shutil
from extractors import extract_document
from normalizer import normalize_fields
from indexer import Indexer
import pandas as pd
import datetime

INPUT_DIR = Path("input")
RAW_DIR = Path("raw")
MASTER_CSV = Path("master_data.csv")
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

def already_processed(filename: str) -> bool:
    if (RAW_DIR / filename).exists():
        return True
    if MASTER_CSV.exists():
        try:
            df = pd.read_csv(MASTER_CSV)
            if 'source_file' in df.columns and filename in df['source_file'].astype(str).values:
                return True
        except Exception:
            pass
    return False

class NewFileHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        # small delay to allow file to finish copying
        time.sleep(1)
        filename = path.name
        if already_processed(filename):
            print(f"[watcher] {filename} already processed. Skipping.")
            try:
                path.unlink()  # optionally remove duplicate upload
            except:
                pass
            return
        print(f"[watcher] Processing new file: {filename}")
        try:
            text, tables, metadata = extract_document(path)
            normalized = normalize_fields(text, tables, metadata)
            normalized["source_file"] = filename
            normalized["ingested_at"] = datetime.datetime.utcnow().isoformat()
            append_master(normalized)
            indexer.add_document(filename, text, metadata)
            indexer.save()
            dest = RAW_DIR / filename
            if dest.exists():
                dest = RAW_DIR / f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
            shutil.move(str(path), str(dest))
            print(f"[watcher] Done: {filename} archived to raw/")
        except Exception as e:
            print(f"[watcher] Error processing {filename}: {e}")

if __name__ == "__main__":
    event_handler = NewFileHandler()
    observer = Observer()
    observer.schedule(event_handler, str(INPUT_DIR), recursive=False)
    observer.start()
    print("[watcher] Watching input/ for new files. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()