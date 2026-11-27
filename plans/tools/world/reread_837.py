"""Read the 837 files again into a private world copy, as the release does on each host's first cycle (F6's reread on
the server and on Vercel): every file the store read before x12_837.READ_VERSION, found by its SHA-256 in a folder of
files named by their SHA-256 (the private fetch of the 837 files from Dropbox).

Patient data: run it on a private copy only (never on the live store), with `python3 -I -B`. It prints counts only.

usage: python3 -I -B reread_837.py <code tree> <store.sqlite> <folder of 837 files named by their sha256>
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[-1])
        return 2
    tree, db, folder = argv[1:4]
    sys.path.insert(0, os.path.abspath(tree))
    from remitwatch import x12_837        # the tree's own reader, so its READ_VERSION decides what is read again
    conn = sqlite3.connect(db)
    todo = x12_837.files_to_reread(conn)
    t0 = time.time()
    done = missing = claims = updated = 0
    for sha in todo:
        path = os.path.join(folder, sha)
        if not os.path.isfile(path):
            missing += 1
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        r = x12_837.reread_837_bytes(conn, data)
        done += 1
        claims += r.get("claims", 0)
        updated += r.get("updated", 0)
    left = len(x12_837.files_to_reread(conn))
    keyed = conn.execute("SELECT COUNT(*) FROM claims_837 WHERE patient_key IS NOT NULL").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM claims_837").fetchone()[0]
    conn.close()
    print(f"reader version {x12_837.READ_VERSION}: {len(todo)} files to read again, {done} read, {missing} not in the folder; "
          f"{claims} claims parsed, {updated} rows updated; {left} files still before this version; "
          f"{keyed} of {total} claims carry a patient key; {time.time() - t0:.0f} s")
    return 0 if missing == 0 and left == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
