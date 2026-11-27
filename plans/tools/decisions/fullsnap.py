"""Private: copy the findings table (all columns) and results_items into a snapshot file. Prints counts only."""
import os, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
if os.path.exists(dst):
    os.remove(dst)
c = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
o = sqlite3.connect(dst)
os.chmod(dst, 0o600)
for t in ("findings", "results_items", "detection_runs"):
    try:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
    except sqlite3.Error:
        continue
    if not cols:
        continue
    o.execute(f"CREATE TABLE {t} ({', '.join(cols)})")
    rows = c.execute(f"SELECT * FROM {t}").fetchall()
    o.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * len(cols))})", rows)
    print(t, len(rows))
o.commit()
