"""The failing tests of fx-windows' needs from other fixers (plans/progress/fx-windows.md, "Needs from others"). Invented
data only. Not collected by the suite (this file's name is not test_*.py): each fails on wip/fx-windows until the fixer
named in its docstring makes the change; then move it into that fixer's tests. Run from the repository root:
    python3 -m pytest -q plans/progress/fx-windows_needs_tests.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

KINDS = [False, True]


@pytest.fixture
def ema_env(monkeypatch):
    from remitwatch import ema_checks as ec, ema_reports as er
    from tests import ema_fixtures as F
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES",
              "REMITWATCH_EMA_LEDGER", "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        monkeypatch.delenv(k, raising=False)
    ec._LAST_POST.clear()


@pytest.mark.parametrize("compact", KINDS, ids=["store_file", "compact_image"])
def test_an_ema_denial_from_the_va_network_named_after_its_administrator_is_the_vas(tmp_path, ema_env, compact):
    """F4 need (ema_checks / ema_ledger; or F6 in the two cycles): EMA names the VA's community care network after its
    administrator, and its weekly exports carry the network's payer id (VACCN) on that name's rows. A denial EMA posted
    for such a claim, with no 835 (the network sends none), must be the VA's: group 'VA Community Care', its windows
    (first filing 38 U.S.C. 1703D(b)'s 180 days). Today ema_reports.payer_group_of maps the name to the VA only from the
    map learn_payer_aliases builds, and nothing calls learn_payer_aliases: the item is 'Optum (UHC)', commercial, dated by
    the UnitedHealthcare-administered plans' 12 months. On the real copy: 151 EMA_DENIED items ($71,386) and 32 NOT_SENT
    items ($1,950) of the VA's network carry Optum's group and windows.
    The change: call ema_reports.learn_payer_aliases(conn) once the weekly exports are read and before the EMA checks and
    the ledger's refresh use the payer groups (for example at the start of ema_checks' checks or of ema_ledger.refresh,
    or in pipeline's and cloud's cycles after the exports), so EMA's name-only paths group the network by its payer id."""
    from remitwatch import ema_checks as ec, ema_ledger as el, ema_reports as er
    from remitwatch.store import Store
    from tests import ema_fixtures as F, test_ema_checks as TC
    from tests.test_workable import ema
    st = Store(":memory:", compact=True) if compact else Store(str(tmp_path / "store.sqlite"))
    er.ensure_schema(st.conn)
    st.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (TC.SALT_HEX,))
    st.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        name = F.PAYERS["optum"]
        for i in range(25):                 # the weekly exports' rows of that name carry the network's payer id
            ema(st.conn, f"CB0000V{i:02d}C017", payer=name, payer_id="VACCN", claim_status="Resolved/Paid")
        st.conn.commit()
        TC.denial_world(st, lg, tmp_path, TC.c5_rows(link=2, reason="CO-16", payer=name))
        items = [i for i in ec.ema_denials(st.conn, today=TC.TODAY) if i["claim"]["payer_name"] == name]
        assert len(items) == 1, items
        assert (items[0]["claim"]["payer_group"], items[0]["claim"]["payer_kind"]) == ("VA Community Care", "va"), items[0]["claim"]
    finally:
        lg.close()
        st.close()


@pytest.mark.parametrize("compact", KINDS, ids=["store_file", "compact_image"])
def test_the_ema_summaries_take_a_changed_name_rule_without_a_new_report(tmp_path, ema_env, monkeypatch, compact):
    """F4 need (ema_ledger.refresh): the EMA summaries (ema_lines, and the A/R's stored rows) keep the payer group the name
    rules gave when Part A last ran, and refresh runs Part A only when the posting ledger changed. A release that changes
    the name rules (codes.PAYER_RULES; here fx-windows' reading of EMA's 'Medicaid of Kentucky' as Kentucky's own
    program) re-runs detection (detect.RULE_FILES holds codes.py) but leaves the stored groups as they were until the next
    Production report arrives, so the checks that read them (not_sent, unanswered_claims) date such a claim by the old
    group's windows ('Medicaid (other)': the 90-day default instead of Kentucky's 12 months). On the real copy one
    NOT_SENT item of that name kept 'Medicaid (other)' after the change.
    The change: keep a hash of the name rules in meta (codes.PAYER_RULES, ema_reports' VA rule and alias maps) and, when
    it differs, run Part A (or re-group the stored rows with rw_payer_group) at the next refresh."""
    from remitwatch import codes, ema_ledger as el, ema_reports as er
    from remitwatch.store import Store
    from tests import test_ema_checks as TC
    name = "Medicaid of Kentucky"
    new_rules = list(codes.PAYER_RULES)
    old_rules = [r for r in new_rules if r[1] != "Medicaid KY"]          # the release before: the name reads as 'Medicaid (other)'
    st = Store(":memory:", compact=True) if compact else Store(str(tmp_path / "store.sqlite"))
    er.ensure_schema(st.conn)
    st.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (TC.SALT_HEX,))
    st.conn.commit()
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        monkeypatch.setattr(codes, "PAYER_RULES", old_rules)
        er._GROUPERS.clear()
        TC.load(st, lg, tmp_path, TC.c5_rows(link=2, reason="CO-16", payer=name))
        groups = {g for (g,) in st.conn.execute("SELECT DISTINCT payer_group FROM ema_lines WHERE primary_payer = ?", (name,))}
        assert groups == {"Medicaid (other)"}, groups                    # what the earlier release stored
        monkeypatch.setattr(codes, "PAYER_RULES", new_rules)             # the release with the new rule starts
        er._GROUPERS.clear()
        el.refresh(st, lg, parts="auto", log=TC.Log())
        groups = {g for (g,) in st.conn.execute("SELECT DISTINCT payer_group FROM ema_lines WHERE primary_payer = ?", (name,))}
        assert groups == {"Medicaid KY"}, groups
    finally:
        lg.close()
        st.close()
