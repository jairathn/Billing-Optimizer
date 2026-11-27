"""Private: what the panel says for one item of a few kinds (the block's words: aggregates and public rule text only)."""
import json, sqlite3, sys
tree, world = sys.argv[1], sys.argv[2]
sys.path.insert(0, tree)
from remitwatch import rarely_paid as rp, story
c = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
print("meta bytes:", len(c.execute("SELECT value FROM meta WHERE key=?", (rp.WHY_META,)).fetchone()[0]))
for where in ("signal='CODE_CHANGED' AND original_procedure_code='77600' AND work_state='work_now'",
              "signal='EMA_DENIED' AND payer_group='Optum (UHC)' AND work_state='work_now' AND service_date < '2026-01-01'",
              "payer_group='Medicaid KY' AND procedure_code='99213' AND work_state='work_now' AND category='denial'",
              "signal='DENIED_AUTH' AND payer_group='UnitedHealthcare Medicare Advantage' AND work_state='work_now'"):
    f = c.execute(f"SELECT * FROM findings WHERE COALESCE(stale,0)=0 AND {where} LIMIT 1").fetchone()
    if not f:
        print("none for", where); continue
    f = dict(f)
    x = story.explain(c, f["finding_id"], today="2026-11-24")
    for b in x["why_unpaid"][:1]:
        print("==", b["title"], "|", b["period"], "| verdict:", b["verdict"], "| match:", b["match"])
        print("   ", b["verdict_text"])
        for t in b["evidence_text"]:
            print("   -", t)
        print("    sources:", [s["title"][:60] for s in b["sources"]][:3])
    nx = x["next_step"]
    print("   next step starts:", (nx["action"] or "")[:150].split(" Check what")[0], "| payer_rule:", nx.get("payer_rule"),
          "| buttons:", nx["buttons"])
