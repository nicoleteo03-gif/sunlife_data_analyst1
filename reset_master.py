# Reset master_data.csv to an empty canonical header (backups master_data.csv first)
from pathlib import Path
import shutil
import datetime
import pandas as pd

MASTER = Path("master_data.csv")
if MASTER.exists():
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    bak = MASTER.with_name(f"master_data_backup_{stamp}.csv")
    shutil.copy2(MASTER, bak)
    print("Backup created:", bak)

# canonical header (no stray empty column)
cols = ["client_name", "date", "amount", "department", "file_ext", "source_file", "ingested_at"]
df = pd.DataFrame(columns=cols)
df.to_csv(MASTER, index=False)
print("master_data.csv reset with header:", cols)