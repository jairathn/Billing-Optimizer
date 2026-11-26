# Private-world tools (copied from the 2026-11-24/25 session's scratch folder)

These build and run private copies of the real store. Everything they touch is patient data: run them only in a private
scratch folder (mode 700), with `python3 -I -B`, print aggregates only, never copy their outputs into the repository.
Each script still names the old session's scratch folder in a constant near its top; point it at your own.

- `world_ingest.py`: reads EMA's four reports (A/R and Production, 2025 and 2026) into a copy of the store image with the
  branch's own readers, then refreshes the posting ledger. The result is the "world" (store + `world_ema_ledger.sqlite`).
  The reports are in Dropbox under `/Trizetto Data/Trizetto/AR and Collections Reports from EMA`; the store image is
  `/RemitWatch-state/store.sqlite.gz` (download with the Dropbox connector's single-use link).
- `build_world2.py`: replaces a world's 837 tables with those of a newer Vercel image (the 835 side must be identical).
- `build_img_ema.py <tree>`: Vercel's compact image plus EMA's tables, as the merged code would hold them on Vercel.
- `cycle_run.py`: one server cycle on a world copy (`F5_SRC`=code tree, `P2_DIR`=folder, `P2_DB`=store file name;
  argument: the day, e.g. 2026-11-24): ledger refresh, payer answer times, detection, activity, the refresh after.
- `snap.py <store> <snapshot>` and `three_cycles.sh`: snapshot the findings and compare consecutive cycles item by item
  (release gate 4: a second cycle with no new data changes nothing).
- `p2_serverless.py <tree> <uncompressed image> <day>`: loads an image as Vercel does and runs detection, printing memory.
- `trace_lib.py`, `trace16.py`: private hand-tracing helpers (keys, codes, dollars, days relative to the service).

The auditor that scores a run is `../claim_audit_a16.py` with the scripts in `../a16/` (see `../README.md`).
