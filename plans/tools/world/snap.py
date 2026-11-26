import sqlite3, sys, os
src, dst = sys.argv[1], sys.argv[2]
c = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
rows = c.execute("select finding_id, signal, status, work_state, stale, expected_cents, shortfall_cents, appeal_block, state_basis from findings").fetchall()
meta = c.execute("select key, value from meta where key like '%dirty%' or key like '%keep%' or key like 'ema%' or key like 'ledger%'").fetchall()
o = sqlite3.connect(dst); o.execute("create table f(finding_id, signal, status, work_state, stale, expected_cents, shortfall_cents, appeal_block, state_basis)")
o.executemany("insert into f values (?,?,?,?,?,?,?,?,?)", rows); o.execute("create table m(key, value)"); o.executemany("insert into m values (?,?)", meta); o.commit(); os.chmod(dst, 0o600)
print("snap", len(rows), "meta keys", [k for k, _ in meta][:12])
