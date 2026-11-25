"""A compact serverless image as the merged code would hold it on Vercel: Vercel's live image of 2026-11-24 10:28 (store.sqlite.gz)
loaded as cloud.load_raw does (Store.from_bytes), plus EMA's A/R and Production tables as push 2's readers built them in the
private world copy. Writes img_ema.sqlite (all EMA tables) and img_ar.sqlite (A/R tables only). Prints sizes only."""
import gzip, os, sqlite3, sys
SP = "/tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad"
TREE = sys.argv[1]
sys.path.insert(0, TREE); sys.dont_write_bytecode = True
from remitwatch.store import Store
AR = ("ema_ar", "ema_ar_items", "ema_claim_links", "ema_sources", "ema_report_files")
PROD = ("ema_lines", "ema_line_payments", "ema_line_reasons", "ema_link_map", "ema_link_events", "ema_checks", "ema_months")
src = f"{SP}/world2/ema_tables.sqlite"
if not os.path.exists(src):
    w = sqlite3.connect(f"file:{SP}/audit/world.sqlite?mode=ro", uri=True)
    w.execute(f"ATTACH '{src}' AS o")
    for t in AR + PROD:
        sql = w.execute("select sql from sqlite_master where type='table' and name=?", (t,)).fetchone()[0]
        w.execute(sql.replace(f"CREATE TABLE {t}", f"CREATE TABLE o.{t}", 1).replace(f'CREATE TABLE "{t}"', f'CREATE TABLE o."{t}"', 1))
        w.execute(f"INSERT INTO o.{t} SELECT * FROM main.{t}")
    w.commit(); w.close()
    os.chmod(src, 0o600)
for name, tables in (("img_ema", AR + PROD), ("img_ar", AR)):
    data = gzip.open(f"{SP}/world2/img/store.sqlite.gz").read()
    st = Store.from_bytes(data); del data
    c = st.conn
    c.execute(f"ATTACH '{src}' AS e")
    for t in tables:
        mine = [r[1] for r in c.execute(f"pragma main.table_info({t})")]
        theirs = {r[1] for r in c.execute(f"pragma e.table_info({t})")}
        cols = [x for x in mine if x in theirs]
        c.execute(f"DELETE FROM main.{t}")
        c.execute(f"INSERT INTO main.{t} ({','.join(cols)}) SELECT {','.join(cols)} FROM e.{t}")
    c.commit(); c.execute("DETACH e")
    out = st.to_bytes()
    p = f"{SP}/world2/{name}.sqlite"
    with open(p, "wb") as fh: fh.write(out)
    os.chmod(p, 0o600)
    gz = len(gzip.compress(out, 6))
    print(name, "image MB", round(len(out) / 1e6, 1), "gz MB", round(gz / 1e6, 1))
    del out, st
