"""
Payer routes, the reviews of 2026-11-28 (routes-r1 and routes-r2): each finding's failing test, ported from the reviewers'
folders, with the behaviour the fixes chose where a reviewer's test pinned the old design.

- r1-1: Original Medicare turns a reopening down: the next request is a redetermination of the first decision, on that
  decision's clock, never a QIC reconsideration.
- r1-2: "I asked the payer" is a line in the item's history that changes no status: a claim no payer answered stays to do by
  its first-filing limit, and a "no" after a status call is no turned-down appeal.
- r1-3: HealthSpring's appeal (65 days) takes medical necessity, authorization and referral denials; its claim
  reconsideration (180 days) the payment disputes; participating providers get one level of appeal.
- r1-4: Start the fax only for a first-level appeal whose published number is the confirmed appeal number the app's send
  uses; every other fax is sent by hand after checking the number.
- r1-5: the Also fold and the alternatives say whether their way was confirmed, with the fax words and the limit.
- r1-6: the table keeps no fact the verifier contradicted, in its gaps and in the rows the unit added.
- r2-1 to r2-15: the values the card copies, the order of its ways, its buttons, a payer the table does not cover.

Every row is invented (the builders of tests/test_states.py and tests/test_routes.py).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

import pytest

from remitwatch import actions, cloud, routes, story
from tests import ema_fixtures as F
from tests.test_routes import (MEMBER, NAME, PAGE_PW, REPO, TABLE, _browser_ready, _env, _site, card, every_text, store,  # noqa: F401
                               world)
from tests.test_states import TODAY, event, item, line_key, ln, remit

FEATURES = os.path.join(REPO, "remitwatch", "static", "features.js")
RENDER = os.path.join(REPO, "tests", "routes_card_render.js")
PRESS = os.path.join(REPO, "tests", "routes_card_press.js")
XHR = {"X-Requested-With": "remitwatch"}


def _digits(s) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _all_fields(c) -> list[dict]:
    return [x for v in [c["best"]] + c["others"] for x in v["fields"] + v["facts"]]


def _html(c, tmp_path, buttons=(), item_fields=None) -> str:
    """The card as the drawer draws it (features.js xpRouteHtml, tests/routes_card_render.js)."""
    p = tmp_path / "card.json"
    p.write_text(json.dumps(dict(c, _buttons=list(buttons), _item=item_fields or {})), encoding="utf-8")
    return subprocess.run(["node", RENDER, FEATURES, str(p)], capture_output=True, text=True, check=True, timeout=60).stdout


def _main_button(html: str) -> str:
    m = re.search(r'<div class="actions rc-actions">(.*?)</div>', html, re.S)
    assert m, "the card has no row of actions"
    first = re.search(r"<button[^>]*>(.*?)</button>", m.group(1), re.S)
    return first.group(1).strip() if first else ""


needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node is needed to draw the drawer's card")
needs_browser = pytest.mark.skipif(not _browser_ready(), reason="needs node and Playwright for node")


# ================================================================================================ r1-1: a reopening turned down
def _medicare_rate(store):
    world(store, payer="KY MEDICARE PART B", payer_group="Medicare KY (CGS J15)", payer_kind="medicare", cfi="MB",
          reason=("CO", "45"), signal="UNDERPAID_VS_OWN_RATE", category="underpaid", actionable="appeal", carc="45", status="open")


@pytest.mark.parametrize("note", ["Sent by Portal: myCGS Reopenings form",                       # the card's note before the fix
                                  "Sent a reopening by Portal: myCGS Reopenings form. Reference REF-TEST-3"])
def test_r1_1_medicare_reopening_turned_down_goes_to_a_redetermination_not_the_qic(store, note):
    """routes-r1-1. A reopening's decision cannot be appealed (42 CFR 405.980(a)(5)) and the QIC takes a reconsideration
    only of a redetermination (405.960): after CGS turns a reopening down, the first decision is appealed by
    redetermination, whose 120 days (125 with the receipt presumption) count from that decision's remittance."""
    _medicare_rate(store)
    first, ex = card(store)
    assert ex["next_step"]["route"] == "rate" and first["request"] == "medicare_reopening"
    assert first["sent_words"] == "a reopening"
    event(store, "F1", "manual", at="2026-04-01T10:00:00", frm="open", to="appealed", note=note)
    event(store, "F1", "manual", at="2026-04-20T10:00:00", frm="appealed", to="in_progress", note="The payer said no: no change")
    store.conn.execute("UPDATE findings SET status='in_progress' WHERE finding_id='F1'")
    store.conn.commit()
    after, _ = card(store)
    assert after["request"] == "medicare_redetermination", (after["request"], after["best"]["name"])
    assert "C2C" not in after["best"]["name"] and "myCGS" in after["best"]["name"]
    # the remittance of Mar 28, 2026 opened the window: 120 days from its receipt, presumed 5 days after its date
    assert after["deadline"] == "2026-07-31", after["deadline"]
    assert "Medicare turned the reopening down on Apr 20, 2026" in after["deadline_note"]


def test_r1_1_a_redetermination_turned_down_still_goes_to_the_qic(store):
    """The control: what was turned down was the redetermination itself (an appeal), so the next level is the QIC."""
    world(store, payer="KY MEDICARE PART B", payer_group="Medicare KY (CGS J15)", payer_kind="medicare", cfi="MB", status="open")
    c0, _ = card(store)
    assert c0["request"] == "medicare_redetermination" and c0["sent_words"] == "a redetermination"
    event(store, "F1", "manual", at="2026-04-01T10:00:00", frm="open", to="appealed",
          note="Sent a redetermination by Portal: myCGS Redetermination form")
    event(store, "F1", "manual", at="2026-04-20T10:00:00", frm="appealed", to="in_progress", note="The payer said no: upheld")
    store.conn.execute("UPDATE findings SET status='in_progress' WHERE finding_id='F1'")
    store.conn.commit()
    c1, _ = card(store)
    assert c1["request"] == "second_level_appeal" and "C2C" in c1["best"]["name"]


# ================================================================================================ r1-2: I asked the payer
def _unanswered(store, due="2026-05-20"):
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial", signal="NO_ANSWER",
          category="no_answer", carc=None, actionable="follow_up", appeal_deadline=due, status="open")


@pytest.mark.parametrize("asked", ["sent", "event"])
def test_r1_2_a_claim_status_call_the_payer_answered_is_no_first_appeal(store, asked):
    """routes-r1-2. A "no" after a status call (the old card's Sent 'Asked the payer by ...', or the card's history line
    now) is no turned-down appeal: the next request is still the claim's, never the payer's second level."""
    _unanswered(store, due="2026-12-20")
    c0, ex = card(store)
    assert ex["next_step"]["route"] == "call" and c0["request"] == "claim_status_or_reprocess"
    if asked == "sent":
        event(store, "F1", "manual", at="2026-04-01T10:00:00", frm="open", to="appealed",
              note="Asked the payer by Clearinghouse: EDI 276/277 Claim Status Inquiry and Response")
        frm = "appealed"
    else:
        event(store, "F1", "asked", at="2026-04-01T10:00:00", frm="open", to="open",
              note="Asked the payer: Clearinghouse: EDI 276/277 Claim Status Inquiry and Response")
        frm = "open"
    event(store, "F1", "manual", at="2026-04-08T10:00:00", frm=frm, to="in_progress", note="The payer said no: no claim on file")
    store.conn.execute("UPDATE findings SET status='in_progress' WHERE finding_id='F1'")
    store.conn.commit()
    c1, ex1 = card(store)
    assert ex1["next_step"]["route"] == "call"
    assert c1["request"] == "claim_status_or_reprocess", (c1["request"], c1["best"]["name"])



@pytest.mark.parametrize("note", ["Call or portal check",                                   # The payer is working on it
                                  "Asked the payer by Phone: provider services"])            # the card's note before the fix
def test_r1_2_a_status_call_on_a_denial_is_no_level_of_appeal(store, note):
    """A denial whose person only asked the payer (a call or a portal check, marked Sent) and heard no: the call was no
    level of appeal, so the first-level appeal is still the request, never the payer's second level."""
    world(store, payer="AETNA", payer_group="Aetna", payer_kind="commercial", status="open")
    c0, _ = card(store)
    assert c0["request"] == "reconsideration_or_first_appeal"
    event(store, "F1", "manual", at="2026-04-01T10:00:00", frm="open", to="appealed", note=note)
    event(store, "F1", "manual", at="2026-04-08T10:00:00", frm="appealed", to="in_progress", note="The payer said no: denial stands")
    store.conn.execute("UPDATE findings SET status='in_progress' WHERE finding_id='F1'")
    store.conn.commit()
    c1, _ = card(store)
    assert c1["request"] == "reconsideration_or_first_appeal", (c1["request"], (c1.get("best") or {}).get("name"))

@pytest.mark.parametrize("mode", ["server", "serverless"])
def test_r1_2_i_asked_the_payer_is_a_line_in_the_history_that_parks_nothing(store, tmp_path, monkeypatch, mode):
    """routes-r1-2 (the state). What the card's "I asked the payer" posts (the item's event API, 'asked') changes no status:
    the claim no payer answered stays to do, by its first-filing limit, on the server's store; on Vercel the line waits in
    the journal like every other action. Before the fix the card posted the Sent, and the claim waited past the limit."""
    from remitwatch.state import LocalState
    _unanswered(store)
    store.conn.commit()
    client = _site(str(tmp_path / "store.sqlite"), tmp_path, monkeypatch, mode)
    before = client.get("/api/findings/F1/explain").json()
    rc = before["route_card"]
    assert rc["request"] == "claim_status_or_reprocess" and rc["deadline"] == "2026-05-20" and rc["sent_words"] == ""
    r = client.post("/api/findings/F1/event", headers=XHR,
                    json={"event": "asked", "note": "Clearinghouse: EDI 276/277 Claim Status Inquiry and Response. Reference REF-TEST-4"})
    assert r.status_code == 200, r.text
    if mode == "serverless":
        acts = [a for _n, a in cloud.read_actions(LocalState(str(tmp_path / f"state-{mode}")))]
        assert [(a["kind"], a["event"]) for a in acts] == [("event", "asked")], acts
        assert "Asked the payer: Clearinghouse" in acts[0]["note"]
        return
    after = client.get("/api/findings/F1/explain").json()
    st, was = after["explain"]["state"], before["explain"]["state"]
    # the state is what it was (to do, or can't be checked where no source reaches the answer's day): never waiting
    assert st["work_state"] == was["work_state"] != "waiting", (st["work_state"], st.get("rule"), st.get("follow_up_on"))
    assert st["rule"] == was["rule"] and not st.get("follow_up_on")
    assert after["route_card"]["request"] == "claim_status_or_reprocess" and after["route_card"]["deadline"] == "2026-05-20"
    conn = sqlite3.connect(str(tmp_path / "server-server" / "store.sqlite"))
    try:
        assert conn.execute("SELECT status FROM findings WHERE finding_id='F1'").fetchone()[0] == "open"
        kinds = conn.execute("SELECT kind, note FROM finding_events WHERE finding_id='F1'").fetchall()
    finally:
        conn.close()
    assert kinds == [("asked", "Asked the payer: Clearinghouse: EDI 276/277 Claim Status Inquiry and Response. Reference REF-TEST-4")]


def test_r1_2_the_api_takes_no_unknown_line_and_the_history_names_this_one():
    import remitwatch.api as api
    assert api.EVENT_KINDS["asked"] == "Asked the payer"
    src = open(FEATURES, encoding="utf-8").read()
    assert "asked:'Asked the payer'" in src


# ================================================================================================ r1-3: HealthSpring
def test_r1_3_healthspring_medical_necessity_denial_takes_the_65_day_appeal_not_the_180_day_reconsideration(store):
    """routes-r1-3. HealthSpring's 2026 participating provider manual (the rows' own source): an appeal is for a decision on
    medical necessity, clinical guidelines or prior authorization and referral requirements, within 65 days of the original
    decision, to HealthSpring Appeals (PO Box 650065 or fax 855-699-8985); a claim reconsideration (180 days from the claim
    payment date) is for payment matters."""
    world(store, payer="CIGNA", payer_group="Cigna Medicare Advantage", payer_kind="medicare_advantage", cfi="16",
          reason=("CO", "50"), signal="DENIED_MEDICAL_NECESSITY", carc="50")
    c, ex = card(store)
    assert ex["next_step"]["route"] == "appeal" and c["group"] == "HealthSpring" and c["variant"] == "clinical"
    assert "65 days" in c["time_limit"], c["time_limit"]
    where = " ".join([c["best"]["where"]] + [o["where"] for o in c["others"]])
    assert "650065" in where and "855-699-8985" in where, where
    assert "615-401-4642" not in where and "PO Box 1004" not in where


def test_r1_3_healthspring_rows_keep_the_sources_facts():
    """routes-r1-3 (the rows): the reconsideration's 180 days and its Provider Payment Disputes line, the appeal's 65 days,
    and no second level: participating providers get one level of appeal."""
    rows = {r["key"]: r for r in TABLE["routes"] if r["group"] == "HealthSpring"}
    n3, n4 = rows["n3"], rows["n4"]
    assert n3["request"] == n4["request"] == "reconsideration_or_first_appeal"
    assert n3["variant"] == "main" and n3["time_limit"] == "180 days from the claim payment date."
    assert any("Attn: Provider Payment Disputes PO Box 1004" in ch["where"] for ch in n3["channels"])
    assert n4["variant"] == "clinical" and n4["time_limit"].startswith("65 days from the original decision")
    assert not any(r["request"] == "second_level_appeal" for r in rows.values())
    assert "one level of appeal" in TABLE["single_level"]["HealthSpring"]["text"]


@needs_node
def test_r1_3_a_healthspring_second_level_says_there_is_none(store, tmp_path):
    """After HealthSpring upholds a participating provider's appeal, the card says there is no second level to file, from
    the manual, and offers nothing to send."""
    world(store, payer="CIGNA", payer_group="Cigna Medicare Advantage", payer_kind="medicare_advantage", cfi="16", status="in_progress")
    event(store, "F1", "manual", at="2026-04-01T10:00:00", frm="open", to="appealed",
          note="Sent a first-level appeal by Fax: HealthSpring Appeals, participating provider post-service appeal")
    event(store, "F1", "manual", at="2026-04-20T10:00:00", frm="appealed", to="in_progress", note="The payer said no: upheld")
    c, _ = card(store)
    assert c["request"] == "second_level_appeal" and c["single_level"] and "best" not in c
    assert c["fallback"] == TABLE["single_level"]["HealthSpring"]["text"] and c["deadline"] is None
    html = _html(c, tmp_path)
    assert "one level of appeal" in html and 'id="rcSent"' not in html and "HealthSpring provider manual 2026" in html


# ================================================================================================ r1-4: the fax
def _fax_numbers(store, group, **numbers):
    actions.ensure_fax_tables(store.conn)
    for purpose, number in numbers.items():
        store.conn.execute("INSERT INTO payer_fax (payer_group, purpose, number, confirmed_by, confirmed_at, auto_ok) VALUES (?,?,?,?,?,1)",
                           (group, purpose, number, "Owner", "2026-04-01"))
    store.conn.commit()


@needs_node
def test_r1_4_the_card_never_names_a_number_its_send_does_not_use(store, tmp_path, monkeypatch):
    """routes-r1-4. The app's one send (api.appeals_send) faxes a first-level appeal to the confirmed appeal number
    (api._payer_number). A second level whose easiest way is a fax (UMWA's, 800-382-7792) is no request that send carries:
    the card names the number to fax by hand and offers no Start the fax, whatever numbers are on file. Where it does offer
    the send (a first-level appeal), the number it names is the one the send uses."""
    import remitwatch.api as api
    world(store, payer="UMWA HEALTH AND RETIREMENT FUNDS", payer_group="UMWA Health & Retirement Funds", payer_kind="commercial",
          status="in_progress")
    event(store, "F1", "manual", at="2026-04-01T10:00:00", frm="open", to="appealed", note="Sent a first-level appeal by Portal: UMWA")
    event(store, "F1", "manual", at="2026-04-20T10:00:00", frm="appealed", to="in_progress", note="The payer said no: upheld")
    _fax_numbers(store, "UMWA Health & Retirement Funds", appeal="8005550123", level_2="8003827792")
    c, ex = card(store)
    fx = c["best"]["fax"]
    assert c["request"] == "second_level_appeal" and c["best"]["channel"] == "fax" and fx["published"] == "800-382-7792"
    assert fx["by_hand"] and not fx["sendable"] and "Faxes go to" not in fx["words"]
    assert "(800) 382-7792" in fx["words"] and "first-level appeals only" in fx["words"]
    html = _html(c, tmp_path, buttons=["fax_payer"] + list(ex["next_step"]["buttons"]))
    assert _main_button(html) == "Copy the fax number", _main_button(html)
    # a first-level appeal whose published number is the confirmed appeal number: the send's number, and the card's
    store.conn.execute("DELETE FROM finding_events")
    world(store, payer="OPTUM UNITEDHEALTH CARE", payer_group="VA Community Care", payer_kind="va", payer_id="VACCN")
    _fax_numbers(store, "VA Community Care", appeal="8776666597")
    _site(str(tmp_path / "store.sqlite"), tmp_path, monkeypatch, "server")
    c2, _ = card(store)
    fx2 = c2["best"]["fax"]
    assert c2["request"] == "reconsideration_or_first_appeal" and fx2["sendable"]
    store.conn.commit()
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "store.sqlite"))
    assert _digits(fx2["on_file"]) == api._payer_number("VA Community Care") == _digits(fx2["published"])
    assert _main_button(_html(c2, tmp_path, buttons=["fax_payer"])) == "Start the fax"



def test_r1_4_a_second_level_never_names_the_first_levels_number(store):
    """With only the first level's appeal number on file (fax.number_for falls back to it for level 2), the card names no
    second-level number on file: it says to fax the published number by hand, after confirming it."""
    world(store, payer="UMWA HEALTH AND RETIREMENT FUNDS", payer_group="UMWA Health & Retirement Funds", payer_kind="commercial",
          status="in_progress")
    event(store, "F1", "manual", at="2026-04-01T10:00:00", frm="open", to="appealed", note="Sent a first-level appeal by Portal: UMWA")
    event(store, "F1", "manual", at="2026-04-20T10:00:00", frm="appealed", to="in_progress", note="The payer said no: upheld")
    _fax_numbers(store, "UMWA Health & Retirement Funds", appeal="8005550123")
    c, _ = card(store)
    fx = c["best"]["fax"]
    assert c["request"] == "second_level_appeal" and fx["by_hand"] and not fx["sendable"] and fx["on_file"] == ""
    assert "555-0123" not in fx["words"] and "800-382-7792" in fx["words"] and "Confirm it before the first fax" in fx["words"]

@needs_node
@pytest.mark.parametrize("case", ["tricare_coding_edit", "healthspring_clinical"])
def test_r1_4_start_the_fax_waits_for_the_requests_own_confirmed_number(store, tmp_path, case):
    """routes-r1-4 (second case). The appeal number on file was confirmed for the payer's other first-level request
    (TRICARE's allowable charge review, HealthSpring's payment reconsideration), and this request's published number is
    another (TRICARE's claims auditing reconsideration, HealthSpring's clinical appeal): no Start the fax until a person
    confirms the number for it."""
    if case == "tricare_coding_edit":
        world(store, payer="TRICARE EAST", payer_group="Tricare", payer_kind="tricare", cfi="CH", reason=("CO", "97"),
              signal="EM_BUNDLED_NO_MODIFIER", category="bundling", carc="97", actionable="appeal")
        group, other, published = "Tricare", "8774890011", "877-489-0042"
    else:
        world(store, payer="CIGNA", payer_group="Cigna Medicare Advantage", payer_kind="medicare_advantage", cfi="16")
        group, other, published = "Cigna Medicare Advantage", "6154014642", "855-699-8985"
    _fax_numbers(store, group, appeal=other)
    c, _ = card(store)
    fx = c["best"]["fax"]
    assert c["request"] == "reconsideration_or_first_appeal" and c["best"]["channel"] == "fax" and fx["published"] == published
    assert fx["confirmed"] and not fx["same"] and not fx["sendable"] and "Check which is right" in fx["words"]
    assert _main_button(_html(c, tmp_path, buttons=["fax_payer"])) == "Confirm the number first"


# ================================================================================================ r1-5: the Also fold
def _also_block(html: str) -> str:
    m = re.search(r'<details class="dfold rc-also">.*?</details>', html, re.S)
    assert m, "the card shows no 'Also' fold"
    return m.group(0)


@needs_node
@pytest.mark.parametrize("payer,group", [("CIGNA", "Cigna"), ("UHC SHARED SERVICES", "United Healthcare Shared Services")])
def test_r1_5_the_also_fold_marks_an_unverified_way_not_confirmed(store, tmp_path, payer, group):
    """routes-r1-5. The proof of timely filing beside a status request near the first-filing limit can rest on a way the
    verifier could not confirm (Cigna's o14, UHSS's u34): the fold says so, with why, and the limit."""
    world(store, payer=payer, payer_group=group, payer_kind="commercial", signal="NO_ANSWER", category="no_answer", carc=None,
          actionable="follow_up", appeal_deadline="2026-05-20")
    c, _ = card(store)
    assert c["request"] == "claim_status_or_reprocess" and c.get("also"), c.get("request")
    assert c["also"]["best"]["verified"] is False and c["also"]["best"]["unverified_why"]
    also = _also_block(_html(c, tmp_path))
    assert "not confirmed" in also and "Limit: " in also, also


@needs_node
def test_r1_5_a_fax_number_in_the_also_fold_carries_the_confirm_first_words(store, tmp_path):
    """routes-r1-5 (fax). UHSS's proof of timely filing goes by fax (u34, 866-427-7703), a request the app's send does not
    carry: wherever the card prints the number, it says to confirm it before the first fax."""
    world(store, payer="UHC SHARED SERVICES", payer_group="United Healthcare Shared Services", payer_kind="commercial",
          signal="NO_ANSWER", category="no_answer", carc=None, actionable="follow_up", appeal_deadline="2026-05-20")
    c, _ = card(store)
    best = c["also"]["best"]
    assert best["channel"] == "fax" and best["fax"]["by_hand"] and "Confirm it before the first fax" in best["fax"]["words"]
    also = _also_block(_html(c, tmp_path))
    assert "866-427-7703" in also and "Confirm it before the first fax" in also, also


@needs_node
def test_r1_5_the_alternatives_say_whether_their_way_was_confirmed(store, tmp_path):
    """routes-r1-5 (the 'Another plan type?' list): an alternative whose easiest way the research rated low (Anthem's
    Federal Employee Program row) carries the chip, and the data says why."""
    world(store, payer="BLUE CROSS TEST", payer_group="Anthem / BCBS", payer_kind="commercial")
    c, _ = card(store)
    fep = next(a for a in c["alternatives"] if "Federal Employee Program" in a["when"])
    assert fep["verified"] is False and fep["unverified_why"]
    m = re.search(r"<summary>Another plan type\?</summary>(.*?)</details>", _html(c, tmp_path), re.S)
    line = next(li for li in re.findall(r"<li>(.*?)</li>", m.group(1), re.S) if "Federal Employee Program" in li)
    assert "not confirmed" in line, line


@needs_node
def test_control_the_renderer_prints_the_chip_on_the_cards_main_line(store, tmp_path):
    """A control: the renderer prints 'not confirmed' for an unverified easiest way on the card's main line."""
    world(store, payer="CIGNA", payer_group="Cigna", payer_kind="commercial", signal="DENIED_TIMELY_FILING", reason=("CO", "29"), carc="29")
    c, _ = card(store)
    assert c["request"] == "timely_filing_proof" and c["best"]["verified"] is False
    assert "not confirmed" in _html(c, tmp_path).split('<div class="small muted">', 1)[0]


# ================================================================================================ r1-6: the table
def test_r1_6_the_gaps_keep_no_fact_the_verifier_contradicted():
    """routes-r1-6. The verifier contradicted 'no Indiana quick contact guide was found' (Anthem's in_contact_0002.pdf) and
    'no Humana page ... states the EDI corrected-claim format' (Humana's tip sheet states CLM05-3 = 7)."""
    gaps = " ".join(TABLE["gaps"])
    assert "no Indiana quick contact guide was found" not in gaps
    assert "no Humana page for these lines states the EDI corrected-claim format" not in gaps
    assert "Humana's Claim Disputes and Corrected Claims Tip Sheet (August 2026) states CLM05-3 = 7" in gaps
    assert {c["route"] for c in TABLE["corrections"]} >= {"gaps"}


def test_r1_6_the_wps_waiver_gap_matches_the_corrected_route():
    m15 = next(r for r in TABLE["routes"] if r["key"] == "m15")
    assert m15["channels"][0]["verified"] and "PO Box 8940" in m15["channels"][0]["where"]
    assert not any("Claims Manager mailing address were not retrieved" in g for g in TABLE["gaps"])
    assert not any("search-index snippet" in g and "WPS timely filing waiver" in g for g in TABLE["gaps"])


def test_r1_6_the_corrected_humana_ma_row_cites_humanas_own_tip_sheet_first():
    a23 = next(r for r in TABLE["routes"] if r["key"] == "a23")
    line, _ = routes._source_line(a23["channels"][0]["sources"], TABLE["checked"])
    assert "CarePlus" not in line and line.startswith("From Humana, Claim Disputes and Corrected Claims Tip Sheet"), line
    assert "REF F8) comes only from a CarePlus" in a23["channels"][0]["notes"]


def test_r1_6_the_new_anthem_clinical_row_does_not_repeat_the_contradicted_phone_fact():
    n1 = next(r for r in TABLE["routes"] if r["key"] == "n1")
    phone = next(c for c in n1["channels"] if c["channel"] == "phone")
    assert "individual" in n1["scope"] and phone["where"] == "800-345-4344"
    assert "800-676-2583" in phone["notes"], phone["notes"]


# ================================================================================================ r2: the values
def _downcode(store):
    """Anthem paid 99214 as 99213 (the 835's SVC06 names the code billed): the dispute is about 99214."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", payer="BLUE CROSS TEST",
               claims=[{"pcn": F.claim_id(1), "pccn": "ICN-TEST-1", "received": "2026-03-05",
                        "lines": [ln("99213", 235, 33.90, [("CO", "45", 171.10), ("PR", "3", 30)], allowed=63.90, lcn="1",
                                     ocode="99214", rmk=("N22",))]}])
    item(store, signal="DOWNCODE_SVC06", category="downcoding", severity="high", actionable="appeal", carc=None, rarc="N22",
         procedure_code="99213", billed_code="99214", original_procedure_code="99214", code_category="em",
         code_pair="99214→99213", level_drop=1, charge_cents=23500, allowed_cents=6390, paid_cents=3390, expected_cents=9088,
         shortfall_cents=2698, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)


def test_r2_1_a_downcode_card_copies_the_code_billed_not_the_payers_lower_code(store):
    """routes-r2-1. Every code field and Copy all carry the code billed (99214); the payer's code is its own fact."""
    _downcode(store)
    c, ex = card(store)
    assert ex["next_step"]["route"] == "appeal" and "99214 as billed" in ex["next_step"]["action"]
    codes_ = [x["value"] for x in _all_fields(c) if "codes" in (x.get("keys") or []) and x.get("value")]
    assert codes_ and all(v.startswith("99214") for v in codes_), codes_
    lines = c["copy_all"].split("\n")
    assert "Code and modifiers: 99214" in lines and "Code the payer paid: 99213" in lines, c["copy_all"]


def test_r2_1_a_code_the_charge_only_implies_is_never_copied_as_billed(store):
    """A higher code inferred from the charge alone (no SVC06, no claim file or EMA row) is no code billed: the card copies
    the code on the remittance."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", payer="BLUE CROSS TEST",
               claims=[{"pcn": F.claim_id(1), "pccn": "ICN-TEST-1", "received": "2026-03-05",
                        "lines": [ln("99213", 235, 63.90, [("CO", "45", 171.10)], allowed=63.90, lcn="1")]}])
    item(store, signal="DOWNCODE_SVC06", category="downcoding", actionable="appeal", carc=None, procedure_code="99213",
         billed_code="99214", original_procedure_code=None, evidence={"charge_implies": "99214"}, charge_cents=23500,
         paid_cents=6390, expected_cents=9088, shortfall_cents=2698, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    vals = routes.values(store.conn, dict(store.conn.execute("SELECT * FROM findings WHERE finding_id='F1'").fetchone(),
                                          evidence={"charge_implies": "99214"}), {}, {}, {"route": "appeal"}, "Anthem / BCBS", False, TODAY)
    assert vals["codes"] == "99213" and vals["paid_code"] == ""


@pytest.mark.parametrize("mode", ["server", "serverless"])
def test_r2_1_on_both_hosts_the_api_card_copies_the_code_billed(store, tmp_path, monkeypatch, mode):
    _downcode(store)
    store.conn.commit()
    client = _site(str(tmp_path / "store.sqlite"), tmp_path, monkeypatch, mode)
    rc = client.get("/api/findings/F1/explain").json()["route_card"]
    assert rc["request"] == "reconsideration_or_first_appeal"
    assert "Code and modifiers: 99214" in rc["copy_all"].split("\n"), rc["copy_all"]


def _credit(store, *, payer, group, kind="commercial", cents=22548, fid="F1"):
    """A credit balance EMA shows on a claim the payer answered (the A/R's item has no claim key of its own)."""
    item(store, finding_id=fid, signal="CREDIT_BALANCE", category="refund", actionable="review", carc=None, payer_name=payer,
         payer_group=group, payer_kind=kind, expected_cents=cents, shortfall_cents=cents, paid_cents=None, appeal_deadline=None,
         deadline_basis=None, evidence={"credit_cents": cents, "overpaid_by": group})


@pytest.mark.parametrize("payer,group", [("CIGNA", "Cigna"), ("BLUE CROSS TEST", "Anthem / BCBS"), ("AETNA", "Aetna"),
                                         ("HUMANA", "Humana Medicare Advantage")])
def test_r2_2_a_refund_card_names_the_amount_to_send_back(store, payer, group):
    """routes-r2-2. The amount the next step says is owed back is on every refund card and in Copy all, and a refund way's
    bare 'Amount' field holds it."""
    kind = "medicare_advantage" if "Medicare Advantage" in group else "commercial"
    _credit(store, payer=payer, group=group, kind=kind)
    c, ex = card(store)
    assert ex["next_step"]["route"] == "refund" and ex["next_step"]["action"].startswith("Overpaid by $225.48")
    assert c["request"] == "overpayment_refund"
    assert "$225.48" in c["copy_all"], c["copy_all"]
    assert not [x for x in c["best"]["fields"] if x["label"].strip().lower() == "amount" and not x["value"]], c["best"]["fields"]


def _cgs_refund(store, *, payer_of_claim="KY MEDICARE PART B", cfi="MB"):
    remit(store, trace=F.check_no(1), paid_on="2026-03-28", payer=payer_of_claim,
          claims=[{"pcn": F.claim_id(1), "pccn": "ICN-TEST-1", "received": "2026-03-05", "cfi": cfi,
                   "lines": [ln("11102", 425, 129.04, [("CO", "45", 295.96)], allowed=129.04, lcn="1")]}])
    _credit(store, payer="KY MEDICARE PART B", group="Medicare KY (CGS J15)", kind="medicare", cents=12904)


def test_r2_3_a_refund_card_fills_the_claims_numbers_the_store_holds(store):
    """routes-r2-3. A credit balance from EMA's A/R has no claim key; the payer's remittance for its EMA claim ID is in the
    store: the card fills the payer's claim number and, with names on only, the patient and member ID."""
    _cgs_refund(store)
    c, ex = card(store, names=True)
    assert c["request"] == "overpayment_refund" and ex["next_step"]["route"] == "refund"
    got = {x["label"]: x["value"] for x in c["best"]["fields"]}
    assert got.get("Claim ICN") == "ICN-TEST-1", got
    assert got.get("Patient name") == f"{NAME}, ALPHA" and got.get("Medicare ID") == MEMBER, got
    assert "ICN-TEST-1" in c["copy_all"]
    off, _ = card(store, names=False)
    assert NAME not in every_text(off) and MEMBER not in every_text(off) and "ICN-TEST-1" in off["copy_all"]


def test_r2_3_another_payers_claim_is_never_borrowed(store):
    """The EMA claim ID's only remittance is another payer's: the refund card names no claim number of that payer's."""
    _cgs_refund(store, payer_of_claim="UNITEDHEALTHCARE", cfi="12")
    c, _ = card(store, names=True)
    assert c["request"] == "overpayment_refund"
    assert "ICN-TEST-1" not in every_text(c) and NAME not in every_text(c)


@pytest.mark.parametrize("mode", ["server", "serverless"])
def test_r2_3_on_both_hosts_the_refund_card_reads_the_claim_by_its_ema_claim_id(store, tmp_path, monkeypatch, mode):
    _cgs_refund(store)
    store.conn.commit()
    client = _site(str(tmp_path / "store.sqlite"), tmp_path, monkeypatch, mode)
    rc = client.get("/api/findings/F1/explain").json()["route_card"]
    assert rc["request"] == "overpayment_refund" and "Claim ICN: ICN-TEST-1" in rc["copy_all"].split("\n"), rc["copy_all"]


def test_r2_4_a_cgs_refund_never_copies_the_date_of_service_as_the_new_value_or_the_msp_amounts(store):
    """routes-r2-4. 'New Value (units, date of service, procedure code)' and 'MSP only: ... dates of service' are no date of
    service: they are left empty, each saying what goes there."""
    _cgs_refund(store)
    c, _ = card(store)
    assert c["request"] == "overpayment_refund" and "Overpayment Recovery Request" in c["best"]["name"]
    seen = 0
    for x in c["best"]["fields"]:
        if x["label"].startswith(("New Value", "MSP only")):
            seen += 1
            assert x["value"] == "" and x["fill"] and "03/02/2026" not in x["fill"], x
    assert seen == 2
    assert "New Value (units, date of service, procedure code): 03/02/2026" not in c["copy_all"]


@pytest.mark.parametrize("npi,tin,want", [("NPI-TEST-1", "", "NPI-TEST-1"), ("", "TIN-TEST-1", "TIN-TEST-1")])
def test_r2_5_an_npi_or_tin_field_takes_whichever_of_the_two_is_set(store, monkeypatch, npi, tin, want):
    """routes-r2-5. 'Billing NPI or TIN' takes the NPI when it is set, else the tax ID."""
    if npi:
        monkeypatch.setenv("PRACTICE_NPI", npi)
    if tin:
        monkeypatch.setenv("PRACTICE_TIN", tin)
    world(store, payer="AETNA", payer_group="Aetna", payer_kind="commercial", signal="NO_ANSWER", category="no_answer", carc=None,
          actionable="follow_up", appeal_deadline="2026-12-20")
    c, _ = card(store)
    assert c["request"] == "claim_status_or_reprocess"
    f = next(x for v in [c["best"]] + c["others"] for x in v["fields"] if x["label"] == "Billing NPI or TIN")
    assert f["value"] == want, f


def test_r2_6_a_payment_dispute_card_carries_the_amount_the_payer_should_have_paid(store):
    """routes-r2-6. A rate item's card says what the payer should have paid and by how much it fell short: in the reason it
    puts to the payer and as facts Copy all carries."""
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial", reason=("CO", "45"),
          signal="UNDERPAID_VS_OWN_RATE", category="underpayment", actionable="appeal", carc=None, expected_cents=9000,
          shortfall_cents=4000, paid_cents=5000, allowed_cents=5000)
    c, ex = card(store)
    assert ex["next_step"]["route"] == "rate" and "$90.00" in ex["next_step"]["action"], ex["next_step"]["action"]
    assert c["request"] == "reconsideration_or_first_appeal"
    lines = c["copy_all"].split("\n")
    assert "Should have paid: $90.00" in lines and "Underpaid by: $40.00" in lines, c["copy_all"]
    reasons = [x["value"] for v in [c["best"]] + c["others"] for x in v["fields"] if "reason" in (x.get("keys") or [])]
    assert reasons and all("$50.00" in r and "$90.00" in r and "$40.00" in r for r in reasons), reasons


def test_r2_7_every_empty_field_says_what_to_fill(store):
    """routes-r2-7. A field the store cannot fill says what goes there, never only 'by hand': on CGS's reopening form, and
    for every label in the table. The line's number on the claim comes from the remittance."""
    world(store, payer="KY MEDICARE PART B", payer_group="Medicare KY (CGS J15)", payer_kind="medicare", cfi="MB", reason=("CO", "4"),
          signal="DENIED_MISSING_MODIFIER", actionable="correct_and_resubmit",
          deadline_basis="a reopening of the clerical error within 1 year of the remittance")
    store.conn.execute("UPDATE lines SET line_seq=1")          # as the 835 reader numbers a claim's lines, from 1
    store.conn.commit()
    c, _ = card(store)
    assert c["request"] == "medicare_reopening"
    vague = [x["label"] for x in c["best"]["fields"] if not x.get("value") and not x.get("set") and x.get("fill") in ("by hand", "")]
    assert not vague, vague
    assert {x["label"]: x["value"] for x in c["best"]["fields"]}["Line (1-50)"] == "1"
    for r in TABLE["routes"]:
        for ch in r["channels"]:
            fields, _steps = routes.fill(ch["fields"], {k: "" for k in routes.HINTS})
            for x in fields:
                assert x.get("set") or (x["fill"] and x["fill"] != "by hand"), (r["key"], x["label"])



def test_r2_7_a_label_no_rule_knows_still_says_what_to_fill():
    """A label a later table adds that no field rule knows is still a field that says what goes there."""
    fields, steps = routes.fill(["Invented label no payer prints"], {})
    assert not steps and fields[0]["value"] == "" and fields[0]["fill"] == routes.OTHER_FILL

@needs_node
def test_r2_8_a_partly_filled_field_says_what_it_still_needs(tmp_path):
    """routes-r2-8. A field filled in part shows what it still needs beside its value."""
    c = {"request": "reconsideration_or_first_appeal", "route": "appeal", "payer": "Invented Payer", "request_label": "Reconsideration",
         "names_shown": False, "copy_all": "x", "others": [], "not_accepted": [], "alternatives": [], "time_limit": "",
         "best": {"channel": "payer_portal", "name": "Invented portal", "line": "Invented portal", "where": "", "verified": True,
                  "action": {"kind": "payer_portal", "label": "Open the portal", "url": "https://invented.example"}, "steps": [],
                  "attachments": [], "facts": [], "sources": [], "source_line": "Checked",
                  "fields": [{"label": "Provider or Facility name, address, phone number, email, and either NPI or TIN",
                              "value": "Invented Clinic · 1 Test Way · 555-0100 · NPI-TEST-1", "keys": ["practice_name"],
                              "fill": "the billing email (set PRACTICE_EMAIL)"}]}}
    html = _html(c, tmp_path)
    assert "also needed: the billing email (set PRACTICE_EMAIL)" in html, html


def test_r2_14_the_reference_recorded_with_i_sent_it_fills_the_next_levels_reference_field(store):
    """routes-r2-14. The portal's submission number typed with I sent it fills the next level's 'reference numbers from
    earlier attempts'."""
    world(store, payer="HUMANA", payer_group="Humana", payer_kind="commercial")
    event(store, "F1", "manual", at="2026-04-01T10:00:00", frm="open", to="appealed",
          note="Sent by Portal: Availity: Claim Status, Dispute Claim, then submit from the Appeals worklist. Reference HUM-REF-1")
    event(store, "F1", "manual", at="2026-04-20T10:00:00", frm="appealed", to="in_progress", note="The payer said no: upheld")
    store.conn.execute("UPDATE findings SET status='in_progress' WHERE finding_id='F1'")
    store.conn.commit()
    c, _ = card(store)
    assert c["request"] == "second_level_appeal", c["request"]
    ref = [x for v in [c["best"]] + c["others"] for x in v["fields"] if "earlier_reference" in (x.get("keys") or [])]
    assert ref and all("HUM-REF-1" in x["value"] for x in ref), ref


def test_r2_15_a_claim_total_field_holds_the_claims_total_not_one_lines_charge(store):
    """routes-r2-15. 'Total billed amount of claim' holds the claim's total ($240.00 on two lines), not the item's line."""
    tk = remit(store, trace=F.check_no(1), paid_on="2026-03-28", payer="UMR",
               claims=[{"pcn": F.claim_id(1), "pccn": "ICN-TEST-1", "received": "2026-03-05",
                        "lines": [ln("99213", 150, 0, [("CO", "50", 150)], lcn="1"),
                                  ln("11102", 90, 60, [("CO", "45", 30)], allowed=60, lcn="2")]}])
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", actionable="appeal", payer_name="UMR", payer_group="UMR",
         payer_kind="commercial", charge_cents=15000, line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    c, _ = card(store)
    assert c["request"] == "reconsideration_or_first_appeal" and c["group"] == "UMR", (c["request"], c.get("group"))
    tot = [x for v in [c["best"]] + c["others"] for x in v["fields"] if "total billed amount of claim" in x["label"].lower()]
    assert tot and all(x["value"] == "$240.00" for x in tot), [(x["label"], x["value"]) for x in tot]


# ================================================================================================ r2: the order of the ways
def _status_inquiry(ch: dict) -> bool:
    return ch["channel"] == "clearinghouse" and any(x in ch["name"] for x in ("276", "277"))


@pytest.mark.parametrize("payer,group", [("CIGNA", "Cigna"), ("BLUE CROSS TEST", "Anthem / BCBS"), ("UNITEDHEALTHCARE", "UnitedHealthcare")])
def test_r2_9_an_eob_to_fetch_is_not_sent_to_a_status_inquiry(store, payer, group):
    """routes-r2-9. A 276/277 returns a status code, never the EOB the next step asks for: the portal comes first."""
    item(store, signal="EMA_DENIED", category="denial", carc="50", actionable="appeal", payer_name=payer, payer_group=group,
         payer_kind="commercial", payment_date="2026-04-02", line_key=None, claim_key=None, transaction_key=None)
    c, ex = card(store)
    assert ex["next_step"]["route"] == "eob_first" and "EOB" in ex["next_step"]["action"], ex["next_step"]["action"]
    assert c["request"] == "claim_status_or_reprocess"
    assert not _status_inquiry(c["best"]) and c["best"]["channel"] == "payer_portal", c["best"]["name"]
    assert any(_status_inquiry(o) for o in c["others"])         # the inquiry stays, among the other ways


def test_r2_10_a_coordination_of_benefits_fix_is_not_sent_to_a_status_inquiry(store):
    """routes-r2-10. A status inquiry can neither update the member's other coverage nor reprocess the claim."""
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial", reason=("CO", "22"),
          signal="DENIED_OTHER", carc="22", actionable="appeal")
    c, ex = card(store)
    assert ex["next_step"]["route"] == "cob", ex["next_step"]["route"]
    assert c["request"] == "claim_status_or_reprocess" and "reprocess" in c["request_why"]
    assert not _status_inquiry(c["best"]), c["best"]["name"]
    assert any(_status_inquiry(o) for o in c["others"])


# ================================================================================================ r2: the buttons
@needs_node
def test_r2_13_a_clearinghouse_way_has_a_main_button(store, tmp_path):
    """routes-r2-13. A corrected claim or status request sent from EMA through the clearinghouse has its main button (it
    opens the item's claim in EMA)."""
    world(store, payer="UNITEDHEALTHCARE", payer_group="UnitedHealthcare", payer_kind="commercial", reason=("CO", "4"),
          signal="DENIED_CODING", actionable="correct_and_resubmit")
    c, ex = card(store)
    assert c["request"] == "corrected_claim" and c["best"]["channel"] == "clearinghouse"
    assert c["best"]["action"]["label"] == "Send it from EMA"
    html = _html(c, tmp_path, buttons=ex["next_step"]["buttons"])
    assert _main_button(html) == "Send it from EMA" and 'data-rc="ema"' in html


def _serve_and_press(db: str, fid: str, mode: str = "main") -> dict:
    """Serve the app over `db` (uvicorn, as the server does; no scheduler) and press the card's buttons
    (tests/routes_card_press.js)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DROPBOX_", "TRIZETTO_", "SRFAX_", "ANTHROPIC_", "SMTP_", "MODMED_",
                                                                     "PRACTICE_"))
           and k not in ("REPORT_TO", "ALERT_WEBHOOK_URL", "REMITWATCH_MODE", "REMITWATCH_OWNER_PASSWORD", "REMITWATCH_SHOW_PHI",
                         "REMITWATCH_ALLOW_OPEN", "REMITWATCH_STATE_DIR", "REMITWATCH_DB", "VERCEL", "VERCEL_ENV", "CRON_SECRET")}
    env.update(REMITWATCH_PASSWORD=PAGE_PW, REMITWATCH_USER="oda", REMITWATCH_SYNC_INTERVAL_MINUTES="0", PYTHONDONTWRITEBYTECODE="1",
               REMITWATCH_DB=db, REMITWATCH_MODE="server")
    srv = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port),
                            "--log-level", "warning"], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        got = subprocess.run(["node", PRESS, str(port), "oda", PAGE_PW, fid, mode], capture_output=True, text=True, timeout=240, cwd=REPO)
        assert got.returncode == 0, got.stderr[-2000:]
        return json.loads(got.stdout.strip().splitlines()[-1])
    finally:
        srv.terminate()
        srv.wait(timeout=10)


def _site_db(store, tmp_path) -> str:
    db = str(tmp_path / "site.sqlite")
    dst = sqlite3.connect(db)
    try:
        store.conn.backup(dst)
    finally:
        dst.close()
    return db


def _today(n: int = 0) -> str:
    return (dt.date.today() + dt.timedelta(days=n)).isoformat()


@needs_browser
def test_r2_11_a_mail_refunds_main_button_prints_a_cover_sheet(store, tmp_path):
    """routes-r2-11. Cigna takes a refund only as a check by mail. Its main button opens a cover sheet to print (where to
    send it, the amount, the claim's numbers), never a letter the item does not have."""
    item(store, signal="CREDIT_BALANCE", category="refund", actionable="review", carc=None, payer_name="CIGNA", payer_group="Cigna",
         payer_kind="commercial", expected_cents=22548, shortfall_cents=22548, paid_cents=None, appeal_deadline=None, deadline_basis=None,
         evidence={"credit_cents": 22548, "overpaid_by": "Cigna"}, service_date=_today(-60), payment_date=_today(-40),
         first_seen=_today(-30))
    store.conn.commit()
    want = routes.card(store.conn, "F1", explain=story.explain(store.conn, "F1", today=_today()), today=_today())
    assert want and want["request"] == "overpayment_refund" and want["best"]["channel"] == "mail"
    seen = _serve_and_press(_site_db(store, tmp_path), "F1")
    assert seen["card"] and seen["main"] == "Print the cover sheet", seen
    assert not seen["errors"] and not seen["err"], seen
    assert any(u.startswith("blob:") for u in seen["popups"]), seen


@needs_browser
def test_r2_12_an_unknown_payers_card_still_copies_the_claims_facts(store, tmp_path):
    """routes-r2-12. A payer the research does not cover: the card shows the claim's own facts, the c key copies them, and
    I sent it records how it went."""
    tk = remit(store, trace="TRACE-TEST-9", paid_on=_today(-20), payer="ACME HEALTH PLAN",
               claims=[{"pcn": "ZZ00T0001C001", "pccn": "ICN-TEST-1", "received": _today(-35),
                        "lines": [ln("99213", 150, 0, [("CO", "50", 150)], svc=_today(-40), lcn="1")]}])
    item(store, signal="DENIED_MEDICAL_NECESSITY", carc="50", actionable="appeal", payer_name="ACME HEALTH PLAN",
         payer_group="Acme Health Plan", payer_kind="commercial", service_date=_today(-40), payment_date=_today(-20),
         first_seen=_today(-19), appeal_deadline=_today(150), line_key=line_key(store, tk), claim_key=f"{tk}-0", transaction_key=tk)
    store.conn.commit()
    c = routes.card(store.conn, "F1", explain=story.explain(store.conn, "F1", today=_today()), today=_today())
    assert c and c["fallback"] == routes.FALLBACK and c["sent_words"] == "a first-level appeal"
    assert "Payer claim number: ICN-TEST-1" in c["copy_all"].split("\n")
    seen = _serve_and_press(_site_db(store, tmp_path), "F1", mode="sent")
    assert seen["card"] and "No checked route" in seen["card_text"], seen
    assert "ICN-TEST-1" in seen["keyed"], seen
    sent = [p for p in seen["posts"] if p["path"].endswith("/status")]
    assert sent and sent[-1]["body"]["status"] == "appealed", seen["posts"]
    assert sent[-1]["body"]["notes"] == "Sent a first-level appeal by Invented payer portal. Reference REF-TEST-2"


@needs_browser
def test_r1_2_i_asked_the_payer_in_a_browser_posts_no_sent(store, tmp_path):
    """routes-r1-2 in a browser: the card's "I asked the payer" posts a line in the item's history ('asked') and no status;
    the item stays to do."""
    item(store, signal="NO_ANSWER", category="no_answer", carc=None, actionable="follow_up", payer_name="UNITEDHEALTHCARE",
         payer_group="UnitedHealthcare", payer_kind="commercial", service_date=_today(-60), payment_date=None, paid_cents=0,
         appeal_deadline=_today(17), deadline_basis="the first-filing limit", status="open", first_seen=_today(-30))
    store.conn.commit()
    db = _site_db(store, tmp_path)
    seen = _serve_and_press(db, "F1", mode="sent")
    assert seen["card"] and "I asked the payer" in seen["card_text"], seen
    paths = [p["path"] for p in seen["posts"]]
    assert paths == ["/api/findings/F1/event"], seen["posts"]
    assert seen["posts"][0]["body"]["event"] == "asked" and seen["posts"][0]["body"]["note"].endswith("Reference REF-TEST-2")
    assert "Noted in its history" in seen["msg"], seen
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT status FROM findings WHERE finding_id='F1'").fetchone()[0] == "open"
    finally:
        conn.close()
