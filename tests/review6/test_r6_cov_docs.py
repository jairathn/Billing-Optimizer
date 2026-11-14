"""The Data tab's list (static/index.html) and docs/MISSING_DATA.md ask for ModMed's all-patient EHI export ("every
claim, submission, ERA link, adjustment and payment for the whole history in one consistent form"). Nothing in the code
reads it: the sync downloads only .rmt/.835/.era/.edi/.xlsx/.txt/.x12/.dat files, and ingest_new_files routes .xlsx to
the EMA reader and X12 to the 835/837 readers and skips everything else, so the pipe-delimited export is skipped. The
appointment export on the same list says "Nothing on this site reads it yet"; the EHI export does not. Invented data only."""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import REPO  # noqa: E402

from remitwatch.dropbox_sync import WANTED_EXT, ingest_new_files  # noqa: E402
from remitwatch.store import Store  # noqa: E402


def test_the_ehi_export_is_asked_for_but_nothing_reads_it(tmp_path):
    ehi = tmp_path / "claim_submission.txt"
    ehi.write_text("claim_id|submission_date|payer_id\nCB0000001C017|2026-09-01|ANTHM\n")
    st = Store(str(tmp_path / "s.sqlite"))
    out = ingest_new_files(st, [str(ehi)], log=lambda *a: None)
    st.close()
    assert (out["skipped"], out["ema"], out["remits"], out["claims_837"]) == (1, 0, 0, 0)
    assert ".csv" not in WANTED_EXT and ".zip" not in WANTED_EXT
    page = open(os.path.join(REPO, "remitwatch", "static", "index.html"), encoding="utf-8").read()
    items = re.findall(r"<li>(.*?)</li>", page, flags=re.S)
    ehi_item = next(i for i in items if "EHI export" in i)
    appointment_item = next(i for i in items if "appointment export" in i)
    assert "Nothing on this site reads it yet" in appointment_item
    doc = open(os.path.join(REPO, "docs", "MISSING_DATA.md"), encoding="utf-8").read()
    doc_item = doc[doc.index("**Once, the all-patient EHI export.**"):doc.index("**The remittance files for the weeks that are missing.**")]
    says = lambda text: re.search(r"nothing on (this|the) site reads", text, flags=re.I) is not None     # noqa: E731
    assert says(appointment_item) and says(doc[doc.index("**The EMA appointment export**"):])
    assert says(ehi_item) and says(doc_item), \
        "the Data tab and MISSING_DATA.md ask for the EHI export without saying that nothing on the site reads it yet"
