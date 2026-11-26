"""world2 = world.sqlite (A.sqlite + EMA's reports read by push 2's readers + audit tables) with the 837 history replaced by
Vercel's image of 2026-11-24 10:28 (claims_837 / lines_837 2024-01..2026-11), and the readers' bookkeeping (files,
dropbox_seen) taken from the image. The 835 side of both is identical (checked by table counts). Prints counts only."""
import shutil, sqlite3, sys
SP = "/tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad"
SRC, IMG, OUT = f"{SP}/audit/world.sqlite", f"{SP}/world2/img/store.sqlite", f"{SP}/world2/world2.sqlite"
shutil.copyfile(SRC, OUT)
c = sqlite3.connect(OUT)
c.execute("ATTACH ? AS img", (f"file:{IMG}?mode=ro",)) if False else c.execute(f"ATTACH '{IMG}' AS img")
for t in ("claims", "lines", "adjustments", "plb", "transactions", "remarks", "findings"):
    a = c.execute(f"select count(*) from main.{t}").fetchone()[0]; b = c.execute(f"select count(*) from img.{t}").fetchone()[0]
    assert a == b, (t, a, b)
for t in ("claims_837", "lines_837", "files", "dropbox_seen"):
    cm = [r[1] for r in c.execute(f"pragma main.table_info({t})")]
    ci = [r[1] for r in c.execute(f"pragma img.table_info({t})")]
    assert cm == ci, (t, cm, ci)
    c.execute(f"DELETE FROM main.{t}")
    c.execute(f"INSERT INTO main.{t} SELECT * FROM img.{t}")
    print(t, c.execute(f"select count(*) from main.{t}").fetchone()[0])
c.commit()
c.execute("DETACH img")
print(c.execute("pragma quick_check").fetchone())
c.close()
