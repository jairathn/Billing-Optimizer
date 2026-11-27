"""
Detection engine: turns the raw remittance store into a table of findings.

Each finding is one thing a payer did that cost the practice money (or one
thing the practice did that let a payer keep money), with the evidence needed
to act on it. Findings are idempotent: re-running detection keeps the status
(open / appealed / resolved / dismissed) of findings that already exist.

Signals (category: signal)
--------------------------
downcoding   DOWNCODE_SVC06            payer reported the submitted code (SVC06) and paid a lower level
             DOWNCODE_VS_BILLED        the claim file or EMA shows a higher level was billed than the code on the remit
             DOWNCODE_CHARGE           charge amount is the practice's fee for a higher level than the paid code
             DOWNCODE_LEVEL_REDUCED    CARC 186/150 or a level remark on a paid visit: payment cut for the level, code unchanged
             DOWNCODE_RATE             allowed equals the payer's usual rate for a lower level
             DOWNCODE_LINE_SWAP        billed visit denied and a lower-level visit line paid in its place
             DOWNCODE_LEVEL_DENIED     CARC 150/186 or a level remark on an unpaid visit
             CODE_CHANGED              payer changed the code (SVC06 or remark N22) outside the known level families
bundling     EM_BUNDLED_NO_MODIFIER    E/M paid $0 as bundled and no modifier 25/57/24 was billed (correct and resubmit)
             EM_BUNDLED_MOD57_MINOR    E/M-57 bundled; same-day procedures are 0/10-day global (57 is wrong, use 25)
             EM_BUNDLED_MOD57_MAJOR    E/M-57 bundled; a 90-day global procedure was done the same day (appeal)
             EM_BUNDLED_MOD25          E/M-25 bundled (appeal: significant, separately identifiable)
             EM_BUNDLED_MOD24          E/M-24 denied inside a global period (appeal with documentation)
             ADDON_BUNDLED             add-on code (17312, 11103, 17003...) paid $0 as bundled (payer error)
             PROCEDURE_BUNDLED         a procedure (Mohs stage, flap, repair) bundled into another (appeal with NCCI)
denial       DENIED_MISSING_MODIFIER   CO-16 with RARC N822/N823/N519: correct and resubmit
             DENIED_CODING             CO-4/181/182/M20/M51/N56: code or modifier error, correct and resubmit
             DENIED_MISSING_PRIMARY    CO-B15/107: add-on billed without a payable primary
             DENIED_MISSING_INFO       CO-16/226/227/250/251/252: resubmit with the requested information
             DENIED_AUTH               CO-197/198/15/39/210/243: authorization failure
             DENIED_LEVEL_OF_SERVICE   CO-150/186 on a procedure: payer says the records do not support the service as billed
             DENIED_MEDICAL_NECESSITY  CO-50/B12/B22/167/A1/272: appeal with records
             DENIED_FREQUENCY          CO-151/119/222/273/B14: appeal with records (CO-151 with M86 on a service the bill
                                       already had paid is the payer's duplicate denial: no item)
             DENIED_NONCOVERED         CO-96/204/49/55/200...: verify benefits, bill patient where allowed
             DENIED_TIMELY_FILING      CO-29
             DENIED_PROVIDER           CO-B7/170/171/185/208: enrollment or credentialing failure
             DENIED_DUPLICATE_UNPAID   OA-18/B13 and the same line was never paid on any remit
             DENIED_OTHER              paid $0 with another payer-responsibility CARC
             CLAIM_DENIED_UNPAID       whole claim denied (CLP02 = 4) and never paid later
             (a denial is closed when the service is paid later: on the same bill, on a corrected claim under a new bill
             number or to another payer for the same patient, date and code, or when EMA shows the payment first; and when
             the payer that denied it answers it again itself, as secondary after saying another payer covers it first, or
             onto the patient's deductible, coinsurance or copay)
underpayment UNDERPAID_VS_OWN_RATE     allowed below the payer's own usual rate (rank-aware) for this code and tier, at an
                                       amount the payer does not use routinely (a one-off); the claim's plan's own rate where
                                       an employer plan or product pays a schedule of its own; also the highest-valued
                                       procedure of a day billed on separate claims paid as a second one, by the net owed
             MPPR_EXCESS               multiple-procedure reduction on an add-on code or deeper than policy
             OLD_FEE_SCHEDULE          services dated after the payer's new rates took effect, paid at its previous year's
                                       amounts: one repricing request per payer and year, with its claims
fees         FEE_SKIM                  PLB fees withheld (Zelis transaction fees, origination fees)
             TAKEBACK                  PLB WO overpayment recovery above a threshold
contract     CONTRACT_RATE_GAP         commercial payer's usual rate far below Medicare for a high-volume code
             MA_BELOW_MEDICARE         Medicare Advantage payer's usual rate below the Medicare fee schedule
             MPPR_POLICY               payer routinely pays later procedures below the standard 50 percent
             CHARGE_BELOW_RATE         the practice's charge is below what the payer allows, so the payer paid the charge
             SECOND_RATE               payer routinely allows a lower amount for a code (PA/NP rate, another office's plan,
                                       multiple-procedure rule, a rate change; or no reason shown, on 5 lines or more): one
                                       finding per payer, code, year and amount
             PROVIDER_RATE_GAP         one clinician paid at a level of their own below the payer's rate that no rule explains
                                       (not the PA/NP 85 percent of a PA or NP): one review per payer and clinician
info         G2211_DENIED              per payer and year: G2211 denied most of the time
             MIPS_ADJUSTMENT           per year: net MIPS adjustment (CARC 144)
             SEQUESTRATION             per year: sequestration (CARC 253)

From EMA's A/R and Production reports (remitwatch.ema_checks, wired in the order of the tech lead's amendment 4.5)
denial       NOT_SENT                  a claim that never left EMA (Batched for Printing or Sending, Invalid)
             REJECTED_PRE_PAYER        also from the reports' Rejected status (the same id as the weekly exports' item)
             NO_ANSWER                 also from Production: a claim no payer answered (G8)
             EMA_DENIED                a denial only EMA saw: no remittance on file (C5)
             FORWARDED_TO_CARRIER      CO-B11 (or claim status 19 naming a carrier): the claim went on to another carrier
             SECONDARY_NOT_BILLED      the primary paid and left a balance a secondary should pay; nothing from the secondary
fees         REMIT_NOT_POSTED          an 835 payment EMA never posted, or posted short (C4)
contract     EMA_DENIAL_AS_CONTRACTUAL a payer's denials EMA writes off as contractual (C7; outside the money totals)
refund       CREDIT_BALANCE            insurance paid more than a line was owed: refund or recoupment, the owner decides
Each item also carries its line's outcome in EMA (ema_outcome: paid after, auto written off, written off by policy...),
what EMA wrote off on it (C2), EMA's overlay columns (W2) and its state (work_state, follow_up_on, state_basis: the one
place a state is decided is remitwatch.states).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from array import array
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from .codes import (ADD_ON_CODES, AGREEMENT_FILING_NOTE, DRUG_RE, EM_CODES, EM_LEVEL, GLOBAL_DAYS, MEDICARE_SAME_DAY_EM, MPPR_CODES,
                    SKIN_SUBSTITUTE_RE, appeal_rule, carc_text, code_category, corrected_rule, corrected_window, filing_rule, is_unlisted,
                    legal_deadline, normalize_payer, payer_group_kind, pos_class, rarc_text, rule_at, rule_facts, second_claim_window,
                    window_end)
from . import codes as _codes
from .codes import part_fits
from .fees import FeeTable, family_of, is_downcode
from .store import Store

DETECT_RULES = "2.12"           # 2.12: push 2 (2.10, 2.11: EMA's reports, work states, the payer's own limits) and the
                                #      live hotfixes of 2026-11 (2.9) together. 2.9: the day the remittances reach is the
                                #      feed's, not the newest payment of any payer (feed_date); windows stated in months or
                                #      years are calendar months; Medicare counts from presumed receipt; only overlapping
                                #      copies of one remittance supersede each other
# whether what EMA has posted as paid closes an item (resolve_elsewhere, ema_checks.paid_in_ema), or only adds a hint: the
# billers check EMA, and an item EMA shows paid is not work
EMA_CLOSES = os.environ.get("REMITWATCH_EMA_CLOSES", "1") != "0"
# the files whose rules a run applies: any change re-analyses the archive once (a module not in this release is left out)
RULE_FILES = ("detect.py", "codes.py", "fees.py", "link.py", "pfs.py", "ema_reports.py", "ema_ledger.py", "ema_checks.py",
              "states.py", "story.py", "archive_rules.py", "archive.py", "rarely_paid.py",
              os.path.join("data", "payer_code_rules.json"))


def _rules_hash() -> str:
    """This file and the ones whose rules it applies: payer windows and reason codes (codes.py), CMS's global periods
    and multiple-procedure indicators (pfs.py), fee families (fees.py), how remittance lines are tied to EMA rows
    (link.py), EMA's reports and the checks on them (ema_reports.py, ema_ledger.py, ema_checks.py), the item states
    (states.py), the item story (story.py), the archive-only rules (archive_rules.py) and the researched rules of codes
    a payer almost never pays (rarely_paid.py, data/payer_code_rules.json)."""
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in RULE_FILES:
        path = os.path.join(here, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            h.update(name.encode() + b"\0" + fh.read())
    return h.hexdigest()[:8]


# The version recorded with each run, compared on the next cycle: any change to these files re-analyses the archive once.
DETECT_VERSION = DETECT_RULES + "-" + _rules_hash()
DERIVED_SCHEMA = "3"        # bump when the layout of a derived table changes; the tables are dropped and rebuilt

# B20 ("furnished by another provider", with M86 "already paid for the same or a similar service within a set time") is a
# global-period or same-service edit, worked like a bundled service, not a benefit the plan excludes
BUNDLING_CARCS = {"97", "B10", "236", "234", "231", "M15", "B20"}
LEVEL_CARCS = {"150", "186"}
CODING_CARCS = {"4", "181", "182", "5", "6", "11", "146", "199"}
PRIMARY_CARCS = {"B15", "107"}
INFO_CARCS = {"16", "226", "227", "250", "251", "252", "133"}
AUTH_CARCS = {"197", "198", "15", "39", "210", "243"}
MEDNEC_CARCS = {"50", "B12", "B22", "167", "56", "A1", "B5", "272", "58", "216"}
FREQ_CARCS = {"151", "119", "222", "273", "B14", "35"}
NONCOV_CARCS = {"96", "204", "49", "55", "B8", "200", "26", "27", "31", "109", "24", "256", "246", "112", "B9", "299", "193", "114", "189"}
# CO-B11: the payer sent the claim on to the proper carrier (a crossover, a delegated group). Not a denial of the service:
# its money is expected from that carrier (FORWARDED_TO_CARRIER; remitwatch.states waits 45 days for it)
FORWARD_CARCS = {"B11"}
# reasons a payer gives when the claim is another payer's to pay (coordination of benefits, coverage dates, not its member,
# not its jurisdiction, sent on): another payer that then pays the line in full has paid the right claim
OTHER_PAYER_CARCS = {"22", "109", "B11", "31", "26", "27", "200", "177", "B9"}
# remarks with which a payer asks for the primary payer's answer first (N36: the claim must meet the primary payer's
# requirements; MA04, N4, N479: the primary's payment information or explanation of benefits; MA92, N245: the other
# insurance's plan; N522: a duplicate of a claim processed, or to be processed, as a crossover claim): with
# OTHER_PAYER_CARCS, a payer's answers that another payer covers the claim first. Once that same payer has processed the
# service as secondary, such an answer is settled (trace findings T-C3, T-N3)
COB_RARCS = {"N36", "MA04", "N4", "N479", "MA92", "N245", "N522"}
# the patient's share as a payer puts it. Its other patient-responsibility reasons are not the patient's balance:
# coordination of benefits (PR-22), out of network (242), coverage ended (27), information the patient was asked for (227)
# and not its insured (31) are denials to work (calibration fix 7), and the plan's exclusions wait on the owner
COST_SHARE = {"1": "deductible", "2": "coinsurance", "3": "copay"}
TIMELY_CARCS = {"29"}
PROVIDER_CARCS = {"B7", "170", "171", "185", "208", "242", "54", "147"}
DUP_CARCS = {"18", "B13"}
# CARC 22 (covered by another payer) and 23 (the prior payer's adjudication) on an answer: processed after another payer's
# answer, whatever claim status it reports (fx-ledger-r1-3; states.AFTER_OTHER_CARCS)
AFTER_OTHER_CARCS = frozenset({"22", "23"})
CONTRACT_REMARKS = {"N381"}         # "consult our contractual agreement": a CO-45 the contract explains, not a denial
PAIR_ORDER_MIN, PAIR_ORDER_SHARE = 5, 0.8   # a payer's own ranking of two procedures: claims needed, share ranked first
IGNORE_CARCS = {"45", "253", "144", "23", "94", "187", "59", "131", "237", "A2", "P1", "W1", "3", "2", "1"}
MODIFIER_RARCS = {"N822", "N823", "N519", "M78", "N793"}
CODING_RARCS = {"M20", "M51", "N56", "M53"}
LEVEL_RARCS = {"M25", "N657", "N610", "M85", "N22"}     # level-of-service remarks; only meaningful on visit codes
PRICING_MODIFIERS = {"26", "TC", "50", "52", "53", "54", "55", "56", "62", "66", "80", "81", "82", "AS", "22", "51", "73", "74", "FY", "GY"}
DOWNCODE_SIGNALS = ("DOWNCODE_SVC06", "DOWNCODE_VS_BILLED", "DOWNCODE_CHARGE", "DOWNCODE_LEVEL_REDUCED", "DOWNCODE_RATE",
                    "DOWNCODE_LINE_SWAP", "DOWNCODE_LEVEL_DENIED")
MANUAL_STATUSES = ("in_progress", "appealed", "lost")
# money items from a remittance: each gets an end date (the payer's window to dispute its answer), so that every one can
# expire (a review-type denial and a claim sent on to another carrier included)
END_DATED_CATEGORIES = ("denial", "bundling", "downcoding", "underpayment")
# denials a corrected claim can fix (the authorization number, the clinician's enrollment, the primary code, a modifier): where
# the payer's manual sets a corrected-claim limit (codes.CORRECTED_RULES), that route stays open beside the dispute
CORRECTABLE_APPEAL_SIGNALS = frozenset({"DENIED_AUTH", "DENIED_PROVIDER", "DENIED_MISSING_PRIMARY", "PROCEDURE_BUNDLED", "ADDON_BUNDLED",
                                        "EM_BUNDLED_MOD25", "EM_BUNDLED_MOD57_MAJOR", "EM_BUNDLED_MOD24"})

# Claims EMA shows sent and unpaid with no answer from the payer (Detector.run_ema_open_claims): how long a payer has to
# answer before a claim counts as unanswered (Kentucky's prompt-pay law gives 30 days for a clean claim, KRS 304.17A-702).
NO_ANSWER_DAYS = int(os.environ.get("REMITWATCH_NO_ANSWER_DAYS", "30") or 30)
SELF_PAY = re.compile(r"SELF|PATIENT|CASH|PRIVATE PAY|NO INSURANCE|UNINSURED", re.I)
# EMA's claim statuses, matched by words. The practice's exports use Payment Pending, Resolved/Paid, Resolved/Other,
# Denied, Batched for Printing, Rejected, Ready for Posting, Invalid and Inactive/Voided (tests/test_workable.py pins how
# each is read). EMA's "Claims State" column is a US state, never part of the status.
EMA_REJECTED = re.compile(r"REJECT", re.I)              # never reached the payer: fix and send it again
EMA_DENIED = re.compile(r"DENI", re.I)                  # the payer answered: a denial is worked from its remittance, not chased
EMA_SETTLED = re.compile(r"\bPAID\b|VOID|SETTLED|COLLECTION|RESOLVED|CANCEL", re.I)
# closed or written off with nothing paid and no answer from any payer: money given up, listed while it can still be asked for
EMA_GAVE_UP = re.compile(r"CLOSED|WRITE.?OFF|WRITTEN OFF|ZERO BALANCE", re.I)
EMA_NOT_SENT = re.compile(r"HOLD|READY|UNBILLED|NOT SENT|NOT SUBMITTED|DRAFT|QUEUED|UNSUBMITTED", re.I)
# waiting in EMA's print batch: with no submission date it was never sent, so it is sent (Fix and resubmit), not chased
EMA_PRINT_BATCH = re.compile(r"BATCH|PRINT", re.I)
# items with no remittance behind them (no line or claim key); all three share the id signal EMA_OPEN_CLAIM, so the work
# people record follows one claim from never sent to rejected to unanswered
EMA_CLAIM_SIGNALS = ("NO_ANSWER", "REJECTED_PRE_PAYER", "NOT_SENT")
# the dates an unanswered claim's "sent" can come from (run_ema_open_claims); the date of service never shows it went out on time
SENT_SUBMITTED, SENT_BILLED, SENT_DOS = "the claim's submission date in EMA", "the date EMA created the bill", "the date of service"
SENT_AGAIN = "the day after the bill's previous submission or answer (EMA has no date for this one yet)"
# The 837 claim files (claims_837: one row per claim in a file; BHT04, the day the file was made, is the day its claims went
# out). A claim's newest send dates it where EMA gives no date or an older one (trace finding T-I4); a claim the 837s show
# sent that EMA's weekly exports do not list is chased on its own (run_837_open_claims, T-I2), under its own id signal so
# its item never collides with the exports' one for the same claim.
SENT_837 = "the day its 837 claim file was sent"
OPEN_837_ID = "837_OPEN_CLAIM"
PRIMARY_STATUSES = ("1", "19", "4", "22")      # a payer processed the claim as its primary (CLP02), or reversed it
COMPONENTS = ("26", "TC")                      # the parts of a service a line can bill: the reading (26), the technical part
# A payer whose 837 claims never get an ERA (the VA's community care network, paid through its administrator): at least
# NO_ERA_MIN_CLAIMS claims sent and due, at most NO_ERA_MAX_SHARE of them answered by any 835. EMA's postings are the
# only source of its answers: its claims are judged by them, and counted on the Data tab when they cannot show it.
NO_ERA_MIN_CLAIMS = 10
NO_ERA_MAX_SHARE = 0.05
# [{"payer", "payer_id", "claims", "billed_cents", "since", "listed", "answered_in_ema", "unchecked"}]: the claims due that the
# 837 pass judged for each such payer, its earliest send, and how many it listed, EMA showed answered, or nothing could check
NO_ERA_META = "feed:no_era_837"
# Weeks one feed shows claims sent and the other none (trace finding T-N13: a hole in the 837 files or in EMA's claim
# exports), inside the weeks both reach, with at least SEND_WEEK_MIN claims on the side that has them
SEND_WEEKS_META = "feed:send_weeks"
SEND_WEEK_MIN = 5
# Claims sent that no payer has answered yet and that are not due yet (the 837 pass lists them only at their due day): on no
# list meanwhile, so the Data tab shows them with their due days (blindness is loud). {"n", "billed_cents", "first_due",
# "last_due", "by_group": {group: {"n", "billed_cents", "first_due"}}, "by_due_week": {week: {"n", "billed_cents"}},
# "claims": [[claim id, group, sent, due, billed cents]] (the first WAITING_LIST_MAX by due day), "sent_by_week": {week:
# claims sent}, "at"}. sent_by_week is every claim the 837s show sent in the window, what the never-answered claims of a
# week are measured against (insights.unanswered_weeks: a batch that never reached the payers stands out).
WAITING_837_META = "feed:waiting_837"
WAITING_LIST_MAX = 2000
# Each payer group's usual answer time after a send, from the 837s: days from a claim's send to the first remittance that
# answered it, 95th percentile over the two years before the newest remittance (20 answers at least, never under
# NO_ANSWER_DAYS). A bill sent again waits that long for its answer (states: the resend's follow-up day).
ANSWER_DAYS_META = "detect:answer_days_837"
# EMA writes the 837s: its claim for one has its charges posted the day the file went out or shortly before. Of the bills
# the link map ties to one EMA claim on the real data, 87% had EMA's first charge posting on the bill's first 837 send day
# and 7% the day before. An EMA claim EMA's own bridges could not tie to an 837 claim is looked for in that window.
SEND_DAY_SLACK = 1
# EMA payer ids of the VA's community care network. EMA names these claims after the network's administrator ("Optum
# Unitedhealth Care"), whose own commercial rules are not the VA's.
VA_PAYER_IDS = frozenset({"VACCN", "TWVACCN"})
VA_GROUP = ("VA Community Care", "va")
# payer groups that hold many payers: within one, only the payer's own name says who a claim went to
MANY_PAYER_GROUPS = ("Medicare Supplement carriers", "Self-funded plan TPAs")
# a Payment row in EMA is the insurer's when its activity or payment method says so (a copay or a card payment is the patient's)
INSURER_PAYMENT = re.compile(r"INSURANCE|\bINS\b|ERA|835|EFT|PAYER", re.I)
_SUBMISSION = re.compile(r"C(\d{1,3})$")

# columns added after the first release; created by ALTER TABLE on older stores
EXTRA_FINDING_COLS = {
    "ema_handling": "TEXT", "billed_code": "TEXT", "code_pair": "TEXT", "level_drop": "INTEGER", "pos": "TEXT", "pos_class": "TEXT",
    "plan": "TEXT", "month": "TEXT", "service_month": "TEXT", "modifier_class": "TEXT", "location": "TEXT", "diagnosis": "TEXT",
    "deadline_basis": "TEXT", "recovered_cents": "INTEGER", "recovered_date": "TEXT", "appealed_at": "TEXT", "status_changed_at": "TEXT",
    "stale": "INTEGER DEFAULT 0", "auto_note": "TEXT", "prev_status": "TEXT", "hint": "TEXT",
    # what the biller wrote off in EMA on the item's line (its ERA & Payment Line Adjustments rows, the payer's own reasons:
    # the patient's share is not a write-off), and the newest date of those write-offs
    "written_off_cents": "INTEGER", "written_off_on": "TEXT",
    # EMA's reports (remitwatch.ema_checks): where the write-off figure comes from ('era_lines' | 'production'), its kind
    # (contractual, noncontractual, moved_to_patient), when ('at_posting' | 'later') and the evidence for an automatic one
    # ('rule_proven': EMA's ERA export shows its posting rule; 'posting_day': inferred from the posting date)
    "written_off_source": "TEXT", "written_off_kind": "TEXT", "written_off_when": "TEXT", "ema_writeoff_evidence": "TEXT",
    # the item's EMA claim number, the claim's newest status (context only, never the outcome) with its source and date,
    # the line's balance, the newest work EMA shows on it, and the line's own outcome from its postings
    "ema_link": "INTEGER", "ema_status": "TEXT", "ema_status_src": "TEXT", "ema_as_of": "TEXT", "ema_balance_cents": "INTEGER",
    "ema_worked_on": "TEXT", "ema_worked_how": "TEXT", "ema_outcome": "TEXT", "ema_outcome_on": "TEXT",
    "source": "TEXT",           # where the item comes from: '835' (a remittance), 'ema' (EMA's reports alone)
    # the item's state (remitwatch.states, amendment 13.1): work_now | waiting | done | accepted | expired | cant_check, the
    # day a waiting item comes back, and the fact that decided it
    "work_state": "TEXT", "follow_up_on": "TEXT", "state_basis": "TEXT",
    # why a bulk "appeal everything appealable" must leave it out (paid, credit, reprocessed, waiting), else NULL
    "appeal_block": "TEXT",
    # the payer's own limit where the due date shown is a safer, earlier one ("file by appeal_deadline; the payer's limit
    # is legal_deadline"); NULL when the two are the same day
    "legal_deadline": "TEXT",
    # the worklist's ranking (remitwatch.selfcheck: the item's dollars x its pattern's recovery rate, with the basis), and the
    # rule states.py decided the state by (the self-check and the decision log name it)
    "expected_value_cents": "INTEGER", "recovery_rate": "REAL", "recovery_basis": "TEXT", "state_rule": "TEXT",
}
EM_SQL = ",".join(f"'{c}'" for c in sorted(EM_CODES))
AUTO_NOTE_PREFIX = "Paid on a later remittance"
# items from EMA's reports alone, or carrying their own facts: the line outcome (ema_checks.line_outcomes) is worked out
# for the denials only EMA saw, never for these (an EMA payment on their own line is what they are about)
EMA_NATIVE_SIGNALS = ("NOT_SENT", "REMIT_NOT_POSTED", "EMA_DENIAL_AS_CONTRACTUAL", "SECONDARY_NOT_BILLED",
                      "CREDIT_BALANCE")
EMA_WIRING_KEY = "ema:wiring"               # meta: what the run did with EMA's reports (counts by step, failed steps)
UNSEEN_KEY = "ema:unanswered_unseen"        # meta: unanswered claims no source can check yet (count, billed $, due days)
PAYER_POLICY_KEY = "ema:payer_policy"       # meta: codes a payer never pays, folded out of the work (835 and EMA-only items)
POLICY_SUMMARY_KEY = "ema:policy_summary"   # meta: the write-off pattern table for the owner's policy (O11), from the run
PAYMENT_OUTCOMES = ("paid_after", "paid_after_other")   # the line outcomes an EMA closure (C6, rebilled) rests on


def _usd(cents) -> str:
    c = int(cents or 0)
    return f"-${abs(c) / 100:,.2f}" if c < 0 else f"${c / 100:,.2f}"

FINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    signal TEXT, category TEXT, severity TEXT, actionable TEXT, title TEXT,
    line_key TEXT, claim_key TEXT, transaction_key TEXT, patient_control_number TEXT,
    payer_name TEXT, payer_group TEXT, payer_kind TEXT, payer_id TEXT,
    rendering_provider_npi TEXT, rendering_provider_name TEXT, provider_tier TEXT,
    service_date TEXT, payment_date TEXT, year TEXT,
    procedure_code TEXT, modifiers TEXT, original_procedure_code TEXT, code_category TEXT,
    charge_cents INTEGER, allowed_cents INTEGER, paid_cents INTEGER, expected_cents INTEGER, shortfall_cents INTEGER,
    carc TEXT, carc_text TEXT, rarc TEXT, rarc_text TEXT,
    evidence TEXT, appeal_deadline TEXT, ema_handling TEXT,
    status TEXT DEFAULT 'open', notes TEXT, run_id TEXT, first_seen TEXT, last_seen TEXT
);
CREATE INDEX IF NOT EXISTS ix_f_signal ON findings(signal);
CREATE INDEX IF NOT EXISTS ix_f_payer ON findings(payer_group);
CREATE INDEX IF NOT EXISTS ix_f_date ON findings(service_date);
CREATE INDEX IF NOT EXISTS ix_f_status ON findings(status);
CREATE INDEX IF NOT EXISTS ix_f_pcn ON findings(patient_control_number);
CREATE INDEX IF NOT EXISTS ix_f_claim ON findings(claim_key);
CREATE INDEX IF NOT EXISTS ix_f_cat ON findings(category);

CREATE TABLE IF NOT EXISTS payer_rates (
    payer_group TEXT, payer_kind TEXT, year TEXT, code TEXT, tier TEXT,
    mode_cents INTEGER, median_cents INTEGER, p10_cents INTEGER, p90_cents INTEGER, n INTEGER, mode_share REAL,
    medicare_cents INTEGER, pct_of_medicare REAL, pos_class TEXT DEFAULT 'office',
    PRIMARY KEY (payer_group, year, code, tier, pos_class)
);
CREATE TABLE IF NOT EXISTS exposure (
    month TEXT, payer_group TEXT, payer_kind TEXT, npi TEXT, tier TEXT, code TEXT, pos_class TEXT, plan TEXT,
    lines INTEGER, zero_paid INTEGER, charge_cents INTEGER, allowed_cents INTEGER, paid_cents INTEGER,
    PRIMARY KEY (month, payer_group, payer_kind, npi, tier, code, pos_class, plan)
);
CREATE INDEX IF NOT EXISTS ix_exp_payer ON exposure(payer_group, month);
CREATE TABLE IF NOT EXISTS exposure_daily (
    day TEXT, payer_group TEXT, payer_kind TEXT, npi TEXT, scope TEXT, lines INTEGER,
    PRIMARY KEY (day, payer_group, payer_kind, npi, scope)
);
CREATE TABLE IF NOT EXISTS rate_points (
    day TEXT, payer_group TEXT, payer_kind TEXT, code TEXT, tier TEXT, pos_class TEXT, allowed_cents INTEGER, n INTEGER,
    PRIMARY KEY (day, payer_group, payer_kind, code, tier, pos_class, allowed_cents)
);
CREATE TABLE IF NOT EXISTS finding_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, finding_id TEXT, at TEXT, kind TEXT, from_status TEXT, to_status TEXT,
    amount_cents INTEGER, actor TEXT, note TEXT, run_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_fe_finding ON finding_events(finding_id);
CREATE INDEX IF NOT EXISTS ix_fe_at ON finding_events(at);
CREATE TABLE IF NOT EXISTS progress_snapshots (
    run_id TEXT, at TEXT, category TEXT, status TEXT, n INTEGER, cents INTEGER, PRIMARY KEY (run_id, category, status)
);
CREATE TABLE IF NOT EXISTS fee_table (year TEXT, code TEXT, charge_cents INTEGER, share REAL, PRIMARY KEY (year, code, charge_cents));
CREATE TABLE IF NOT EXISTS provider_tiers (npi TEXT PRIMARY KEY, name TEXT, tier TEXT, ratio REAL, n INTEGER);
CREATE TABLE IF NOT EXISTS providers (npi TEXT PRIMARY KEY, name TEXT, tier TEXT, lines INTEGER, location TEXT);
CREATE TABLE IF NOT EXISTS detection_runs (run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, version TEXT, lines INTEGER, findings INTEGER, summary TEXT);
CREATE TABLE IF NOT EXISTS swing_moves (
    run_id TEXT, category TEXT, appeared_n INTEGER, appeared_cents INTEGER, vanished_n INTEGER, vanished_cents INTEGER,
    PRIMARY KEY (run_id, category)
);
"""


def _fid(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def _days_between(a: str | None, b: str | None) -> int | None:
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except (TypeError, ValueError):
        return None


def _add_days(iso: str | None, days: int) -> str | None:
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()
    except ValueError:
        return None


def real_day(d) -> str | None:
    """A stored date (YYYY-MM-DD) when it is a day on the calendar, else None. The store keeps a date as the file wrote it,
    and a remittance can carry one no calendar has (BPR16 20260931, or 20270229): it says nothing of how far a feed reaches."""
    try:
        date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError):
        return None
    return str(d)[:10]


FEED_WINDOW_DAYS = 120          # the remittance lines that weigh each payer group's say in feed_date
FEED_META = "feed:date"         # the feed date the last analysis used, for a copy that does not hold every line (Vercel's)
# The payers whose remittances stop before the day a claim sent to them was due, while the feed goes on (misuse check CH-8):
# [{"payer", "since" (its last remittance), "claims", "billed_cents"}], most dollars first. Those claims are not judged
# unanswered (run_ema_open_claims), and the Data tab's checks name each payer (insights.data_health). Each analysis saves
# it, and the dashboard's copy carries it.
PAYERS_BEHIND_META = "feed:payers_behind"
EVENTS_SEEN_META = "detect:events_seen"     # the newest finding_events id the last detection run could see (Detector.run)
# Reversals (claim status 22) of a payment that is not on file: no earlier payment of the reversal's own amount from that
# payer for the bill, code and date (trace finding T-I5). They cancel nothing. {"claims", "lines", "cents"} for those whose
# service is dated on or after the payer's first remittance on file (that payment's remittance is missing), and the same
# with "older_" for the rest (paid before the remittances on file begin). Each analysis saves it; the Data tab's checks
# name it (insights.data_health), and the dashboard's copy carries it.
REVERSALS_NOT_ON_FILE_META = "ledger:reversals_not_on_file"
# The ledger's reversal pairing reads these payer companies (payer_company) as one: UnitedHealthcare's administrator UMR,
# formerly Wausau Benefits, reverses under the Wausau name (its own group) payments it made as UMR (Detector._build_ledger)
LEDGER_COMPANIES = {"umr": "uhc"}
# A claim id written inside a recoupment's reference, among other text (the payer's own claim number before or after it, a
# letter, a date): the practice's claim ids are a bill id and a submission suffix (bill_of). Trace finding T-I1.
CLAIM_ID_IN_TEXT = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{5,6}C\d{1,3}")
# The Data tab names such a payer once its last remittance is more than PAYER_QUIET_DAYS before the day the feed reaches
# (the ten days its feed check allows the whole feed): a payer that pays every week or so is between two files, not
# stopped, and the claims due since its last file wait for the next one.
PAYER_QUIET_DAYS = 10
# A hole in the feed: more than FEED_GAP_DAYS in a row with no remittance from any payer, followed by newer ones (weeks a
# person forgot to put in the folder, then the newest week). Nobody can see what the payers paid in the hole, so the feed
# reaches only the last day before it. The live archive's longest stretch without a payment from mid-2024 to its end was
# 4 days (weekends and holidays). The rule needs a real feed to judge: FEED_GAP_MIN_DAYS payment days in the window.
FEED_GAP_DAYS = 7
FEED_GAP_MIN_DAYS = 20
# Only a hole near the newest remittances holds the feed: once the remittances after it span more than
# FEED_GAP_RECENT_DAYS (five weeks), the weeks since are in and the hole is history (weeks that may never come), and it
# would otherwise hold the feed back for up to FEED_WINDOW_DAYS.
FEED_GAP_RECENT_DAYS = 35


def feed_date(conn: sqlite3.Connection, today: str | None = None, hole: dict | None = None) -> str | None:
    """The day the remittance feed reaches: a payment date after today counts as today; each payer group's newest payment
    date is how far its remittances reach; and the feed reaches the latest day D such that payer groups holding at least
    half of the remittance lines of the FEED_WINDOW_DAYS before the newest (clamped) date are current through D. One stray
    file from one payer (a small one, an adjustment, a file dated in the future) moves it only when that payer carries half
    the volume. Nor does the newest week put in after weeks that are missing: when no payer paid for more than
    FEED_GAP_DAYS in a row, the feed reaches only the last day before that hole, while the remittances after the hole
    span at most FEED_GAP_RECENT_DAYS (an older hole is history). With fewer than three payer groups it is
    the newest date, clamped to today. A payment date no calendar has is skipped (real_day). None with no remittances.
    Today is the practice's day unless given (runtime.practice_today: from 7 PM Eastern the UTC date is already tomorrow).
    `hole`, when given (a dict), is filled with the hole the feed stops at, if one does (feed_gap)."""
    from . import runtime
    today = today or runtime.practice_today()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "transactions" not in tables:
        return None
    last: dict[str, str] = {}
    for name, d in conn.execute("""SELECT payer_name, MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0
                                   AND payment_date IS NOT NULL GROUP BY 1""").fetchall():
        if not real_day(d):     # a newest date no calendar has: the payer's newest real one before it
            d = next((x for (x,) in conn.execute("""SELECT DISTINCT payment_date FROM transactions WHERE payer_name IS ?
                                                     AND COALESCE(superseded,0)=0 AND payment_date < ? ORDER BY 1 DESC""", (name, d))
                      if real_day(x)), None)
            if not d:
                continue
        d = min(str(d)[:10], today)
        g = normalize_payer(name)[0]
        if d > last.get(g, ""):
            last[g] = d
    if not last:
        return None
    newest = max(last.values())
    vol: Counter = Counter()
    if "lines" in tables:
        for name, n in conn.execute("""SELECT t.payer_name, COUNT(*) FROM transactions t JOIN lines l ON l.transaction_key = t.transaction_key
                                       WHERE COALESCE(t.superseded,0)=0 AND t.payment_date > ? GROUP BY 1""",
                                    (_add_days(newest, -FEED_WINDOW_DAYS),)).fetchall():
            vol[normalize_payer(name)[0]] += n
    groups = [g for g in last if vol.get(g)]
    if len(groups) < 3:
        return newest
    total, acc, reach = sum(vol[g] for g in groups), 0, newest
    for g in sorted(groups, key=lambda x: last[x], reverse=True):
        acc += vol[g]
        if 2 * acc >= total:
            reach = last[g]
            break
    # a hole: the feed reaches only the last day before the first one in the window that the newest remittances follow
    # closely (at most FEED_GAP_RECENT_DAYS of them after it)
    days = sorted({min(x, today) for x in (real_day(d) for (d,) in conn.execute(
        """SELECT DISTINCT payment_date FROM transactions WHERE COALESCE(superseded,0)=0 AND payment_date IS NOT NULL
           AND payment_date > ?""", (_add_days(newest, -FEED_WINDOW_DAYS),))) if x})
    if len(days) >= FEED_GAP_MIN_DAYS:
        for a, b in zip(days, days[1:]):
            if (date.fromisoformat(b) - date.fromisoformat(a)).days > FEED_GAP_DAYS \
                    and (date.fromisoformat(days[-1]) - date.fromisoformat(b)).days <= FEED_GAP_RECENT_DAYS:
                if hole is not None and a <= reach:     # the feed stops at the hole, not where most payers' files end
                    hole.update({"from": _add_days(a, 1), "to": _add_days(b, -1), "newest": days[-1]})
                return min(reach, a)
    return reach


def feed_gap(conn: sqlite3.Connection, today: str | None = None) -> dict | None:
    """The hole feed_date stops at, if it stops at one (misuse check CH-6): weeks with no remittance from any payer, then
    newer remittances. "from" and "to": its first and last days with no remittance from any payer; "newest": the newest
    remittance (a payment date after today counts as today). None when no hole holds the feed back. Worked out from the
    store; each analysis saves it with the feed date (FEED_META), for a copy that does not hold every line
    (insights.feed_hole)."""
    hole: dict = {}
    feed_date(conn, today, hole)
    return hole or None


def gap_words(gap: dict) -> str:
    """What the stale-feed alert, the fax hold and the Data tab's feed check say of such a hole: the dates and what to do."""
    return (f"No remittance from any payer between {gap['from']} and {gap['to']}: put those weeks in the folder (the newest "
            f"remittance is {gap['newest']}).")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the derived tables and add columns introduced after the first release. Safe to call on every start."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    cur = conn.execute("SELECT value FROM meta WHERE key='derived_schema'").fetchone()
    if not cur or cur[0] != DERIVED_SCHEMA:
        for t in ("payer_rates", "exposure", "exposure_daily", "rate_points"):      # derived data, rebuilt by every detection run
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('derived_schema', ?)", (DERIVED_SCHEMA,))
    conn.executescript(FINDINGS_DDL)
    have = {r[1] for r in conn.execute("PRAGMA table_info(findings)")}
    for col, decl in EXTRA_FINDING_COLS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE findings ADD COLUMN {col} {decl}")
    if "auto_note" not in have:     # older stores kept the automatic note in the notes column
        conn.execute(f"UPDATE findings SET auto_note = notes, notes = NULL WHERE notes LIKE '{AUTO_NOTE_PREFIX}%'")
    conn.executescript("""CREATE INDEX IF NOT EXISTS ix_f_month ON findings(month);
                          CREATE INDEX IF NOT EXISTS ix_f_npi ON findings(rendering_provider_npi);""")
    conn.commit()
    from . import ema_reports; ema_reports.ensure_schema(conn)      # noqa: E702  (EMA's report tables, the dashboard copy's too)
    from . import repair, runlog; runlog.ensure_schema(conn); repair.ensure_schema(conn)      # noqa: E702  (logs: run_log, item_decisions, repair_log)


ORG_NAME_WORDS = ("DERMATOLOG", "ASSOC", " PSC", "CENTER", "CLINIC", "SURGERY", "DERMA")


def best_provider_name(names: Counter) -> str:
    """Payers spell the rendering provider many ways and some send the group name; pick the commonest personal name."""
    clean = Counter()
    for n, k in names.items():
        n = " ".join((n or "").replace(",", ", ").split())
        if n:
            clean[n] += k
    personal = Counter({n: k for n, k in clean.items() if not any(w in n.upper() for w in ORG_NAME_WORDS)})
    pick = personal or clean
    return top_of(pick)[0] if pick else ""


def modifier_class(mods: list[str]) -> str:
    for m in ("25", "57", "24", "59", "XS", "XE", "XU", "XP", "76", "79"):
        if m in mods:
            return m
    return "other" if mods else "none"


LINE_COLUMNS = ("line_key", "claim_key", "transaction_key", "procedure_code", "modifiers", "charge_cents", "paid_cents", "paid_units",
                "original_procedure_code", "original_modifiers", "original_units", "service_date", "allowed_cents",
                "patient_control_number", "claim_status_code", "payer_name", "payer_id", "payment_date", "rendering_provider_npi",
                "claim_filing_indicator", "contract_class", "facility_type_code", "rp_name", "txn_payment_date", "trace_number",
                "group_number")
# A line with no service date of its own (no DTM*472) takes a one-day period of its own or of its claim (DTM*150/151,
# DTM*232/233) in load(); one that covers several days stays undated, and an undated line is never matched to a payment.
# l.get("statement_from") is not a column here and always comes back empty. group_number is the claim's REF*1L, the
# employer plan (_plan_schedules): it is read only to tell one plan's rates from another's and is never shown or logged.
LINE_DERIVED = ("modifiers_list", "year", "payer_group", "payer_kind", "pricing_mod", "pos", "pos_class", "plan", "month",
                "provider_tier", "surg_rank", "surg_count", "clean")


class Line:
    """One service line with its claim and payment context. It behaves like the dict the rules were written against
    (l["x"], l.get("x"), l["x"] = v) but is a slotted object: with 400,000 lines in memory a dict each costs close to a
    gigabyte more, which is the difference between fitting a small serverless function and not."""
    __slots__ = LINE_COLUMNS + LINE_DERIVED

    def __getitem__(self, k):
        try:
            return getattr(self, k)
        except AttributeError:
            raise KeyError(k) from None

    def __setitem__(self, k, v) -> None:
        setattr(self, k, v)

    def get(self, k, default=None):
        return getattr(self, k, default)

    def setdefault(self, k, v):
        try:
            return getattr(self, k)
        except AttributeError:
            setattr(self, k, v)
            return v

    def keys(self):
        return [k for k in self.__slots__ if hasattr(self, k)]

    def __contains__(self, k) -> bool:
        return hasattr(self, k)


exec("def _line_init(self, r):\n" + "".join(f"    self.{n} = r[{i}]\n" for i, n in enumerate(LINE_COLUMNS)))
Line.__init__ = _line_init      # noqa: F821  (generated above: one bytecode store per column is several times faster than a loop)


def top_of(cnt: Counter) -> tuple:
    """(value, count) of the most common value; on a tie the first in sorted order, never the first that happened to be read."""
    return min(cnt.items(), key=lambda kv: (-kv[1], str(kv[0])))


def usual_amount(cnt: Counter) -> tuple[int, int]:
    """The most common amount in a group and how many lines carry it. When two amounts are equally common the answer
    must not depend on the order the files were read in, so the one nearest the middle of the group wins, then the lower."""
    top = max(cnt.values())
    tied = [v for v, k in cnt.items() if k == top]
    if len(tied) == 1:
        return tied[0], top
    vals = sorted(cnt.elements())
    mid = vals[len(vals) // 2]
    return min(tied, key=lambda v: (abs(v - mid), v)), top


def is_surgical(code: str | None) -> bool:
    """A procedure Medicare ranks and reduces as a second or later procedure on the same day (CMS multiple-procedure
    indicator 2 or 3). Add-ons, pathology, injections and other codes CMS does not reduce are not ranked."""
    return bool(code) and code in MPPR_CODES and code not in ADD_ON_CODES


SECOND_RATE_MIN_LINES = 5        # a lower amount the payer allowed this often for a code is a rule of the payer's, not a one-off
STATE_NAMES = {"KY": "Kentucky", "IN": "Indiana"}

# The payer's own rate (the owner's rules on rate evidence, plans/audit_calibration_2026-11-24.md section 3 fix 5 and section
# 4.1; trace finding T-N4). The yardstick is the payer group's own rate for the code, the place of service, the year and the
# component (global, 26 or TC), read on full-rate lines: an amount about half of another that holds a fifth of the lines is a
# multiple-procedure reduction, and one at the PA/NP 85 percent of another, billed mostly by PAs and NPs, is that tier; neither
# is the rate. A tie goes to the full amount. A usual rate needs a clear majority of those lines (FULL_SHARE). A clinician's
# own rate is used only where it is a recognised tier: at least TIER_MIN_LINES payments with TIER_MIN_SHARE at one amount, and
# that amount 100 percent of the payer's rate (the same rate) or 85 percent of it for a PA or NP. Any other level is a
# discount no rule explains, listed once per payer and clinician (PROVIDER_RATE_GAP), never taken as the clinician's rate.
FULL_SHARE = 0.6
HALF_BAND = (0.47, 0.53)
NP_BAND = (0.83, 0.87)
SAME_BAND = 0.01
SHARE_TO_DROP = 0.2
TIER_MIN_LINES, TIER_MIN_SHARE = 10, 0.6
NP_TIER = 0.85
MAX_UNITS = 20                  # a count past this is a misread quantity: counted once
# Plans of one payer group (fix7c; _plan_schedules): an employer plan (REF*1L) or product (REF*CE) with PLAN_UNIT_MIN clean
# lines is read apart when its physicians' own amounts (PLAN_MIN lines at one amount, a clear majority) differ from the rest
# of its payer's on PLAN_DIFFER_MIN codes or more, each by PLAN_GAP and $2 at least (what an underpayment needs: a plan a
# few percent off its payer's rate is never called short by it). PLAN_SEP joins the payer group and the plan's label.
PLAN_UNIT_MIN, PLAN_MIN, PLAN_DIFFER_MIN, PLAN_GAP = 10, 3, 2, 0.05
PLAN_SEP = "#plan:"
# A code raised later in the year than the payer's new schedule shows it by this many old-amount visits after the payer's
# new rates took effect and this many paid at its own raised rate (emit_old_schedules)
OLD_RUN_LINES = 5
# A product class's typical amount for a code needs 10 lines in a setting; with fewer, this many of the line's own setting
# size a denial before the class's lines of every setting together (a surgery center's amounts are not an office's)
CLASS_FEW_MIN = 3
COMPONENTS = ("26", "TC")
RATE_COMPONENTS = ("",) + COMPONENTS          # _rate_lines keeps a line's component as its index here
RATE_SIGNALS = ("UNDERPAID_VS_OWN_RATE", "SECOND_RATE", "MPPR_EXCESS", "PROVIDER_RATE_GAP",      # items a baseline makes: never new past their window
                "OLD_FEE_SCHEDULE")
# product classes priced on Medicare's fee schedule: a payer of these kinds with no rate of its own is sized at Medicare's
MEDICARE_PRICED = ("medicare", "medicare_advantage", "supplement", "va", "tricare")


def in_band(x: float, band: tuple[float, float]) -> bool:
    return band[0] <= x <= band[1]


def professional_service(code: str | None) -> bool:
    """A clinician's own service, which a payer pays a PA or NP 85 percent of: a procedure (CPT 10000-69999), a visit or
    other medicine service (90800-99999) or a HCPCS G code. Not a drug, a supply, a vaccine, a lab test or a pathology
    read: their price does not depend on who ordered them."""
    c = (code or "").strip().upper()
    if c.isdigit() and len(c) == 5:
        n = int(c)
        return 10000 <= n < 70000 or 90800 <= n < 100000
    return c.startswith("G")


def full_rate(cnt: Counter, np_lines: Counter | None = None, halves: bool = True) -> dict | None:
    """The payer's full rate from the amounts it allowed per unit (`cnt`) and how many of each a PA or NP billed (`np_lines`):
    {'mode', 'n' (the full-rate lines), 'share', 'n_all', 'np' (its PA/NP amount when one stands out), 'half'}. Amounts about
    half of one that holds SHARE_TO_DROP of the lines (`halves`: a code the multiple-procedure rule reduces) and amounts at 85
    percent of one, billed mostly by PAs and NPs, are left out of the mode and its share; a tie goes to the higher amount."""
    n_all = sum(cnt.values())
    if not n_all:
        return None
    big = [a for a, k in cnt.items() if k >= SHARE_TO_DROP * n_all and a > 0]
    np_lines = np_lines or Counter()
    half, tier = set(), {}
    for a, k in cnt.items():
        if halves and any(in_band(a / b, HALF_BAND) for b in big if b > a):
            half.add(a)
        elif np_lines.get(a, 0) * 2 >= k and any(in_band(a / b, NP_BAND) for b in big if b > a):
            tier[a] = k
    kept = Counter({a: k for a, k in cnt.items() if a not in half and a not in tier}) or cnt
    top = max(kept.values())
    mode = max(a for a, k in kept.items() if k == top)
    n = sum(kept.values())
    return {"mode": mode, "n": n, "share": top / n, "n_all": n_all,
            "np": max(tier, key=lambda a: (tier[a], a)) if tier else None, "half": sorted(half)}


def _counts_json(raw: str | None) -> Counter:
    """An archive snapshot's amounts and how many lines had each ('{"11760": 24}') as a Counter; text that does not read
    counts nothing, and the run's log says so."""
    try:
        d = json.loads(raw or "{}")
        return Counter({int(a): int(b) for a, b in d.items()}) if isinstance(d, dict) else Counter()
    except (ValueError, TypeError) as exc:
        from .runlog import caught
        caught(exc, "detect.locked_year_counts")
        return Counter()


class LaterPayment(tuple):
    """A later payment as one event (Detector.later_payment, the claim rules' covering payment), with what the closure's
    recovery is measured from (Detector._recover): `wants`, [((bill, code, date), the item's units of it, the standing
    payments of it that came after the item's answer)]."""
    wants: list = []


def _service_keys(item: dict, evidence: dict, code: str | None, dos: str | None) -> list[tuple]:
    """The services (code, date) a recovery is counted against: a line item's own code and date; a whole claim's item every
    code and date it lists as unpaid (its codes on its date when it lists none)."""
    if item.get("line_key"):
        return [(code, dos)]
    ev = evidence if isinstance(evidence, dict) else {}
    pairs = [(p[0], p[1] or dos) for p in ev.get("unpaid") or [] if isinstance(p, (list, tuple)) and len(p) == 2 and p[0]]
    if not pairs:
        pairs = [(c, dos) for c in ev.get("codes") or [] if c]
    return sorted(set(pairs)) or [(code, dos)]


def patient_keys(pid: str | None, last: str | None, first: str | None) -> tuple[str | None, str | None]:
    """(same person, same name). The first needs the member number AND the name, the rule the dashboard uses for "this
    patient": one member number often covers a family, and two people can share a name. The second, the name alone,
    is used only when EMA ties both claims to one patient (a claim sent to another payer has another member number)."""
    norm = lambda v: "".join(ch for ch in (v or "").upper() if ch.isalnum())
    p, name = norm(pid), (norm(last) + "|" + norm(first)) if norm(last) and norm(first) else None
    return (f"P:{p}|{name}" if len(p) >= 6 and name else None), (f"N:{name}" if name else None)


def second_rate_reason(ratio: float, key_tier: str, tiers: Counter, states: Counter, usual_states: Counter, first_dos: str | None,
                       usual_last_dos: str | None, plans: Counter | None = None, usual_plans: Counter | None = None,
                       surgical_with_another: float = 0.0, unnamed: int | None = None, clinician_paid_usual: bool = False,
                       last_dos: str | None = None, usual_first_dos: str | None = None) -> tuple[str, bool]:
    """The likely explanation for a payer's routine lower amount, in plain words, and whether the remittances support one.
    A reason must hold for at least 80 percent of the lines (a line whose office or plan is unknown counts against it).
    Without support the lines are still one routine-rate item to review once they are SECOND_RATE_MIN_LINES or more
    (emit_second_rates), else one-off underpayments to appeal.
    `unnamed`: lines whose remittance names no rendering clinician (a line with a named clinician of unknown type is not
    taken for a PA or NP). `clinician_paid_usual`: a clinician on these lines is paid the usual rate on other visits.
    `last_dos`, `usual_first_dos`: the newest of these lines and the payer's first visit paid at its rate for the code, any
    clinician's at the rate that clinician is held to (every line dated before it: the payer's earlier rate, raised since,
    trace finding T-N4; a line dated after it was owed the new rate: fix7c)."""
    n = sum(tiers.values()) or 1
    parts = []
    npp = tiers.get("nonphysician", 0) + (tiers.get("unknown", 0) if unnamed is None else unnamed)
    if 0.83 <= ratio <= 0.87 and key_tier in ("physician", "unknown") and npp / n >= 0.8 and not clinician_paid_usual:
        parts.append("85 percent of the usual rate is what many plans pay a physician assistant or nurse practitioner"
                     + (f"; {tiers.get('nonphysician', 0)} of these {n} lines are by a PA or NP" if tiers.get("nonphysician")
                        else "; the remittances do not name the clinician on these lines"))
    if 0.48 <= ratio <= 0.52 and surgical_with_another >= 0.8:
        parts.append("Half the usual rate, with another procedure for the patient that day: how most payers pay a second procedure "
                     "(the multiple-procedure rule)")
    known = {k: v for k, v in states.items() if k in STATE_NAMES}
    uknown = {k: v for k, v in usual_states.items() if k in STATE_NAMES}
    kn, un = sum(known.values()), sum(uknown.values())
    if kn >= 3 and un >= 3:
        for st in sorted(known, key=known.get, reverse=True):
            here, there = known[st] / n, uknown.get(st, 0) / un
            # most of that office's own lines are at this amount: its rate, not the payer's usual rate missed on them
            if here >= 0.8 and here - there >= 0.4 and known[st] / (known[st] + uknown.get(st, 0)) >= 0.8:
                parts.append(f"{known[st]} of the {n} lines are {STATE_NAMES[st]} visits, against {there:.0%} of the lines "
                             f"paid at the usual rate: probably that office's plan or fee schedule")
                break
    pn, upn = sum((plans or {}).values()), sum((usual_plans or {}).values())
    if pn >= 3 and upn >= 3:
        for plan, v in plans.most_common(3):
            if plan in ("", "(none)"):
                continue
            here, there = v / pn, usual_plans.get(plan, 0) / upn
            if here >= 0.8 and here - there >= 0.25 and v / (v + usual_plans.get(plan, 0)) >= 0.8:
                parts.append(f"{v} of the {pn} lines are under the plan \"{plan}\", which is on {there:.0%} of the lines paid at the usual rate")
                break
    if first_dos and usual_last_dos and first_dos > usual_last_dos:
        parts.append(f"Every line is from {first_dos} on, after the last visit paid at the usual rate ({usual_last_dos}): a rate change")
    elif last_dos and usual_first_dos and last_dos < usual_first_dos:
        parts.append(f"Every line is from before {usual_first_dos}, the first visit paid at the usual rate: the payer's earlier rate, "
                     "raised since (a rate change); check the date the new rate took effect")
    if not parts:
        return "", False
    parts.append("Check it against the contract before appealing; if the payer is wrong, one request can fix every claim listed "
                 "that is still inside its filing window")
    return ". ".join(parts) + ".", True


# Items that stand for many claims, each with its own filing deadline (evidence 'deadlines'): a routine-rate review and a
# repricing request. Their amount is every claim's; the deadline views count each claim by its own date (pattern_windows).
PATTERN_SIGNALS = ("SECOND_RATE", "OLD_FEE_SCHEDULE")
PATTERN_SQL = "('" + "','".join(PATTERN_SIGNALS) + "')"


def pattern_windows(conn: sqlite3.Connection, statuses: tuple = ("open",)) -> list[tuple]:
    """Every claim filing deadline of the findings that stand for many claims (PATTERN_SIGNALS: a routine-rate review, a
    repricing request) with these statuses, as (finding_id, payer_group, procedure_code, deadline, lines, cents of
    shortfall). Such a finding stands for many claims with their own deadlines, so the deadline views (summary, the due
    filter, Progress, the deadline alert, the weekly report) count its claims rather than the single date on its row. A
    deadline of None is a line whose window is unknown."""
    q = ",".join("?" * len(statuses))
    out = []
    for fid, grp, code, ev in conn.execute(f"""SELECT finding_id, payer_group, procedure_code, evidence FROM findings
                                              WHERE signal IN {PATTERN_SQL} AND COALESCE(stale,0)=0 AND status IN ({q})""", tuple(statuses)):
        try:
            dl = (json.loads(ev) if ev else {}).get("deadlines") or []
        except ValueError:
            dl = []
        out += [(fid, grp, code, x[0], x[1] or 0, x[2] or 0) for x in dl if isinstance(x, list) and len(x) == 3]
    return out


_EMA_CLAIM_ID = re.compile(r"([A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}")


GAVE_UP_IN_EMA = ("WRITE_OFF", "TRANSFER_RESPONSIBILITY")


_REASON = re.compile(r"^\s*(CO|PR|OA|PI|CR)?\s*-?\s*([A-Z0-9]{1,4})\s*$", re.I)


def reason_of(carc: str | None) -> tuple[str, str]:
    """A reason as EMA's export writes it ('CO-45', 'CO - 45', 'co45', '45') as its group and code: ('CO', '45')."""
    m = _REASON.match(carc or "")
    return ((m.group(1) or "").upper(), m.group(2).upper()) if m else ("", (carc or "").strip().upper())


def written_off_of(handling: list[dict], cap: int | None = None, own: str | None = None, sign: int = 1) -> tuple[int | None, str | None]:
    """What the biller gave up in EMA among these ERA adjustment rows (link.line_handling): the rows written off, or moved on
    to the patient or the next payer, on the payer's own reasons for not paying. EMA's routine posting of a contractual
    reduction (CO-45 and the other IGNORE_CARCS) is not giving anything up, unless that reason is the finding's own (`own`:
    CO-59 for a multiple-procedure reduction), and a patient-responsibility amount (PR) is the patient's share: neither
    counts. A row counts once though several exports repeat it (one without its transaction date adds nothing to the same
    row with one), a reversal nets out (`sign` is the direction EMA posts a write-off in; a reversal alone counts nothing),
    and the total is capped at the money in dispute. Returns the amount and the newest date among the rows counted."""
    own_code = reason_of(own)[1] if own else None
    rows: dict[tuple, dict] = {}
    for h in handling:
        if (h.get("adjustment_status") or "").upper() not in GAVE_UP_IN_EMA or not h.get("adjustments_cents"):
            continue
        group, code = reason_of(h.get("carc"))
        if group == "PR" or (code in IGNORE_CARCS and code != own_code):
            continue
        same = (h.get("cpt"), h.get("service_date"), group, code, h["adjustments_cents"])
        rows.setdefault(same, {})[h.get("transaction_date") or ""] = h["adjustments_cents"]
    net: dict[tuple, list] = {}
    for (cpt, dos, group, code, _cents), by_date in rows.items():
        if len(by_date) > 1:
            by_date.pop("", None)           # the same row read from a layout without its transaction date
        n = net.setdefault((cpt, dos, group, code), [0, ""])
        for when, cents in by_date.items():
            n[0] += cents
            n[1] = max(n[1], when)
    given = {k: v for k, v in net.items() if v[0] * (sign or 1) > 0}
    total = sum(abs(v[0]) for v in given.values())
    if not total:
        return None, None
    if cap and cap > 0:
        total = min(total, cap)
    return total, max((v[1] for v in given.values()), default="") or None


def bill_of(pcn: str | None) -> str:
    """EMA claim ids are a bill id (two letters, two digits and five more characters) plus a submission suffix (C017,
    C024, ...). Any other claim id is its own bill: ids such as a date and a sequence number share their first characters
    across patients."""
    m = _EMA_CLAIM_ID.fullmatch(pcn or "")
    return m.group(1) if m else (pcn or "")


def sub_no(pcn: str | None) -> int:
    """A claim id's submission number (C017 -> 17), 0 when it has none."""
    m = _SUBMISSION.search(pcn or "")
    return int(m.group(1)) if m else 0


component_of = _codes.component_of        # the part of a service a line bills (codes.component_of: ema_checks reads it too)


def item_part(x: tuple) -> str:
    """The part of the service a never-answered claim's item bills (code, date, charge, units, item id, modifiers): its
    modifiers' (component_of), '' when it has none."""
    return component_of(x[5] if len(x) > 5 else None)


NO_CHARGE_CENTS = 100       # a line billed under $1 (a $0 line, or a penny EMA puts on a post-operative visit) is owed nothing
# modifiers that make a second line of the same code and day another service (the other side, a distinct site or session, a
# repeat): two bills of one patient's visit are one service only when these agree (the one-visit rule, answers-1)
DISTINCT_MODIFIERS = frozenset({"RT", "LT", "50", "59", "XE", "XS", "XP", "XU", "76", "77", "91", "E1", "E2", "E3", "E4", "FA", "F1",
                                "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "TA", "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"})


def _visit_line(x: tuple) -> tuple:
    """One service of a never-answered claim's item (code, date, charge, units, item id, modifiers), as the one-visit rule
    compares two bills' lines: its date, code, the part it bills (26, TC or the whole) and the modifiers that make it a
    separate service."""
    mods = x[5] if len(x) > 5 else None
    if isinstance(mods, str):
        mods = re.findall(r"[A-Za-z0-9]{2}", mods)
    return (x[1], x[0], item_part(x), tuple(sorted({str(m).upper() for m in mods or ()} & DISTINCT_MODIFIERS)))


def _no_charge(charge) -> bool:
    """A line whose known charge is $0 or a token under $1: nothing is owed on it (a charge that is not known is not)."""
    return charge is not None and charge < NO_CHARGE_CENTS


_OWN = "own"        # an answer on the claim's own EMA claim: whatever part each of its lines bills, it is that line's


_COMPANY_ALIASES = {"unitedhealthcare": "uhc", "united": "uhc", "uhc": "uhc", "aarp": "uhc", "optum": "uhc", "bcbs": "anthem",
                    "blue": "anthem"}


def payer_company(group: str | None) -> str:
    """The company behind a payer group, the way an 837 names a payer (one payer id for all of a company's products):
    Anthem / BCBS and Anthem / BCBS Medicare Advantage are one company; a group of many payers (the Medicare supplement
    carriers, the self-funded plans' administrators) is only itself; the VA's network is the VA's."""
    g = " ".join(str(group or "").split())
    if not g:
        return ""
    if g == VA_GROUP[0]:
        return "va"
    if g in MANY_PAYER_GROUPS:
        return "=" + g.lower()
    first = re.split(r"[^a-z0-9]+", g.lower())[0]
    if first in ("medicaid", "medicare"):
        # a state's Medicaid, the Medicaid plans no group names (United Healthcare's community plan...), a Medicare
        # contractor: each is only itself
        return "=" + g.lower()
    return _COMPANY_ALIASES.get(first, first)


def _one_payer(a_id: str | None, a_name: str | None, b_id: str | None, b_name: str | None) -> bool:
    """Whether two 837 sends went to one payer: the same payer id, else the same company by the payer's name (payer_company:
    an id the clearinghouse changed, a send with no id). A send that names no payer, or only an unknown one, matches none."""
    ia, ib = str(a_id or "").strip(), str(b_id or "").strip()
    if ia and ia == ib:
        return True
    ca = payer_company(normalize_payer(a_name)[0]) if a_name else ""
    cb = payer_company(normalize_payer(b_name)[0]) if b_name else ""
    return bool(ca) and ca != "unknown" and ca == cb


def week_of(day: str | None) -> str | None:
    """The Monday of a date's week (ISO), or None for a date no calendar has."""
    try:
        d = date.fromisoformat(str(day)[:10])
    except (TypeError, ValueError):
        return None
    return (d - timedelta(days=d.weekday())).isoformat()


def settled_line_sql(a: str) -> str:
    """SQL that is true for an EMA claim line (ema_lines, alias `a`) EMA shows settled by its payer: nothing open, the
    payer's contractual write-down posted, no write-off of the practice's own, and money paid on it (by insurance, or by
    the patient: the allowed amount was theirs). A line with nothing paid on it (a G2211 the payer allowed nothing for,
    or one whose allowed amount was the patient's and was adjusted off) is settled too when another line of the same EMA
    claim was paid so: the payer processed the claim."""
    return (f"(COALESCE({a}.balance, 0) <= 0 AND COALESCE({a}.charges, 0) > 0 AND COALESCE({a}.contractual, 0) > 0 "
            f"AND COALESCE({a}.noncontractual, 0) = 0 AND (COALESCE({a}.ins_paid, 0) + COALESCE({a}.pat_paid_joined, 0) > 0 "
            f"OR EXISTS (SELECT 1 FROM ema_lines sib WHERE sib.link = {a}.link "
            f"AND (sib.svc <> {a}.svc OR sib.cpt <> {a}.cpt) AND COALESCE(sib.balance, 0) <= 0 "
            f"AND COALESCE(sib.ins_paid, 0) + COALESCE(sib.pat_paid_joined, 0) > 0)))")


# The open items one run put on the list and took off it, item by item (guard audit L3-9): the swing alarm (alerts.py)
# reads them beside the per-category totals, which net out when a run replaces many items with as many others. Open means
# what someone can still work: To do, Started, Sent; the context rows and stale rows are not work.
SWING_OPEN_STATUSES = ("open", "in_progress", "appealed")


def _open_items(rows) -> dict[str, tuple[str, int]]:
    """{finding_id: (category, shortfall cents)} of the open items among `rows` (dicts of findings rows)."""
    return {r["finding_id"]: (r.get("category") or "", r.get("shortfall_cents") or 0) for r in rows
            if r.get("status") in SWING_OPEN_STATUSES and r.get("category") != "info" and not r.get("stale")}


def record_moves(conn: sqlite3.Connection, run_id: str, before: dict[str, tuple[str, int]]) -> None:
    """Write swing_moves for one run: by category, the open items that appeared and those that vanished since `before` (the
    open items when the run's save began), with their dollars. The changes people make between runs are already in
    `before`, so what is counted is the run's own work (and the reasons hidden for a payer it applied after the save)."""
    conn.row_factory = sqlite3.Row
    after = _open_items(dict(r) for r in conn.execute("SELECT finding_id, category, status, shortfall_cents, stale FROM findings"))
    by: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for fid in after.keys() - before.keys():
        cat, cents = after[fid]
        by[cat][0] += 1
        by[cat][1] += cents
    for fid in before.keys() - after.keys():
        cat, cents = before[fid]
        by[cat][2] += 1
        by[cat][3] += cents
    conn.execute("DELETE FROM swing_moves WHERE run_id=?", (run_id,))
    conn.executemany("INSERT INTO swing_moves (run_id, category, appeared_n, appeared_cents, vanished_n, vanished_cents) VALUES (?,?,?,?,?,?)",
                     [(run_id, cat, *v) for cat, v in by.items()])


class Detector:
    def __init__(self, store: Store, run_id: str | None = None, today: str | None = None):
        self.store = store
        self.conn = store.conn
        ensure_schema(self.conn)
        self.run_id = run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
        self.now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # the day the run judges windows and freshness by (EMA's checks, the states): the practice's day, as the pages see it
        # (runtime.practice_today: from 7 PM Eastern the UTC date is already tomorrow; review mr-8)
        from . import runtime
        self.today = (today or runtime.practice_today())[:10]
        # EMA's A/R and Production reports: whether the store has them, the line outcome of each item (computed once), and
        # the steps that failed (recorded, never fatal: the remittance items are still produced)
        self.ema_on = False
        self.ema_outcomes: dict[str, dict] = {}
        self.ema_errors: list[dict] = []
        self.ema_counts: Counter = Counter()
        self._export_claims: set[str] = set()       # claim ids the weekly exports' path listed (that path wins)
        self._export_links: set[int] = set()        # their EMA claim numbers, as claim_answers tied them
        self._837_claims: set[str] = set()          # claim ids the 837s' pass judged (run_837_open_claims: that pass wins)
        self.findings: dict[str, dict] = {}
        self.second_rates: dict[tuple, list] = defaultdict(list)     # (baseline key, amount) -> lines the payer routinely allowed less
        self.yard_by_key: dict[tuple, dict] = {}                      # baseline key -> the yardstick those lines were measured by
        self.provider_gaps: dict[tuple, list] = defaultdict(list)    # (payer group, NPI) -> lines at a clinician's unexplained discount
        self.old_schedule: dict[tuple, list] = defaultdict(list)     # (payer group, year) -> lines paid on the year before's schedule
        self.own_plans: dict[str, dict] = {}                          # payer group -> its plans with a schedule of their own
        self.np_state: dict[tuple, dict] = {}                         # (rate group, year) -> {state: PA/NP share} where it differs
        self._day_state: tuple | None = None                          # _office_state's (clinician days, EMA locations) by state
        self.yard_obs: dict[tuple, Counter] = {}
        # the payer's own rates (_build_yardsticks), empty until build_baselines has run
        self.yard: dict[tuple, dict] = {}
        self.prov_rates: dict[tuple, dict] = {}
        self.np_ratio: dict[tuple, float] = {}
        self.medicare_yard: dict[tuple, int] = {}
        self.comp_split: dict[tuple, float] = {}
        self.class_typical: dict[tuple, dict] = {}
        self.class_few: dict[tuple, dict] = {}
        self.charge_ratio: dict[tuple, tuple] = {}
        self.schedule_ratio: dict[tuple, tuple] = {}
        self.kind_of_group: dict[str, str] = {}
        self.kinds: dict[str, str] = {}
        self.ema_paid: dict[tuple, tuple] = {}                        # (claim id, code, date) -> (insurance payments in EMA, export)
        self.ema_patient: dict[str, str] = {}                         # claim id -> EMA patient id (full charge layout only)
        self.payers_behind: list[dict] = []                           # PAYERS_BEHIND_META, from run_ema_open_claims
        self.no_era: list[dict] = []                                  # NO_ERA_META, from run_837_open_claims
        self.worked_underpaid: set[str] = {r[0] for r in store.conn.execute(
            """SELECT line_key FROM findings WHERE signal='UNDERPAID_VS_OWN_RATE' AND line_key IS NOT NULL
               AND (status != 'open' OR notes IS NOT NULL OR appealed_at IS NOT NULL)""")}       # their own finding is kept, never folded
        # A routine-rate finding someone worked keeps exactly the lines it had when they worked it, whatever the rules now say
        # about its amount or reason; lines paid at that amount later go to a new finding.
        # whole-claim denials already listed: a bill's finding stays on the submission that has it (and the work on it)
        self.prev_claims: dict[str, dict] = {}
        manual_at = dict(store.conn.execute("""SELECT finding_id, MAX(at) FROM finding_events WHERE kind='manual' GROUP BY finding_id""").fetchall()) \
            if store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='finding_events'").fetchone() else {}
        for fid, ck, st, notes, appealed, auto, before in store.conn.execute(
                """SELECT finding_id, claim_key, status, notes, appealed_at, auto_note, prev_status FROM findings
                   WHERE signal='CLAIM_DENIED_UNPAID' AND claim_key IS NOT NULL AND COALESCE(stale,0)=0"""):
            # worked: something a person did (an automatic closure is not, but what they did before it still is); changed:
            # when they last changed its status
            worked = bool(notes) or bool(appealed) or st in ("in_progress", "appealed", "lost", "dismissed") or (st == "resolved" and not auto) \
                or (st == "resolved" and bool(auto) and before in ("in_progress", "appealed"))
            self.prev_claims[ck] = {"worked": worked, "changed": manual_at.get(fid) or "", "status": st}
        self.closed_lines: set[str] = {r[0] for r in store.conn.execute(
            "SELECT DISTINCT line_key FROM findings WHERE line_key IS NOT NULL AND status IN ('dismissed','lost')")}
        # a clinician's discount someone worked keeps every claim it had in its dollars (emit_provider_gaps)
        self.worked_gaps: set[str] = {r[0] for r in store.conn.execute(
            """SELECT finding_id FROM findings WHERE signal='PROVIDER_RATE_GAP'
               AND (status != 'open' OR notes IS NOT NULL OR appealed_at IS NOT NULL)""")}
        # a repricing request someone worked keeps the claims it was sent with, as a worked routine-rate review does: its lines
        # are kept with it whatever the rules now say, and services found since go to a request of their own (emit_old_schedules)
        self.old_known: set[str] = set()                # every repricing request on file (one all repriced since closes)
        self.worked_old: dict[str, dict] = {}
        self.line_to_old: dict[str, str] = {}
        self.claim_to_old: dict[tuple, str] = {}        # (claim id, code, date, allowed): a remittance delivered again
        self.old_worked_lines: dict[str, list] = defaultdict(list)
        self.old_gone: dict = defaultdict(list)          # request (its id) or (payer group, year) -> its lines taken back since
        for fid, st, notes, appealed, ev in store.conn.execute(
                "SELECT finding_id, status, notes, appealed_at, evidence FROM findings WHERE signal='OLD_FEE_SCHEDULE'"):
            self.old_known.add(fid)
            if st == "open" and notes is None and appealed is None:
                continue
            e = json.loads(ev) if ev else {}
            if e.get("line_keys") and e.get("key_parts"):
                self.worked_old[fid] = e
                for lk in e["line_keys"]:
                    self.line_to_old.setdefault(lk, fid)
                for cl_ in e.get("claims") or []:
                    if isinstance(cl_, list) and len(cl_) >= 4:
                        self.claim_to_old.setdefault((cl_[0], cl_[2], cl_[1], cl_[3]), fid)
        self.worked_patterns: dict[str, dict] = {}
        self.line_to_pattern: dict[str, str] = {}
        self.claim_to_pattern: dict[tuple, str] = {}    # (claim id, code, date, allowed): a remittance delivered again has new line keys
        self.pattern_lines: dict[str, list] = defaultdict(list)
        for fid, code, ev in store.conn.execute("""SELECT finding_id, procedure_code, evidence FROM findings WHERE signal='SECOND_RATE'
                                                   AND (status != 'open' OR notes IS NOT NULL OR appealed_at IS NOT NULL)"""):
            e = json.loads(ev) if ev else {}
            if e.get("line_keys") and e.get("key_parts"):
                self.worked_patterns[fid] = e
                for lk in e["line_keys"]:
                    self.line_to_pattern.setdefault(lk, fid)
                for cl_ in e.get("claims") or []:
                    if isinstance(cl_, list) and len(cl_) >= 3:
                        self.claim_to_pattern.setdefault((cl_[0], code, cl_[1], cl_[2]), fid)

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        c = self.conn
        c.row_factory = sqlite3.Row
        # only the columns the rules read, and repeated strings shared: this list is the bulk of the memory a run needs
        cur = c.execute("""
            SELECT l.line_key, l.claim_key, l.transaction_key, l.procedure_code, l.modifiers, l.charge_cents, l.paid_cents, l.paid_units,
                   l.original_procedure_code, l.original_modifiers, l.original_units,
                   COALESCE(l.service_date,
                            CASE WHEN l.service_period_start = COALESCE(l.service_period_end, l.service_period_start) THEN l.service_period_start END,
                            CASE WHEN cl.statement_from = COALESCE(cl.statement_to, cl.statement_from) THEN cl.statement_from END) AS service_date,
                   l.allowed_cents,
                   l.patient_control_number, l.claim_status_code, l.payer_name, l.payer_id, l.payment_date, l.rendering_provider_npi,
                   cl.claim_filing_indicator, cl.contract_class, cl.facility_type_code, cl.rendering_provider_name AS rp_name,
                   t.payment_date AS txn_payment_date, t.trace_number, NULLIF(TRIM(cl.group_number), '') AS group_number
            FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key
            JOIN transactions t ON t.transaction_key = l.transaction_key
            WHERE t.superseded = 0
        """)
        names = [d[0] for d in cur.description]
        assert tuple(names) == LINE_COLUMNS
        shared = {"procedure_code", "modifiers", "original_procedure_code", "original_modifiers", "service_date", "claim_status_code",
                  "payer_name", "payer_id", "payment_date", "rendering_provider_npi", "claim_filing_indicator", "contract_class",
                  "facility_type_code", "rp_name", "txn_payment_date", "trace_number", "claim_key", "transaction_key",
                  "patient_control_number", "group_number"}
        share_at = [i for i, n in enumerate(names) if n in shared]
        pool: dict[str, str] = {}
        self.lines = []
        while True:
            chunk = cur.fetchmany(20000)
            if not chunk:
                break
            for r in chunk:
                r = list(r)
                for i in share_at:
                    v = r[i]
                    if v is not None:
                        r[i] = pool.setdefault(v, v)
                self.lines.append(Line(r))
        # the derived values repeat across lines too: one object each, shared (the modifier lists are only ever read)
        mods_of: dict = {}
        kind_of: dict = {}
        for l in self.lines:
            m = l["modifiers"]
            if m not in mods_of:
                mods_of[m] = json.loads(m) if m else []
            l["modifiers_list"] = mods_of[m]
            y = (l.get("service_date") or l.get("payment_date") or "")[:4]
            l["year"] = pool.setdefault(y, y)
            pk = (l["payer_name"], l["claim_filing_indicator"])
            if pk not in kind_of:
                kind_of[pk] = tuple(pool.setdefault(v, v) for v in payer_group_kind(*pk))
            l["payer_group"], l["payer_kind"] = kind_of[pk]
            l["pricing_mod"] = next((m for m in l["modifiers_list"] if m in PRICING_MODIFIERS), None)
            l["pos"] = l.get("facility_type_code")
            l["pos_class"] = pos_class(l["pos"])
            plan = (l.get("contract_class") or "").strip() or None
            l["plan"] = pool.setdefault(plan, plan) if plan else None
            mo = (l.get("payment_date") or l.get("txn_payment_date") or "")[:7]
            l["month"] = pool.setdefault(mo, mo)
        del mods_of, kind_of
        adj = defaultdict(list)
        same: dict[tuple, tuple] = {}       # (group, reason, amount) repeats across lines: the same fee-schedule cut
        for r in c.execute("SELECT line_key, group_code, reason_code, amount_cents FROM adjustments WHERE line_key IS NOT NULL"):
            t = (r[1], r[2], r[3] or 0)
            adj[r[0]].append(same.setdefault(t, t))
        del same
        self.adj = adj
        rem = defaultdict(list)
        for r in c.execute("SELECT line_key, code FROM remarks WHERE line_key IS NOT NULL"):
            rem[r[0]].append(pool.setdefault(r[1], r[1]))
        self.rem = rem
        del pool
        self.claim_adj = defaultdict(list)
        for r in c.execute("SELECT claim_key, group_code, reason_code, amount_cents FROM adjustments WHERE level='claim'"):
            self.claim_adj[r["claim_key"]].append((r["group_code"], r["reason_code"], r["amount_cents"] or 0))
        self.claim_rem = defaultdict(list)
        for r in c.execute("SELECT claim_key, code FROM remarks WHERE level='claim'"):
            self.claim_rem[r["claim_key"]].append(r["code"])
        # the amount allowed where AMT*B6 only repeats the payment beside a patient share (a payer that reports what it paid
        # as allowed): the charge less the payer's own adjustments (CO, OA, PI), which is what it allowed for the patient and
        # itself together (the owner's rule on rate evidence, section 3 fix 5)
        for l in self.lines:
            alw, pd = l["allowed_cents"], l["paid_cents"] or 0
            if alw and alw == pd and l["charge_cents"]:
                adj_l = adj.get(l["line_key"]) or ()
                if any(g == "PR" and a > 0 for g, _rc, a in adj_l):
                    priced = l["charge_cents"] - sum(a for g, _rc, a in adj_l if g in ("CO", "OA", "PI"))
                    if priced > alw:
                        l["allowed_cents"] = priced
        # the units a line billed where its remittance leaves SVC05 empty (or 0) and SVC07 empty: the 837 line, else EMA's
        # charge line, of the claim id, code and date (one with the same charge first). Only counts above one are kept
        # (_units_from_bill reads them only for a line whose adjustments account for its whole charge)
        self.bill_units: dict[tuple, dict] = {}
        have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sources = []
        if "lines_837" in have:
            sources.append("SELECT patient_control_number, service_date, procedure_code, charge_cents, units FROM lines_837 WHERE units > 1")
        if "ema_rows" in have:
            sources.append("""SELECT claim_id, service_date, cpt, charges_cents, units FROM ema_rows
                              WHERE family='charge_lines' AND activity_type='Charge' AND units > 1""")
        for q in sources:
            for pcn, dos, code, chg, units in c.execute(q):
                if pcn and dos and code and units and units <= MAX_UNITS:
                    k = self.bill_units.setdefault((pcn, dos, code), {})
                    k.setdefault(abs(chg or 0), abs(units))
        self.lines_by_claim: dict[str, list[dict]] = defaultdict(list)
        for l in self.lines:
            self.lines_by_claim[l["claim_key"]].append(l)
        # the payer's answers to claims the practice voided (CLP09 8): no line or claim item is made from them, as nothing in
        # them is owed (trace finding T-N5). Their lines still count in the ledger: a void's reversal takes its payment back
        self.void_claims: set[str] = {r[0] for r in c.execute(
            """SELECT cl.claim_key FROM claims cl JOIN transactions t ON t.transaction_key = cl.transaction_key
               WHERE t.superseded = 0 AND TRIM(cl.claim_frequency_code) = '8'""")}
        self.standing = self._build_ledger()
        self.partly_paid: dict[str, tuple] = {}         # line_key -> (first later payment, units paid, units billed)
        self.used_payments: set[tuple] = set()          # payments already counted as recovered on a finding (EMA's, by date)
        # an 835 payment counted as recovered, by its paying line: [units of it used, cents credited from it]. Each unit pays
        # one finding (trace finding T-C5: keyed by date, trace, claim and code, a second line paid on the same claim
        # recorded $0)
        self.used_units: dict[str, list] = {}
        paid_lines: Counter = Counter()
        for l in self.lines:
            if l["line_key"] in self.standing and l.get("claim_status_code") in ("1", "19") and l.get("service_date"):
                paid_lines[(bill_of(l["patient_control_number"]), l.get("procedure_code"), l["service_date"])] += 1
        groups, multi = Counter(), Counter()
        for (_b, code, _d), n in paid_lines.items():
            groups[code] += 1
            multi[code] += n > 1
        self.once_codes = {code for code, g in groups.items() if g >= 20 and multi[code] <= 0.015 * g}     # paid at most once a day
        del paid_lines
        # every payment that still stands, by bill, code and date: (payment date, trace, amount standing, claim status, claim id, units,
        # payer, date of service, the paying line, its modifiers)
        self.paid_events: dict[tuple, list[tuple]] = defaultdict(list)
        self.paid_by_code: dict[tuple, list[tuple]] = defaultdict(list)     # (bill, code) -> the same events, undated lines too
        for l in self.lines:
            amt = self.standing.get(l["line_key"])
            if amt:
                e = (l.get("payment_date") or l.get("txn_payment_date") or "", l.get("trace_number"), amt, l.get("claim_status_code"),
                     l["patient_control_number"], self.paid_units_of(l), l.get("payer_id") or l.get("payer_name"), l.get("service_date"),
                     l["line_key"], l["modifiers"])
                self.paid_by_code[(bill_of(l["patient_control_number"]), l["procedure_code"])].append(e)
                if l.get("service_date"):
                    self.paid_events[(bill_of(l["patient_control_number"]), l["procedure_code"], l["service_date"])].append(e)
        # EMA links and biller handling (built by link.build_links when EMA exports are loaded)
        self.links: dict[str, dict] = {}
        self.handling: dict[str, list[dict]] = defaultdict(list)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "line_links" in tables:
            for r in c.execute("""SELECT ll.line_key, ll.state, ll.bill_item_id, ll.ema_cpt, ll.ema_modifiers, ll.ema_units, ll.ema_charges_cents,
                                         ll.ema_claim_status, e.location, e.icd10
                                  FROM line_links ll LEFT JOIN ema_rows e ON e.row_fingerprint = ll.ema_row_fingerprint
                                  WHERE ll.state IN ('exact_composite','fallback_candidate')"""):
                self.links[r["line_key"]] = dict(r)
        # practice location: from any EMA row for the claim id, else the rendering provider's usual site when one site dominates
        self.claim_location: dict[str, str] = {}
        self.npi_location: dict[str, str] = {}
        if "ema_rows" in tables:
            loc_votes: dict[str, Counter] = defaultdict(Counter)
            for r in c.execute("SELECT claim_id, location, COUNT(*) n FROM ema_rows WHERE claim_id IS NOT NULL AND location IS NOT NULL GROUP BY 1,2"):
                loc_votes[r[0]][r[1]] += r[2]
            self.claim_location = {k: top_of(v)[0] for k, v in loc_votes.items()}
            npi_votes: dict[str, Counter] = defaultdict(Counter)
            seen_claims: set[str] = set()
            for l in self.lines:
                loc = self.claim_location.get(l.get("patient_control_number") or "")
                if loc and l.get("rendering_provider_npi") and l["claim_key"] not in seen_claims:
                    seen_claims.add(l["claim_key"])
                    npi_votes[l["rendering_provider_npi"]][loc] += 1
            for npi, v in npi_votes.items():
                top, n = top_of(v)
                if sum(v.values()) >= 20 and n / sum(v.values()) >= 0.85:
                    self.npi_location[npi] = top
        self._fill_setting(tables)
        # what was billed, from the 837 claim files and the EMA charge lines: (claim id, date of service) -> {code: source}
        self.billed: dict[tuple, dict[str, str]] = defaultdict(dict)
        if "lines_837" in tables:
            for r in c.execute("SELECT patient_control_number, service_date, procedure_code FROM lines_837 WHERE procedure_code IS NOT NULL"):
                self.billed[(r[0], r[1])].setdefault(r[2], "837 claim file")
        if "ema_rows" in tables:
            for r in c.execute("""SELECT claim_id, service_date, cpt FROM ema_rows
                                  WHERE family='charge_lines' AND activity_type='Charge' AND claim_id IS NOT NULL AND cpt IS NOT NULL"""):
                self.billed[(r[0], r[1])].setdefault(r[2], "EMA charge line")
        if "line_handling" in tables:
            for r in c.execute("SELECT line_key, cpt, service_date, carc, rarc, adjustment_status, adjustment_subtype, adjustments_cents, payments_cents, transaction_date FROM line_handling"):
                self.handling[r["line_key"]].append(dict(r))
            # the direction EMA posts a write-off in (its reversals go the other way): the sign most of its write-offs carry
            pos, neg = c.execute("""SELECT COALESCE(SUM(adjustments_cents > 0),0), COALESCE(SUM(adjustments_cents < 0),0) FROM line_handling
                                    WHERE adjustment_status IN ('WRITE_OFF','TRANSFER_RESPONSIBILITY')""").fetchone()
            self.wo_sign = -1 if neg > pos else 1
        if "ema_rows" in tables:
            # what EMA has posted from insurers on each billed line, as of the newest export that lists it (the full Encounter
            # and Charge Lines layout carries it). It is by claim, code and date, not by line or payer, so resolve_elsewhere
            # reads it only where the claim carries the code once on that date, and a whole claim needs each of its codes.
            when = "COALESCE(s.created_at, '')" if "ema_sources" in tables else "''"
            join = "LEFT JOIN ema_sources s ON s.sha256 = e.source_sha256" if "ema_sources" in tables else ""
            newest: dict[tuple, tuple] = {}
            for cid, cpt, dos, paid, src, at in c.execute(f"""SELECT e.claim_id, e.cpt, e.service_date, e.insurance_payments_cents, e.source_name, {when}
                                                             FROM ema_rows e {join}
                                                             WHERE e.family='charge_lines' AND e.activity_type='Charge' AND e.claim_id IS NOT NULL
                                                               AND e.insurance_payments_cents IS NOT NULL"""):
                k = (cid, cpt, dos)
                if k not in newest or (at, src or "") > newest[k][1:]:
                    newest[k] = (paid or 0, at, src or "")
            self.ema_paid = {k: (v[0], v[2]) for k, v in newest.items() if v[0] > 0}
            self.ema_patient = {cid: pat for cid, pat in c.execute("""SELECT claim_id, MAX(patient_id) FROM ema_rows WHERE family='charge_lines'
                                                                      AND claim_id IS NOT NULL AND COALESCE(patient_id,'') <> '' GROUP BY 1""")}
        # surgical rank within each claim: 1 = highest allowed surgical procedure (paid 100 percent), 2+ reduced
        for claim_lines in self.lines_by_claim.values():
            surg = [x for x in claim_lines if is_surgical(x.get("procedure_code")) and (x.get("allowed_cents") or 0) > 0]
            surg.sort(key=lambda x: -(x.get("allowed_cents") or 0))
            for i, x in enumerate(surg):
                x["surg_rank"] = i + 1
            for x in claim_lines:
                x.setdefault("surg_rank", 0)
                x["surg_count"] = len(surg)

    def _fill_setting(self, tables: set[str]) -> None:
        """The place of service of a line whose remittance leaves CLP08 empty (some payers never send it, so a surgical
        site's claims read as office claims and its own facility schedule as a cut of the office rate): the 837's place of
        service for the claim id, else that of the claim's EMA location where the remittances that do name a place of
        service show the location is one setting (90 percent of at least 20 claims: a surgery center). A place of service
        the remittance gives is never changed."""
        todo = [l for l in self.lines if not l.get("pos")]
        if not todo:
            return
        c = self.conn
        fac837: dict[str, str] = {}
        if "claims_837" in tables:
            # the claim's latest 837 says where it was billed from: a correction (frequency 7) over the claim first sent, then
            # the newest file (fix7c-r2-10: a first 837 with the wrong place of service and its correction tied the vote)
            want = {l.get("patient_control_number") for l in todo}
            latest: dict[str, tuple] = {}
            for pcn, pos, freq, made, key in c.execute(
                    """SELECT patient_control_number, TRIM(place_of_service), TRIM(frequency_code), bht_created, claim_837_key FROM claims_837
                       WHERE patient_control_number IS NOT NULL AND COALESCE(TRIM(place_of_service), '') <> ''"""):
                if pcn in want:
                    rank = (freq == "7", made or "", key or "")
                    if pcn not in latest or rank > latest[pcn][0]:
                        latest[pcn] = (rank, pos)
            fac837 = {pcn: v[1] for pcn, v in latest.items()}
            del latest, want
        loc_pos: dict[str, str] = {}
        if self.claim_location:
            per_loc: dict[str, Counter] = defaultdict(Counter)
            seen: set[str] = set()
            for l in self.lines:
                loc = self.claim_location.get(l.get("patient_control_number") or "")
                if loc and l.get("pos") and l["claim_key"] not in seen:
                    seen.add(l["claim_key"])
                    per_loc[loc][l["pos"]] += 1
            for loc, v in per_loc.items():
                top, n = top_of(v)
                if sum(v.values()) >= 20 and n >= 0.9 * sum(v.values()):
                    loc_pos[loc] = top
        if not fac837 and not loc_pos:
            return
        filled = Counter()
        for l in todo:
            pcn = l.get("patient_control_number") or ""
            pos = fac837.get(pcn) or loc_pos.get(self.claim_location.get(pcn) or "")
            if pos:
                l["pos"] = pos
                l["pos_class"] = pos_class(pos)
                filled[l["pos_class"]] += 1
        self.ema_counts["setting_from_837_or_location"] = sum(filled.values())

    def _office_state(self, l) -> str | None:
        """The state of the office a visit was in ('KY', 'IN'): the state of the Medicare carrier the same clinician's claims
        of that day went to (CGS J15 for Kentucky, WPS J8 for Indiana), else that of the claim's EMA location where the
        clinician days at it show one state (90 percent of at least 20). None when neither tells. A clinician-day with
        Medicare claims to both carriers (a morning in each office) is not known from the carrier, whatever order the files
        were read in (fix7c-r2-5): its claim's location decides, else None (the lower of the states' rates holds)."""
        got = self._day_state
        if got is None:
            seen: dict[tuple, str] = {}
            for x in self.lines:
                grp = x.get("payer_group") or ""
                if (grp.startswith("Medicare KY") or grp.startswith("Medicare IN")) and x.get("rendering_provider_npi") and x.get("service_date"):
                    k, st = (x["rendering_provider_npi"], x["service_date"]), grp[9:11]
                    seen[k] = st if seen.get(k, st) == st else ""          # "": claims to both carriers that day
            day = {k: st for k, st in seen.items() if st}
            del seen
            per_loc: dict[str, Counter] = defaultdict(Counter)
            if self.claim_location and day:
                for x in self.lines:
                    st = day.get((x.get("rendering_provider_npi"), x.get("service_date")))
                    loc = self.claim_location.get(x.get("patient_control_number") or "") if st else None
                    if loc:
                        per_loc[loc][st] += 1
            loc_state = {}
            for loc, v in per_loc.items():
                top, n = top_of(v)
                if sum(v.values()) >= 20 and n >= 0.9 * sum(v.values()):
                    loc_state[loc] = top
            got = self._day_state = (day, loc_state)
        day, loc_state = got
        st = day.get((l.get("rendering_provider_npi"), l.get("service_date")))
        if st:
            return st
        return loc_state.get(self.claim_location.get(l.get("patient_control_number") or "") or "") if loc_state else None

    # ------------------------------------------------------------------ baselines
    def build_baselines(self) -> None:
        by_npi: dict[tuple, Counter] = defaultdict(Counter)
        names: dict[str, str] = {}
        for l in self.lines:
            if l["payer_kind"] == "medicare" and l["procedure_code"] in ("99213", "99214", "99203") and l.get("claim_status_code") in ("1", "19") \
                    and (l.get("paid_cents") or 0) > 0 and l.get("allowed_cents") and not l.get("original_procedure_code") and l.get("rendering_provider_npi") \
                    and not l["pricing_mod"]:
                by_npi[(l["rendering_provider_npi"], l["year"], l["procedure_code"])][l["allowed_cents"]] += 1
                names[l["rendering_provider_npi"]] = l.get("rp_name") or ""
        top_by_year_code: dict[tuple, int] = {}
        for (npi, year, code), cnt in by_npi.items():
            mode = usual_amount(cnt)[0]
            top_by_year_code[(year, code)] = max(top_by_year_code.get((year, code), 0), mode)
        tier_votes: dict[str, list[float]] = defaultdict(list)
        for (npi, year, code), cnt in by_npi.items():
            mode = usual_amount(cnt)[0]
            top = top_by_year_code[(year, code)]
            if top:
                tier_votes[npi].append(mode / top)
        self.provider_tier: dict[str, str] = {}
        tier_rows = []
        for npi, ratios in tier_votes.items():
            r = sorted(ratios)[len(ratios) // 2]
            tier = "nonphysician" if r < 0.92 else "physician"
            self.provider_tier[npi] = tier
            tier_rows.append((npi, names.get(npi, ""), tier, round(r, 3), len(ratios)))
        name_votes: dict[str, Counter] = defaultdict(Counter)
        line_counts: Counter = Counter()
        for l in self.lines:
            l["provider_tier"] = self.provider_tier.get(l.get("rendering_provider_npi") or "", "unknown")
            if l.get("rendering_provider_npi"):
                name_votes[l["rendering_provider_npi"]][l.get("rp_name") or ""] += 1
                line_counts[l["rendering_provider_npi"]] += 1
        self.provider_name = {npi: best_provider_name(v) for npi, v in name_votes.items()}
        for l in self.lines:
            if l.get("rendering_provider_npi"):
                l["rp_name"] = self.provider_name.get(l["rendering_provider_npi"]) or l.get("rp_name")
        self.conn.execute("DELETE FROM providers")
        self.conn.executemany("INSERT OR REPLACE INTO providers (npi, name, tier, lines, location) VALUES (?,?,?,?,?)",
                              [(npi, self.provider_name.get(npi, ""), self.provider_tier.get(npi, "unknown"), n, self.npi_location.get(npi))
                               for npi, n in line_counts.items()])
        self.conn.execute("DELETE FROM provider_tiers")
        self.conn.executemany("INSERT INTO provider_tiers (npi, name, tier, ratio, n) VALUES (?,?,?,?,?)", tier_rows)

        self.fees = FeeTable()
        for l in self.lines:
            self.fees.observe(l["year"], l["procedure_code"], l["charge_cents"], l["paid_units"], bool(l.get("original_procedure_code")))
        self.fees.finalize()
        self.conn.execute("DELETE FROM fee_table")
        self.conn.executemany("INSERT OR REPLACE INTO fee_table (year, code, charge_cents, share) VALUES (?,?,?,?)",
                              [(r["year"], r["code"], r["charge_cents"], r["share"]) for r in self.fees.to_rows()])

        # payer rates from clean, full-price lines: primary claims, one unit, no code change, no pricing modifier,
        # and (for surgical codes) the highest-ranked procedure on the claim so multiple-procedure reductions do not pollute the mode
        counts: dict[tuple, Counter] = defaultdict(Counter)
        self.kinds: dict[str, str] = {}
        for l in self.lines:
            if l.get("claim_status_code") not in ("1", "19") or (l.get("paid_cents") or 0) <= 0 or not l.get("allowed_cents"):
                continue
            if l.get("original_procedure_code") or l.get("paid_units") not in (None, 1, 1.0) or l["pricing_mod"]:
                continue
            if l["surg_rank"] > 1:
                continue
            if any(rc in ("59", "B10", "97", "236", "234", "150", "186") for _g, rc, _a in self.adj.get(l["line_key"], [])):
                continue
            if self._same_day_policy_claim(l):
                continue
            l["clean"] = True
            counts[(l["payer_group"], l["year"], l["procedure_code"], l["provider_tier"], l["pos_class"])][l["allowed_cents"]] += 1
            self.kinds[l["payer_group"]] = l["payer_kind"]
        self.rates: dict[tuple, dict] = {}
        market_counts: dict[tuple, Counter] = defaultdict(Counter)
        for key, cnt in counts.items():
            n = sum(cnt.values())
            market_counts[(key[1], key[2], key[3], key[4])].update(cnt)
            if n < 5:
                continue
            mode, mode_n = usual_amount(cnt)
            vals = sorted(cnt.elements())
            self.rates[key] = {"mode": mode, "median": vals[len(vals) // 2], "p10": vals[int(0.1 * (len(vals) - 1))],
                               "p90": vals[int(0.9 * (len(vals) - 1))], "n": n, "share": mode_n / n, "key": key}
        self.rate_counts = counts       # how often each amount was allowed: tells a payer's routine second rate from a one-off
        # cross-payer typical allowed (median) per year, code and tier: the last-resort expectation
        self.market: dict[tuple, dict] = {}
        for key, cnt in market_counts.items():
            n = sum(cnt.values())
            if n >= 10:
                vals = sorted(cnt.elements())
                self.market[key] = {"median": vals[len(vals) // 2], "n": n}
        self.medicare: dict[tuple, int] = {}
        for (grp, year, code, tier, pos), r in self.rates.items():
            if self.kinds.get(grp) == "medicare" and r["n"] >= 10:
                key = (year, code, tier, pos)
                if key not in self.medicare or grp.startswith("Medicare KY"):
                    self.medicare[key] = r["mode"]
        self.conn.execute("DELETE FROM payer_rates")
        rows = []
        for (grp, year, code, tier, pos), r in self.rates.items():
            med = self.medicare.get((year, code, tier, pos))
            rows.append((grp, self.kinds.get(grp, "other"), year, code, tier, r["mode"], r["median"], r["p10"], r["p90"], r["n"],
                         round(r["share"], 3), med, round(r["mode"] / med, 4) if med else None, pos))
        self.conn.executemany("""INSERT OR REPLACE INTO payer_rates (payer_group, payer_kind, year, code, tier, mode_cents, median_cents, p10_cents,
                                 p90_cents, n, mode_share, medicare_cents, pct_of_medicare, pos_class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        self._build_yardsticks()

        # each payer's own multiple-procedure practice: typical allowed as a share of its usual rate for the 2nd and for later procedures
        ratios: dict[tuple, list[float]] = defaultdict(list)
        for l in self.lines:
            rank = l.get("surg_rank", 0)
            code = l.get("procedure_code")
            if rank < 2 or not is_surgical(code) or code in ADD_ON_CODES or l.get("claim_status_code") not in ("1", "19"):
                continue
            if (l.get("paid_cents") or 0) <= 0 or not l.get("allowed_cents") or l.get("original_procedure_code") or l["pricing_mod"] \
                    or l.get("paid_units") not in (None, 1, 1.0):
                continue
            r = self.rates.get((l["payer_group"], l["year"], code, l["provider_tier"], l["pos_class"]))
            if r and r["n"] >= 10 and r["mode"]:
                ratios[(l["payer_group"], 2 if rank == 2 else 3)].append(l["allowed_cents"] / r["mode"])
        self.mppr: dict[tuple, dict] = {}
        for key, vals in ratios.items():
            if len(vals) < 15:
                continue
            med = sorted(vals)[len(vals) // 2]
            snap = min((1.0, 0.75, 0.5, 0.25), key=lambda p: abs(p - med))
            if abs(snap - med) <= 0.08:
                self.mppr[key] = {"pct": snap, "n": len(vals), "median": round(med, 3)}
        from . import archive           # unit E: a year the archive took keeps the baselines measured on its whole year
        archive.apply_frozen_rates(self)
        self.conn.commit()

    def _same_day_policy_claim(self, l: dict) -> bool:
        """Medicare's same-day rule from 2027 (CY 2027 physician fee schedule final rule, Federal Register November 9,
        2026): when an office visit with modifier 25 is billed on the same day as a 0-day global procedure, the most
        expensive service is paid at 100 percent and every other service that day at 80 percent, a 10- or 90-day
        procedure on the claim included; a 10- or 90-day procedure alone does not bring the rule in. True for every line
        of such a claim. Applied to Medicare Advantage too: a plan that follows Medicare's payment rules pays the same
        way, and one that pays in full is not shown as underpaying. Such lines are kept out of the 2027 baselines."""
        if l.get("payer_kind") not in ("medicare", "medicare_advantage") or (l.get("service_date") or "") < MEDICARE_SAME_DAY_EM["effective"]:
            return False
        claim_lines = self.lines_by_claim.get(l["claim_key"], [])
        has_em25 = any(o.get("procedure_code") in EM_CODES and "25" in o["modifiers_list"] for o in claim_lines)
        has_zero_day = any(GLOBAL_DAYS.get(o.get("procedure_code") or "") == 0 and o.get("procedure_code") not in EM_CODES for o in claim_lines)
        return has_em25 and has_zero_day

    def _rate_line(self, l) -> tuple[int, str] | None:
        """(amount per unit, component) of a clean full-rate line the payer's rate is read on (claim status 1 or 19, paid,
        one listed code unchanged, first procedure on its claim, no pricing modifier but 26/TC, no multiple-procedure,
        bundling or level cut, not priced off the clinician's contract (CO-147: what archive rule 8 disputes, never the
        payer's rate, though a few such lines agree: fix7c), not under Medicare's 2027 same-day rule, a count of units that
        is a count); None otherwise."""
        if l.claim_status_code not in ("1", "19") or (l.paid_cents or 0) <= 0 or not l.allowed_cents:
            return None
        code = l.procedure_code
        if not code or l.original_procedure_code or l.surg_rank > 1 or is_unlisted(code):
            return None
        mods = l.modifiers_list
        if mods and any(m in PRICING_MODIFIERS and m not in COMPONENTS for m in mods):
            return None
        if any(rc in ("59", "B10", "97", "236", "234", "150", "186", "147") for _g, rc, _a in self.adj.get(l.line_key, ())):
            return None
        if self._same_day_policy_claim(l):
            return None
        u = self.paid_units_of(l)
        if u != 1 and (u < 1 or u > MAX_UNITS or u != int(u) or DRUG_RE.match(code) or SKIN_SUBSTITUTE_RE.match(code)):
            return None
        per = int(round(l["allowed_cents"] / u))
        return (per, component_of(mods)) if per > 0 else None

    def _rate_lines(self) -> tuple[array, bytearray]:
        """_rate_line of every line, once, in two compact arrays by the line's place in self.lines: the amount per unit (0
        for a line the rate is not read on) and the component (an index into RATE_COMPONENTS). The rate tables and the plans
        read them several times; a list of tuples would cost tens of megabytes on the serverless image."""
        n = len(self.lines)
        per_of, comp_of = array("q", bytes(8 * n)), bytearray(n)
        for i, l in enumerate(self.lines):
            got = self._rate_line(l)
            if got:
                per_of[i] = got[0]
                comp_of[i] = RATE_COMPONENTS.index(got[1])
        return per_of, comp_of

    def _plan_schedules(self, per_of: array | None = None, comp_of: bytearray | None = None) -> None:
        """The employer plans (the claim's group number, REF*1L) and products (its contract class, REF*CE) of a payer group
        that pay a schedule of their own (fix7c: the hand-check of the underpayments the 60 percent rule dropped found one
        large employer plan paying its own amounts beside the payer's network rate, a product with amounts of its own, a
        group paying Medicare's). A plan with at least PLAN_UNIT_MIN clean lines shows its own rate for a code (year, place,
        component) where its physicians' lines hold one amount (PLAN_MIN lines, a clear majority); the rest of the payer's
        lines, those of the plans already read apart left out, show the payer's. A plan whose own amounts differ from the
        rest on PLAN_DIFFER_MIN codes or more, and on more codes than they agree, pays a schedule of its own. The plan that
        differs most is read apart first (ties: the smaller), then the others are compared again, so the network the rest
        stands for is never the large plan's own schedule. The group number is read first; a product is read on the lines no
        employer plan of its own holds. Sets self.own_plans: {payer group: {(dimension, value): label}}; the label is a
        digest, so neither the group number nor the product's name is ever part of a key, a title or a log."""
        self.own_plans: dict[str, dict] = {}
        if per_of is None or comp_of is None:
            per_of, comp_of = self._rate_lines()
        lines = self.lines
        for dim in ("g", "c"):
            # two passes: the plans' sizes first, then the amounts of the payers that have a plan large enough to read
            # (memory: one counter per code of the payer and of each of its large plans, not of the thousands of small ones)
            units = Counter()
            for i, l in enumerate(lines):
                if per_of[i]:
                    v = self._plan_value(l, dim)
                    if v:
                        units[(l.payer_group, v)] += 1
            big = {u for u, n in units.items() if n >= PLAN_UNIT_MIN}
            sizes = {u: units[u] for u in big}
            del units
            if not big:
                continue
            groups = {g for g, _v in big}
            total: dict[tuple, Counter] = defaultdict(Counter)
            obs_u: dict[tuple, dict] = defaultdict(lambda: defaultdict(Counter))
            for i, l in enumerate(lines):
                per = per_of[i]
                if not per or l.payer_group not in groups or l.provider_tier == "nonphysician":
                    continue
                if dim == "c" and self._own_employer_plan(l):
                    continue                # read in its employer plan's own schedule
                k = (l.year, l.procedure_code, l.pos_class, RATE_COMPONENTS[comp_of[i]])
                total[(l.payer_group,) + k][per] += 1
                v = self._plan_value(l, dim)
                if v and (l.payer_group, v) in big:
                    obs_u[(l.payer_group, v)][k][per] += 1
            own: dict[tuple, dict] = {}
            for u, keys in obs_u.items():
                for k, cnt in keys.items():
                    r = full_rate(cnt, halves=is_surgical(k[1])) if sum(cnt.values()) >= PLAN_MIN else None
                    if r is not None and r["n"] >= PLAN_MIN and r["share"] >= FULL_SHARE:
                        own.setdefault(u, {})[k] = (r["mode"], cnt)
            del obs_u
            removed: dict[tuple, Counter] = defaultdict(Counter)
            apart: set[tuple] = set()
            while True:
                best = None
                for u, rates in own.items():
                    if u in apart:
                        continue
                    differ = agree = 0
                    for k, (mode, cnt) in rates.items():
                        tk = (u[0],) + k
                        rest = total[tk] - removed[tk] - cnt
                        if sum(rest.values()) < 5:
                            continue        # too few other lines of the payer to compare with
                        rr = full_rate(rest, halves=is_surgical(k[1]))
                        if rr is None or rr["share"] < FULL_SHARE:
                            continue
                        if abs(mode / rr["mode"] - 1) <= SAME_BAND:
                            agree += 1
                        elif abs(mode / rr["mode"] - 1) >= PLAN_GAP and abs(mode - rr["mode"]) >= 200:
                            differ += 1     # by as much as an underpayment is: a smaller gap is never called short anyway
                    if differ >= PLAN_DIFFER_MIN and differ > agree:
                        score = (differ - agree, -sizes[u], u[1])
                        if best is None or score > best[0]:
                            best = (score, u)
                if best is None:
                    break
                u = best[1]
                apart.add(u)
                for k, (_mode, cnt) in own[u].items():
                    removed[(u[0],) + k].update(cnt)
            for g, v in sorted(apart):
                self.own_plans.setdefault(g, {})[(dim, v)] = dim + hashlib.sha256(f"{dim}|{v}".encode()).hexdigest()[:8]
        self.ema_counts["plans_with_own_schedule"] = sum(len(v) for v in self.own_plans.values())

    def _own_employer_plan(self, l) -> bool:
        g = l.get("group_number")
        return bool(g) and ("g", g) in self.own_plans.get(l.get("payer_group"), {})

    def _plan_value(self, l, dim: str) -> str | None:
        """A line's employer plan ('g': the claim's group number) or product ('c': its contract class); for the product, only
        where its employer plan does not pay a schedule of its own (that plan is read first)."""
        if dim == "g":
            return l.get("group_number") or None
        return None if self._own_employer_plan(l) else (l.get("plan") or None)

    def _rate_group(self, l) -> str:
        """The group a line's rate is read in: its payer group, or that group with the label of its employer plan or product
        where that pays a schedule of its own (_plan_schedules)."""
        grp = l.get("payer_group") or ""
        own = self.own_plans.get(grp)
        if not own:
            return grp
        g = l.get("group_number")
        label = own.get(("g", g)) if g else None
        if label is None and l.get("plan"):
            label = own.get(("c", l["plan"]))
        return f"{grp}{PLAN_SEP}{label}" if label else grp

    @staticmethod
    def _split_group(rg: str) -> tuple[str, str | None]:
        """(payer group, plan label or None) of a rate group."""
        grp, _sep, label = (rg or "").partition(PLAN_SEP)
        return grp, (label or None)

    def _np_ratio_of(self, rg: str, grp: str, year: str) -> float | None:
        """The PA/NP rate over every code (np_ratio) of a rate group for a year: a plan read apart that shows no rule of its own
        (fewer than TIER_MIN_LINES PA/NP lines of its own measured) takes its payer group's (fix7c-r1-5: a large plan's few NP
        lines were held to its full physician rate though its payer pays NPs 85 percent everywhere)."""
        q = self.np_ratio.get((rg, year))
        if q is None and rg != grp and self.__dict__.get("np_votes", {}).get((rg, year), 0) < TIER_MIN_LINES:
            q = self.np_ratio.get((grp, year))
        return q

    def _np_state_of(self, rg: str, grp: str, year: str) -> dict | None:
        """The payer's PA/NP rule by the office's state (np_state) for a rate group, a plan read apart that shows too few PA/NP
        lines of its own taking its payer group's, as _np_ratio_of does."""
        rule = self.np_state.get((rg, year))
        if rule is None and rg != grp and self.__dict__.get("np_votes", {}).get((rg, year), 0) < TIER_MIN_LINES:
            rule = self.np_state.get((grp, year))
        return rule

    def _few_own(self, rg: str, grp: str, year: str, code: str, pos: str, comp: str) -> tuple[int, int, int] | None:
        """The payer's own amount for a code in a setting where it has too few clean lines for a rate of its own but they
        agree: (amount, lines at it, lines), at least two at one amount and more than half of them, after the multiple-
        procedure halves (an amount about half of another it paid) are left out. Its plan's lines first, else the payer
        group's. None otherwise."""
        for g in ((rg, grp) if rg != grp else (rg,)):
            cnt = self.yard_obs.get((g, year, code, pos, comp))
            if not cnt or (g, year, code, pos, comp) in self.yard:
                continue
            full = self._halves_dropped(cnt, code)
            if not full:
                continue
            top, n = max(full.items(), key=lambda kv: (kv[1], kv[0]))
            total = sum(full.values())
            if n >= 2 and n * 2 > total:
                return top, n, total
        return None

    @staticmethod
    def _halves_dropped(cnt: Counter, code: str) -> Counter:
        """A payer's amounts for a code with the multiple-procedure halves left out (an amount about half of another amount it
        paid, for a code the rule reduces), however few the full amounts are (full_rate keeps a half that most lines hold)."""
        if not is_surgical(code):
            return Counter(cnt)
        return Counter({a: k for a, k in cnt.items() if not any(b > a and in_band(a / b, HALF_BAND) for b in cnt)})

    def _own_plan_amount(self, l, code: str, allowed: int) -> bool:
        """A line of a payer that has plans with schedules of their own (_plan_schedules), read on the payer's other plans,
        allowed exactly one of those plans' own rates for the code (its full rate, or its PA/NP rate): most likely that
        plan's own schedule under a group number too new or too small to be read apart (an employer's plan renumbered in
        mid-year). It is left unjudged, as a plan's line with no rate of its own is: never called short against the network
        rate (fix7c-r2-13)."""
        grp = l.get("payer_group") or ""
        plans = self.own_plans.get(grp)
        if not plans or self._rate_group(l) != grp or allowed <= 0:
            return False
        year, pos, comp = str(l.get("year") or ""), l.get("pos_class") or "office", component_of(l.get("modifiers_list"))
        per = allowed / max(self.paid_units_of(l), 1)
        for label in plans.values():
            pg = f"{grp}{PLAN_SEP}{label}"
            rec = self.yard.get((pg, year, code, pos, comp))
            if not rec:
                continue
            amounts = {rec["mode"]} | ({rec["np"]} if rec.get("np") else set())
            q = self._np_ratio_of(pg, grp, year)
            if q and q < 1:
                amounts.add(int(round(rec["mode"] * q)))
            if any(abs(per - a) <= 1 for a in amounts):
                return True
        return False

    def _own_floor(self, l, code: str) -> int | None:
        """The least the payer has paid for a code in the line's setting and year (its own clean full lines, halves left out,
        its plan's where it has some, else its payer group's): a denial is never sized below it (fix7c-r1-4)."""
        grp, year = l.get("payer_group") or "", str(l.get("year") or "")
        pos, comp = l.get("pos_class") or "office", component_of(l.get("modifiers_list"))
        rg = self._rate_group(l)
        for g in ((rg, grp) if rg != grp else (rg,)):
            cnt = self.yard_obs.get((g, year, code, pos, comp))
            full = self._halves_dropped(cnt, code) if cnt else None
            if full:
                return min(full)
        return None

    def _keep_locked_years(self, obs: dict, obs_np: dict, obs_md: dict, prov: dict, by_class: dict, np_lines: list) -> list:
        """A service year the archive took bills of (locked) keeps the rates measured on the whole year before the archive
        took anything: its snapshot (archive_rates: each payer group's paid amounts per code, provider type and place)
        stands in for the lines left, which are mostly the bills of open items (their amounts could look usual). A key the
        snapshot lacks stays only where a line read after the year was locked (a late remittance) counts toward it; a
        clinician's own amounts of a locked year stay only there too, and a component (26, TC) is read as the fee schedule
        splits the year's global rate. The same rule archive.apply_frozen_rates applies to the older figures. Returns the
        PA/NP lines (key, amount, office state) with the locked years' taken from the snapshot (whose state is not known).
        The snapshot does not tell one plan from another: a plan with a schedule of its own keeps no rate of its own for a
        locked year (its lines are not judged short against the payer's, _yardstick)."""
        from . import archive
        locked = archive._locked_years(self.conn)
        if not locked or not archive._have(self.conn, "archive_rates"):
            return np_lines
        late = {(k[0], str(k[1]), k[2], k[4]) for k in archive._post_lock_keys(self, locked)}
        frozen: dict[tuple, dict] = defaultdict(dict)
        kinds: dict[str, str] = {}
        q = ",".join("?" * len(locked))
        for grp, year, code, tier, pos, kind, raw in self.conn.execute(
                f"SELECT payer_group, year, code, tier, pos_class, payer_kind, counts FROM archive_rates WHERE year IN ({q})", sorted(locked)):
            frozen[(grp, str(year), code, pos)][tier or "unknown"] = _counts_json(raw)
            if kind:
                kinds[grp] = kind

        def keep(key: tuple) -> bool:
            return str(key[1]) not in locked or (key[:4] in late and key[:4] not in frozen)
        for table in (obs, obs_np, obs_md, prov):
            for key in [k for k in table if not keep(k)]:
                del table[key]
        for key in [k for k in by_class if str(k[0]) in locked]:
            del by_class[key]
        np_lines = [x for x in np_lines if keep(x[0])]
        for (grp, year, code, pos), tiers in frozen.items():
            key = (grp, year, code, pos, "")
            for tier, cnt in tiers.items():
                obs[key].update(cnt)
                if tier == "nonphysician":
                    obs_np[key].update(cnt)
                    np_lines += [(key, a, None) for a, k in cnt.items() for _ in range(k)]
                elif tier == "physician":
                    obs_md[key].update(cnt)
                kind = self.kind_of_group.setdefault(grp, kinds.get(grp))
                by_class[(year, code, pos, "", kind)].update(cnt)
        return np_lines

    def _build_yardsticks(self) -> None:
        """The payer's own rate per unit (the yardstick every item is sized and judged by), read on its clean full-rate lines
        of every clinician: by payer group (which keeps Medicare Advantage, Medicaid and commercial plans apart), year, code,
        place of service and component (global, 26, TC). A line billing several units counts at its amount per unit (SVC05,
        or the units billed where SVC05 is empty: paid_units_of); a drug's or skin substitute's units are doses, and an
        unlisted code has no rate. Also: each clinician's own amounts (a recognised tier only, _yardstick), the payer's PA/NP
        rate across codes, Medicare's own rates (the fee schedule) and the split of a global into its components there, and
        each product class's typical amount (the fallback when a payer has no rate of its own). An employer plan or a
        product with a schedule of its own (_plan_schedules) is read apart: its rates on its own lines, the payer's on the
        other plans' lines; its rate group is the payer group with the plan's label (_rate_group)."""
        obs: dict[tuple, Counter] = defaultdict(Counter)
        obs_np: dict[tuple, Counter] = defaultdict(Counter)
        prov: dict[tuple, Counter] = defaultdict(Counter)
        by_class: dict[tuple, Counter] = defaultdict(Counter)
        self.kind_of_group: dict[str, str] = {}
        # a clinician's own rate is read only for an NPI the remittances give a person's first name for: a group's NPI
        # (NM1*82*2) stands for every clinician of the practice, whose types differ
        person = {r[0] for r in self.conn.execute("""SELECT DISTINCT rendering_provider_npi FROM claims WHERE rendering_provider_npi IS NOT NULL
                                                     AND COALESCE(rendering_provider_first, '') <> ''""")}
        to_charge: dict[tuple, list] = defaultdict(lambda: [0, 0, 0])     # (payer group, year) -> [allowed, charge, lines]
        to_schedule: dict[tuple, list] = defaultdict(list)                 # (payer group, year) -> allowed over the fee schedule's
        obs_md: dict[tuple, Counter] = defaultdict(Counter)                # the physicians' lines
        from .mpfs import medicare_amount
        np_lines: list[tuple] = []
        per_of, comp_of = self._rate_lines()
        self._plan_schedules(per_of, comp_of)
        for i, l in enumerate(self.lines):
            per = per_of[i]
            if not per:
                continue
            comp = RATE_COMPONENTS[comp_of[i]]
            code = l["procedure_code"]
            key = (self._rate_group(l), l["year"], code, l["pos_class"], comp)
            obs[key][per] += 1
            self.kind_of_group[l["payer_group"]] = l["payer_kind"]
            if (l.get("charge_cents") or 0) >= l["allowed_cents"]:
                t = to_charge[(l["payer_group"], l["year"])]
                t[0] += l["allowed_cents"]
                t[1] += l["charge_cents"]
                t[2] += 1
            if l["provider_tier"] == "nonphysician":
                obs_np[key][per] += 1
                np_lines.append((key, per, self._office_state(l)))
            elif l["provider_tier"] == "physician":
                obs_md[key][per] += 1
            if l.get("rendering_provider_npi") in person:
                prov[key + (l["rendering_provider_npi"],)][per] += 1
            by_class[(l["year"], code, l["pos_class"], key[4], l["payer_kind"])][per] += 1
            # the payer's own level against the fee schedule (its allowed amount over Medicare's for the code), for a code it
            # has no rate of its own for (no_rate_size); independent of which of the practice's fee schedules billed the line
            fs = medicare_amount(l["year"], "IN" if " IN" in (l["payer_group"] or "") else "KY", code, facility=l["pos_class"] == "facility") \
                if not key[4] else None
            if fs:
                to_schedule[(l["payer_group"], l["year"])].append(per / fs)
        np_lines = self._keep_locked_years(obs, obs_np, obs_md, prov, by_class, np_lines)
        self.yard_obs = obs
        self.yard: dict[tuple, dict] = {}
        for key, cnt in obs.items():
            if sum(cnt.values()) < 5:
                continue
            npc = obs_np.get(key)
            # the full rate is read on the lines of the clinicians not known to be PAs or NPs (where PAs and NPs bill most of a
            # code, visits, their 85 percent would otherwise pass for the payer's rate); where those show no clear majority
            # (clinicians the files cannot type paid at levels of their own), on the physicians' lines, where there are enough
            md = obs_md.get(key) or Counter()
            others = cnt - (npc or Counter())
            if not professional_service(key[2]):
                r = full_rate(cnt, halves=is_surgical(key[2]))      # a drug, a supply or a test: its price is not the clinician's
                npc = None
            else:
                r = full_rate(others, halves=is_surgical(key[2])) if sum(others.values()) >= 5 else None
                if (r is None or r["share"] < FULL_SHARE) and sum(md.values()) >= 5:
                    r = full_rate(md, halves=is_surgical(key[2]))
                if r is None:
                    r = full_rate(cnt, npc, halves=is_surgical(key[2]))
            r["n_all"] = sum(cnt.values())
            r["key"] = key
            if npc and r["np"] is None and sum(npc.values()) >= 5:
                np_mode = max(npc.items(), key=lambda kv: (kv[1], kv[0]))[0]
                if abs(np_mode / r["mode"] - 1) <= SAME_BAND:
                    r["np_full"] = True                 # this payer pays a PA or NP the full rate for the code
                elif in_band(np_mode / r["mode"], NP_BAND):
                    r["np"] = np_mode
            self.yard[key] = r
        # the rate groups and codes with a facility rate of their own (usual_rate looks for one only there)
        self._fac_codes = {(k[0], k[2]) for k in self.yard if k[3] == "facility"}
        # the payer's PA/NP rate over every code: how its PAs' and NPs' amounts sit against its full rate; and the same in each
        # state's offices, where the payer's rule follows the state (one state's PAs and NPs paid in full, the other's 85 percent)
        votes: dict[tuple, Counter] = defaultdict(Counter)
        by_state: dict[tuple, Counter] = defaultdict(Counter)
        for key, per, st in np_lines:
            r = self.yard.get(key)
            if r:
                x = per / r["mode"]
                vote = "1.00" if abs(x - 1) <= SAME_BAND else "0.85" if in_band(x, NP_BAND) else "other"
                votes[(key[0], key[1])][vote] += 1
                if st:
                    by_state[(key[0], key[1], st)][vote] += 1
        self.np_ratio: dict[tuple, float] = {}
        # how many PA/NP lines each rate group's rule was read on (a plan read apart with too few takes its payer's rule)
        self.np_votes: dict[tuple, int] = {k: sum(v.values()) for k, v in votes.items()}
        for k, v in votes.items():
            top, n = top_of(v)
            if sum(v.values()) >= TIER_MIN_LINES and n >= TIER_MIN_SHARE * sum(v.values()) and top != "other":
                self.np_ratio[k] = 1.0 if top == "1.00" else NP_TIER
        self.np_state: dict[tuple, dict] = {}
        for (grp, year, st), v in by_state.items():
            top, n = top_of(v)
            if sum(v.values()) >= TIER_MIN_LINES and n >= TIER_MIN_SHARE * sum(v.values()) and top != "other":
                self.np_state.setdefault((grp, year), {})[st] = 1.0 if top == "1.00" else NP_TIER
        self.np_state = {k: v for k, v in self.np_state.items() if len(set(v.values())) > 1}
        # each clinician's own amount where it is clear (TIER_MIN_LINES, TIER_MIN_SHARE): a recognised tier or a discount
        self.prov_rates: dict[tuple, dict] = {}
        for key, cnt in prov.items():
            n = sum(cnt.values())
            if n < TIER_MIN_LINES:
                continue
            top = max(cnt.values())
            if top < TIER_MIN_SHARE * n:
                continue
            self.prov_rates[key] = {"mode": max(a for a, k in cnt.items() if k == top), "n": n, "share": top / n}
        # Medicare's own rates (the fee schedule): the fallback for Medicare and its Advantage plans, and where a payer has no
        # rate of its own for a component, how the fee schedule splits the global into its 26 and TC parts
        self.medicare_yard: dict[tuple, int] = {}
        med_modes: dict[tuple, tuple[int, int]] = {}       # (year, code, place, component) -> (Medicare's amount, its lines)
        # Kentucky's carrier first, then the one with the most lines: at least 10 for a rate, 3 for the components' split
        for key, cnt in sorted(obs.items(), key=lambda kv: (not kv[0][0].startswith("Medicare KY"), -sum(kv[1].values()), kv[0])):
            grp, year, code, pos, comp = key
            n = sum(cnt.values())
            if self.kind_of_group.get(grp) != "medicare" or n < 3:
                continue
            mode = self.yard[key]["mode"] if key in self.yard else max(cnt.items(), key=lambda kv: (kv[1], kv[0]))[0]
            med_modes.setdefault((year, code, pos, comp), (mode, n))
            if n >= 10:
                self.medicare_yard.setdefault((year, code, pos, comp), mode)
        self.comp_split: dict[tuple, float] = {}
        for (year, code, pos, comp), (mode, _n) in med_modes.items():
            if not comp or pos != "office":
                continue
            whole = med_modes.get((year, code, pos, ""), (None, 0))[0]
            other = med_modes.get((year, code, pos, "TC" if comp == "26" else "26"), (None, 0))[0]
            share = mode / whole if whole else mode / (mode + other) if other else None
            if share and 0 < share < 1:
                self.comp_split[(year, code, comp)] = share
        self.class_typical: dict[tuple, dict] = {}
        # and across settings ('any'), for a code whose lines are too few in each setting alone: the last resort of _rate
        # (fix7c: once the 837 named the surgery center's claims, a code billed in both settings could have too few in each)
        any_setting: dict[tuple, Counter] = defaultdict(Counter)
        for (y_, code_, _pos, comp_, cls_), cnt in by_class.items():
            any_setting[(y_, code_, "any", comp_, cls_)].update(cnt)
        # a setting's own lines too few for a typical amount (CLASS_FEW_MIN to 9): what sizes a denial in that setting before
        # the lines of every setting together, whose median mixes a surgery center's amounts with an office's (fix7c: the world
        # run showed office lines of a code sized by a pool most of whose lines were the surgery center's lower amounts)
        self.class_few: dict[tuple, dict] = {}
        for key, cnt in list(by_class.items()) + list(any_setting.items()):
            n = sum(cnt.values())
            if n >= 10 or (n >= CLASS_FEW_MIN and key[2] != "any"):
                vals = sorted(cnt.elements())
                (self.class_typical if n >= 10 else self.class_few)[key] = {"median": vals[n // 2], "n": n}
        del any_setting
        # what each payer allows of the practice's charge over the codes it pays (at least 20 lines): its own measure of a
        # code it has no rate for, the charge being the practice's fee for that code
        self.charge_ratio: dict[tuple, tuple[float, int]] = {k: (min(1.0, v[0] / v[1]), v[2]) for k, v in to_charge.items()
                                                             if v[2] >= 20 and v[1] > 0}
        self.schedule_ratio: dict[tuple, tuple[float, int]] = {k: (sorted(v)[len(v) // 2], len(v)) for k, v in to_schedule.items()
                                                               if len(v) >= 20}

    def _own_charge_ratio(self, grp: str, year: str) -> tuple[float, int, str] | None:
        """The payer's own allowed-to-charge ratio for the year, else the year before or after."""
        got = self.charge_ratio.get((grp, year))
        if got:
            return got + (year,)
        if year.isdigit():
            for y in (str(int(year) - 1), str(int(year) + 1)):
                got = self.charge_ratio.get((grp, y))
                if got:
                    return got + (y,)
        return None

    def no_rate_size(self, l: dict, code: str | None, charge: int | None, units: float = 1) -> tuple[int | None, str]:
        """What a line with no rate of its payer's own (nor its product class's) is worth, never above its charge: the payer's
        own level against the fee schedule (the median of its allowed amounts over Medicare's for the codes it pays) times the
        fee schedule's amount for this code and its units; for a code the fee schedule does not price, the payer's own share
        of the practice's charges times this line's charge. Never another class's rate itself; (None, why) when the payer has
        paid too little to tell."""
        from .mpfs import medicare_amount
        grp, year = l.get("payer_group") or "", str(l.get("year") or "")
        fs = medicare_amount(year, "IN" if " IN" in grp else "KY", code or "", facility=l.get("pos_class") == "facility") \
            if not component_of(l.get("modifiers_list")) else None
        q = self.schedule_ratio.get((grp, year)) if fs else None
        if q:
            got = int(round(q[0] * fs * max(1, units or 1)))
            if charge and 0 < charge < got:
                got = charge
            return got, (f"no rate of {grp}'s own for {code}: {grp}'s own level against the fee schedule ({q[0]:.0%} of Medicare's "
                         f"amounts, the middle of its {q[1]} paid lines in {year}) x the fee schedule's ${fs / 100:,.2f}"
                         + (f" x {units:g} units" if units and units > 1 else "") + (", capped at the line's charge" if got == charge else ""))
        got = self._own_charge_ratio(grp, year) if charge and charge > 0 else None
        if not got:
            return None, f"no baseline: {grp or 'the payer'} has no rate of its own for {code}, and too few paid lines to measure"
        q, n, y = got
        return int(round(charge * q)), (f"no rate of {grp}'s own for {code}: the charge x what {grp} allowed of the charges on its "
                                        f"{n} paid lines in {y} ({q:.0%})")

    def _comp_share(self, year: str, code: str, comp: str) -> tuple[float, str] | None:
        """The share of a code's global that a component is under the fee schedule (Medicare's own payments), for the year or
        the one nearest it."""
        s = self.comp_split.get((year, code, comp))
        if s:
            return s, year
        near = sorted((abs(int(y) - int(year)) if y.isdigit() and year.isdigit() else 99, y, v)
                      for (y, c, p), v in self.comp_split.items() if c == code and p == comp)
        return (near[0][2], near[0][1]) if near else None

    def _own_yard(self, grp: str, year: str, code: str, pos: str, comp: str, exact: bool = False) -> tuple[dict | None, str]:
        """The payer's own rate record for the year, else the year before or after (the payer's own, one year apart); with
        `exact`, the year's own record only."""
        r = self.yard.get((grp, year, code, pos, comp))
        if r or exact or not year.isdigit():
            return r, year
        for y in (str(int(year) - 1), str(int(year) + 1)):
            r = self.yard.get((grp, y, code, pos, comp))
            if r:
                return r, y
        return None, year

    def _backed(self, r: dict) -> bool:
        """Whether 20 or more of the payer's clean paid lines back a usual rate (T-N4's evidence): the lines it was read on,
        or, where it was read on a few physicians' lines because PAs and NPs bill most of the code, every clean line of the
        code paid at it, at its PA/NP rate or at the rate the line is held to, when those are a clear majority of all of
        them but the multiple-procedure halves (PAs and NPs paid the same or 85 percent confirm the full rate)."""
        if (r.get("n") or 0) >= 20:
            return True
        key = r.get("payer_key")
        yard, cnt = self.yard.get(key), self.yard_obs.get(key)
        if not yard or not cnt:
            return False
        full = yard["mode"]
        amounts = {full, r.get("mode")}
        if yard.get("np"):
            amounts.add(yard["np"])
        elif self.np_ratio.get(key[:2]) == NP_TIER:
            amounts.add(int(round(full * NP_TIER)))
        halves = set(yard.get("half") or ())
        n_all = sum(k for a, k in cnt.items() if a not in halves)
        return n_all >= 20 and sum(cnt.get(a, 0) for a in amounts if a) >= FULL_SHARE * n_all

    def _np_rate(self, r: dict, grp: str, year: str, kind: str | None, code: str | None = None) -> tuple[int | None, str]:
        """The payer's rate for a PA or NP for this code: its own PA/NP amount for the code; else, for a clinician's own
        service (professional_service), its PA/NP rate over every code (85 percent, or the full rate; a plan read apart that
        shows none of its own takes its payer's: _np_ratio_of), else Medicare's 85 percent for Medicare. (None, '') where it
        pays the full rate. `grp` is the rate group (a payer group, or one of its plans)."""
        if not professional_service(code):
            return None, ""
        if r.get("np"):
            return r["np"], f"; the payer's PA/NP amount for it, {r['np'] / r['mode']:.0%} of that"
        if r.get("np_full"):
            return None, ""
        q = self._np_ratio_of(grp, self._split_group(grp)[0], year)
        if q == NP_TIER or (q is None and kind == "medicare"):
            return int(round(r["mode"] * NP_TIER)), ("; at the PA/NP 85 percent this payer pays on its other codes" if q
                                                     else "; at the PA/NP 85 percent Medicare pays")
        return None, ""

    def _yardstick(self, l: dict, code: str, pos: str, comp: str, pool: bool | None = None) -> tuple[int | None, str, dict | None]:
        """The payer's own rate per unit for this line, and the record it rests on (None when the figure is not the payer's
        own): the payer group's rate for the year (else the next one), the place of service and the component (a component
        with no rate of the payer's own: its global rate split as the fee schedule splits it), at the clinician's recognised
        tier: the clinician's own amount where it is the PA/NP 85 percent of the payer's rate, else the payer's PA/NP rate
        for a PA or NP. A clinician's other level is never the yardstick: a discount below it is listed apart (the record's
        'discount', PROVIDER_RATE_GAP). With no rate of the payer's own: Medicare's for Medicare and its Advantage plans, else
        the typical amount of the same product class (commercial, Medicaid...), never another class's.
        A line of an employer plan or product that pays a schedule of its own (_plan_schedules) is held to that plan's rate;
        where the plan shows none for the code, the payer's other plans' rate sizes the line, and the record says so
        ('plan_unknown': no line is called short against another plan's rate). Where the payer pays PAs and NPs by the
        office's state (np_state), a PA's or NP's line is held to the rate of the state of the visit (_office_state), the
        lower of the states' where that is not known."""
        grp, year = l.get("payer_group") or "", str(l.get("year") or "")
        rg = self._rate_group(l)
        kind = l.get("payer_kind") or self.kinds.get(grp) or self.kind_of_group.get(grp)
        tier = l.get("provider_tier") or "unknown"
        where = {"office": "", "any": ", all settings"}.get(pos, ", facility setting")
        part = f"{code}-{comp}" if comp else code
        r, y, split = self._own_rate(rg, year, code, pos, comp)
        plan, plan_unknown = (rg != grp), False
        if r is None and plan:
            # the plan pays a schedule of its own and shows no rate of its own for the code: its payer's other plans' rate
            # sizes the line, and no line is called short against it
            r, y, split = self._own_rate(grp, year, code, pos, comp)
            plan_unknown = r is not None
        if r is None:
            if kind in MEDICARE_PRICED:
                # Medicare's own rate: the fee schedule a Medicare Advantage plan follows, the amount a supplement pays its
                # share of, and what the VA's community care and TRICARE pay by regulation
                med = self.medicare_yard.get((year, code, pos, comp))
                if med:
                    rate = int(round(med * NP_TIER)) if tier == "nonphysician" and professional_service(code) else med
                    why = {"va": "; the VA's community care pays the Medicare rate", "tricare": "; TRICARE pays the Medicare rate"}.get(kind, "")
                    return rate, f"Medicare {year} rate for {part} ({tier}{where}){why}", None
            np_q = self._np_ratio_of(rg, grp, year)
            # the payer's own amounts in this setting where they agree, though too few for a rate of its own (fix7c-r1-4: a
            # surgery center's few claims of a code, paid alike), before any other payer's: good enough to size a denial,
            # never to call a line short
            few = self._few_own(rg, grp, year, code, pos, comp)
            if few:
                rate = int(round(few[0] * NP_TIER)) if tier == "nonphysician" and professional_service(code) and np_q == NP_TIER else few[0]
                return rate, (f"{grp}'s own {year} amount for {part} ({tier}{where}): {few[1]} of its {few[2]} lines, too few for a "
                              "rate of its own"), None
            # the typical amount of the payer's own product class (a payer of no known class: a commercial plan's, which most are).
            # Where the class has lines enough across settings but too few in the line's own (fix7c-r1-2: once the 837 named the
            # surgery center's claims, a code billed in both settings can have too few in each), the setting's own few lines
            # (CLASS_FEW_MIN), then its lines of every setting together: a surgery center's amounts are not an office's. A caller
            # that tries one setting after another passes pool False (_rate, usual_rate's office figure for a facility line), so
            # the payer's own figure in a later setting is never passed over for them.
            cls = kind if kind and kind != "other" else "commercial"
            steps = [(self.class_typical, pos, where)]
            if (l.get("pool", True) if pool is None else pool) and (year, code, "any", comp, cls) in self.class_typical:
                steps += [(self.__dict__.get("class_few") or {}, pos, f"{where}, the few lines of this setting"),
                          (self.class_typical, "any", ", all settings")]
            for table, p, how in steps:
                m = table.get((year, code, p, comp, cls))
                if m:
                    rate = int(round(m["median"] * NP_TIER)) if tier == "nonphysician" and professional_service(code) and np_q == NP_TIER else m["median"]
                    return rate, f"typical {year} allowed for {part} among {cls} plans ({tier}{how}, n={m['n']})", None
            return None, "no baseline", None
        full = r["mode"]
        pk = r["key"]
        label = "full"
        if plan and not plan_unknown:
            label = f"full plan {self._split_group(rg)[1]}"
            basis = (f"{grp} usual {y} rate for {part} in this claim's plan, which pays a schedule of its own (its group number or "
                     f"product on the remittance) ({tier}{where}, n={r['n']}{split})")
        elif plan_unknown:
            basis = (f"{grp} usual {y} rate for {part} on its other plans ({tier}{where}, n={r['n']}{split}); this claim's plan pays a "
                     "schedule of its own and shows no rate of its own for the code, so the line is sized by it but not called short")
        else:
            basis = f"{grp} usual {y} rate for {part} ({tier}{where}, n={r['n']}{split})"
        suffix = label[4:]
        out = {"mode": full, "n": r["n"], "share": r["share"], "key": (grp, y, part, label, pos), "payer_key": pk, "tier": "full"}
        if plan_unknown:
            out["plan_unknown"] = True
        npi = l.get("rendering_provider_npi")
        rule = self._np_state_of(pk[0], grp, y) if tier == "nonphysician" and professional_service(code) and not split else None
        if rule:
            # this payer pays PAs and NPs by the office's state: the state of this visit decides
            st = self._office_state(l)
            known = st in rule
            q = rule[st] if known else min(rule.values())
            rate = full if q >= 1 else int(round(full * q))
            out.update(mode=rate, key=(grp, y, part, f"pa_np {st}{suffix}" if known else f"pa_np{suffix}", pos), tier="pa_np")
            if known:
                why = f"; {grp} pays a PA or NP {q:.0%} of it in {STATE_NAMES.get(st, st)}'s offices, where this visit was"
            else:
                states = ", ".join(f"{STATE_NAMES.get(s, s)} {v:.0%}" for s, v in sorted(rule.items()))
                why = f"; {grp} pays a PA or NP by the office's state ({states}) and this visit's state is not known: the lower"
            return rate, basis + why, out
        own = self.prov_rates.get(pk[:4] + (comp, npi)) if npi and not split else None
        if own:
            ratio = own["mode"] / full
            if abs(ratio - 1) <= SAME_BAND:
                return full, basis, out             # the clinician is paid the payer's full rate: paid less, short
            if in_band(ratio, NP_BAND) and tier != "physician":
                out.update(mode=own["mode"], key=(grp, y, part, f"clinician {npi}{suffix}", pos), tier="clinician")
                return own["mode"], f"{basis}; this clinician's own rate, the PA/NP {ratio:.0%} of it (n={own['n']})", out
        if tier == "nonphysician":
            np_rate, why = self._np_rate(r, pk[0], y, kind, code)
            if np_rate:
                out.update(mode=np_rate, key=(grp, y, part, f"pa_np{suffix}", pos), tier="pa_np")
                basis, full = basis + why, np_rate
            elif professional_service(code) and not r.get("np_full") and self._np_ratio_of(pk[0], grp, y) is None:
                out["np_unknown"] = True    # what this payer pays its PAs and NPs is not known (_emit_underpaid)
        if own and own["mode"] < full * (1 - SAME_BAND) and not in_band(own["mode"] / r["mode"], HALF_BAND):
            out["discount"] = dict(own, npi=npi)
        return full, basis, out

    def _own_rate(self, rg: str, year: str, code: str, pos: str, comp: str) -> tuple[dict | None, str, str]:
        """The rate record of a rate group (a payer group, or one of its plans) for the code, the year it is of and, for a
        component it has no rate of its own for, how its global rate was split ('' otherwise). A plan's own schedule is read
        for the line's own year only: a plan has far fewer lines than its payer, its rates change from year to year, and a
        year it shows no rate for is not the year before's or after's (measured on the world: a plan's 2026 rate held its 2025
        visits short)."""
        exact = PLAN_SEP in (rg or "")
        r, y = self._own_yard(rg, year, code, pos, comp, exact)
        if r is None and comp:
            g, gy = self._own_yard(rg, year, code, pos, "", exact)
            share = self._comp_share(gy, code, comp) if g else None
            if g and share:
                return (dict(g, mode=int(round(g["mode"] * share[0])), np=None, np_full=g.get("np_full")), gy,
                        f", the {comp} part of its {code} rate of ${g['mode'] / 100:,.2f} as the {share[1]} fee schedule splits it ({share[0]:.0%})")
        return r, y, ""

    def _rate_lookup(self, l: dict, code: str, pos: str) -> tuple[int | None, str, dict | None]:
        return self._yardstick(l, code, pos, component_of(l.get("modifiers_list")))

    def _code_once(self, l: dict) -> bool:
        """The claim carries this line's code once on its date, so EMA's rows (by claim, code and date) are this line's own."""
        ck = l.get("claim_key")
        if not ck:
            return True
        per = self.__dict__.setdefault("_per_code", {})
        cnt = per.get(ck)
        if cnt is None:
            cnt = per[ck] = Counter((x.get("service_date"), x.get("procedure_code")) for x in self.lines_by_claim.get(ck, []))
        return cnt[(l.get("service_date"), l.get("procedure_code"))] <= 1

    def usual_rate(self, l: dict, code: str) -> tuple[int | None, str, dict | None]:
        """The payer's usual allowed amount per unit for this code, the part of it the line bills (26, TC or the whole), the
        clinician's recognised tier and the place-of-service class (_yardstick). The third value is the rate record when the
        figure is the payer's own (None for fallbacks). An unlisted code has none. A line whose setting nothing names (no
        CLP08, no 837 or EMA location that tells, _fill_setting) and that the payer allowed exactly its own facility rate for
        was priced at the facility (a surgical site's claim): it is held to that rate."""
        if is_unlisted(code):
            return None, f"no rate for {code}: an unlisted code is priced claim by claim", None
        pos = l.get("pos_class") or "office"
        comp = component_of(l.get("modifiers_list"))
        # a facility line: the payer's office figure at the fee schedule's facility share (below) comes before the class's few
        # facility lines and its lines of every setting together (fix7c-r1-2's fallbacks, read last)
        rate, basis, r = self._yardstick(l, code, pos, comp, pool=False if pos == "facility" else None)
        allowed = l.get("allowed_cents") or 0
        fac = self.__dict__.get("_fac_codes") or ()
        if pos == "office" and not l.get("pos") and allowed > 0 and rate and code == l.get("procedure_code") \
                and ((self._rate_group(l), code) in fac or (l.get("payer_group"), code) in fac):
            units = self.paid_units_of(l) if l.get("line_key") else 1
            frate, fbasis, fr = self._yardstick(l, code, "facility", comp, pool=False)
            if fr is not None and frate and frate < rate and abs(allowed / max(units, 1) - frate) <= 1:
                return frate, fbasis + ("; the remittance does not name the place of service and the payer allowed its facility rate: "
                                        "a facility claim"), fr
        if rate is None and pos == "facility":
            # no facility-setting baseline: the office figure, at the fee schedule's facility share of it where the fee schedule
            # prices the code; good enough to size a denial, never to call a line underpaid
            rate, basis, _r = self._yardstick(l, code, "office", comp, pool=False)
            if rate is not None:
                from .mpfs import medicare_amount
                y = str(l.get("year") or "")
                off, fac = medicare_amount(y, "KY", code), medicare_amount(y, "KY", code, facility=True)
                if off and fac and fac < off:
                    return (int(round(rate * fac / off)), basis + f" at the fee schedule's facility share of it ({fac / off:.0%}); "
                                                                  "no facility-setting baseline", None)
                return rate, basis + " (office-setting figure; no facility-setting baseline)", None
            if l.get("pool", True):
                # no office figure either: the class's few facility lines, then its lines of every setting together
                rate, basis, r = self._yardstick(l, code, pos, comp, pool=True)
        return rate, basis, r

    def mppr_pct(self, l: dict) -> tuple[float, bool]:
        """Share of the usual rate this payer normally allows for a 2nd or later procedure, and whether it was learned from its remits."""
        p = self.mppr.get((l["payer_group"], 2 if l.get("surg_rank", 0) == 2 else 3))
        return (p["pct"], True) if p else (0.5, False)

    def expected_allowed(self, l: dict, code: str) -> tuple[int | None, str]:
        """Rank-aware expectation: the payer's usual full rate, reduced for a second or later surgical procedure."""
        exp, basis, _r = self._expected_allowed(l, code)
        return exp, basis

    def _expected_allowed(self, l: dict, code: str) -> tuple[int | None, str, dict | None]:
        """expected_allowed, with the rate record it rests on (None for a fallback that is not the payer's own rate)."""
        rate, basis, r = self.usual_rate(l, code)
        if rate is None:
            return None, basis, None
        rank = l.get("surg_rank", 0)
        if rank >= 2 and code not in ADD_ON_CODES and is_surgical(code):
            pct, learned = self.mppr_pct(l)
            note = " (this payer's usual practice)" if learned else ""
            return int(round(rate * pct)), f"{basis}; multiple-procedure rank {rank} -> {int(pct*100)}%{note}", r
        if self._same_day_reduced(l, rate):
            pct = MEDICARE_SAME_DAY_EM["pct"]
            return (int(round(rate * pct)), f"{basis}; Medicare same-day visit and procedure rule from {MEDICARE_SAME_DAY_EM['effective']} -> "
                                            f"{int(pct*100)}%", r)
        return rate, basis, r

    def _same_day_reduced(self, l: dict, rate: int) -> bool:
        """True when the same-day rule pays this line at 80 percent: the rule covers its claim and another service on
        the claim was worth more, so this one is not the most expensive."""
        if not self._same_day_policy_claim(l):
            return False
        others = [o for o in self.lines_by_claim.get(l["claim_key"], []) if o["line_key"] != l["line_key"] and (o.get("allowed_cents") or 0) > 0]
        return any((o.get("allowed_cents") or 0) > rate for o in others)

    def downcode_expected(self, l: dict, billed: str, paid_code: str, allowed: int) -> tuple[int | None, str]:
        """Expected allowed for the billed code of a downcoded line. Where the payer has no baseline for this place of
        service, scale the amount actually allowed by the payer's office-setting ratio between the two codes."""
        _rate, _basis, r = self.usual_rate(l, billed)
        if r is None and allowed and l.get("surg_rank", 0) < 2:
            hi, _b, hr = self._rate_lookup(l, billed, "office")
            lo, _b2, lr = self._rate_lookup(l, paid_code, "office")
            if hi and lo and hr and lr and hi > lo:
                return int(round(allowed * hi / lo)), (f"allowed amount scaled by this payer's usual {billed} to {paid_code} ratio "
                                                       f"(${hi/100:.2f} to ${lo/100:.2f}); no baseline for this place of service")
        return self.expected_allowed(l, billed)

    def paid_like(self, l: dict, code: str, allowed: int) -> str | None:
        """The lower visit level whose usual rate at this payer equals the allowed amount, checked for every provider type."""
        fam = family_of(code)
        if not fam or not allowed:
            return None
        years = [l["year"]] + ([str(int(l["year"]) - 1)] if l["year"].isdigit() else [])
        for lower in sorted(EM_CODES, reverse=True):
            lf = family_of(lower)
            if not lf or lf[0] != fam[0] or lf[1] >= fam[1]:
                continue
            for y in years:
                for t in ("physician", "nonphysician", "unknown"):
                    r = self.rates.get((l["payer_group"], y, lower, t, l.get("pos_class", "office")))
                    if r and abs(r["mode"] - allowed) <= 1:
                        return lower
        return None

    def billed_higher(self, l: dict, code: str) -> tuple[str, str, list[str]] | None:
        """A higher level of the same code family that the claim file or EMA shows was billed for this claim and date,
        when the code on the remit was not billed at all. Returns (billed code, source, all billed codes)."""
        dos = l.get("service_date") or l.get("statement_from")
        billed = self.billed.get((l.get("patient_control_number"), dos))
        if not billed or code in billed:
            return None
        higher = [b for b in billed if is_downcode(b, code)]
        if not higher:
            return None
        best = max(higher, key=lambda b: (family_of(b) or ("", 0))[1])
        return best, billed[best], sorted(billed)

    # ------------------------------------------------------------------ helpers
    def _line_carcs(self, l: dict):
        adj = self.adj.get(l["line_key"], [])
        payer_adj = [(g, rc, a) for g, rc, a in adj if g in ("CO", "PI", "OA")]
        pr = sum(a for g, rc, a in adj if g == "PR")
        top, best = None, -1
        for g, rc, a in payer_adj:
            if rc in IGNORE_CARCS:
                continue
            if a > best:
                best, top = a, rc
        return adj, top, pr, {rc for _g, rc, _a in payer_adj}

    def _build_ledger(self) -> dict[str, int]:
        """What is left of every payment once the payer's takebacks are applied: {line_key: cents still paid}, with payments
        taken back in full left out. Every take is kept with its date (self.took), for the recovery a closure records.
        A reversal (claim status 22) cancels an earlier payment it reverses: the same bill, code and date, from the same payer
        when it names one (the payer by any of its ids and names seen together on the reversal's claim or the payment's: a
        payer reverses under another of its payer ids than it paid under), and of the reversal's own amount (as paid, or as
        it still stands), preferring the same claim id,
        then the same payer claim number, then the most recent. A reversal restates the payment it takes back: when no
        payment of its amount is on file, the version it reverses is not on file, and it cancels nothing (another version's
        payment stays paid; trace finding T-I5). Those reversals are counted for the Data tab (REVERSALS_NOT_ON_FILE_META).
        Only a payment dated before the reversal can be cancelled: the archive starts in 2024, and a same-day payment beside
        a reversal is the repayment of an original paid before it. A payment made again after a reversal is a new payment
        and stands.
        Claim-level takebacks follow: PLB WO (an overpayment recovered) and FB (a balance carried forward, negative, or
        recovered, positive), each read for the claim it names (_plb_names: a claim id or a payer claim number, alone or
        among other text, trace finding T-I1), netted per claim and payer (a balance carried forward and recovered later is
        taken once; an adjustment, CS or 72, naming the claim nets with them), and a reversal sent without service lines.
        Each takes back that payer's earlier payments on that claim (for an entry naming a claim, the payer by any of its
        ids and names seen together on that claim: a payer recoups under another of its payer ids, or names).
        Medicare's J1 (non-reimbursable) entries naming a claim net with that claim's WO and FB entries too. Medicare sends
        one beside its reversal and new payment of the claim, withholding what the new payment adds to the one reversed
        (or the whole new payment): it takes, from each line of that new payment, what the line adds to the reversed one
        first, then as a WO does (a J1 can take a payment of its own remittance; a WO only an earlier one).
        A PLB entry is skipped where a reversal of the claim the same day already took back as much (not a J1: it withholds
        the new payment beside the reversal), and where that payer's reversal of the claim (any date) already took the claim
        back and carried its balance forward under a reference naming no claim: the entry recovers that balance, not the
        claim's payment again. What the PLB entries took from each payment is kept (self.plb_took), for the services a
        takeback left unpaid (_taken_back_items)."""
        when = lambda l: l.get("payment_date") or l.get("txn_payment_date") or ""          # noqa: E731
        payer = lambda l: l.get("payer_id") or l.get("payer_name")                         # noqa: E731
        payer_word = lambda name: re.sub(r"[^A-Z0-9]", "", (name or "").upper())         # noqa: E731

        def company(l) -> str:
            c = payer_company(l.get("payer_group") or normalize_payer(l.get("payer_name"))[0])
            return LEDGER_COMPANIES.get(c, c)
        left: dict[str, int] = {}
        pos: dict[tuple, list] = defaultdict(list)
        revs = []
        for l in self.lines:
            st, amt = l.get("claim_status_code"), l.get("paid_cents") or 0
            if st in ("1", "19", "2", "3") and amt > 0:
                left[l["line_key"]] = amt
                pos[(bill_of(l["patient_control_number"]), l.get("procedure_code"), l.get("service_date"))].append(l)
            elif st == "22" and amt < 0:
                revs.append(l)
        # (bill, code, date) -> [(the payment's line, the day it was taken, cents)]: what each reversal and recoupment took,
        # when (a closure's recovery is the money its units got after its denial less what stood then and was taken since)
        self.took: dict[tuple, list] = defaultdict(list)
        # what the PLB entries took from each payment: (the payment's line, the day, cents, the entry: its reference, reasons,
        # payer name), for the services a takeback left unpaid (_taken_back_items)
        self.plb_took: list[tuple] = []
        # the payments whose takeback is on an item of its own (a TAKEBACK item of the line, or the recoupment's own listing),
        # and those whose takeback the payer's own denial of the service carries (_taken_back_items), marked in ledger_taken
        # once the items are made (_mark_taken_listed)
        self.taken_listed: set[str] = set()
        self.taken_on_denial: set[str] = set()

        def take_from(p, cents: int, day: str, entry: dict | None = None) -> None:
            left[p["line_key"]] -= cents
            self.took[(bill_of(p["patient_control_number"]), p.get("procedure_code"), p.get("service_date"))].append((p, day or "", cents))
            if entry is not None:
                self.plb_took.append((p, day or "", cents, entry))
        # in one order whatever order the remittances were read in, on either kind of store (the row ids follow the reading
        # order): which entry names a takeback, and the order J1 entries are applied in, must not depend on it (hosts-4)
        plb_rows = self.conn.execute("""SELECT p.reference, p.amount_cents, t.payment_date, COALESCE(t.payer_id, t.payer_name), p.transaction_key,
                                               p.reason_code, t.payer_name
                                        FROM plb p JOIN transactions t ON t.transaction_key = p.transaction_key
                                        WHERE t.superseded = 0 AND p.reason_code IN ('WO', 'FB', 'CS', '72', 'J1')
                                          AND COALESCE(p.reference, '') <> ''
                                        ORDER BY t.payment_date, p.transaction_key, p.reference, p.reason_code, p.amount_cents""").fetchall()
        # each entry under the claim it names (a balance carried under the payer's claim number and recovered under the claim
        # id, or the reverse, is one balance); an entry naming no claim on file nets under its own reference. An adjustment
        # (CS) or authorized return (72) naming a claim nets with that claim's recoupments: a payer lists a recoupment and,
        # in the same remittance, an adjustment of the same amount naming the claim that cancels it. Only a claim with a WO,
        # FB or J1 entry is taken back; CS, 72 and J1 entries naming no claim are fees and other money, not the claim's.
        names, named = self._plb_names(plb_rows)
        # The payer of a claim: one payer sends remittances under several payer ids and names (a recoupment or a reversal
        # under another id than the payment it takes back, or under the same id with its plan's name). For each claim a PLB
        # entry names, and each claim of a reversal and of the payments it may cancel, an id and a name seen together on one
        # of its answers, reversals or entries are one payer; they are joined claim by claim, never across claims. An entry
        # naming no claim keeps its payer id. `by` is ("payer", the claim's payer the entry belongs to) or ("key", its payer id).
        parent: dict = {}

        def find(x):
            root = x
            while parent.get(root, root) != root:
                root = parent[root]
            while x != root:
                parent[x], x = root, parent.get(x, x)
            return root

        def join(claim, key, pname) -> None:
            word = payer_word(pname)
            a, b = find((claim, "K", key)), find((claim, "N", word))
            if word and a != b:
                parent[a] = b
        svc = lambda l: (bill_of(l["patient_control_number"]), l.get("procedure_code"), l.get("service_date"))      # noqa: E731
        claim_ids = sorted({name for name in names if name in named} | {r["patient_control_number"] for r in revs}
                           | {p["patient_control_number"] for r in revs for p in pos.get(svc(r), [])})
        reversals: list[tuple] = []         # (claim id, payer id, remittance) of the named claims' reversals that took money
        dos_of: dict[str, str] = {}         # a named claim's first date of service (its statement), for the Data tab's count
        for i in range(0, len(claim_ids), 500):
            part = claim_ids[i:i + 500]
            for pcn, st, paid_, who_, pname, tk, dos in self.conn.execute(
                    f"""SELECT c.patient_control_number, c.claim_status_code, c.paid_cents, COALESCE(t.payer_id, t.payer_name), t.payer_name,
                               c.transaction_key, c.statement_from FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
                        WHERE t.superseded = 0 AND c.patient_control_number IN ({','.join('?' * len(part))})""", part):
                join(pcn, who_, pname)
                if st == "22" and (paid_ or 0) < 0 and pcn in named:
                    reversals.append((pcn, who_, tk))
                if dos and (pcn not in dos_of or dos < dos_of[pcn]):
                    dos_of[pcn] = dos
        for (_ref, _amount, _day, who, _tk, _code, pname), name in zip(plb_rows, names):
            if name in named:
                join(name, who, pname)

        def one_payer(a, b, claims) -> bool:
            """Payer ids a and b are one payer on one of these claims."""
            return a == b or any(find((c, "K", a)) == find((c, "K", b)) for c in claims)
        reversed_on: Counter = Counter()        # (claim id or payer claim number, date) -> cents a line reversal took back
        lost: list[tuple] = []                  # reversals of a payment not on file: (claim id, cents, date of service, payer)
        if revs:
            keys = sorted({p["claim_key"] for r in revs for p in pos.get(svc(r), [])} | {r["claim_key"] for r in revs})
            pccn: dict[str, str | None] = {}
            for i in range(0, len(keys), 500):
                part = keys[i:i + 500]
                pccn.update(self.conn.execute(f"SELECT claim_key, payer_claim_control_number FROM claims WHERE claim_key IN ({','.join('?' * len(part))})",
                                              part).fetchall())
            for r in sorted(revs, key=when):
                need = amount = -r["paid_cents"]
                cands = [p for p in pos.get(svc(r), []) if left[p["line_key"]] > 0 and when(p) < when(r)
                         and (not payer(r) or one_payer(payer(r), payer(p), (r["patient_control_number"], p["patient_control_number"])))]
                # only a payment of the reversal's own amount: one of another amount is another version's payment (T-I5)
                cands = [p for p in cands if p["paid_cents"] == amount or left[p["line_key"]] == amount]
                if not cands and pccn.get(r["claim_key"]):
                    # a payer that reverses under another id and name of its company than it paid under (an administrator
                    # reversing under its former name: Wausau for UMR) never joins by id or name: the payer's own claim
                    # number and the amount pair them, within one payer company
                    cands = [p for p in pos.get(svc(r), []) if left[p["line_key"]] > 0 and when(p) < when(r)
                             and (p["paid_cents"] == amount or left[p["line_key"]] == amount)
                             and pccn.get(p["claim_key"]) == pccn.get(r["claim_key"]) and company(p) and company(p) == company(r)]
                if not cands:
                    lost.append((r["patient_control_number"], amount, r.get("service_date"), payer(r)))
                    continue
                cands.sort(key=when, reverse=True)          # most recent first, then (a stable sort) the closest match first
                cands.sort(key=lambda p: (p["patient_control_number"] != r["patient_control_number"],
                                          not pccn.get(r["claim_key"]) or pccn.get(p["claim_key"]) != pccn.get(r["claim_key"])))
                for p in cands:
                    take = min(left[p["line_key"]], need)
                    take_from(p, take, when(r))
                    need -= take
                    if need <= 0:
                        break
                taken = amount - max(need, 0)
                for ref in {r["patient_control_number"], pccn.get(r["claim_key"])} - {None}:
                    reversed_on[(ref, when(r))] += taken
        takebacks = []              # (references, cents, date, payer, kind)
        net: dict[tuple, list] = defaultdict(lambda: [0, ""])
        carried: dict[str, int] = defaultdict(int)      # remittance -> what it carried forward under references naming no claim
        recouped: set[tuple] = set()    # (name, by) with a WO, FB or J1 entry
        j1s: dict[tuple, list] = defaultdict(list)      # (name, by) -> its J1 entries: (remittance, cents)
        entry_of: dict[tuple, dict] = {}                # (name, by) -> what names it: its largest entry's reference, the reasons
        self.plb_wo_keys: dict[tuple, str] = {}         # (remittance, reference, cents) of a WO entry naming a claim -> that claim
        self.plb_wo_by: dict[tuple, tuple] = {}         # the same keys -> (claim, by): the claim's entries net together
        entries_of: dict[tuple, list] = defaultdict(list)   # (name, by) -> its WO, FB and J1 entries taking money: (key, cents, payer)
        for (ref, amount, day, who, tk, code, pname), name in zip(plb_rows, names):
            if code not in ("WO", "FB") and name not in named:
                continue
            by = ("payer", find((name, "K", who))) if name in named else ("key", who)
            n = net[(name, by)]
            n[0] += amount or 0
            n[1] = max(n[1], day or "")
            if code in ("WO", "FB", "J1"):
                recouped.add((name, by))
                if (amount or 0) < 0 and name not in named:
                    carried[tk] += -(amount or 0)
                if (amount or 0) > 0:
                    entries_of[(name, by)].append(((tk, ref, amount), amount, who, code, day or ""))
            if code == "J1":
                j1s[(name, by)].append((tk, amount or 0))
            if code == "WO" and name in named:
                self.plb_wo_keys[(tk, ref, amount)] = name
                self.plb_wo_by[(tk, ref, amount)] = (name, by)
            e = entry_of.setdefault((name, by), {"reference": ref, "reasons": [], "payer": pname, "cents": amount or 0, "claim": name,
                                                  "day": day or ""})
            e["reasons"] = sorted(set(e["reasons"]) | {code})
            # the largest entry names the takeback; of equal ones, the latest, then the reference: the same one whatever order
            # the remittances were read in, on either kind of store (hosts-4)
            if ((amount or 0), day or "", ref or "") > (e["cents"], e["day"], e["reference"] or ""):
                e.update(reference=ref, payer=pname, cents=amount or 0, day=day or "")
        takebacks += [((name,), amount, day, by, "j1" if (name, by) in j1s else "wo") for (name, by), (amount, day) in net.items()
                      if amount > 0 and (name, by) in recouped]
        # the lines of each remittance carrying a J1 entry for the claim it names: the reversal and the new payment it sits beside
        j1_lines: dict[tuple, list] = defaultdict(list)
        want_j1 = {(tk, name) for (name, _by), ents in j1s.items() for tk, _c in ents}
        if want_j1:
            for l in self.lines:
                if (l["transaction_key"], l["patient_control_number"]) in want_j1:
                    j1_lines[(l["transaction_key"], l["patient_control_number"])].append(l)
        # (claim id, payer) whose reversal sits in a remittance that carried a balance forward without naming the claim: a
        # later PLB entry naming the claim recovers that balance (a payer carries it under its own number and recovers it
        # under the claim's), which the reversal already took from the claim. Only up to what that reversal left owed in its
        # remittance: its amount less the same remittance's re-payment of the bill by that payer (a re-adjudication paying the
        # same again leaves nothing), and no more than the remittance carried. Any other recoupment of the claim, later or
        # larger, takes its payments as usual (money-5)
        deferred: dict[tuple, int] = defaultdict(int)
        held = sorted({(pcn, who_, tk) for pcn, who_, tk in reversals if carried.get(tk, 0) > 0})
        if held:
            in_tk: dict[str, list] = defaultdict(list)
            tks = sorted({tk for _p, _w, tk in held})
            for i in range(0, len(tks), 500):
                part = tks[i:i + 500]
                for tk, pcn, st, paid_, who_ in self.conn.execute(
                        f"""SELECT c.transaction_key, c.patient_control_number, c.claim_status_code, c.paid_cents, COALESCE(t.payer_id, t.payer_name)
                            FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
                            WHERE c.transaction_key IN ({','.join('?' * len(part))})""", part):
                    in_tk[tk].append((pcn, st, paid_ or 0, who_))
            # what the remittance carried is shared by the claims it reversed, in one order (the remittance, then the claim id),
            # so their budgets never add up to more than it carried (fx-ledger-r2-8)
            spare = dict(carried)
            for pcn, who_, tk in sorted(held, key=lambda h: (h[2], h[0], str(h[1]))):
                root = find((pcn, "K", who_))
                if (pcn, ("payer", root), tk) in deferred:
                    continue
                rev = sum(-paid_ for p, st, paid_, w in in_tk[tk] if p == pcn and st == "22" and paid_ < 0 and find((pcn, "K", w)) == root)
                again = sum(paid_ for p, st, paid_, w in in_tk[tk] if bill_of(p) == bill_of(pcn) and st in ("1", "2", "3", "19") and paid_ > 0
                            and one_payer(who_, w, (pcn, p)))
                use = max(0, min(rev - again, spare.get(tk, 0)))
                spare[tk] = spare.get(tk, 0) - use
                deferred[(pcn, ("payer", root), tk)] = use
            budget: dict[tuple, int] = defaultdict(int)
            for (pcn, by_, _tk), cents in deferred.items():
                budget[(pcn, by_)] += cents
            deferred = budget
        # what the ledger could not apply of the recoupments naming a claim: (name, by) -> cents, for the Data tab's count of
        # takebacks of payments not on file and for a recoupment's own listing (money-1, money-6)
        self.plb_unapplied: dict[tuple, int] = {}
        # what the ledger set out to take back for a claim (its entries' net, less a balance its reversal had carried):
        # (name, by) -> cents. A claim whose entries net to nothing, or that the same remittance's reversal took back, has
        # none: its recoupments take back nothing of their own (fx-ledger-r2-1)
        self.plb_claimed: dict[tuple, int] = {}
        self.plb_entries = dict(entries_of)
        takebacks += [(tuple(x for x in (pcn, cn) if x), -paid, day, ("key", who), "reversal") for pcn, cn, paid, day, who in self.conn.execute(
            """SELECT c.patient_control_number, c.payer_claim_control_number, c.paid_cents, COALESCE(c.payment_date, t.payment_date), COALESCE(t.payer_id, t.payer_name)
               FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
               WHERE t.superseded = 0 AND c.claim_status_code = '22' AND c.paid_cents < 0
                 AND NOT EXISTS (SELECT 1 FROM lines l WHERE l.claim_key = c.claim_key)""")]
        by_id: dict[str, set] = defaultdict(set)        # claim id -> claims
        by_number: dict[str, set] = defaultdict(set)    # payer claim number -> claims
        if takebacks:
            refs = sorted({ref for t in takebacks for ref in t[0]})
            for i in range(0, len(refs), 500):
                part = refs[i:i + 500]
                q = ','.join('?' * len(part))
                for ck, pcn, cn in self.conn.execute(f"""SELECT claim_key, patient_control_number, payer_claim_control_number FROM claims
                                                        WHERE patient_control_number IN ({q}) OR payer_claim_control_number IN ({q})""", part + part):
                    if pcn in part:
                        by_id[pcn].add(ck)
                    if cn in part:
                        by_number[cn].add(ck)
            for refs_, amount, day, by, kind in sorted(takebacks, key=lambda t: (t[2] or "", t[0])):
                if kind == "wo" and max((reversed_on.get((ref, day), 0) for ref in refs_), default=0) >= amount:
                    continue            # the same remittance reversed that claim: the WO reports the same money again
                if kind in ("wo", "j1"):
                    for ref in refs_:
                        # that payer's reversal took the claim back and carried the balance this recovers: up to that balance
                        use = min(deferred.get((ref, by), 0), amount)
                        if use > 0:
                            deferred[(ref, by)] -= use
                            amount -= use
                    if amount <= 0:
                        continue
                    if by[0] == "payer":
                        self.plb_claimed[(refs_[0], by)] = self.plb_claimed.get((refs_[0], by), 0) + amount
                ours = ((lambda l: find((refs_[0], "K", payer(l))) == by[1]) if by[0] == "payer" else    # noqa: E731
                        (lambda l: by[1] is None or payer(l) == by[1]))
                entry = entry_of.get((refs_[0], by)) if kind != "reversal" else None
                for tk, cents in j1s.get((refs_[0], by), []):
                    # a J1 withholds what the new payment beside it adds to the one it reverses, line by line (the same code,
                    # date and modifiers reversed in that remittance), or the whole new payment where nothing was reversed
                    ls = j1_lines.get((tk, refs_[0]), [])
                    back: dict[tuple, list] = defaultdict(list)
                    for x in ls:
                        if x.get("claim_status_code") == "22":
                            back[(x.get("procedure_code"), x.get("service_date"), x.get("modifiers"))].append(-(x.get("paid_cents") or 0))
                    for x in ls:
                        if x.get("claim_status_code") == "22" or left.get(x["line_key"], 0) <= 0 or not ours(x):
                            continue
                        was = back[(x.get("procedure_code"), x.get("service_date"), x.get("modifiers"))]
                        more = min((x.get("paid_cents") or 0) - (was.pop(0) if was else 0), left[x["line_key"]], cents, amount)
                        if more > 0:
                            take_from(x, more, day, entry)
                            cents -= more
                            amount -= more
                if amount <= 0:
                    continue
                paid = []
                # the claim the takeback names by its id first; its payer claim number only when nothing under that id stands
                # (a J1 also takes a payment of its own remittance: it withholds the payment beside it)
                for index in (by_id, by_number):
                    paid = [l for ck in sorted(set().union(*(index.get(ref, set()) for ref in refs_))) for l in self.lines_by_claim.get(ck, [])
                            if left.get(l["line_key"], 0) > 0 and (when(l) < (day or "~") or (kind == "j1" and when(l) == day)) and ours(l)]
                    if paid:
                        break
                for l in sorted(paid, key=lambda l: (left[l["line_key"]] != amount, -left[l["line_key"]], l["line_key"])):
                    take = min(left[l["line_key"]], amount)
                    take_from(l, take, day, entry)
                    amount -= take
                    if amount <= 0:
                        break
                if amount > 0 and kind in ("wo", "j1") and by[0] == "payer":
                    # money the payer took naming a claim whose payment is not on file (or no longer stands): a gap, never
                    # dropped (money-1)
                    self.plb_unapplied[(refs_[0], by)] = amount
        # each claim a recoupment named whose money the ledger could not apply: (claim id, cents, entries, payer, date of
        # service), counted for the Data tab beside the reversals of payments not on file
        not_applied = []
        for (name, by), cents in sorted(self.plb_unapplied.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            ents = entries_of.get((name, by)) or []
            dos = dos_of.get(name) or min((l.get("service_date") or l.get("statement_from") or "" for ck in by_id.get(name, set())
                                           for l in self.lines_by_claim.get(ck, [])), default="")
            not_applied.append((name, cents, len(ents), ents[0][2] if ents else None, dos))
        not_applied += self._recouped_not_on_file(plb_rows, names, named)
        self._count_not_on_file(lost, not_applied)
        self._save_taken(left)
        return {k: v for k, v in left.items() if v > 0}

    def _save_taken(self, left: dict) -> None:
        """What the recoupments (PLB WO, FB, J1 naming a claim) took from each payment, saved for the item states: the states
        read it beside each 835 answer (states._r835), so a payment a takeback left unpaid never proves an item paid
        (paid_proof sees reversals on the remittances themselves, not recoupments on other checks). ledger_taken holds one row
        per payment line a recoupment took from: the cents taken, the day of the last take, what still stands, and 'listed'
        (1 once the takeback is on an item of its own: a TAKEBACK item of the line, or the recoupment's own listing for a
        claim it took from only in part; 2 when the payer's own denial of the service, which the payment had answered, carries
        it; _mark_taken_listed). A listed takeback is worked on that item, so the payment still answers the other items of the
        service (the same money never worked twice); an unlisted one is on no item but the service's own answer, which the
        payment then no longer pays. Rebuilt on every run, on either kind of store (the dashboard's copy does not carry it:
        its states are the stored ones)."""
        per: dict[str, list] = {}
        for p, day, cents, _entry in getattr(self, "plb_took", None) or []:
            r = per.setdefault(p["line_key"], [0, ""])
            r[0] += cents
            r[1] = max(r[1], day or "")
        c = self.conn
        c.execute("DROP TABLE IF EXISTS ledger_taken")
        c.execute("CREATE TABLE ledger_taken (line_key TEXT PRIMARY KEY, taken_cents INTEGER, taken_on TEXT, standing_cents INTEGER, "
                  "listed INTEGER NOT NULL DEFAULT 0)")
        c.executemany("INSERT INTO ledger_taken (line_key, taken_cents, taken_on, standing_cents) VALUES (?,?,?,?)",
                      [(lk, v[0], v[1] or None, max(0, left.get(lk, 0))) for lk, v in sorted(per.items())])

    def _mark_taken_listed(self) -> None:
        """ledger_taken's 'listed' mark (_save_taken) for the payments whose takeback is now on an item: 1, an item of its own
        (a TAKEBACK item of the line, or the recoupment's own listing); 2, the payer's own denial of the service, which the
        payment had answered (_taken_back_items' 'mine')."""
        own = set(getattr(self, "taken_listed", None) or ())
        marks = [(1, sorted(own)), (2, sorted(set(getattr(self, "taken_on_denial", None) or ()) - own))]
        if not any(keys for _v, keys in marks) or not self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger_taken'").fetchone():
            return
        for value, keys in marks:
            for i in range(0, len(keys), 500):
                part = keys[i:i + 500]
                self.conn.execute(f"UPDATE ledger_taken SET listed = {value} WHERE line_key IN ({','.join('?' * len(part))})", part)

    def _plb_names(self, rows: list) -> tuple[list[str], set[str]]:
        """The claim each PLB entry (rows of reference, amount, date, payer, ...) names, for netting and taking back,
        and the set of those that are claims on file. A reference names a claim when it is its claim id, or holds one among
        other text (CLAIM_ID_IN_TEXT: the payer's own claim number before or after it, a letter, a date; the claim id must be
        on file, and only one); else when it is, or holds as a word of its own, a payer claim number of that payer that
        names one claim (a number shared by several claims is no name). Any other reference names itself: it nets only with
        entries that carry the same text, and is looked up whole (trace finding T-I1: references were looked up whole only,
        so a recoupment written '<the payer's number> <claim id>' was never applied)."""
        refs = [str(r[0] or "").strip() for r in rows]
        inside = [sorted(set(CLAIM_ID_IN_TEXT.findall(x.upper()))) for x in refs]
        words = [[w for w in re.split(r"[^0-9A-Za-z]+", x) if len(w) >= 6 and w != x] for x in refs]
        on_file: set[str] = set()
        want = sorted({x for x in refs if x} | {i for f in inside for i in f})
        for i in range(0, len(want), 500):
            part = want[i:i + 500]
            on_file.update(r[0] for r in self.conn.execute(f"SELECT DISTINCT patient_control_number FROM claims WHERE patient_control_number IN "
                                                           f"({','.join('?' * len(part))})", part))
        ids_of_number: dict[tuple, set] = defaultdict(set)
        want = sorted({x for x in refs if x} | {w for ws in words for w in ws})
        for i in range(0, len(want), 500):
            part = want[i:i + 500]
            for pcn, cn, who_ in self.conn.execute(f"""SELECT c.patient_control_number, c.payer_claim_control_number, COALESCE(t.payer_id, t.payer_name)
                                                       FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
                                                       WHERE c.payer_claim_control_number IN ({','.join('?' * len(part))})""", part):
                ids_of_number[(cn, who_)].add(pcn)
        number_to_id = {k: next(iter(v)) for k, v in ids_of_number.items() if len(v) == 1 and next(iter(v))}
        names: list[str] = []
        named: set[str] = set()
        for (_ref, _amount, _day, who, *_rest), x, ids, ws in zip(rows, refs, inside, words):
            ids = [i for i in ids if i in on_file]
            nums = {number_to_id[(w, who)] for w in ws if (w, who) in number_to_id}
            name = (x if x in on_file else number_to_id.get((x, who)) or (ids[0] if len(ids) == 1 else None)
                    or (next(iter(nums)) if not ids and len(nums) == 1 else None))
            if name:
                named.add(name)
            names.append(name or x)
        return names, named

    def _recouped_not_on_file(self, rows: list, names: list, named: set) -> list[tuple]:
        """The recoupments naming a claim id no remittance on file answers (a claim paid and recouped outside the archive):
        PLB entries whose reference holds exactly one claim id (CLAIM_ID_IN_TEXT) that is not on file, netted by claim id and
        payer (WO, FB and J1 with the CS and 72 entries naming it, as the ledger nets a claim's entries). A positive net with a
        WO, FB or J1 entry took money no payment on file shows: (claim id, cents, entries, payer, the first date of service of
        the bill's claims on file, older), counted for the Data tab beside the rest (fx-ledger-r2-7). With nothing of the bill
        on file it is counted as older than the archive."""
        off: dict[tuple, list] = {}
        for (ref, amount, _day, who, _tk, code, _pname), name in zip(rows, names):
            if name in named:
                continue
            ids = set(CLAIM_ID_IN_TEXT.findall(str(ref or "").upper()))
            if len(ids) != 1:
                continue
            g = off.setdefault((next(iter(ids)), who), [0, 0, False])
            g[0] += amount or 0
            if code in ("WO", "FB", "J1") and (amount or 0) > 0:
                g[1] += 1
                g[2] = True
        out = []
        for (cid, who), (cents, n, took_money) in sorted(off.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            if cents <= 0 or not took_money:
                continue
            b = bill_of(cid)
            row = self.conn.execute("""SELECT MIN(statement_from) FROM claims WHERE patient_control_number >= ? AND patient_control_number < ?""",
                                    (b, b + "~")).fetchone() if b else None
            dos = row[0] if row and row[0] else ""
            out.append((cid, cents, n, who, dos, not dos))
        return out

    def _count_not_on_file(self, lost: list[tuple], recouped: list[tuple] | None = None) -> None:
        """REVERSALS_NOT_ON_FILE_META's counts: the takebacks of payments that are not on file. From the line reversals that
        found no payment of their amount, (claim id, cents, date of service, payer), and the recoupments (PLB WO, FB or J1)
        naming a claim whose money the ledger could not apply, (claim id, cents, entries, payer, date of service[, older]):
        a payer took the money back and no payment on file shows what it took (money-1; a claim id no remittance on file
        answers comes with older set when nothing of its bill is on file: _recouped_not_on_file). A service dated on or after the payer's
        first remittance on file was paid in a remittance that should be on file too: that one is missing. The rest take
        back payments made before the remittances on file begin. "claims" and "cents" count both kinds (a claim once);
        "lines" counts the reversals' lines and "entries" the recoupments' entries, with "recouped_claims" and
        "recouped_cents" for the recoupments alone (each with its "older_" twin)."""
        keys = ("claims", "lines", "cents", "entries", "recouped_claims", "recouped_cents")
        self.not_on_file = {pre + k: 0 for pre in ("", "older_") for k in keys}
        if not lost and not recouped:
            return
        first = {who: day for who, day in self.conn.execute(
            """SELECT COALESCE(payer_id, payer_name), MIN(payment_date) FROM transactions WHERE superseded = 0 GROUP BY 1""")}
        claims: dict[str, set] = {"": set(), "older_": set()}
        took: dict[str, set] = {"": set(), "older_": set()}
        for pcn, cents, dos, who in lost:
            pre = "older_" if (dos or "") < (first.get(who) or "") else ""
            claims[pre].add(pcn)
            self.not_on_file[pre + "lines"] += 1
            self.not_on_file[pre + "cents"] += cents
        for pcn, cents, n, who, dos, *older in recouped or []:
            pre = "older_" if (older and older[0]) or (dos and dos < (first.get(who) or "")) else ""
            claims[pre].add(pcn)
            took[pre].add(pcn)
            self.not_on_file[pre + "entries"] += n
            self.not_on_file[pre + "cents"] += cents
            self.not_on_file[pre + "recouped_cents"] += cents
        for pre, ids in claims.items():
            self.not_on_file[pre + "claims"] = len(ids)
            self.not_on_file[pre + "recouped_claims"] = len(took[pre])

    def paid_cover(self, bill: str, code: str | None, dos: str | None, expected: int | None = None, after: str | None = None) -> list[tuple]:
        """The payments still standing for this bill, code and date that count as paying it: a primary payment, or a
        secondary one covering at least half the expected amount; made after `after` when given. An undated line (a claim
        over several days with no line dates) matches the bill's undated payments of the code."""
        if not code:
            return []
        evs = self.paid_events.get((bill, code, dos), []) if dos else [e for e in self.paid_by_code.get((bill, code), []) if e[7] is None]
        return [e for e in evs if (after is None or (e[0] and e[0] > after)) and (e[3] in ("1", "19") or (expected and (e[2] or 0) >= 0.5 * expected))]

    def with_second_shares(self, bill: str, code: str | None, dos: str | None, events: list[tuple]) -> list[tuple]:
        """These payments of a service (from paid_cover), with the second payers' payments of it made on or after the first
        primary payment among them, whatever their amount: a secondary paying its share after the primary's payment is part
        of what the service brought, for the recovery a closure records and the payments it names. paid_cover leaves a
        secondary under half the expected amount out, which is right for whether a service is paid: a coinsurance alone
        never pays a primary's denial."""
        first = min((e[0] for e in events if e[3] in ("1", "19") and e[0]), default=None)
        if not first or not code:
            return events
        units = sum(e[5] for e in events if e[3] in ("1", "19"))
        evs = self.paid_events.get((bill, code, dos), []) if dos else [e for e in self.paid_by_code.get((bill, code), []) if e[7] is None]
        out, got = list(events), Counter()
        # each second payer's share of the units the primary paid, its first payments after the primary's: a second payer
        # that paid the same share twice is not two shares
        for e in sorted((e for e in evs if e[3] in ("2", "3") and (e[0] or "") >= first and e not in events), key=lambda e: (e[0], e[8])):
            if got[e[6]] < units:
                got[e[6]] += e[5]
                out.append(e)
        return out

    @staticmethod
    def units_paid(events: list[tuple]) -> float:
        """Units these standing payments pay: the primary payments' units (a secondary pays its share of the same units), or
        the secondary payments' units when no primary payment stands."""
        prim = [e for e in events if e[3] in ("1", "19")]
        return sum(e[5] for e in (prim or events))

    def units_billed(self, claim_key: str, code: str | None, dos: str | None) -> float:
        """Units billed on a claim for a code and date, over every line of it (two specimens of 88305 are two units): one
        payment for one of them must not close the other's denial. A $0.00 line that carries no adjustments bills nothing
        (trace finding T-N7: a note line beside the service is no second unit)."""
        return sum(self.billed_units_of(ln) for ln in self.lines_by_claim.get(claim_key, [])
                   if ln.get("procedure_code") == code and ln.get("service_date") == dos
                   and ((ln.get("charge_cents") or 0) != 0 or self.adj.get(ln["line_key"]))) or 1

    def _units_from_bill(self, l) -> float | None:
        """The units a line billed when its remittance does not say (SVC05 empty or 0): SVC07, else the 837 line or EMA's
        charge line of its claim id, code and date. Read only where the line's adjustments and payment account for its whole
        charge, which says the payer answered every unit billed (the owner's rule on units, section 3 fix 2); else None."""
        charge = l.get("charge_cents") or 0
        if not charge or abs(charge - (l.get("paid_cents") or 0) - sum(a for _g, _rc, a in self.adj.get(l.get("line_key")) or ())) > 1:
            return None
        got = abs(l.get("original_units") or 0) or None
        if got is None:
            by_charge = getattr(self, "bill_units", {}).get((l.get("patient_control_number"), l.get("service_date"), l.get("procedure_code")))
            if by_charge:
                got = by_charge.get(abs(charge)) or (next(iter(by_charge.values())) if len(by_charge) == 1 else None)
        return got

    def billed_units_of(self, l) -> float:
        """The units a line billed: SVC07, else SVC05, else (both empty or 0) the units billed as _units_from_bill reads
        them, else 1."""
        return abs(l.get("original_units") or 0) or abs(l.get("paid_units") or 0) or self._units_from_bill(l) or 1

    def paid_units_of(self, l) -> float:
        """The units a payment pays: SVC05, else (empty or 0) the units billed where the line's adjustments account for its
        whole charge, else 1."""
        return abs(l.get("paid_units") or 0) or self._units_from_bill(l) or 1

    def billed_units(self, l) -> float:
        """The units one line billed, for sizing its denial: the units the payer reports as billed (SVC07) or the line's
        own (SVC05), else the units billed as the 837 or EMA give them (billed_units_of), at least 1. A drug or a skin
        substitute (its units are doses, priced by remitwatch.archive_rules) and a count past 20 (a misread quantity) count
        once."""
        code = l.get("procedure_code") or ""
        if DRUG_RE.match(code) or SKIN_SUBSTITUTE_RE.match(code):
            return 1
        u = self.billed_units_of(l)
        return u if 1 < u <= MAX_UNITS else 1

    def line_expected(self, l, code: str | None = None, units: float | None = None) -> tuple[int | None, str]:
        """What a line's denial is worth: the payer's own rate per unit for the line (expected_allowed: its component, rank
        and the clinician's recognised tier) times the units it billed, never above the charge billed (a payer allows the
        lesser of the two). (None, why) where there is no rate."""
        code = code or l.get("procedure_code")
        exp, basis, rec = self._expected_allowed(l, code)
        units = self.billed_units(l) if units is None else units
        charge = l.get("charge_cents") or 0
        # a size that is not the payer's own rate is never below the least the payer has paid for the code in this setting
        # (fix7c-r1-4: a surgery center's claim sized by the payer's share of charges, or by another payer's amounts, below
        # every amount it pays there itself); a later procedure's own reduction and a PA's or NP's rate stay below it
        floor = (self._own_floor(l, code) if code and rec is None and not is_unlisted(code) and l.get("surg_rank", 0) < 2
                 and l.get("provider_tier") != "nonphysician" else None)
        if exp is None:
            if DRUG_RE.match(code or "") or SKIN_SUBSTITUTE_RE.match(code or ""):
                return None, basis          # a drug's or skin substitute's units are doses: priced by archive rule 10, never by a share
            got, why = self.no_rate_size(l, code, charge, units)
            if got is None and is_unlisted(code) and charge > 0:
                return charge, f"{basis}; the charge billed, the most it can bring"
            if floor and (got or 0) < floor * max(units, 1):
                got, why = int(round(floor * max(units, 1))), (f"the least {l.get('payer_group') or 'the payer'} has paid for {code} in "
                                                               f"this setting (${floor / 100:,.2f}), above {why[0].lower() + why[1:]}")
                if 0 < charge < got:
                    got, why = charge, f"{why}, capped at the line's charge"
            return got, (f"{basis}; {why}" if is_unlisted(code) else why)
        if floor and exp < floor and not self._same_day_reduced(l, floor):
            exp, basis = floor, f"{basis}, raised to the least {l.get('payer_group') or 'the payer'} has paid for {code} in this setting"
        if units > 1:
            exp, basis = int(round(exp * units)), f"{basis} x {units:g} units billed"
        if 0 < charge < exp:
            exp, basis = charge, f"{basis}, capped at the line's charge"
        return exp, basis

    def crossover_carrier(self, claim_key: str | None) -> str | None:
        """The carrier a remittance says it sent the claim on to (NM1*TT), if it names one."""
        if not claim_key:
            return None
        cache = self.__dict__.setdefault("_crossover", {})
        if claim_key not in cache:
            r = self.conn.execute("SELECT crossover_carrier_name FROM claims WHERE claim_key=?", (claim_key,)).fetchone()
            cache[claim_key] = ((r[0] or "").strip() or None) if r else None
        return cache[claim_key]

    def later_payment(self, l: dict, code: str | None = None, expected: int | None = None, min_paid: int | None = None) -> tuple | None:
        """The later remit that paid this bill, code and date of service (a reworked or appealed line), as one event: its
        date, trace and claim are the earliest payment's; amount and units are those of every payment counted.
        A primary payment counts; a secondary payment counts only when it covers at least half the expected amount.
        For a line paid nothing, the later payments must cover the units it billed; short of that the line is recorded in
        self.partly_paid for a hint. `min_paid` is for lines that were paid short under the same code: the later payment
        must be clearly larger than the short one, so a repeat of the same reduced payment does not close the finding.
        The event carries (LaterPayment.wants) what the closure's recovery is measured from (_recover): the service, the
        line's own units, and every later payment of the service that counts as paying it."""
        mine = l.get("payment_date") or l.get("txn_payment_date") or ""
        code = code or l.get("procedure_code")
        standing = self.paid_cover(bill_of(l.get("patient_control_number")), code, l.get("service_date"), expected)
        # later: after this remittance, or in it on another claim of the bill (a corrected claim paid as the original was denied)
        later = [e for e in standing if e[0] and (e[0] > mine or (e[0] == mine and e[4] != l.get("patient_control_number")))]
        events = later if min_paid is None else [e for e in later if (e[2] or 0) >= min_paid]
        if not events:
            return None
        first = min(events)
        if not (l.get("paid_cents") or 0):
            # the bill's payments still standing for this code and date must cover every unit its claim billed for them
            need, got = self.units_billed(l["claim_key"], code, l.get("service_date")), self.units_paid(standing)
            if got < need:
                self.partly_paid[l["line_key"]] = (first, got, need)
                return None
        out = LaterPayment(first if len(events) == 1 else first[:2] + (sum(e[2] for e in events),) + first[3:5] + (self.units_paid(events),) + first[6:])
        bill = bill_of(l.get("patient_control_number"))
        out.wants = [((bill, code, l.get("service_date")), self.billed_units_of(l),
                      self.with_second_shares(bill, code, l.get("service_date"), later))]
        return out

    def _recover(self, own: dict | None, mine: str, own_pcn: str | None, wants: list) -> tuple[int, int, list, bool]:
        """What a closure recovered (trace findings T-C5, T-C6): the money the item's own units got after its answer (`mine`,
        on the item's claim id `own_pcn`), from the later standing payments of each service it lists (wants: [((bill,
        code, date), the item's units of it, those payments)]).
        Each payer's payments give the item's units in date order (a payment for the same modifiers as the item's own line
        first), and each unit pays one finding only (self.used_units, by the paying line): a payer that paid the line twice
        counts once, a second payer's payment of the same units (a supplemental plan after Medicare) counts too. The money is
        capped by what the service gained after the item's answer: those payments, less what stood then and has been taken
        back since (a payment that only replaces one the payer reversed is no recovery, and a short payment the payer
        reversed and paid again recovered the difference), less what other findings already took from them.
        Returns (cents recovered, cents of the payments that paid the item's units, those payments as [event, units, cents],
        whether every unit of them was already counted on other findings)."""
        when = lambda p: p.get("payment_date") or p.get("txn_payment_date") or ""          # noqa: E731
        before = lambda p: when(p) < mine or (when(p) == mine and p["patient_control_number"] == own_pcn)     # noqa: E731
        own_key = (bill_of(own.get("patient_control_number")), own.get("procedure_code"), own.get("service_date")) if own else None
        rec_all, value_all, picks_all, taken_elsewhere = 0.0, 0.0, [], False
        for key, k, evs in wants:
            if not evs:
                continue
            # money that stood when the item was answered and was taken back since: from this service's payments, and from
            # the item's own line when it is another service (a downcode paid again under the code billed)
            lost = sum(c for p, day, c in self.took.get(key, ()) if day > mine and before(p))
            if own is not None and own_key != key:
                lost += sum(c for p, day, c in self.took.get(own_key, ()) if p["line_key"] == own["line_key"] and day > mine)
            budget = sum(e[2] for e in evs) - lost - sum(self.used_units.get(e[8], (0, 0))[1] for e in evs)
            mods = own.get("modifiers") if own else None
            by_payer: dict = defaultdict(list)
            for e in sorted(evs, key=lambda e: (bool(own) and e[9] != mods, e[0], e[1] or "", e[8])):
                by_payer[e[6]].append(e)
            value, picks = 0.0, []
            for es in by_payer.values():
                need = k
                for e in es:
                    if need <= 0:
                        break
                    free = e[5] - self.used_units.get(e[8], (0, 0))[0]
                    if free <= 0:
                        taken_elsewhere = True
                        continue
                    units = min(free, need)
                    picks.append([e, units, e[2] * units / e[5]])
                    value += picks[-1][2]
                    need -= units
            rec = max(0.0, min(value, budget))
            for p in picks:             # the units are this finding's now, with their share of what it recovered
                used = self.used_units.setdefault(p[0][8], [0, 0.0])
                used[0] += p[1]
                used[1] += p[2] * rec / value if value else 0
            rec_all, value_all, picks_all = rec_all + rec, value_all + value, picks_all + picks
        return int(round(rec_all)), int(round(value_all)), picks_all, taken_elsewhere and not picks_all

    @staticmethod
    def _paid_words(picks: list, fallback: tuple) -> tuple[str, dict]:
        """The closure note's words for the payments that paid the item's units (picks from _recover), else for the later
        payment found (`fallback`: date, trace, cents), and the evidence's paid_later fields: the first of them, the cents
        they paid for the item's units, and each one when there are several."""
        if not picks:
            return (f"Paid on a later remittance ({fallback[0]}, trace {fallback[1]}, ${(fallback[2] or 0)/100:.2f})",
                    {"payment_date": fallback[0], "trace": fallback[1], "paid_cents": fallback[2]})
        rows: dict[tuple, list] = {}
        for e, _u, cents in sorted(picks, key=lambda p: (p[0][0], p[0][1] or "")):
            r = rows.setdefault((e[0], e[1]), [e, 0.0])
            r[1] += cents
        said = "; ".join(f"{e[0]}, trace {e[1]}, ${c/100:.2f}" for e, c in rows.values())
        first = next(iter(rows.values()))[0]
        detail = {"payment_date": first[0], "trace": first[1], "paid_cents": int(round(sum(c for _e, c in rows.values()))),
                  "claim_status": first[3], "claim": first[4]}
        if len(rows) > 1:
            detail["payments"] = [{"payment_date": e[0], "trace": e[1], "claim": e[4], "paid_cents": int(round(c))} for e, c in rows.values()]
        return (f"Paid on a later remittance ({said})" if len(rows) == 1 else f"Paid on later remittances ({said})"), detail

    def _settled_by_answer(self, answers: list, code: str | None, dos: str | None, denier: tuple, when: str, pcn: str | None,
                           cob: bool, mods: list | None = None) -> tuple[str, list] | None:
        """How the payer that denied this code and date has settled it since with an answer the payment rules do not count
        (a payment that small, or none), with the answers that prove it. `answers` are the bill's lines, any claim status;
        `denier` is the payer that denied it (_one_payer).
        - ('as_secondary', lines): its denial said another payer covers the claim first (`cob`), and it then processed the
          code and date as secondary (claim status 2 or 3), applying the primary payer's answer (OA-23) or paying, whatever
          the amount: the denial was of the claim sent to it as if it were primary (trace findings T-C3, T-N3);
        - ('to_patient', lines): it answered the code and date again as primary, allowed it, paid nothing and put the
          allowed amount on the patient's deductible, coinsurance or copay with no reason of its own beside its pricing
          (trace finding T-N6). Another payer's answer, its other plan's included, settles nothing here: a second payer may
          owe the first's deductible.
        One later answer (one claim) settles it only when every line of it that can be the denied one settles it alike:
        every line of the code and date, or, where the denied claim billed the code and date on several lines (`mods`, the
        denied line's modifiers), the lines with the same modifiers. A line paid there and its twin put on the patient say
        nothing of which one was denied. Later: after the denial's remittance, or in it on another claim id of the bill. An
        answer its payer reversed since (claim status 22), or a payment taken back, counts for nothing."""
        if not code or not dos:
            return None
        at = lambda o: o.get("payment_date") or o.get("txn_payment_date") or ""      # noqa: E731
        mine = [o for o in answers if o.get("procedure_code") == code and o.get("service_date") == dos and self._one_payer(o, denier)]
        # the payer's reversals of the code and date: under its own id, or under another payer id with its name and plan (a
        # payer can take an answer back under another id than it gave it under, and the ledger pairs a reversal by its id)
        revs = [o for o in answers if o.get("claim_status_code") == "22" and o.get("procedure_code") == code and o.get("service_date") == dos
                and (self._one_payer(o, denier) or (bool(denier[2]) and o.get("payer_name") == denier[2] and o.get("payer_group") == denier[1]))]
        reversed_on = [(o["patient_control_number"], at(o)) for o in revs]

        def stands(o: dict) -> bool:
            if (o.get("paid_cents") or 0) > 0:
                # a payment the ledger still counts, unless a reversal of its claim id since takes that very amount back
                return self.standing.get(o["line_key"], 0) > 0 and not any(
                    r["patient_control_number"] == o["patient_control_number"] and at(r) > at(o)
                    and (r.get("paid_cents") or 0) == -(o.get("paid_cents") or 0) for r in revs)
            return not any(p == o["patient_control_number"] and d > at(o) for p, d in reversed_on)
        by_claim: dict[str, list] = defaultdict(list)
        for o in mine:
            if o.get("claim_status_code") != "22" and (at(o) > when or (at(o) == when and o["patient_control_number"] != pcn)) and stands(o) \
                    and (mods is None or sorted(o.get("modifiers_list") or []) == sorted(mods)):
                by_claim[o["claim_key"]].append(o)
        ok = {"as_secondary": lambda o: o.get("claim_status_code") in ("2", "3")
              and (self.standing.get(o["line_key"], 0) > 0 or self._applied_primary(o)),
              "to_patient": lambda o: o.get("claim_status_code") in ("1", "19") and self._to_patient(o)}
        for how in (("as_secondary", "to_patient") if cob else ("to_patient",)):
            rows = [o for ls in by_claim.values() if all(ok[how](o) for o in ls) for o in ls]
            if rows:
                return how, rows
        return None

    @staticmethod
    def _one_payer(o: dict, denier: tuple) -> bool:
        """This line is the denying payer's own answer: the same payer id (or name), the same plan (its payer group, as the
        claim filing indicator refines it: a company's Medicaid plan and its Medicare Advantage plan are two payers under one
        id, as archive_rules._two_plans has it) and, in a group that holds many payers, the same payer name."""
        key, grp, name = denier
        return ((o.get("payer_id") or o.get("payer_name")) == key and o.get("payer_group") == grp
                and (grp not in MANY_PAYER_GROUPS or o.get("payer_name") == name))

    def _applied_primary(self, o: dict) -> bool:
        """A line answered as secondary applied the primary payer's answer: OA-23 on the line, or on its claim when the line
        carries no reason of its own for not paying."""
        adj = self.adj.get(o["line_key"], [])
        if any(g == "OA" and rc == "23" for g, rc, _a in adj):
            return True
        own = [rc for g, rc, a in adj if g in ("CO", "OA", "PI") and rc not in IGNORE_CARCS and a]
        return not own and any(g == "OA" and rc == "23" for g, rc, _a in self.claim_adj.get(o["claim_key"], []))

    def _to_patient(self, o: dict) -> bool:
        """A line allowed and paid nothing, the allowed amount on the patient's deductible, coinsurance or copay, and no other
        reason beside the payer's pricing: a patient-responsibility reason that is not the patient's share (COST_SHARE) is a
        denial, not the patient's balance."""
        if (o.get("paid_cents") or 0) or not (o.get("allowed_cents") or 0) > 0:
            return False
        adj = self.adj.get(o["line_key"], [])
        other = [rc for g, rc, a in adj if a and ((g == "PR" and rc not in COST_SHARE) or (g != "PR" and rc not in IGNORE_CARCS))]
        return not other and sum(a for g, rc, a in adj if g == "PR" and rc in COST_SHARE) > 0

    def _close_by_answer(self, f: dict, how: str, rows: list, codes: list, answers: list, paid_rows: list | None = None) -> None:
        """Close an item by the payer's own later answer that settled it (_settled_by_answer), that answer as its proof
        (evidence settled_by and paid_later). What that payer paid after its denial is recovered: as secondary, its own
        payment (never the primary's), on `paid_rows` where given (a whole claim: every line of its codes the payer paid
        since, the ones its payment rules already counted included), else on the settling lines; to the patient, only what
        it paid on a claim's other codes. Where no other payer's answer as primary to the service is on file, the item says
        the primary's remittance is missing, as a claim only a secondary answered does (T-C2)."""
        at = lambda o: o.get("payment_date") or o.get("txn_payment_date") or ""      # noqa: E731
        first = min(rows, key=lambda o: (at(o), o["patient_control_number"], o["line_key"]))
        day, other_id = at(first), first["patient_control_number"]
        grp = first.get("payer_group") or first.get("payer_name") or "The payer"
        what = "service" if f.get("line_key") else "claim"
        detail: dict = {"payer": grp, "date": day, "claim": other_id}
        paid = sum(self.standing.get(o["line_key"], 0) for o in (rows if paid_rows is None else paid_rows))
        if how == "as_secondary":
            itself = (first.get("payer_id") or first.get("payer_name"), first.get("payer_group"), first.get("payer_name"))
            served = {(o.get("procedure_code"), o.get("service_date")) for o in rows}
            primary = any(not self._one_payer(o, itself) and o.get("claim_status_code") in ("1", "19", "4")
                          and (o.get("procedure_code"), o.get("service_date")) in served for o in answers)
            # the owner's decision 3 (2026-11-26): what arrived is the recovery, and what the payer still owes for the
            # service in that role stays listed with its proof, whatever its size (archive_rules.owed_in_second_role)
            due = self._second_role_due(f, rows, paid_rows, answers, itself)
            if due and due["left_cents"] > 0:
                self._second_role_short(f, due, grp, day, primary)
                return
            note = (f"{grp} processed this {what} as secondary on {day} (claim {other_id})"
                    + (f" and paid {_usd(paid)}, its share" if paid > 0 else ", applying the primary payer's answer (OA-23) and paying nothing more")
                    + ": its denial as the primary payer is settled"
                    + ("" if primary else ". The primary's remittance is not on file: look it up on the payer's portal, or ask the payer "
                                          "to send it again"))
            detail.update(how="processed as secondary", paid_cents=paid, codes=codes, primary_remittance="on file" if primary else "not on file")
        else:
            share: Counter = Counter()
            for o in rows:
                for g, rc, a in self.adj.get(o["line_key"], []):
                    if g == "PR" and rc in COST_SHARE:
                        share[rc] += a
            allowed = sum(o.get("allowed_cents") or 0 for o in rows)
            words = " and ".join(COST_SHARE[rc] for rc in sorted(share) if share[rc] > 0)
            note = (f"Processed to the patient's responsibility: {grp} answered this {what} again on {day} (claim {other_id}), allowed "
                    f"{_usd(allowed)} and put {_usd(sum(share.values()))} on the patient's {words}; nothing is owed by the payer"
                    + (f" (it paid {_usd(paid)} on the claim's other codes)" if paid > 0 else ""))
            detail.update(how="processed to the patient's responsibility", allowed_cents=allowed, patient_cents=sum(share.values()), codes=codes)
        later = {"payment_date": day, "claim": other_id, "trace": first.get("trace_number"), "paid_cents": paid,
                 "claim_status": first.get("claim_status_code"), "how": detail["how"]}
        if paid > 0:
            self._mark_paid(f, day, paid, note, later)        # recovered once: a payment counted on another item is not again
        ev = json.loads(f["evidence"]) if f.get("evidence") else {}
        ev["settled_by"] = detail
        if paid <= 0:
            ev["paid_later"] = later
            f["recovered_cents"], f["recovered_date"] = 0, day
            f["auto_status"], f["auto_notes"] = "resolved", note
        f["evidence"] = json.dumps(ev, default=str)
        f["hint"] = None
        self.ema_counts["settled_" + how] += 1

    def _second_role_due(self, f: dict, rows: list, paid_rows: list | None, answers: list, itself: tuple) -> dict | None:
        """What the payer that denied the item as primary owes for its services as secondary, and what is left after what
        it paid (archive_rules.owed_in_second_role): its answer lines (the settling ones and the ones it paid since), their
        claim's OA-23 where no line of that claim carries one, and what the other payers paid for those services (their
        standing payments on the bill)."""
        from .archive_rules import owed_in_second_role
        mine = {o["line_key"]: o for o in list(rows) + list(paid_rows or [])}
        with_line_oa23 = {o["claim_key"] for o in mine.values()
                          if any(g == "OA" and rc == "23" for g, rc, _a in self.adj.get(o["line_key"], []))}
        claim_done: set = set()
        lines = []
        for o in mine.values():
            adj = list(self.adj.get(o["line_key"], []))
            ck = o["claim_key"]
            if ck not in with_line_oa23 and ck not in claim_done:
                # the prior payer's answer applied once, on the claim (a line with no reason of its own)
                adj += [(g, rc, a) for g, rc, a in self.claim_adj.get(ck, []) if g == "OA" and rc == "23"]
                claim_done.add(ck)
            lines.append({"allowed": o.get("allowed_cents"), "charge": o.get("charge_cents"), "paid": self.standing.get(o["line_key"], 0),
                          "adj": adj})
        served = {(o.get("procedure_code"), o.get("service_date")) for o in mine.values()}
        firsts = [o for o in answers if not self._one_payer(o, itself) and (o.get("procedure_code"), o.get("service_date")) in served
                  and o.get("claim_status_code") != "22"]
        others = sum(self.standing.get(o["line_key"], 0) for o in firsts)
        # what the other payers left on the line (the patient share on their answers that paid): the most this payer can
        # owe in its role (review r1-7)
        paying = [o for o in firsts if self.standing.get(o["line_key"], 0) > 0]
        left = sum(a for o in paying for g, _rc, a in self.adj.get(o["line_key"], []) if g == "PR") if paying else None
        return owed_in_second_role(lines, others, f.get("expected_cents"), left)

    def _second_role_short(self, f: dict, due: dict, grp: str, day: str, primary: bool) -> None:
        """The payer paid as secondary less than it owes for the service in that role: the item stays listed at what is
        left (no dollar floor), its answer as the proof (evidence second_role), never closed by it."""
        from .archive_rules import second_role_words
        text = second_role_words(due, grp, "as secondary", day)
        ev = json.loads(f["evidence"]) if f.get("evidence") else {}
        ev["second_role"] = dict(due, date=day, role="as secondary", text=text, size_before_cents=f.get("shortfall_cents"),
                                 primary_remittance="on file" if primary else "not on file")
        f["evidence"] = json.dumps(ev, default=str)
        f["shortfall_cents"] = due["left_cents"]
        f["hint"] = text
        self.ema_counts["second_role_short"] += 1

    def _settle_unpaid_lines(self) -> None:
        """After the line rules: a line denial (or a bundled line) left unpaid that the payer which denied it has settled since
        with an answer the payment rules do not count (_settled_by_answer) is closed, that answer as its proof. Before the
        archive rules, which leave a closed item alone."""
        todo = [f for f in self.findings.values() if f.get("line_key") and f.get("category") in ("denial", "bundling")
                and not f.get("auto_status") and not (f.get("paid_cents") or 0) and f.get("service_date")]
        if not todo:
            return
        bill_at: dict[str, str] = {}

        def bill(pcn: str | None) -> str:
            if pcn not in bill_at:
                bill_at[pcn] = bill_of(pcn)
            return bill_at[pcn]
        bills = {bill(f["patient_control_number"]) for f in todo}
        answers: dict[str, list] = defaultdict(list)
        for l in self.lines:
            if l.get("service_date") and bill(l["patient_control_number"]) in bills:
                answers[bill(l["patient_control_number"])].append(l)
        for f in todo:
            remarks = set(self.rem.get(f["line_key"], [])) | set(self.claim_rem.get(f["claim_key"], []))
            cob = bool({rc for _g, rc, _a in self.adj.get(f["line_key"], [])} & OTHER_PAYER_CARCS or remarks & COB_RARCS)
            ans = answers.get(bill(f["patient_control_number"]), [])
            # the denied claim's lines of the code and date: on several, the denied one is told apart by its modifiers
            own = [x for x in self.lines_by_claim.get(f["claim_key"], [])
                   if x.get("procedure_code") == f.get("procedure_code") and x.get("service_date") == f["service_date"]]
            line = next((x for x in own if x["line_key"] == f["line_key"]), None)
            mods = (line.get("modifiers_list") or []) if line is not None and len(own) > 1 else None
            denier = (f.get("payer_id") or f.get("payer_name"), f.get("payer_group"), f.get("payer_name"))
            got = self._settled_by_answer(ans, f.get("procedure_code"), f["service_date"], denier, f.get("payment_date") or "",
                                          f["patient_control_number"], cob, mods)
            if got:
                self._close_by_answer(f, got[0], got[1], [f["procedure_code"]], ans)

    def emit_second_rates(self) -> None:
        """A payer's routine lower amount for a code (the same amount on at least SECOND_RATE_MIN_LINES clean lines of the
        line's own payer, year, code, provider type and place of service) becomes one finding with its claims, when the
        remittances support a reason for most of its lines (the rate for a PA or NP, one office's plan, one plan, a
        multiple-procedure reduction, a rate change). It carries each claim's filing deadline and shows the last. Without a
        reason, each line stays a one-off underpayment to appeal. A finding someone worked keeps the lines it had, and is
        emitted again with them whatever the rules now say; lines paid at that amount since go to a new finding. Services
        paid on the payer's previous year's schedule are settled first (emit_old_schedules)."""
        for fid, w in self.worked_patterns.items():
            lines = self.pattern_lines.get(fid)
            if lines:
                kp = tuple(w["key_parts"])
                self._emit_pattern(kp, lines, w.get("usual_cents"), w.get("note") or "", w.get("continues"))
        self.emit_old_schedules()
        self.emit_provider_gaps()
        if not self.second_rates:
            return
        surg_lines: dict[tuple, set] = defaultdict(set)     # (bill, date) -> primary surgical lines still paid
        for l in self.lines:
            if is_surgical(l.get("procedure_code")) and l.get("procedure_code") not in ADD_ON_CODES and l.get("claim_status_code") in ("1", "19") \
                    and l["line_key"] in self.standing and l.get("service_date"):
                surg_lines[(bill_of(l.get("patient_control_number")), l["service_date"])].add(l["line_key"])
        state_of = lambda l: self._office_state(l) or "unknown"         # noqa: E731
        # the yardstick each group's lines were measured by (_yardstick: the payer's rate, its PA/NP rate or a clinician's tier)
        usual = {key: self.yard_by_key[key]["mode"] for key, _a in self.second_rates if key in self.yard_by_key}
        # NPIs the remittances give a person's first name for: a clinician. A group's NPI (NM1*82*2) names the practice only.
        person = {r[0] for r in self.conn.execute("""SELECT DISTINCT rendering_provider_npi FROM claims WHERE rendering_provider_npi IS NOT NULL
                                                     AND COALESCE(rendering_provider_first, '') <> ''""")}
        usual_states: dict[tuple, Counter] = defaultdict(Counter)
        usual_plans: dict[tuple, Counter] = defaultdict(Counter)
        usual_npis: dict[tuple, set] = defaultdict(set)
        usual_last: dict[tuple, str] = {}
        # the first visit the payer paid at its rate for the code (any clinician, at the rate each is held to): the date its rate
        # took effect, by date of service (trace finding T-N4's 'raised since' holds only for visits dated before it)
        first_at_rate: dict[tuple, str] = {}
        wanted = {(k[0], k[2].split("-")[0]) for k in usual}
        for l in self.lines:
            if not l.get("clean") or (l["payer_group"], l["procedure_code"]) not in wanted:
                continue
            rate_l, _b, rr = self.usual_rate(l, l["procedure_code"])
            key = rr.get("key") if rr else None
            d = l.get("service_date") or ""
            if key and d and rate_l and abs((l.get("allowed_cents") or 0) - rate_l) <= 1:
                fk = (key[0], key[1], key[2], key[4])
                if fk not in first_at_rate or d < first_at_rate[fk]:
                    first_at_rate[fk] = d
            if key in usual and l.get("allowed_cents") == usual[key]:
                usual_states[key][state_of(l)] += 1
                usual_plans[key][(l.get("contract_class") or "").strip() or "(none)"] += 1
                if l.get("rendering_provider_npi") in person:
                    usual_npis[key].add(l["rendering_provider_npi"])
                if d > usual_last.get(key, ""):
                    usual_last[key] = d
        for (key, amount), group in self.second_rates.items():
            r = self.yard_by_key.get(key)
            if not r or not group:
                continue
            lines = [l for l, _one in group]
            grp, year, code, ylabel, pos = key
            tier = "unknown" if ylabel.startswith("full") else "nonphysician"     # the PA/NP reason reads the yardstick's own tier
            rate = r["mode"]
            ratio = amount / rate
            tiers = Counter(l.get("provider_tier") or "unknown" for l in lines)
            unnamed = sum(1 for l in lines if (l.get("provider_tier") or "unknown") == "unknown" and l.get("rendering_provider_npi") not in person)
            same_clinician = any(l.get("rendering_provider_npi") in usual_npis.get(key, ()) for l in lines)
            states = Counter(state_of(l) for l in lines)
            dos = sorted(l.get("service_date") or "" for l in lines)
            plans = Counter((l.get("contract_class") or "").strip() or "(none)" for l in lines)
            surgical = is_surgical(code) and code not in ADD_ON_CODES
            with_another = (sum(1 for l in lines if len(surg_lines.get((bill_of(l.get("patient_control_number")), l.get("service_date")), set()) - {l["line_key"]}) >= 1)
                            / len(lines)) if surgical else 0.0
            note, supported = second_rate_reason(ratio, tier, tiers, states, usual_states.get(key, Counter()), dos[0] or None, usual_last.get(key),
                                                 plans, usual_plans.get(key, Counter()), with_another, unnamed, same_clinician,
                                                 last_dos=dos[-1] or None, usual_first_dos=first_at_rate.get((grp, year, code, pos)))
            if not supported:
                if len(group) < SECOND_RATE_MIN_LINES:
                    for _l, one_off in group:
                        self._emit(*one_off)
                    continue
                # an amount the payer used on this many lines is a rule of its own even where the remittances name no reason:
                # one routine-rate item to review with the payer, not one appeal per line (trace finding T-N4)
                note = ("No reason the remittances show explains this amount: the payer uses it routinely all the same. Check it "
                        "against the contract, then ask the payer once about every claim listed that is still inside its filing window")
            kp, n, continues = ("SECOND_RATE", grp, year, code, ylabel, pos, amount), 0, None
            while _fid("SECOND_RATE", *kp) in self.worked_patterns:      # worked: these lines came after, so they get their own finding
                continues = _fid("SECOND_RATE", *kp)
                n += 1
                kp = ("SECOND_RATE", grp, year, code, ylabel, pos, amount, "continuation", n)
            self._emit_pattern(kp, lines, rate, note, continues, by_tier=tiers, by_state=states, by_plan=plans,
                               at_amount=self.yard_obs.get(r.get("payer_key"), {}).get(amount, 0), for_code=r["n"])

    def _gone(self, l) -> bool:
        """A line paid more than $0 whose payment no longer stands in full: the payer reversed it, or took some of it back
        (self.standing). Its answer is no longer the payer's last word on the service."""
        paid = l.get("paid_cents") or 0
        return paid > 0 and (self.standing.get(l["line_key"]) or 0) < paid

    def _note_gone(self, l, r: dict) -> list:
        """A line a repricing request held, or would hold, that the payer took back (_gone): kept for the request's evidence
        (repriced: paid again since; else taken back), under the request it was sent in, else its payer and year. Returns
        the payer's standing payments of the service made after the line's own (the bill, code and date: paid_cover)."""
        mine = l.get("payment_date") or l.get("txn_payment_date") or ""
        again = [e for e in self.paid_cover(bill_of(l.get("patient_control_number")), l.get("procedure_code"), l.get("service_date"))
                 if e[0] and e[0] > mine]
        key = self.line_to_old.get(l["line_key"]) or (l["payer_group"], l["year"])
        self.old_gone[key].append((l, r, sum(e[2] or 0 for e in again), max((e[0] for e in again), default=None),
                                   [{"date": e[0], "amount_cents": e[2], "kind": "payment", "claim": e[4], "trace": e[1],
                                     "code": l.get("procedure_code")} for e in again]))
        return again

    def _old_schedule_of(self, l, allowed: int, r: dict) -> int | None:
        """The previous year's amount this short line was paid at, where the payer's rate for the code rose from that year to
        the line's (both the payer's own, same plan, place and component) and the line was allowed exactly the previous
        year's amount at the tier it is held to (the full rate; the PA/NP or the state's share of it; the clinician's own
        rate of that year): a service priced on the payer's earlier fee schedule. None otherwise."""
        pk, key = r.get("payer_key"), r.get("key")
        year = str(l.get("year") or "")
        if not pk or not key or key[1] != year or not year.isdigit() or r.get("plan_unknown"):
            return None
        prev_y = str(int(year) - 1)
        cur, prev = self.yard.get(pk), self.yard.get((pk[0], prev_y) + tuple(pk[2:]))
        if not cur or not prev or prev["mode"] >= cur["mode"] or prev["share"] < FULL_SHARE:
            return None
        tier = r.get("tier")
        held = r.get("mode") or cur["mode"]
        if tier == "full":
            old = {prev["mode"]}
        else:
            share = held / cur["mode"]
            old = {int(round(prev["mode"] * share))}
            if tier == "pa_np" and prev.get("np"):
                old.add(prev["np"])
            if tier == "clinician":
                own = self.prov_rates.get((pk[0], prev_y) + tuple(pk[2:5]) + (l.get("rendering_provider_npi"),))
                if own:
                    old.add(own["mode"])
        got = next((o for o in sorted(old) if abs(allowed - o) <= 1 and o < held), None)
        return got

    @staticmethod
    def _rate_code_key(r: dict) -> tuple | None:
        """A rate record's code key: (rate group, code, place, component), its year left out."""
        pk = r.get("payer_key")
        return (pk[0],) + tuple(pk[2:5]) if pk else None

    def _new_schedule_starts(self, wanted: set[tuple]) -> tuple[dict[tuple, str], dict[tuple, str], Counter]:
        """For each (payer group, year) wanted, the first date of service the payer paid at its rate of that year where the
        rate rose from the year before (any code, plan or clinician, at the rate each line is held to): the day its new
        schedule took effect, as its own payments show (a pair with no such payment on file is left out). Also each code's
        own (_rate_code_key): its first date of service paid at its raised rate and how many lines were (for a code raised
        later in the year than the others, emit_old_schedules). One pass over the lines, then each pair's clean lines in
        date order."""
        by_pair: dict[tuple, list] = defaultdict(list)
        for l in self.lines:
            if l.get("clean") and l.get("service_date") and (l["payer_group"], l["year"]) in wanted:
                by_pair[(l["payer_group"], l["year"])].append(l)
        out: dict[tuple, str] = {}
        first_code: dict[tuple, str] = {}
        n_code: Counter = Counter()
        for (grp, year), lines in by_pair.items():
            if not year.isdigit():
                continue
            prev_y = str(int(year) - 1)
            for l in sorted(lines, key=lambda x: (x["service_date"], x["line_key"])):
                rate, _b, rr = self.usual_rate(l, l["procedure_code"])
                if not rr or not rate or abs((l.get("allowed_cents") or 0) - rate) > 1 or rr.get("plan_unknown"):
                    continue
                pk = rr["payer_key"]
                cur, prev = self.yard.get(pk), self.yard.get((pk[0], prev_y) + tuple(pk[2:]))
                if cur and prev and prev["mode"] < cur["mode"]:
                    out.setdefault((grp, year), l["service_date"])
                    ck = self._rate_code_key(rr)
                    first_code.setdefault(ck, l["service_date"])
                    n_code[ck] += 1
        return out, first_code, n_code

    def emit_old_schedules(self) -> None:
        """Services a payer priced on its previous year's schedule (_old_schedule_of) that are dated on or after the day its
        new rate took effect (_new_schedule_starts): the payer owes each the new rate, whenever it processed it (fix7c: a
        Medicare Advantage plan loaded its new year's rates weeks late and never reprocessed the January visits it had paid on
        the old ones). One repricing request per payer and year, with every claim, what each was paid and what it is owed,
        and each claim's filing deadline (the last one on the item). Services dated before that day are the payer's earlier
        rate, raised since: they go back to the usual rules (a routine-rate review with that reason, or one-offs).
        The day is the payer's, unless a code shows a date of its own (fix7c-r2-4): a run of old-amount visits after the
        payer's day (OLD_RUN_LINES or more), all dated before the code's own first visit paid at its raised rate, which comes
        a month or more after the payer's day and is shown by OLD_RUN_LINES visits or more: a rise of that code later in the
        year, so its old-amount visits are its earlier rate, raised since. A code with too few lines to show a date of its own
        (a new patient's visit seen a few times a year) takes the payer's.
        A request someone worked keeps the claims it was sent with (as a worked routine-rate review does); services found
        since go to a request of their own. A claim the payer took back leaves the request (_note_gone): repriced, it is
        closed as recovered on its own line and named on the request; a request whose every claim was repriced is closed."""
        for fid, w in sorted(self.worked_old.items()):
            # what each claim was owed when the request was sent: by its line, else by its claim row (a remittance delivered
            # again has new line keys)
            rates = w.get("rates") or {}
            by_claim = {(c[0], c[2], c[1], c[3]): c[4] for c in w.get("claims") or [] if isinstance(c, list) and len(c) >= 5}
            group = []
            for l in self.old_worked_lines.get(fid, []):
                owed = rates.get(l["line_key"]) or by_claim.get((l.get("patient_control_number"), l.get("procedure_code"),
                                                                 l.get("service_date"), l.get("allowed_cents")))
                if owed:
                    group.append((l, None, {"mode": owed}, None))
            kp = tuple(w["key_parts"])
            self._emit_old_schedule(kp, str(w.get("year") or kp[1]), group, w.get("effective"), self.old_gone.get(fid, []),
                                    w.get("continues"), worked=True)
        pairs = set(self.old_schedule) | {k for k in self.old_gone if isinstance(k, tuple)}
        if not pairs:
            return
        starts, first_code, n_code = self._new_schedule_starts(pairs)
        for grp, year in sorted(pairs):
            group = self.old_schedule.get((grp, year), [])
            start = starts.get((grp, year))
            own_start: dict[tuple, str] = {}
            by_code: dict[tuple, list] = defaultdict(list)
            for x in group:
                by_code[self._rate_code_key(x[2])].append(x)
            for ck, xs in by_code.items():
                first = first_code.get(ck)
                after = [(x[0].get("service_date") or "") for x in xs if start and (x[0].get("service_date") or "") >= start]
                if (first and start and n_code[ck] >= OLD_RUN_LINES and len(after) >= OLD_RUN_LINES and max(after) < first
                        and _add_days(start, 30) <= first):
                    own_start[ck] = first
            owed = []
            for x in group:
                begin = own_start.get(self._rate_code_key(x[2]), start)
                if begin and (x[0].get("service_date") or "") >= begin:
                    owed.append(x)
                elif x[1] is not None:
                    # the earlier rate, raised since: the usual rules (a line below the one-off threshold makes no item)
                    self._emit_underpaid(x[0], x[0].get("allowed_cents") or 0, x[2], x[1], old_schedule=False)
            kp, continues, n = (grp, year), None, 0
            while _fid("OLD_FEE_SCHEDULE", *kp) in self.worked_old:     # sent: services found since get a request of their own
                continues = _fid("OLD_FEE_SCHEDULE", *kp)
                n += 1
                kp = (grp, year, "continuation", n)
            self._emit_old_schedule(kp, year, owed, start, self.old_gone.get((grp, year), []), continues,
                                    own_start={ck[1]: d for ck, d in own_start.items()})
        self.old_schedule.clear()

    def _emit_old_schedule(self, kp: tuple, year: str, group: list, start: str | None, gone: list, continues: str | None,
                           worked: bool = False, own_start: dict | None = None) -> None:
        """One OLD_FEE_SCHEDULE item: a payer's services of `year` paid on its previous year's schedule (`group`: (line,
        one-off, rate record, old amount)), with the claims the payer took back since (`gone`, _note_gone). Its amount is every
        claim's, as a routine-rate review's is: the deadline views count each claim by its own window (pattern_windows). A
        request with no claim left owed is closed when it is on file and the payer repriced its claims (their recoveries are
        counted on their own lines), and is not listed otherwise."""
        grp = kp[0]
        fid = _fid("OLD_FEE_SCHEDULE", *kp)
        rows = []
        for l, _one, r, _old in group:
            new = r.get("mode") or 0
            charge = l.get("charge_cents") or 0
            if 0 < charge < new:
                new = charge            # the payer allows the lesser of the charge and its rate
            if new > (l.get("allowed_cents") or 0):
                rows.append((l, new))
        rows.sort(key=lambda x: (x[0].get("service_date") or "", x[0].get("patient_control_number") or "", x[0]["line_key"]))
        repriced = sorted(((l, r, again, when) for l, r, again, when, _p in gone if again > 0),
                          key=lambda x: (x[0].get("service_date") or "", x[0].get("patient_control_number") or "", x[0]["line_key"]))
        proof = sorted((p for _l, _r, again, _w, ps in gone if again > 0 for p in ps), key=lambda p: (p["date"] or "", p["claim"] or ""))
        taken = [(l, r) for l, r, again, _w, _p in gone if again <= 0]
        if not rows and not (repriced and fid in self.old_known):
            return
        prev_y = str(int(year) - 1) if year.isdigit() else "the previous"
        lines_all = [l for l, _n in rows] or [l for l, *_x in repriced]
        first = lines_all[0]
        deadlines, due, by_due = [], {}, defaultdict(lambda: [0, 0])
        by_code: dict[str, dict] = {}
        for l, new in rows:
            rule = appeal_rule(grp, l.get("payer_kind"), (l.get("contract_class") or "").strip() or None)
            d = window_end(l.get("payment_date") or l.get("txn_payment_date"), rule)
            if d:
                deadlines.append((d, rule["label"] + ("" if rule["verified"] else " (not confirmed against the payer's current manual)")))
                due[l["line_key"]] = d
            by_due[d][0] += 1
            by_due[d][1] += new - (l.get("allowed_cents") or 0)
            c = by_code.setdefault(l["procedure_code"], {"lines": 0, "paid_at": {}, "owed_cents": 0})
            c["lines"] += 1
            c["paid_at"][str(l.get("allowed_cents"))] = new
            c["owed_cents"] += new - (l.get("allowed_cents") or 0)
        live = [(l, new) for l, new in rows if worked or (due.get(l["line_key"]) or "9999-12-31") >= self.today]
        gap_all = sum(new - (l.get("allowed_cents") or 0) for l, new in rows)
        gap_live = sum(new - (l.get("allowed_cents") or 0) for l, new in live)
        codes = sorted(by_code) or sorted({l["procedure_code"] for l in lines_all})
        last_paid = max((l.get("payment_date") or l.get("txn_payment_date") or "") for l in lines_all) or None
        back = sum(max(again - (l.get("paid_cents") or 0), 0) for l, _r, again, _w in repriced)
        s = lambda n: "s" if n != 1 else ""      # noqa: E731
        if rows:
            note = (f"{grp} paid {len(rows)} service{s(len(rows))} dated in {year} at the amounts of its {prev_y} fee schedule. "
                    f"Its {year} rates were already in effect for them: it paid services from {start} on at the {year} rates. None of "
                    f"these was reprocessed. Ask {grp} once to reprocess every claim listed at its {year} rates (a repricing request); "
                    "each claim keeps its own filing deadline")
            if continues:
                note = (f"Found since the repricing request for {grp}'s {year} services was sent: these claims were not in the "
                        f"repricing request sent, so send them in a request of their own. " + note)
            if repriced:
                note += (f". {len(repriced)} claim{s(len(repriced))} listed before {'were' if len(repriced) != 1 else 'was'} repriced "
                         f"since ({_usd(back)} more): each is closed as recovered on its own line")
        else:
            note = (f"{grp} repriced every claim of this request since ({len(repriced)}, {_usd(back)} more than it paid on its "
                    f"{prev_y} fee schedule): each is closed as recovered on its own line, so nothing is left to ask for")
        evidence = {"year": year, "previous_year": prev_y, "effective": start, "lines": len(rows), "lines_open": len(live),
                    "codes": {c: by_code[c] for c in sorted(by_code)}, "gap_all_cents": gap_all, "gap_live_cents": gap_live,
                    "note": note,
                    "claims": [[l.get("patient_control_number"), l.get("service_date"), l["procedure_code"], l.get("allowed_cents"), new,
                                due.get(l["line_key"])] for l, new in rows],
                    "deadlines": [[d, v[0], v[1]] for d, v in sorted(by_due.items(), key=lambda x: (x[0] is None, x[0] or ""))],
                    # what keeps the request's claims with it once someone works it (Detector.__init__)
                    "key_parts": list(kp), "line_keys": [l["line_key"] for l, _n in rows],
                    "rates": {l["line_key"]: new for l, new in rows}}
        if own_start:
            evidence["code_effective"] = dict(sorted(own_start.items()))
        if continues:
            evidence["continues"] = continues
        if repriced:
            evidence["repriced"] = [[l.get("patient_control_number"), l.get("service_date"), l["procedure_code"], l.get("paid_cents"),
                                     again, when] for l, _r, again, when in repriced]
            evidence["repriced_cents"] = back
        if taken:
            evidence["taken_back"] = [[l.get("patient_control_number"), l.get("service_date"), l["procedure_code"], l.get("paid_cents")]
                                      for l, _r in taken]
        base = {"payer_group": grp, "payer_kind": first.get("payer_kind"),
                "payer_name": Counter(l.get("payer_name") for l in lines_all).most_common(1)[0][0] or grp, "year": year,
                "procedure_code": codes[0] if len(codes) == 1 else None, "payment_date": last_paid}
        n_all = len(rows) or len(repriced)
        title = (f"{grp} paid {n_all} service{s(n_all)} of {year} on its {prev_y} fee schedule "
                 f"({', '.join(codes[:4])}{'…' if len(codes) > 4 else ''}): "
                 + ("one repricing request at its {0} rates".format(year) if rows else "every claim repriced since")
                 + (": found since the request was sent" if continues else ""))
        expected = sum(new for _l, new in rows) if rows else sum((r.get("mode") or 0) for _l, r, _a, _w in repriced)
        short = gap_all if rows else sum(max((r.get("mode") or 0) - (l.get("allowed_cents") or 0), 0) for l, r, _a, _w in repriced)
        self._emit("OLD_FEE_SCHEDULE", "underpayment", "medium" if gap_live >= 100000 else "low", "appeal", title, None,
                   expected, short, None, None, evidence, key_parts=kp, claim=base)
        f = self.findings.get(fid)
        if f is None:
            return
        if not rows:
            # every claim repriced: closed, its recovery counted on each claim's own line (never twice)
            # the proof: the payer's payments of the claims since (dated, with their claims), each closed on its own line
            ev = json.loads(f["evidence"])
            ev["recovery_counted_elsewhere"] = True
            last = proof[-1] if proof else {}
            ev["paid_later"] = {"how": "every claim of the request was repriced", "proof": proof,
                                "payment_date": last.get("date"), "claim": last.get("claim"), "trace": last.get("trace"),
                                "paid_cents": sum(p["amount_cents"] or 0 for p in proof)}
            f["evidence"] = json.dumps(ev, default=str)
            f["auto_status"], f["auto_notes"] = "resolved", note
            f["recovered_cents"], f["recovered_date"] = 0, last.get("date")
            return
        if deadlines:
            lo, hi = min(deadlines), max(deadlines)
            f["appeal_deadline"] = hi[0]
            f["deadline_basis"] = (f"the last of its {len(rows)} claims to close; the first closes on {lo[0]}. Each claim has its own window. {hi[1]}"
                                   if lo[0] != hi[0] else f"all {len(rows)} claims. {hi[1]}")
            f["hint"] = f"Filing windows close {lo[0]} to {hi[0]}" if lo[0] != hi[0] else None

    def emit_provider_gaps(self) -> None:
        """A clinician the payer pays at a level of its own below the payer's rate for the code (at least TIER_MIN_LINES
        payments, TIER_MIN_SHARE of them at one amount) that no rule explains (not the same rate, not the PA/NP 85 percent of
        a PA or NP, not a multiple-procedure half): one contract or credentialing review per payer and clinician, with the
        claims paid at that level and what they fell short of the payer's rate (the owner's rule, section 4.1), never one
        appeal per line and never taken as the clinician's rate. It carries each claim's filing deadline and shows the last."""
        for (grp, npi), group in sorted(self.provider_gaps.items()):
            lines = [l for l, _one, _r in group]
            first = lines[0]
            gaps = [(l, one[6], one[6] - (l.get("allowed_cents") or 0)) for l, one, _r in group]
            gap = sum(g for _l, _u, g in gaps)
            codes = Counter(l["procedure_code"] for l in lines)
            by_code = {}
            for l, usual, _g in gaps:
                c = by_code.setdefault(l["procedure_code"], {"usual_cents": usual, "amount_cents": l.get("allowed_cents"), "lines": 0})
                c["lines"] += 1
            deadlines, due, by_due = [], {}, defaultdict(lambda: [0, 0])
            for l, _u, g in gaps:
                rule = appeal_rule(grp, l.get("payer_kind"), (l.get("contract_class") or "").strip() or None)
                d = window_end(l.get("payment_date") or l.get("txn_payment_date"), rule)
                if d:
                    deadlines.append((d, rule["label"] + ("" if rule["verified"] else " (not confirmed against the payer's current manual)")))
                    due[l["line_key"]] = d
                by_due[d][0] += 1
                by_due[d][1] += g
            ordered = sorted(gaps, key=lambda x: (x[0].get("service_date") or "", x[0].get("patient_control_number") or ""))
            name = self.provider_name.get(npi) or first.get("rp_name") or npi
            usual_all = {u for _l, u, _g in gaps}
            amounts = {l.get("allowed_cents") for l in lines}
            pct = (sum(l.get("allowed_cents") or 0 for l in lines) / sum(u for _l, u, _g in gaps)) if gaps else 0
            evidence = {"usual_cents": next(iter(usual_all)) if len(usual_all) == 1 else None,
                        "amount_cents": next(iter(amounts)) if len(amounts) == 1 else None, "pct_of_usual": round(pct, 3),
                        "lines": len(lines), "codes": {c: v for c, v in sorted(by_code.items())},
                        "clinician_npi": npi, "clinician": name, "provider_tier": first.get("provider_tier"),
                        "note": (f"{grp} pays this clinician {pct:.0%} of its usual rate on {len(lines)} claims, at a level of the clinician's "
                                 "own that no rule explains (not the rate for a PA or NP, not the multiple-procedure rule). Check the "
                                 "clinician's credentialing and the contract with the payer; if the payer is wrong, one request can fix "
                                 "every claim listed that is still inside its filing window"),
                        "claims": [[l.get("patient_control_number"), l.get("service_date"), l.get("allowed_cents"), due.get(l["line_key"])]
                                   for l, _u, _g in ordered],
                        "deadlines": [[d, v[0], v[1]] for d, v in sorted(by_due.items(), key=lambda x: (x[0] is None, x[0] or ""))],
                        "line_keys": [l["line_key"] for l, _u, _g in ordered]}
            base = {"payer_group": grp, "payer_kind": first.get("payer_kind"), "payer_name": Counter(l.get("payer_name") for l in lines).most_common(1)[0][0] or grp,
                    "rendering_provider_npi": npi, "rendering_provider_name": name, "provider_tier": first.get("provider_tier"),
                    "year": max(l.get("year") or "" for l in lines) or None,
                    "procedure_code": codes.most_common(1)[0][0] if len(codes) == 1 else None,
                    "payment_date": max((l.get("payment_date") or l.get("txn_payment_date") or "") for l in lines) or None}
            title = (f"{grp} pays {name} {pct:.0%} of its usual rate on {len(lines)} claim{'s' if len(lines) != 1 else ''} "
                     f"({', '.join(sorted(codes)[:4])}{'…' if len(codes) > 4 else ''}): a contract or credentialing question")
            # worth what can still be raised: the claims inside their windows, unless a person worked the item (their request
            # covers every claim it had)
            worked = _fid("PROVIDER_RATE_GAP", grp, npi) in self.worked_gaps
            live = [(l, u, g) for l, u, g in gaps if worked or (due.get(l["line_key"]) or "9999-12-31") >= self.today]
            evidence.update(lines_open=len(live), gap_all_cents=gap)
            gap_live = sum(g for _l, _u, g in live)
            self._emit("PROVIDER_RATE_GAP", "contract", "medium" if gap_live >= 100000 else "low", "review", title, None,
                       sum(u for _l, u, _g in live), gap_live, None, None, evidence, key_parts=(grp, npi), claim=base)
            f = self.findings.get(_fid("PROVIDER_RATE_GAP", grp, npi))
            if f is not None and deadlines:
                lo, hi = min(deadlines), max(deadlines)
                f["appeal_deadline"] = hi[0]
                f["deadline_basis"] = (f"the last of its {len(lines)} claims to close; the first closes on {lo[0]}. Each claim has its own window. {hi[1]}"
                                       if lo[0] != hi[0] else f"all {len(lines)} claims. {hi[1]}")
                f["hint"] = f"Filing windows close {lo[0]} to {hi[0]}" if lo[0] != hi[0] else None

    def _emit_pattern(self, kp: tuple, lines: list, rate: int | None, note: str, continues: str | None, by_tier: Counter | None = None,
                      by_state: Counter | None = None, by_plan: Counter | None = None, at_amount: int | None = None, for_code: int | None = None) -> None:
        """One SECOND_RATE finding for these lines, with each claim's filing deadline."""
        _sig, grp, year, code, tier, pos, amount = kp[:7]
        rate = rate or max((l.get("allowed_cents") or 0) for l in lines)
        n = len(lines)
        ratio = amount / rate if rate else 0
        gap = sum(max(rate - (l.get("allowed_cents") or 0), 0) for l in lines)
        dos = sorted(l.get("service_date") or "" for l in lines)
        quarters = Counter(f"{d[:4]}-Q{(int(d[5:7]) + 2) // 3}" for d in dos if len(d) >= 7)
        names = Counter(l.get("payer_name") for l in lines)
        deadlines, due, by_due = [], {}, defaultdict(lambda: [0, 0])
        for l in lines:
            rule = appeal_rule(grp, l.get("payer_kind"), (l.get("contract_class") or "").strip() or None)
            d = window_end(l.get("payment_date") or l.get("txn_payment_date"), rule)
            if d:
                deadlines.append((d, rule["label"] + ("" if rule["verified"] else " (not confirmed against the payer's current manual)")))
                due[l["line_key"]] = d
            by_due[d][0] += 1           # d is None when the remittance has no payment date: the window is unknown
            by_due[d][1] += max(rate - (l.get("allowed_cents") or 0), 0)
        if tier not in ("physician", "nonphysician", "unknown"):     # a yardstick's label ('full', 'pa_np'): the lines' own type
            tier = Counter(l.get("provider_tier") or "unknown" for l in lines).most_common(1)[0][0]
        # the claims still inside their windows today (a window unknown counts); the item holds every claim, and the deadline
        # views count each by its own window (pattern_windows)
        live = [l for l in lines if (due.get(l["line_key"]) or "9999-12-31") >= self.today]
        base = {"payer_group": grp, "payer_kind": self.kinds.get(grp), "payer_name": names.most_common(1)[0][0] or grp, "year": year,
                "procedure_code": code, "provider_tier": tier, "pos_class": pos,
                "payment_date": max((l.get("payment_date") or l.get("txn_payment_date") or "") for l in lines) or None}
        ordered = sorted(lines, key=lambda x: (x.get("service_date") or "", x.get("patient_control_number") or ""))
        evidence = {"usual_cents": rate, "amount_cents": amount, "pct_of_usual": round(ratio, 3), "lines": n,
                    "payer_lines_at_this_amount": at_amount, "payer_lines_for_code": for_code,
                    "by_provider_type": dict(by_tier or Counter(l.get("provider_tier") or "unknown" for l in lines)),
                    "by_office_state": dict(by_state or {}), "by_plan": dict((by_plan or Counter()).most_common(6)),
                    "by_quarter": dict(sorted(quarters.items())), "first_visit": dos[0] or None, "last_visit": dos[-1] or None, "note": note,
                    # each claim keeps its own filing deadline: the page counts the ones still open on the day it is read
                    "claims": [[l.get("patient_control_number"), l.get("service_date"), l.get("allowed_cents"), due.get(l["line_key"])] for l in ordered],
                    "deadlines": [[d, v[0], v[1]] for d, v in sorted(by_due.items(), key=lambda x: (x[0] is None, x[0] or ""))],
                    # what keeps this finding's lines with it once someone works it (Detector.__init__)
                    "key_parts": list(kp), "line_keys": [l["line_key"] for l in ordered],
                    "lines_open": len(live)}
        if continues:
            evidence["continues"] = continues
        title = (f"{grp} allowed {code} at ${amount/100:,.2f} instead of its usual ${rate/100:,.2f} on {n} line{'s' if n != 1 else ''} "
                 f"in {year} ({ratio:.0%} of usual)" + (": paid since an earlier finding at this amount was worked" if continues else ""))
        # the id is _fid("SECOND_RATE", *kp), and kp itself starts with "SECOND_RATE": kept as the first release had it
        self._emit("SECOND_RATE", "underpayment", "medium" if gap >= 100000 else "low", "review", title, None, rate * n, gap,
                   None, None, evidence, key_parts=kp, claim=base)
        f = self.findings.get(_fid("SECOND_RATE", *kp))
        if f is not None and deadlines:
            # The last deadline, so the pattern stays in the open work while any of its claims can still be raised; the
            # first is on the row, and the finding counts how many claims are still inside their windows.
            first, last = min(deadlines), max(deadlines)
            f["appeal_deadline"] = last[0]
            f["deadline_basis"] = (f"the last of its {n} claims to close; the first closes on {first[0]}. Each claim has its own window. {last[1]}"
                                   if first[0] != last[0] else f"all {n} claims. {last[1]}")
            f["hint"] = f"Filing windows close {first[0]} to {last[0]}" if first[0] != last[0] else None

    def _mark_paid(self, f: dict, paid_date: str | None, paid: int, note: str, detail: dict, used: tuple | None = None,
                   recovery: tuple | None = None, counts: bool = True) -> None:
        """Close a finding the way a later payment on the same bill does, after the line rules have run. One payment counts
        as recovered on one finding only. `recovery` is what _recover measured for it (cents, the payments' cents, their
        picks, counted elsewhere). An 835 payment named by its paying line (detail['line_key'], the archive rules') is
        counted by that line's units, each unit on one finding, at the amount the rule measured (what came after the
        item's answer; trace finding T-C5), or at $0 where the rule says it is no recovery (`counts`). Otherwise `used`
        names the payment (EMA's), by default its date, trace and claim and the finding's code and date of service, and the
        recovery is at most the finding's shortfall."""
        short = f.get("shortfall_cents")
        ev = json.loads(f["evidence"]) if f.get("evidence") else {}
        if not short or short <= 0:
            f["expected_cents"] = f["shortfall_cents"] = short = paid
            ev["basis"] = "amount paid later (no baseline for this code)"
        if recovery is not None:
            seen = recovery[3]
            f["recovered_cents"], f["recovered_date"] = recovery[0], paid_date
        elif detail.get("line_key") and not used:
            own = next((x for x in self.lines_by_claim.get(f.get("claim_key") or "", []) if x["line_key"] == f.get("line_key")), None)
            need = self.billed_units_of(own) if own is not None else 1
            got = self.used_units.setdefault(detail["line_key"], [0, 0.0])
            free = (detail.get("units") or 1) - got[0]
            seen = free <= 0
            # what actually arrived after the item's answer, when the rule measured less than the payment (the payer took
            # back an earlier payment of the service with it: the owner's decision 3)
            cap = detail.get("arrived_cents")
            got_now = max(paid if cap is None else min(paid, int(cap)), 0)
            f["recovered_cents"], f["recovered_date"] = (0 if seen or not counts else got_now), paid_date
            if not seen:
                got[0] += min(free, need)
                got[1] += f["recovered_cents"]
        else:
            # one payment of one line: a later claim paying two of the claim's lines brings two recoveries, never one; a
            # whole claim's item names every code and date it lists, so a line item already closed by the same payment is
            # not counted again on it
            keys = [used] if used else [(paid_date, detail.get("trace"), detail.get("claim"), c, d)
                                        for c, d in _service_keys(f, ev, f.get("procedure_code"), f.get("service_date"))]
            seen = any(k in self.used_payments for k in keys)
            f["recovered_cents"], f["recovered_date"] = (0 if seen else min(paid, short)), paid_date
            self.used_payments.update(keys)
        if seen:
            detail = dict(detail, recovery_counted_elsewhere=True)
        ev["paid_later"] = detail
        f["evidence"] = json.dumps(ev, default=str)
        f["auto_status"], f["auto_notes"] = "resolved", note

    def resolve_elsewhere(self) -> None:
        """Close a denial, or a bundled visit, that was paid on another claim: a corrected claim under a new bill number, or
        the claim sent to another payer. It must be the same patient (member number and name, with the middle initial when
        both claims carry one; another member number only when EMA ties both claims to one patient), the same date and code,
        a payment still standing (a primary payment, or one covering half the expected amount), made after the denial,
        covering the units denied, for a code paid at most once per patient and date, and, for a claim-level denial, every
        code it lists. A payment for one part of a service (the professional part, modifier 26, or the technical part, TC)
        pays that part only: a 26 denial closes on a 26 payment or one for the whole service, a TC denial on a TC payment or
        one for the whole service, and a denial of the whole service on a payment for the whole service or for both parts
        (trace finding T-C1). A duplicate's denial also closes when the service was paid on another claim before it. Anything
        short of that (a payment before the denial, another member number, fewer units, one part of the service, a code
        often paid twice in a day, an adjudication to the patient's deductible, a bundled procedure paid elsewhere) is left
        open with a hint; a payment for the other part only (a TC payment for a 26 denial) is not shown at all.
        What EMA has posted closes it too (EMA_CLOSES): insurance payments in EMA's newest charge export, or on its ERA
        lines after the denial, on every code and date the item lists (a whole claim's every code, not one of them), each
        of at least half of what that code is expected to bring, where the claim carries the code once on that date. Fewer
        codes paid, or REMITWATCH_EMA_CLOSES=0, is only a hint."""
        for f in self.findings.values():           # same bill, fewer units than billed: a hint
            got = self.partly_paid.get(f.get("line_key") or "")
            if got and not f.get("auto_status") and not f.get("hint"):
                e, n, need = got
                ev = json.loads(f["evidence"]) if f.get("evidence") else {}
                ev["partly_paid"] = {"claim": e[4], "payment_date": e[0], "paid_cents": e[2], "units_paid": n, "units_billed": need}
                f["evidence"] = json.dumps(ev, default=str)
                f["hint"] = f"Partly paid on claim {e[4]} ({e[0]}): {n:g} of {need:g} units"
        todo = [f for f in self.findings.values() if not f.get("auto_status") and f.get("claim_key") and f["category"] in ("denial", "bundling")]
        if not todo:
            return
        c = self.conn

        def lines_of(f):
            ls = self.lines_by_claim.get(f["claim_key"], [])
            if f.get("line_key"):
                return [ln for ln in ls if ln["line_key"] == f["line_key"]]
            listed = set(json.loads(f["evidence"]).get("codes") or []) if f.get("evidence") else set()
            return [ln for ln in ls if ln.get("procedure_code") in listed]
        keys = sorted({f["claim_key"] for f in todo})
        who: dict[str, tuple] = {}
        mid: dict[str, str] = {}
        initial = lambda m: "".join(ch for ch in (m or "").upper() if ch.isalpha())[:1]        # noqa: E731
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            for ck, pid, last, first, middle in c.execute(f"SELECT claim_key, patient_id, patient_last, patient_first, patient_middle FROM claims "
                                                          f"WHERE claim_key IN ({','.join('?' * len(part))})", part):
                who[ck], mid[ck] = patient_keys(pid, last, first), initial(middle)
        need: set[tuple] = set()          # (patient key, code, date) the open findings need paid
        for f in todo:
            for k in who.get(f["claim_key"], (None, None)):
                if k:
                    for ln in lines_of(f):
                        if ln.get("service_date") and ln.get("procedure_code"):
                            need.add((k, ln["procedure_code"], ln["service_date"]))
        if not need:
            return
        patients = {k for k, _c, _d in need}
        claim_who: dict[str, tuple] = {}
        for ck, pid, last, first, middle in c.execute("SELECT claim_key, patient_id, patient_last, patient_first, patient_middle FROM claims"):
            ks = patient_keys(pid, last, first)
            if ks[0] in patients or ks[1] in patients:
                claim_who[ck] = ks
                mid[ck] = initial(middle)
        # The part of a service a line bills: the professional part (26), the technical part (TC), or the whole service ("").
        # A payment for one part pays nothing of the other (trace finding T-C1).
        part_of = lambda ln: "26" if "26" in ln["modifiers_list"] else "TC" if "TC" in ln["modifiers_list"] else ""     # noqa: E731
        fits = lambda p, want: not p or p in want or "" in want         # a payment for part p can pay toward these parts  # noqa: E731
        bills = {bill_of(f["patient_control_number"]) for f in todo}
        own_paid: dict[tuple, list] = defaultdict(list)     # (bill, code, date) -> the bill's standing payments: (status, amount, units, part)
        # standing payments: (date, claim id, trace, amount, status, units, claim key, part, line key, payer, modifiers)
        paid: dict[tuple, list] = defaultdict(list)
        to_patient: dict[tuple, list] = defaultdict(list)   # adjudicated at $0 with the allowed amount owed by the patient
        for l in self.lines:
            if not l.get("service_date"):
                continue
            amt = self.standing.get(l["line_key"])
            if amt and bill_of(l["patient_control_number"]) in bills:
                own_paid[(bill_of(l["patient_control_number"]), l.get("procedure_code"), l["service_date"])].append(
                    (l.get("claim_status_code"), amt, self.paid_units_of(l), part_of(l)))
            ks = claim_who.get(l["claim_key"])
            if not ks:
                continue
            pr = 0 if amt else sum(a for g, _rc, a in self.adj.get(l["line_key"], []) if g == "PR")
            if not amt and not (l.get("claim_status_code") in ("1", "19") and pr > 0):
                continue
            for k in ks:
                key = (k, l.get("procedure_code"), l["service_date"])
                if k and key in need:
                    row = (l.get("payment_date") or l.get("txn_payment_date") or "", l["patient_control_number"], l.get("trace_number"),
                           amt or pr, l.get("claim_status_code"), self.paid_units_of(l), l["claim_key"], part_of(l),
                           l["line_key"], l.get("payer_id") or l.get("payer_name"), l["modifiers"])
                    (paid if amt else to_patient)[key].append(row)
        # Codes paid more than once for a patient on a date (Mohs stages, units, fractions): a payment on another claim may
        # be for another stage or lesion. A code counts as paid once when at least 20 patient-dates had it paid and no more
        # than 1.5 percent had two or more paid lines of it, on one bill or several. Measured for the codes needed.
        codes_needed = sorted({code for _k, code, _d in need if code})
        # counted one patient-date at a time, in order: holding every patient-date's lines at once is the largest single
        # block of memory in a run
        groups, repeats = Counter(), Counter()
        last, members = None, set()        # (code, patient, date) -> distinct (bill, line number) paid
        for code, pid, dos, pcn, seq in c.execute(f"""SELECT l.procedure_code, cl.patient_id, l.service_date, l.patient_control_number, l.line_seq
                                     FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
                                     WHERE t.superseded = 0 AND l.claim_status_code IN ('1','19') AND l.paid_cents > 0 AND COALESCE(cl.patient_id,'') <> ''
                                       AND l.procedure_code IN ({','.join('?' * len(codes_needed))})
                                     ORDER BY l.procedure_code, cl.patient_id, l.service_date""", codes_needed):
            if (code, pid, dos) != last:
                if last is not None:
                    groups[last[0]] += 1
                    repeats[last[0]] += len(members) > 1
                last, members = (code, pid, dos), set()
            members.add((bill_of(pcn), seq))
        if last is not None:
            groups[last[0]] += 1
            repeats[last[0]] += len(members) > 1
        once = {code for code, g in groups.items() if g >= 20 and repeats[code] <= 0.015 * g}

        def part_units(rows: list[tuple]) -> Counter:
            """Units these payments pay, by part of the service: rows of (claim status, units, part), the primary payments'
            units of each part (a secondary pays its share of the same units), or the secondary ones' where no primary stands."""
            out: Counter = Counter()
            for p in ("", "26", "TC"):
                rs = [r for r in rows if r[2] == p]
                out[p] = sum(r[1] for r in ([r for r in rs if r[0] in ("1", "19")] or rs))
            return out
        pays = lambda u, p: u[""] + (u[p] if p else min(u["26"], u["TC"]))      # units of part p paid: the whole service, or both parts  # noqa: E731
        part_words = {"26": "the professional part (26)", "TC": "the technical part (TC)"}
        for f in todo:
            mine = f.get("payment_date") or ""
            bill = bill_of(f["patient_control_number"])
            exp = f.get("expected_cents") or 0
            dup = f.get("signal") == "DENIED_DUPLICATE_UNPAID" or (f.get("signal") == "CLAIM_DENIED_UNPAID" and f.get("carc") in DUP_CARCS)
            closable = f["category"] == "denial" or f.get("procedure_code") in EM_CODES
            strict, name = who.get(f["claim_key"], (None, None))
            mine_ema = self.ema_patient.get(f["patient_control_number"])
            mine_mid = mid.get(f["claim_key"], "")
            every = lines_of(f)
            ls = [ln for ln in every if ln.get("service_date") and ln.get("procedure_code")]
            # every unit the claim billed for the code and date (a second specimen is a second unit), against what its own
            # bill still has paid plus what other bills paid
            units: Counter = Counter({(ln["procedure_code"], ln["service_date"]): self.units_billed(f["claim_key"], ln["procedure_code"], ln["service_date"])
                                      for ln in ls})
            # the parts of the service the item's lines bill for each code and date, with the units the claim billed of each
            parts: dict[tuple, Counter] = defaultdict(Counter)
            for ln in ls:
                key, p = (ln["procedure_code"], ln["service_date"]), part_of(ln)
                if p not in parts[key]:
                    parts[key][p] = sum(self.billed_units_of(o) for o in self.lines_by_claim.get(f["claim_key"], [])
                                        if o.get("procedure_code") == key[0] and o.get("service_date") == key[1] and part_of(o) == p)
            found: dict[tuple, dict] = defaultdict(dict)       # same patient, after the denial: can close
            weak: dict[tuple, dict] = defaultdict(dict)        # a hint only
            why_weak: dict[tuple, str] = {}
            for (code, dos) in units:
                per_unit = exp / max(sum(units.values()), 1) if exp else 0
                for k, other_payer in ((strict, False), (name, True)):
                    for e in paid.get((k, code, dos), []) if k else []:
                        if bill_of(e[1]) == bill:
                            continue
                        if not fits(e[7], parts[(code, dos)]):
                            continue                # a payment for the other part of the service (a TC payment for a 26 denial)
                        if not (e[4] in ("1", "19") or (per_unit and e[3] >= 0.5 * per_unit * e[5])):
                            continue
                        theirs = mid.get(e[6], "")
                        if mine_mid and theirs and mine_mid != theirs:
                            continue                # another person with the same name: a father and son
                        if other_payer and not (mine_ema and self.ema_patient.get(e[1]) == mine_ema):
                            weak[(code, dos)][(e[1], e[0], e[7])] = e
                            why_weak.setdefault((code, dos), "it is the same name on another member number, and nothing ties the two claims to one patient")
                        elif not dup and e[0] <= mine:
                            weak[(code, dos)][(e[1], e[0], e[7])] = e
                            why_weak.setdefault((code, dos), "that claim was paid before this denial, so the denial may be for something else")
                        else:
                            found[(code, dos)][(e[1], e[0], e[7])] = e
            per_line = exp / max(len(units), 1) if exp else None

            def paid_units(key: tuple) -> Counter:
                """What other bills paid of this code and date (found), plus what the item's own bill still has paid on it (as
                paid_cover counts it: a primary payment, or a secondary one covering half the expected amount), by part."""
                own = [(st, n, p) for st, amt, n, p in own_paid.get((bill,) + key, []) if st in ("1", "19") or (per_line and amt >= 0.5 * per_line)]
                return part_units([(e[4], e[5], e[7]) for e in found[key].values()]) + part_units(own)
            covered = [key for key in units if key in found and all(pays(paid_units(key), p) >= n for p, n in parts[key].items())]
            # a denial of the whole service of which another claim paid only one part
            half = [key for key in units if key in found and key not in covered and "" in parts[key]
                    and {e[7] for e in found[key].values()} in ({"26"}, {"TC"})]
            best = min((e for v in found.values() for e in v.values()), key=lambda e: e[0], default=None)
            all_once = all(code in once for code, _d in units)
            if closable and best and units and len(covered) == len(units) and all_once:
                how = "the service was already paid on another claim" if dup and best[0] <= mine else "another claim for the same patient, date of service and code"
                # what it recovered: the other claims' payments after the denial for the item's own units (a line's own,
                # every code's for a whole claim); a payment made before it (a duplicate's denial) recovered nothing (T-C5).
                # The note names the payments that paid them (T-C6)
                own = every[0] if f.get("line_key") and every else None
                k_own = self.billed_units_of(own) if own is not None else None
                rec = self._recover(own, mine, f["patient_control_number"],
                                    [((None, code, dos), k_own or n, [(e[0], e[2], e[3], e[4], e[1], e[5], e[9], dos, e[8], e[10])
                                                                      for e in found.get((code, dos), {}).values() if e[0] > mine])
                                     for (code, dos), n in units.items()])
                firsts: dict[tuple, list] = {}
                for e, _u, cents in sorted(rec[2], key=lambda p: (p[0][0], p[0][1] or "")):
                    firsts.setdefault((e[4], e[0], e[1]), [e, 0.0])[1] += cents
                got = list(firsts.values())
                if best[0] > mine and got:
                    said = "; ".join(f"claim {e[4]} ({e[0]}, trace {e[1]}, ${c/100:.2f})" for e, c in got)
                    note = f"Paid on a later remittance under {said}" if len(got) == 1 else f"Paid on later remittances under {said}"
                    e0 = got[0][0]
                    detail = {"payment_date": e0[0], "claim": e0[4], "trace": e0[1], "paid_cents": int(round(sum(c for _e, c in got))),
                              "claim_status": e0[3], "how": how}
                    if len(got) > 1:
                        detail["payments"] = [{"payment_date": e[0], "trace": e[1], "claim": e[4], "paid_cents": int(round(c))} for e, c in got]
                    self._mark_paid(f, e0[0], detail["paid_cents"], note, detail, recovery=rec)
                else:
                    self._mark_paid(f, best[0], best[3], f"Paid on a later remittance under claim {best[1]} ({best[0]}, trace {best[2]}, ${best[3]/100:.2f})"
                                    if best[0] > mine else f"Already paid under claim {best[1]} ({best[0]}, trace {best[2]}, ${best[3]/100:.2f}): this denial is of a duplicate",
                                    {"payment_date": best[0], "claim": best[1], "trace": best[2], "paid_cents": best[3], "claim_status": best[4], "how": how},
                                    recovery=rec)
                f["hint"] = None
                continue
            ev = json.loads(f["evidence"]) if f.get("evidence") else {}
            reason, pick = None, None
            if best:
                pick = best
                reason = ("a bundled procedure is not closed from another claim; check whether that claim paid this one" if not closable
                          else "this code can be paid more than once for a patient on a date" if not all_once
                          else f"only {part_words[next(iter({e[7] for e in found[half[0]].values()}))]} was paid there, not the whole service" if half
                          else "the units paid there are fewer than the units denied" if any(key in found for key in units if key not in covered)
                          else "not every code on the denied claim was paid again")
                codes_paid = sorted({code for code, _d in found})
            else:
                pick = min((e for v in weak.values() for e in v.values()), key=lambda e: e[0], default=None)
                if pick is not None:
                    reason = next(why_weak[key] for key in weak if pick in weak[key].values())
                    codes_paid = sorted({code for code, _d in weak})
            if pick:
                ev["maybe_paid_later"] = {"claim": pick[1], "payment_date": pick[0], "paid_cents": pick[3], "codes_paid": codes_paid, "why_not_closed": reason}
                f["hint"] = f"Possibly paid on claim {pick[1]} ({pick[0]})"
                f["evidence"] = json.dumps(ev, default=str)
                continue
            owed = [e for k in (strict,) if k for key in units for e in to_patient.get((k,) + key, [])
                    if bill_of(e[1]) != bill and e[0] > mine and fits(e[7], parts[key])]
            if owed:
                e = min(owed)
                ev["adjudicated_elsewhere"] = {"claim": e[1], "payment_date": e[0], "patient_owes_cents": e[3]}
                f["hint"] = f"Processed on claim {e[1]} ({e[0]}): ${e[3]/100:,.2f} owed by the patient"
                f["evidence"] = json.dumps(ev, default=str)
                continue
            # EMA's figures are by claim, code and date, so they are read only where the claim carries the code once on that
            # date. Each code and date counts as paid when EMA shows insurance paid it at least half of what it is expected to
            # bring (its share of the item's expected amount). The item closes only when every code and date it lists is
            # paid so: a line, its own; a whole claim, each of its codes, since one code paid says nothing of the others.
            # Anything short of that is a hint, and the codes EMA does not show paid stay listed.
            per_code = Counter((ln.get("service_date"), ln.get("procedure_code")) for ln in self.lines_by_claim.get(f["claim_key"], []))
            share = self._expected_shares(ls, units, exp)
            shown: dict[tuple, tuple] = {}          # (code, date) -> (amount EMA shows paid, its date or None, where)
            for ln in ls:
                key = (ln["procedure_code"], ln["service_date"])
                if per_code[(key[1], key[0])] > 1:
                    continue
                part, seen = share.get(key) or 0, shown.get(key)
                for h in self.handling.get(ln["line_key"], []):
                    amt, when = h.get("payments_cents") or 0, h.get("transaction_date") or ""
                    if amt > 0 and when > mine and (not part or amt >= 0.5 * part) and (seen is None or amt > seen[0]):
                        seen = (amt, when, "EMA ERA & Payment Lines")
                got = self.ema_paid.get((ln.get("patient_control_number"), key[0], key[1]))
                if got and (not part or got[0] >= 0.5 * part) and (seen is None or got[0] > seen[0]):
                    seen = (got[0], None, got[1])
                if seen:
                    shown[key] = seen
            if shown:
                keys = sorted(shown)
                paid_k = sum(v[0] for v in shown.values())
                when_k = max((v[1] for v in shown.values() if v[1]), default=None)
                src = " and ".join(sorted({v[2] for v in shown.values()}))
                # every code and date the item lists, an undated line included, must be among those EMA shows paid
                undated = [(ln.get("procedure_code"), None) for ln in every if ln not in ls]
                missing = [key for key in units if key not in shown] + undated
                many = Counter(c for c, _d in list(units) + undated)        # a code on two dates is named with its date
                name = lambda key: key[0] if many[key[0]] <= 1 or not key[1] else f"{key[0]} on {key[1]}"      # noqa: E731
                ev["ema_shows_paid"] = {"paid_cents": paid_k, "date": when_k, "source": src}
                if len(keys) > 1 or missing:
                    ev["ema_shows_paid"]["codes_paid"] = [name(k) for k in keys]
                if missing:
                    ev["ema_shows_paid"]["codes_not_paid"] = [name(k) for k in missing]
                f["evidence"] = json.dumps(ev, default=str)
                on = f" on {when_k}" if when_k else ""
                which = f" for {', '.join(name(k) for k in keys)}" if len(keys) > 1 or missing else ""
                if EMA_CLOSES and not missing:
                    # what EMA has posted is what the billers see as paid: a claim corrected and paid, a paper check, a
                    # payment that never came as an 835. The item closes; a person can reopen it if that money is for
                    # something else.
                    self._mark_paid(f, when_k, paid_k,
                                    f"EMA shows ${paid_k/100:,.2f} paid by insurance{which}{on} ({src}). Reopen it if that payment is for something else.",
                                    {"payment_date": when_k, "claim": f["patient_control_number"], "paid_cents": paid_k, "how": "EMA shows it paid",
                                     "trace": "EMA " + "; ".join(f"{c} {d}" for c, d in keys), "source": src})
                    f["hint"] = None
                    continue
                f["hint"] = (f"EMA shows ${paid_k/100:,.2f} paid by insurance{which}{on}"
                             + (f", not for {', '.join(name(k) for k in missing)}" if missing else "") + "; check before marking recovered")

    def _expected_shares(self, ls: list, units: Counter, exp: int | float | None) -> dict[tuple, float]:
        """What each code and date of an item is expected to bring, for reading what EMA shows paid on it: the item's
        expected amount, split by each code's usual rate for the line (expected_allowed), or by units where a code has none.
        Empty when the item has no expected amount (then any payment counts)."""
        if not exp or not units:
            return {}
        if len(units) == 1:
            return {key: exp for key in units}
        rate: Counter = Counter()
        for ln in ls:
            key = (ln["procedure_code"], ln["service_date"])
            if key in units:
                rate[key] += self.expected_allowed(ln, key[0])[0] or 0
        if all(rate[key] > 0 for key in units):
            total = sum(rate[key] for key in units)
            return {key: exp * rate[key] / total for key in units}
        total = sum(units.values()) or 1
        return {key: exp * units[key] / total for key in units}

    def _emit(self, signal, category, severity, actionable, title, l, expected, shortfall, carc, rarc, evidence,
              key_parts=None, claim=None, resolved_by: tuple | None = None, id_signal: str | None = None) -> None:
        base = l or claim or {}
        lk = base.get("line_key")
        charge = base.get("charge_cents") or 0
        if lk and expected and 0 < charge < expected:
            # a payer allows the lesser of the charge and its rate: a line is never worth more than its charge (trace T-C7)
            over = expected - charge
            expected = charge
            shortfall = max(shortfall - over, 0) if shortfall else shortfall
            if isinstance(evidence, dict) and evidence.get("basis") and "capped at the line's charge" not in evidence["basis"]:
                evidence = dict(evidence, basis=evidence["basis"] + ", capped at the line's charge")
        ema_handling = None
        written_off, written_on = None, None
        hs: list[dict] = []
        own: list[dict] = []                # the rows that are this finding's own (for what the biller gave up)
        if lk:
            link = self.links.get(lk)
            if link:
                evidence = dict(evidence, ema_link=link["state"], ema_billed=f"{link['ema_cpt']}{'-' + ','.join(json.loads(link['ema_modifiers'])) if link.get('ema_modifiers') else ''} x{link['ema_units']} ${(link['ema_charges_cents'] or 0)/100:.2f}", ema_claim_status=link.get("ema_claim_status"))
            hs = self.handling.get(lk) or []
            own = hs if self._code_once(base) else []
        elif base.get("claim_key"):         # a whole claim: what the biller did on its lines
            claim_lines = self.lines_by_claim.get(base["claim_key"], [])
            hs = [h for x in claim_lines for h in self.handling.get(x["line_key"], [])]
            # the code and date pairs this finding is about (or its codes), not every line of the claim
            pairs = {tuple(p) for p in evidence.get("unpaid") or [] if isinstance(p, (list, tuple)) and len(p) == 2}
            listed = set(evidence.get("codes") or [])
            own = [h for x in claim_lines
                   if (((x.get("procedure_code"), x.get("service_date") or x.get("statement_from")) in pairs) if pairs
                       else (not listed or x.get("procedure_code") in listed)) and self._code_once(x)
                   for h in self.handling.get(x["line_key"], [])]
        if hs:
            statuses = sorted({h["adjustment_status"] for h in hs if h.get("adjustment_status")})
            ema_handling = statuses[0] if len(statuses) == 1 else ("MIXED" if statuses else None)
            evidence = dict(evidence, ema_handling=[f"{h['carc'] or ''} {h['adjustment_status'] or ''} ${(h['adjustments_cents'] or 0)/100:.2f} on {h['transaction_date'] or ''}".strip() for h in hs[:8]])
            written_off, written_on = written_off_of(own, cap=shortfall if (shortfall or 0) > 0 else None, own=carc,
                                                     sign=getattr(self, "wo_sign", 1))
        if resolved_by:
            evidence = dict(evidence, paid_later={"payment_date": resolved_by[0], "trace": resolved_by[1], "paid_cents": resolved_by[2], "claim_status": resolved_by[3]})
        fid = _fid(id_signal or signal, *(key_parts or (base.get("line_key") or base.get("claim_key") or json.dumps(evidence, sort_keys=True),)))
        kind = base.get("payer_kind", "other")
        grp = base.get("payer_group")
        pay_date = base.get("payment_date") or base.get("txn_payment_date")
        plan = base.get("plan") or (base.get("contract_class") or "").strip() or None
        facts = rule_facts(base, carc=carc, plan=plan)   # which plan of the group: the plan's or payer's name, the reason, paid or not
        rule = appeal_rule(grp, kind, **facts)
        deadline, deadline_basis, legal = None, None, None
        fix = None
        # a payer that takes no appeal of an ordinary claim denial (Kentucky Medicaid, 907 KAR 1:671) is worked by a corrected
        # or new claim inside its filing limit, so the item is dated as one; a takeback is a determination it does hear
        no_appeal = bool(rule.get("no_appeal")) and signal != "TAKEBACK" and actionable in ("appeal", "review", "correct_and_resubmit")
        if actionable == "correct_and_resubmit" or no_appeal:
            # MA130 and other remarks can sit on the claim (MOA) as well as on the line
            rems = ",".join(x for x in [rarc or ""] + list(self.claim_rem.get(base.get("claim_key"), [])) if x)
            fix = corrected_rule(grp, kind, rems, signal, carc, **facts)
        if fix:
            # a corrected claim (a reopening for Medicare Part B) runs on its own clock, from the remittance or the date of service
            # (codes.corrected_window: the earlier of two where the remittance does not say which applies)
            deadline, legal = corrected_window(fix, self._first_service_day(base), pay_date)
            deadline_basis = fix["label"] + ("" if fix["verified"] else " (not confirmed against the payer's current manual)")
            if no_appeal:
                deadline_basis += ". It takes no appeal of an ordinary claim denial (907 KAR 1:671 Sections 8 and 9)"
            cross = fix.get("crossover")
            if cross and pay_date:
                # a claim billed to Medicare first may also go within 6 months of Medicare's adjudication where that is later
                # (907 KAR 3:005): dated from Medicare's remittance for the same bill and day where it is on file (review r1-5)
                xday = self._first_answer_of(base, cross["kinds"], grp)
                xend = _codes.add_months(date.fromisoformat(xday[:10]), int(cross["months"])).isoformat() if xday else None
                if xend and deadline and xend > (legal or deadline):
                    legal = xend
                    deadline_basis += (f". Medicare answered it first on {xday}: a claim billed to Medicare may also go 'Within six (6) "
                                       f"months of the Medicare adjudication date' (907 KAR 3:005), until {xend}")
            elif kind != "medicare" and pay_date:
                # the payer's own dispute of the determination stays open beside the corrected claim (Humana's 18 months from
                # the determination outlast its corrected-claim year): the later of the two routes is the payer's limit. Medicare
                # Part B is left out: an unprocessable claim (MA130) has no appeal rights, and its other windows are its own.
                disp = legal_deadline(pay_date, rule) or window_end(pay_date, rule)
                if disp and deadline and disp > (legal or deadline):
                    legal = disp
                    deadline_basis += f". After that, a dispute of the payer's answer stays open until {disp}: {rule_at(pay_date, rule)['label']}"
        elif actionable in ("appeal", "correct_and_resubmit") or signal == "TAKEBACK" or (category in END_DATED_CATEGORIES and pay_date):
            # a takeback is disputed inside the payer's appeal window from the remittance that took the money back; any other
            # money item from a remittance (a non-covered or timely-filing denial to review, a claim sent on to another
            # carrier) ends with the payer's window to dispute that answer, so that it can expire
            deadline = window_end(pay_date, rule)
            legal = legal_deadline(pay_date, rule)
            # the rule the date was counted by: before an effective date, the window before it (deadlines review r2-5)
            used = rule_at(pay_date, rule)
            deadline_basis = used["label"] + ("" if used["verified"] else " (not confirmed against the payer's current manual)")
            if signal == "TAKEBACK":
                deadline_basis += (". Counted from the remittance that took the money back; the payer's overpayment letter, which the "
                                   "remittance does not carry, may start the clock earlier")
            if actionable == "correct_and_resubmit":
                deadline_basis += AGREEMENT_FILING_NOTE
            if signal == "FORWARDED_TO_CARRIER":
                deadline_basis += (". Counted from the remittance that sent the claim on: past it, the payer that forwarded it takes no "
                                   "dispute, and the other carrier's own filing limit applies")
            elif actionable == "review":
                deadline_basis += ". Counted from the remittance with this answer: past it, the payer takes no dispute of it"
            if actionable in ("appeal", "review") and kind != "medicare" and signal in CORRECTABLE_APPEAL_SIGNALS and pay_date:
                # what a corrected claim can fix (the authorization number, the clinician's enrollment, a modifier) can also go as a
                # corrected claim inside the payer's corrected-claim limit, where its manual sets one (Anthem and Humana Medicare
                # Advantage: a year from the date of service): the later date is the payer's limit
                rems = ",".join(x for x in [rarc or ""] + list(self.claim_rem.get(base.get("claim_key"), [])) if x)
                alt = corrected_rule(grp, kind, rems, signal, carc, **facts)
                if alt:
                    c_safe, c_legal = corrected_window(alt, self._first_service_day(base), pay_date)
                    cdate = c_legal or c_safe
                    if cdate and deadline and cdate > (legal or deadline):
                        legal = cdate
                        deadline_basis += (f". A corrected claim with what was missing (such as the authorization number) can still go "
                                           f"until {cdate}: {alt['label']}")
        code = base.get("procedure_code")
        billed = evidence.get("billed_code") or base.get("original_procedure_code") or evidence.get("charge_implies") or code
        fam_b, fam_p = family_of(billed), family_of(code)
        level_drop = (fam_b[1] - fam_p[1]) if (fam_b and fam_p and fam_b[0] == fam_p[0] and billed != code) else None
        if evidence.get("paid_like") and family_of(evidence["paid_like"]) and fam_p:
            level_drop = fam_p[1] - family_of(evidence["paid_like"])[1]
        pair = f"{billed}→{code}" if (billed and code and billed != code) else (f"{code} at {evidence['paid_like']} rate" if evidence.get("paid_like") else None)
        if pair is None and signal in DOWNCODE_SIGNALS and code:
            pair = f"{code} denied for level" if not (base.get("allowed_cents") or 0) else f"{code} reduced"
        link = self.links.get(lk) if lk else None
        dos = base.get("service_date") or base.get("statement_from")
        recovered = None
        auto_notes = None
        if resolved_by and getattr(resolved_by, "wants", None):
            # the money the item's own units got after its answer, less what stood before it, from the payments that paid
            # them, each unit counted on one finding only; the note names those payments (trace findings T-C5, T-C6)
            recovered, value, picks, elsewhere = self._recover(l, pay_date or "", base.get("patient_control_number"), resolved_by.wants)
            if not shortfall or shortfall <= 0:
                # no baseline for this line: the amount the payer later paid for it is the best measure of what was at stake
                expected = shortfall = value or resolved_by[2] or 0
                evidence = dict(evidence, basis="amount paid on the later remittance (no baseline for this code)")
            auto_notes, paid = self._paid_words(picks, resolved_by)
            evidence = dict(evidence, paid_later=dict({"claim_status": resolved_by[3]}, **paid))
            if elsewhere:
                evidence = dict(evidence, recovery_counted_elsewhere=True)
        elif resolved_by:
            if not shortfall or shortfall <= 0:
                # no baseline for this line: the amount the payer later paid is the best measure of what was at stake
                expected = shortfall = resolved_by[2] or 0
                evidence = dict(evidence, basis="amount paid on the later remittance (no baseline for this code)")
            recovered = min(resolved_by[2] or 0, shortfall)
            # one payment of one line (its code and date): a later claim paying two lines is two recoveries; a whole claim's
            # item names every code and date it lists, so a line item the same payment closed is not counted again on it
            keys = [(resolved_by[0], resolved_by[1], resolved_by[4] if len(resolved_by) > 4 else None, c, d)
                    for c, d in _service_keys(base, evidence, code, dos)]
            if any(k in self.used_payments for k in keys):
                recovered = 0           # this payment is already counted as recovered on another finding
                evidence = dict(evidence, recovery_counted_elsewhere=True)
            self.used_payments.update(keys)
        self.findings[fid] = {
            "billed_code": billed, "code_pair": pair, "level_drop": level_drop,
            "pos": base.get("pos") or base.get("facility_type_code"), "pos_class": base.get("pos_class") or pos_class(base.get("facility_type_code")),
            "plan": plan, "month": (pay_date or "")[:7] or None, "service_month": (dos or "")[:7] or None,
            "modifier_class": modifier_class(base.get("modifiers_list") or []) if code else None,
            "location": (link or {}).get("location") or self.claim_location.get(base.get("patient_control_number") or "")
                        or (self.npi_location.get(base.get("rendering_provider_npi") or "", "") + " (provider's usual site)"
                            if self.npi_location.get(base.get("rendering_provider_npi") or "") else None),
            "diagnosis": ((link or {}).get("icd10") or "").split(",")[0].strip() or None,
            "deadline_basis": deadline_basis, "recovered_cents": recovered,
            "recovered_date": ((evidence.get("paid_later") or {}).get("payment_date") or resolved_by[0]) if resolved_by else None,
            "finding_id": fid, "signal": signal, "category": category, "severity": severity, "actionable": actionable, "title": title,
            "line_key": base.get("line_key"), "claim_key": base.get("claim_key"), "transaction_key": base.get("transaction_key"),
            "patient_control_number": base.get("patient_control_number"),
            "payer_name": base.get("payer_name"), "payer_group": grp, "payer_kind": kind, "payer_id": base.get("payer_id"),
            "rendering_provider_npi": base.get("rendering_provider_npi"), "rendering_provider_name": base.get("rp_name") or base.get("rendering_provider_name"),
            "provider_tier": base.get("provider_tier"),
            "service_date": dos, "payment_date": pay_date, "year": base.get("year"),
            "procedure_code": code, "modifiers": ",".join(base.get("modifiers_list") or []) or None,
            "original_procedure_code": base.get("original_procedure_code"), "code_category": code_category(code),
            "charge_cents": base.get("charge_cents"), "allowed_cents": base.get("allowed_cents"), "paid_cents": base.get("paid_cents"),
            "expected_cents": expected, "shortfall_cents": shortfall,
            "carc": carc, "carc_text": carc_text(carc), "rarc": rarc, "rarc_text": rarc_text((rarc or "").split(",")[0]),
            "evidence": json.dumps(evidence, default=str),
            "appeal_deadline": deadline, "legal_deadline": legal if (legal and deadline and legal > deadline) else None,
            "source": "835" if (base.get("line_key") or base.get("claim_key") or base.get("transaction_key")) else None,
            "ema_handling": ema_handling, "written_off_cents": written_off, "written_off_on": written_on,
            "auto_status": "resolved" if resolved_by else None,
            "auto_notes": auto_notes or (f"Paid on a later remittance ({resolved_by[0]}, trace {resolved_by[1]}, ${(resolved_by[2] or 0)/100:.2f})"
                                         if resolved_by else None),
            "run_id": self.run_id,
        }

    # ------------------------------------------------------------------ line rules
    def run_line_rules(self) -> None:
        def dup_units(l: dict, code: str) -> float:
            """Units a duplicate's denial is about: this line's, plus lines of the code and date paid on this same claim (a
            second specimen denied as a duplicate of the first). Other lines denied as duplicates are not counted: a
            resubmission that repeats a paid service three times is three copies of one service, not three services. (For a
            code paid at most once a day, a second line is a charge posted twice: nothing more is owed.)"""
            return self.billed_units_of(l) + (0 if code in self.once_codes else sum(
                self.billed_units_of(o) for o in self.lines_by_claim.get(l["claim_key"], [])
                if o["line_key"] != l["line_key"] and o.get("procedure_code") == code and o.get("service_date") == l.get("service_date")
                and (o.get("paid_cents") or 0) > 0))
        copies = self.answer_copies = self._answer_copies()
        # a denied visit with a lower level paid in its place is one event: the paid line must not also count as a downcode
        swap_paid: set[str] = set()
        primary_changes: set[tuple] = set()
        for claim_lines in self.lines_by_claim.values():
            for x in claim_lines:
                cx, st = x.get("procedure_code"), x.get("claim_status_code")
                if st not in ("1", "19") or not cx:
                    continue
                ox = x.get("original_procedure_code")
                if ox and ox != cx:
                    primary_changes.add((bill_of(x.get("patient_control_number")), x.get("service_date"), ox, cx))
                if cx in EM_CODES and not (x.get("paid_cents") or 0) and not (x.get("allowed_cents") or 0):
                    carcs_x = {rc for g, rc, _a in self.adj.get(x["line_key"], []) if g in ("CO", "PI", "OA")}
                    if carcs_x & LEVEL_CARCS or any(r in LEVEL_RARCS for r in self.rem.get(x["line_key"], [])):
                        for o in claim_lines:
                            if o["line_key"] != x["line_key"] and (o.get("paid_cents") or 0) > 0 and o.get("service_date") == x.get("service_date") \
                                    and is_downcode(cx, o.get("procedure_code")):
                                swap_paid.add(o["line_key"])
        for l in self.lines:
            status = l.get("claim_status_code")
            code = l.get("procedure_code")
            if not code or l["line_key"] in swap_paid or l["claim_key"] in self.void_claims:
                continue        # (a payer's answer to a claim the practice voided makes no item: T-N5)
            mods = l["modifiers_list"]
            adj, top, pr, carcs = self._line_carcs(l)
            rarcs = self.rem.get(l["line_key"], [])
            rarc = ",".join(rarcs) if rarcs else None
            primary = status in ("1", "19")
            orig = l.get("original_procedure_code")
            if orig == code:
                orig = None     # SVC06 repeats the paid code when only the modifiers or units changed
            allowed = l.get("allowed_cents") or 0
            paid = l.get("paid_cents") or 0
            level_amt = sum(a for g, rc, a in adj if rc in LEVEL_CARCS and g in ("CO", "PI", "OA"))
            level_flag = bool(carcs & LEVEL_CARCS) or (code in EM_CODES and any(r in LEVEL_RARCS for r in rarcs))

            # ---- downcoding and code changes (any claim status)
            if orig and orig != code:
                # re-adjudication remits split lines into pieces (B13 "previously paid", token charges): not appealable events
                claim_lines = self.lines_by_claim.get(l["claim_key"], [])
                readjudicated = any(rc == "B13" for ln in claim_lines for _g, rc, _a in self.adj.get(ln["line_key"], [])) \
                    or any(rc == "B13" for _g, rc, _a in self.claim_adj.get(l["claim_key"], []))
                if readjudicated or (l.get("charge_cents") or 0) < 2000:
                    continue
                # a secondary payer that echoes the primary's code change is the same event, not a second downcode
                if not primary and (bill_of(l.get("patient_control_number")), l.get("service_date"), orig, code) in primary_changes:
                    continue
                if is_downcode(orig, code):
                    exp, basis = self.downcode_expected(l, orig, code, allowed)
                    short = (exp - allowed) if exp else None
                    ev = {"basis": basis, "original_modifiers": l.get("original_modifiers"), "claim_status": status}
                    if level_flag:
                        ev["level_adjustment"] = f"{'/'.join(sorted(carcs & LEVEL_CARCS)) or rarc} ${level_amt/100:.2f}"
                    self._emit("DOWNCODE_SVC06", "downcoding", "high" if primary else "medium", "appeal",
                               f"Billed {orig}, paid as {code} ({l['payer_group']})", l, exp, short, top, rarc, ev,
                               resolved_by=self.later_payment(l, orig, expected=exp))
                else:
                    exp, basis = self.expected_allowed(l, orig)
                    short = (exp - allowed) if exp else None
                    if short and short > 100:
                        # a line answered under another code and paid nothing is settled, as a downcode is, by the billed
                        # code paid since on the bill for every unit billed (a corrected claim, or the second payer paying
                        # its share after the first payer's answer)
                        self._emit("CODE_CHANGED", "downcoding", "medium", "review",
                                   f"Payer changed {orig} to {code} ({l['payer_group']})", l, exp, short, top, rarc, {"basis": basis, "claim_status": status},
                                   resolved_by=None if paid else self.later_payment(l, orig, expected=exp))
                continue
            if primary and not orig:
                # the claim file or EMA shows a higher level was billed and the remit does not admit to a change
                hb = self.billed_higher(l, code)
                if hb:
                    exp, basis = self.downcode_expected(l, hb[0], code, allowed)
                    short = (exp - allowed) if exp else None
                    self._emit("DOWNCODE_VS_BILLED", "downcoding", "high", "appeal",
                               f"Billed {hb[0]} per the {hb[1]}, remit shows {code} with no code change reported ({l['payer_group']})",
                               l, exp, short, top, rarc, {"basis": basis, "billed_code": hb[0], "billed_source": hb[1], "billed_codes": hb[2]},
                               resolved_by=self.later_payment(l, hb[0], expected=exp))
                    continue
                implied = self.fees.implied_original(l["year"], code, l.get("charge_cents"))
                if implied:
                    exp, basis = self.downcode_expected(l, implied, code, allowed)
                    short = (exp - allowed) if exp else None
                    self._emit("DOWNCODE_CHARGE", "downcoding", "high", "appeal",
                               f"Charge matches the {implied} fee but paid as {code} ({l['payer_group']})", l, exp, short, top, rarc,
                               {"basis": basis, "charge_implies": implied}, resolved_by=self.later_payment(l, implied, expected=exp))
                    continue
                # payment cut for the level with the billed code left in place (Anthem's CARC 186 pattern)
                if level_flag and code in EM_CODES and allowed > 0:
                    usual, basis, _r = self.usual_rate(l, code)
                    codes186 = "/".join(sorted(carcs & LEVEL_CARCS)) or (rarc or "")
                    exp = usual
                    if level_amt > 0 and (not usual or allowed + level_amt < usual):
                        # the payer's own adjustment amount, unless it exceeds its usual rate for this provider type (it is
                        # computed from the physician fee even when the visit is paid at the non-physician rate)
                        exp, basis = allowed + level_amt, f"allowed amount plus the payer's own level-of-care adjustment ({codes186} ${level_amt/100:.2f})"
                    short = (exp - allowed) if exp else None
                    if short and short > 0:
                        like = self.paid_like(l, code, allowed)
                        self._emit("DOWNCODE_LEVEL_REDUCED", "downcoding", "high", "appeal",
                                   f"{code} paid at a reduced level with the code unchanged ({codes186}{', paid like ' + like if like else ''}, {l['payer_group']})",
                                   l, exp, short, next(iter(sorted(carcs & LEVEL_CARCS)), top), rarc,
                                   {"basis": basis, "paid_like": like, "level_adjustment_cents": level_amt, "usual_rate_cents": usual},
                                   resolved_by=self.later_payment(l, expected=exp, min_paid=paid + short // 2))
                        continue
                if code in EM_CODES and allowed > 0 and paid > 0 and l.get("paid_units") in (None, 1, 1.0) and not l["pricing_mod"]:
                    own, _b, own_r = self.usual_rate(l, code)
                    if own and own_r and allowed < own:
                        lower = self.paid_like(l, code, allowed)
                        if lower:
                            self._emit("DOWNCODE_RATE", "downcoding", "medium", "appeal",
                                       f"{code} allowed at the {lower} rate ({l['payer_group']})", l, own, own - allowed, top, rarc, {"paid_like": lower},
                                       resolved_by=self.later_payment(l, expected=own, min_paid=paid + (own - allowed) // 2))
                            continue
                # remark N22 says the payer added or changed the code but no original code was reported
                if "N22" in rarcs and code not in EM_CODES and allowed > 0:
                    _rate, _b, r = self.usual_rate(l, code)
                    exp, basis = self.expected_allowed(l, code)         # rank-aware: a correctly reduced second procedure is not short
                    self._emit("CODE_CHANGED", "downcoding", "medium", "review",
                               f"Payer says it added or changed {code} (remark N22) without reporting the billed code ({l['payer_group']})",
                               l, exp, (exp - allowed) if (exp and r and exp > allowed) else 0, top, rarc, {"basis": basis, "claim_status": status})
                    continue

            if not primary:
                continue

            # ---- level-of-service denials on unpaid visits (CARC 150/186 or a level remark)
            if level_flag and code in EM_CODES and paid == 0 and allowed == 0:
                exp, basis = self.expected_allowed(l, code)
                # a lower-level visit line paid on the same claim and date means the payer swapped the line rather than denied the visit
                swap = next((o for o in self.lines_by_claim[l["claim_key"]] if o["line_key"] != l["line_key"] and (o.get("paid_cents") or 0) > 0
                             and o.get("service_date") == l.get("service_date") and is_downcode(code, o.get("procedure_code"))), None)
                if swap and exp:
                    self._emit("DOWNCODE_LINE_SWAP", "downcoding", "high", "appeal",
                               f"Billed {code} denied and a {swap['procedure_code']} line paid in its place ({l['payer_group']})",
                               l, exp, exp - (swap.get("allowed_cents") or 0), top or next(iter(carcs & LEVEL_CARCS), None), rarc,
                               {"basis": basis, "paid_line_code": swap["procedure_code"], "paid_line_allowed_cents": swap.get("allowed_cents")},
                               resolved_by=self.later_payment(l, expected=exp))
                    continue
                if exp:
                    self._emit("DOWNCODE_LEVEL_DENIED", "downcoding", "high", "appeal",
                               f"{code}: payer says documentation does not support the level ({top or ','.join(sorted(carcs & LEVEL_CARCS)) or rarc})",
                               l, exp, exp, top or next(iter(carcs & LEVEL_CARCS), None), rarc, {"basis": basis},
                               resolved_by=self.later_payment(l, expected=exp))
                    continue

            # ---- the whole charge taken as a contractual reduction (CO-45) with nothing allowed: the payer paid nothing for
            # the service, a denial in all but name (the answer key's R9), never a fee-schedule cut. Settled after the line
            # rules: not when the payer allowed or paid the same service on another of its answers, and not with a remark that
            # points to the contract (N381)
            charge_l = l.get("charge_cents") or 0
            if paid == 0 and allowed == 0 and pr == 0 and charge_l > 100 and not top and code not in ("99024", "G2211") \
                    and status in ("1", "19") and sum(a for g, rc, a in adj if rc == "45" and g in ("CO", "PI", "OA")) >= charge_l:
                if CONTRACT_REMARKS & set(rarcs):
                    self.ema_counts["co45_contract_remark"] += 1
                    continue
                self.__dict__.setdefault("_co45_cands", []).append((l, rarc))
                continue
            # ---- zero-paid lines (silent denials inside paid claims)
            if paid == 0 and allowed == 0 and pr == 0 and (l.get("charge_cents") or 0) > 0 and top:
                cat = code_category(code)
                if code == "99024" or code == "G2211" or (l.get("charge_cents") or 0) <= 100:
                    continue
                # each unit billed is a service of its own (a second Mohs stage, a third specimen): the denial is of all of
                # them, each at the payer's rate for the part billed, never above the charge (T-C7)
                exp, basis = self.line_expected(l, code)
                later = self.later_payment(l, expected=exp)
                emit = self._emit
                self._emit = lambda *a, **k: emit(*a, **dict(k, resolved_by=later))  # noqa: E731
                if top in FORWARD_CARCS:
                    # sent on to the proper carrier: its money is expected from that carrier, nothing was refused
                    carrier = self.crossover_carrier(l["claim_key"])
                    self._emit("FORWARDED_TO_CARRIER", "denial", "low", "review",
                               f"{code} sent on by {l['payer_group']} to {carrier or 'another carrier'} ({top}): expect that carrier's payment",
                               l, exp, exp, top, rarc, {"basis": basis, "carrier": carrier})
                    self._emit = emit
                    continue
                if top in BUNDLING_CARCS or "M80" in rarcs or "N19" in rarcs or "M15" in rarcs or "N20" in rarcs:
                    if code in EM_CODES:
                        companions = [o for o in self.lines_by_claim[l["claim_key"]] if o["line_key"] != l["line_key"]]
                        comp_codes = sorted({o["procedure_code"] for o in companions if o.get("procedure_code")})
                        globals_ = [GLOBAL_DAYS.get(c) for c in comp_codes]
                        in_global = top == "B20" or "M86" in rarcs
                        if "24" in mods and in_global:
                            # a visit inside another surgery's global period: the 24 (unrelated problem) is what decides it
                            sig, sev, act, ttl = "EM_BUNDLED_MOD24", "medium", "appeal", f"{code}-24 denied inside a global period"
                        elif "25" in mods:
                            sig, sev, act, ttl = "EM_BUNDLED_MOD25", "high", "appeal", f"{code}-25 bundled into same-day procedure ({l['payer_group']})"
                        elif "57" in mods:
                            if any(g == 90 for g in globals_):
                                sig, sev, act, ttl = "EM_BUNDLED_MOD57_MAJOR", "medium", "appeal", f"{code}-57 bundled; 90-day global procedure same day"
                            else:
                                sig, sev, act, ttl = "EM_BUNDLED_MOD57_MINOR", "high", "correct_and_resubmit", f"{code}-57 with 0/10-day global procedure: modifier 57 is wrong, use 25"
                        elif "24" in mods:
                            sig, sev, act, ttl = "EM_BUNDLED_MOD24", "medium", "appeal", f"{code}-24 denied inside a global period"
                        elif not mods:
                            sig, sev, act, ttl = "EM_BUNDLED_NO_MODIFIER", "high", "correct_and_resubmit", f"{code} billed with a procedure and no modifier 25"
                        else:
                            sig, sev, act, ttl = "EM_BUNDLED_MOD25", "medium", "appeal", f"{code} ({','.join(mods)}) bundled"
                        self._emit(sig, "bundling", sev, act, ttl, l, exp, exp, top, rarc, {"basis": basis, "same_day_codes": comp_codes, "global_days": globals_})
                    elif code in ADD_ON_CODES:
                        self._emit("ADDON_BUNDLED", "bundling", "high", "appeal", f"Add-on {code} paid $0 as bundled (add-ons are payable with their primary)", l, exp, exp, top, rarc, {"basis": basis})
                    else:
                        comp = sorted({o["procedure_code"] for o in self.lines_by_claim[l["claim_key"]] if o["line_key"] != l["line_key"] and o.get("procedure_code")})
                        self._emit("PROCEDURE_BUNDLED", "bundling", "high" if (exp or 0) >= 10000 else "medium", "appeal",
                                   f"{code} bundled into another same-day service ({top})", l, exp, exp, top, rarc, {"basis": basis, "same_day_codes": comp})
                    self._emit = emit
                    continue
                if top in INFO_CARCS:
                    if any(r in MODIFIER_RARCS for r in rarcs):
                        self._emit("DENIED_MISSING_MODIFIER", "denial", "high", "correct_and_resubmit", f"{code} denied: missing/invalid modifier ({rarc})", l, exp, exp, top, rarc, {"basis": basis})
                    elif any(r in CODING_RARCS for r in rarcs):
                        self._emit("DENIED_CODING", "denial", "high", "correct_and_resubmit", f"{code} denied: coding error ({rarc})", l, exp, exp, top, rarc, {"basis": basis})
                    else:
                        self._emit("DENIED_MISSING_INFO", "denial", "medium", "correct_and_resubmit", f"{code} denied for missing information ({top} {rarc or ''})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in CODING_CARCS:
                    self._emit("DENIED_CODING", "denial", "high", "correct_and_resubmit", f"{code} denied: code/modifier inconsistent ({top})", l, exp, exp, top, rarc, {"basis": basis, "modifiers": mods})
                    self._emit = emit
                    continue
                if top in PRIMARY_CARCS:
                    self._emit("DENIED_MISSING_PRIMARY", "denial", "medium", "review", f"{code} denied: qualifying/primary service missing or denied ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in AUTH_CARCS:
                    self._emit("DENIED_AUTH", "denial", "high", "appeal", f"{code} denied: authorization ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in LEVEL_CARCS:
                    self._emit("DENIED_LEVEL_OF_SERVICE", "denial", "high", "appeal",
                               f"{code} denied: payer says the records do not support the service as billed ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top == "151" and "M86" in set(rarcs) | set(self.claim_rem.get(l["claim_key"], [])):
                    # CO-151 with M86 (payment already made for the same service) is the payer's duplicate denial: on a
                    # resubmission of a service the bill already had paid, by a payment still standing made on or before
                    # this answer, nothing is owed (trace finding T-N8). Paid only later, the denial is closed by that
                    # payment as any denial is; nothing paid, it is the frequency denial below
                    mine = l.get("payment_date") or l.get("txn_payment_date") or ""
                    have = [e for e in self.paid_cover(bill_of(l["patient_control_number"]), code, l.get("service_date"), exp) if e[0] <= mine]
                    if self.units_paid(have) >= dup_units(l, code):
                        self._emit = emit
                        continue
                if top in MEDNEC_CARCS:
                    self._emit("DENIED_MEDICAL_NECESSITY", "denial", "high", "appeal", f"{code} denied: medical necessity / documentation ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in FREQ_CARCS:
                    self._emit("DENIED_FREQUENCY", "denial", "medium", "appeal", f"{code} denied: frequency/units ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in TIMELY_CARCS:
                    self._emit("DENIED_TIMELY_FILING", "denial", "high", "review", f"{code} denied: timely filing", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in PROVIDER_CARCS:
                    self._emit("DENIED_PROVIDER", "denial", "high", "review", f"{code} denied: provider enrollment/credentialing ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in DUP_CARCS:
                    have = self.paid_cover(bill_of(l["patient_control_number"]), code, l.get("service_date"), exp)
                    if self.units_paid(have) < dup_units(l, code):
                        self._emit("DENIED_DUPLICATE_UNPAID", "denial", "medium", "review", f"{code} denied as duplicate but never paid", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if top in NONCOV_CARCS:
                    self._emit("DENIED_NONCOVERED", "denial", "low", "review", f"{code} not covered ({top})", l, exp, exp, top, rarc, {"basis": basis})
                    self._emit = emit
                    continue
                if cat not in ("postop",):
                    self._emit("DENIED_OTHER", "denial", "low", "review", f"{code} paid $0 ({top})", l, exp, exp, top, rarc, {"basis": basis})
                self._emit = emit
                continue

            # ---- underpayment on paid lines
            if paid > 0 and allowed > 0 and l.get("paid_units") in (None, 1, 1.0) and not orig and not l["pricing_mod"]:
                if carcs & {"B10", "97", "236", "234"}:
                    continue
                if l["line_key"] in copies:
                    continue        # the same answer delivered again the same day: one underpayment, one item (T-I3)
                worked_in = self.line_to_pattern.get(l["line_key"]) or self.claim_to_pattern.get((l["patient_control_number"], code, l.get("service_date"), allowed))
                if worked_in:
                    # worked as part of a routine-rate finding: it stays there whatever the baseline now says
                    self.pattern_lines[worked_in].append(l)
                    continue
                old_in = self.line_to_old.get(l["line_key"]) or self.claim_to_old.get((l["patient_control_number"], code, l.get("service_date"), allowed))
                if old_in and not self._gone(l):
                    # a claim of a repricing request someone sent: it stays in that request whatever the rules now say (one the
                    # payer took back since leaves it below, as any line taken back does)
                    self.old_worked_lines[old_in].append(l)
                    continue
                if allowed == (l.get("charge_cents") or 0):
                    continue        # paid exactly the charge: the fee is below the rate (CHARGE_BELOW_RATE), nothing to appeal
                if self.paid_units_of(l) != 1:
                    continue        # no units on the remittance, several billed: its allowed amount is for all of them
                rate, basis, r = self.usual_rate(l, code)
                # a usual rate needs a clear majority of the payer's full-rate lines, 20 at least (trace finding T-N4), within the
                # claim's plan where the plan pays a schedule of its own (a plan with no rate of its own for the code: no item)
                if not rate or not r or r["share"] < FULL_SHARE or not self._backed(r) or r.get("plan_unknown"):
                    continue
                if self._own_plan_amount(l, code, allowed):
                    continue        # paid exactly a plan's own schedule under a group number not read apart (fix7c-r2-13)
                rank = l.get("surg_rank", 0)
                top_cut = rank >= 2 and is_surgical(code) and code not in ADD_ON_CODES and not self._outranked(l, rate)
                if rank >= 2 and is_surgical(code) and code not in ADD_ON_CODES and not top_cut:
                    if self._same_day_policy_claim(l):
                        continue        # how the 2027 same-day cut and the multiple-procedure cut combine awaits CMS's instructions
                    pct_paid = allowed / rate
                    policy, learned = self.mppr_pct(l)
                    if pct_paid < policy - 0.07:
                        exp = int(round(rate * policy))
                        self._emit("MPPR_EXCESS", "underpayment", "medium", "appeal",
                                   f"{code} paid at {pct_paid:.0%} of the usual rate as procedure #{rank} ({'this payer normally allows' if learned else 'standard policy'} {policy:.0%})",
                                   l, exp, exp - allowed, "59" if "59" in carcs else top, rarc,
                                   {"basis": basis, "rank": rank, "procedures_on_claim": l.get("surg_count"), "pct_of_usual": round(pct_paid, 3),
                                    "carc_59_reported": "59" in carcs, "policy_pct": policy, "policy_learned": learned})
                    continue
                if code in ADD_ON_CODES and "59" in carcs:
                    c59 = sum(a for _g, rc, a in adj if rc == "59")
                    if c59 > 0:
                        self._emit("MPPR_EXCESS", "underpayment", "high", "appeal", f"Multiple-procedure reduction applied to add-on code {code}", l, allowed + c59, c59, "59", rarc, {"basis": basis})
                        continue
                if self._same_day_reduced(l, rate):
                    # paid at 80 percent by Medicare's same-day rule (and by a Medicare Advantage plan that follows it)
                    rate = int(round(rate * MEDICARE_SAME_DAY_EM["pct"]))
                    basis = f"{basis}; Medicare same-day visit and procedure rule from {MEDICARE_SAME_DAY_EM['effective']} -> 80%"
                if 0 < (l.get("charge_cents") or 0) < rate:
                    # the payer allows the lesser of the charge and its rate: never more than the charge is owed
                    rate, basis = l["charge_cents"], f"{basis}, capped at the line's charge"
                if allowed < rate and (rate - allowed) >= 200 and (rate - allowed) / rate >= 0.05:
                    one_off = ("UNDERPAID_VS_OWN_RATE", "underpayment", "medium", "appeal",
                               f"{code} allowed ${allowed/100:.2f} vs usual ${rate/100:.2f} ({l['payer_group']})", l, rate, rate - allowed, top, rarc,
                               {"basis": basis, "usual_share": round(r["share"], 3), "usual_n": r["n"], "rank": rank})
                    if top_cut:
                        # the payer cut this one as a second procedure, but it is the highest-valued procedure on the claim
                        # (the one it paid in full is worth less): the full rate was due (PM case C28)
                        ev = dict(one_off[10], highest_valued=True,
                                  note="Reduced as a second procedure although it is the highest-valued procedure on the claim: "
                                       "the multiple-procedure cut belongs on the lower-valued one")
                        one_off = one_off[:4] + (f"{code} allowed ${allowed/100:.2f} vs usual ${rate/100:.2f} ({l['payer_group']}): "
                                                 "cut as a second procedure although it is the highest-valued one on the claim",) \
                            + one_off[5:10] + (ev,)
                        self.ema_counts["mppr_highest_cut"] += 1
                        self._emit(*one_off)
                        continue
                    if is_surgical(code) and code not in ADD_ON_CODES:
                        # first on its own claim, but perhaps not that day: a reducible procedure paid more on another claim of the
                        # same patient and clinician makes this one the payer's second procedure (settled after the line rules)
                        self.__dict__.setdefault("_mppr_cands", []).append((l, rate, basis, one_off, r))
                        continue
                    self._emit_underpaid(l, allowed, r, one_off)
                elif allowed < rate and not top_cut and l["line_key"] not in self.worked_underpaid:
                    # below what a one-off appeal is worth, but a service the payer priced on its earlier fee schedule is still
                    # owed the rise: it belongs in the payer's one repricing request (fix7c-r1-3: a 1 percent rise left out)
                    old = self._old_schedule_of(l, allowed, r)
                    if old is not None:
                        if not self._gone(l):
                            self.old_schedule[(l["payer_group"], l["year"])].append((l, None, r, old))
                        else:
                            self._note_gone(l, r)
        self._settle_mppr_candidates()
        self._settle_co45_candidates()
        self._settle_unpaid_lines()

    def _answer_copies(self) -> set[str]:
        """The paid lines of an answer the payer delivered again the same day (trace finding T-I3): two remittances (two
        checks, so not one remittance's copies, which the store supersedes) carrying the same claim id's answer, line for
        line the same (status, code, date, modifiers, charge, paid, allowed, units). One underpayment is one item: the
        delivery whose item a person worked keeps it, else the one already listed, else the first remittance (by key); the
        other deliveries' lines are returned (the underpayment rules pass them by). Lines of one remittance are never
        copies of each other: they are units of its answer."""
        groups: dict[tuple, list] = defaultdict(list)
        for l in self.lines:
            if (l.get("paid_cents") or 0) > 0 and l.get("claim_status_code") in ("1", "19"):
                groups[(l["patient_control_number"], l.get("payer_id") or l.get("payer_name"), l.get("payment_date") or l.get("txn_payment_date"),
                        l.get("claim_status_code"), l.get("procedure_code"), l.get("service_date"), l["modifiers"], l.get("charge_cents"),
                        l.get("paid_cents"), l.get("allowed_cents"), l.get("paid_units"))].append(l)
        multi = [g for g in groups.values() if len({x["transaction_key"] for x in g}) > 1]
        if not multi:
            return set()
        keys = sorted({x["line_key"] for g in multi for x in g})
        mark: dict[str, int] = {}           # line key -> 2 when a person worked its item, 1 when it is listed
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            for lk, w in self.conn.execute(f"""SELECT line_key, MAX(CASE WHEN status != 'open' OR notes IS NOT NULL OR appealed_at IS NOT NULL
                                                                     THEN 2 ELSE 1 END)
                                               FROM findings WHERE line_key IN ({','.join('?' * len(part))}) AND COALESCE(stale,0)=0
                                               GROUP BY line_key""", part):
                mark[lk] = w
        out: set[str] = set()
        for g in multi:
            by_tk: dict[str, list] = defaultdict(list)
            for x in g:
                by_tk[x["transaction_key"]].append(x)
            keep = min(by_tk, key=lambda tk: (-max(mark.get(x["line_key"], 0) for x in by_tk[tk]), tk))
            out |= {x["line_key"] for tk, xs in by_tk.items() if tk != keep for x in xs}
        return out

    def _patient_of_claims(self, claim_keys) -> dict[str, str | None]:
        """The patient key (member number, else name) of each 835 claim."""
        keys = sorted({k for k in claim_keys if k})
        who: dict[str, str | None] = {}
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            for ck, pid, last, first in self.conn.execute(f"SELECT claim_key, patient_id, patient_last, patient_first FROM claims "
                                                          f"WHERE claim_key IN ({','.join('?' * len(part))})", part):
                ks = patient_keys(pid, last, first)
                who[ck] = ks[0] or ks[1]
        return who

    def _co45_not_denials(self, cands: list) -> list:
        """The whole-charge CO-45 candidates (line, remarks) that may be denials: those left once the ones the payer paid at
        the claim level, billed again under another code and paid, or has no rate for are counted out
        (_settle_co45_candidates)."""
        at = lambda x: x.get("payment_date") or x.get("txn_payment_date") or ""      # noqa: E731
        who = lambda x: (x.get("payer_group"), x.get("payer_name") if x.get("payer_group") in MANY_PAYER_GROUPS else None)    # noqa: E731
        codes = {l.get("procedure_code") for l, _r in cands}
        bills = {bill_of(l["patient_control_number"]) for l, _r in cands}
        priced: set[tuple] = set()          # (payer, code) the payer allowed or paid at least once: it has a rate for the code
        later: dict[tuple, list] = defaultdict(list)    # (bill, date) -> the payments standing on the bill for that date
        for x in self.lines:
            if x.get("claim_status_code") == "22":
                continue
            if x.get("procedure_code") in codes and ((x.get("allowed_cents") or 0) > 0 or (x.get("paid_cents") or 0) > 0):
                priced.add((who(x), x["procedure_code"]))
            if self.standing.get(x["line_key"], 0) > 0 and x.get("claim_status_code") in ("1", "19") and bill_of(x["patient_control_number"]) in bills:
                later[(bill_of(x["patient_control_number"]), x.get("service_date"))].append(x)
        keys = sorted({l["claim_key"] for l, _r in cands})
        claim_paid: dict[str, int] = {}
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            claim_paid.update(self.conn.execute(f"SELECT claim_key, paid_cents FROM claims WHERE claim_key IN ({','.join('?' * len(part))})",
                                                part).fetchall())
        out = []
        for l, rarc in cands:
            code, ck = l["procedure_code"], l["claim_key"]
            if (claim_paid.get(ck) or 0) > sum(max(x.get("paid_cents") or 0, 0) for x in self.lines_by_claim.get(ck, [])):
                self.ema_counts["co45_claim_level_paid"] += 1       # the claim's money is at its level: the line was paid there
                continue
            own_codes = {x.get("procedure_code") for x in self.lines_by_claim.get(ck, []) if x.get("service_date") == l.get("service_date")}
            flagged = bool(set(self.rem.get(l["line_key"], [])) & CODING_RARCS)
            again = [x for x in later.get((bill_of(l["patient_control_number"]), l.get("service_date")), [])
                     if (at(x) > at(l) or (at(x) == at(l) and x["patient_control_number"] != l["patient_control_number"]))
                     and x.get("payer_group") == l.get("payer_group") and x.get("procedure_code") not in own_codes
                     and not any(o.get("procedure_code") == code and o.get("service_date") == l.get("service_date")
                                 for o in self.lines_by_claim.get(x["claim_key"], []))
                     and (flagged or code_category(x.get("procedure_code")) == code_category(code))]
            if again:
                self.ema_counts["co45_rebilled_paid"] += 1          # billed again under another code, and that code paid
                continue
            if (who(l), code) not in priced:
                self.ema_counts["co45_payer_has_no_rate"] += 1      # the payer never allowed or paid the code: no rate for it
                continue
            out.append((l, rarc))
        return out

    def _settle_co45_candidates(self) -> None:
        """Lines whose whole charge the payer took as CO-45 with nothing allowed: a denial (the answer key's R9), unless the
        same payer allowed or paid the same service (patient, date, code) on another of its answers, a corrected or
        repeated claim it processed (the key's 'recognized' stance: 196 of 222 work-now items of one Medicare Advantage
        plan on real data); or unless the claim came to this payer from another payer that answered it first as primary,
        forwarded it (claim status 19, or a crossover carrier named) and itself allowed nothing for the line, while no
        primary payment of the service stands: the second payer's CO-45 only follows the first payer's denial, which is
        the item to work (a Medigap's answer to Medicare's crossover). None of these is a denial either: the payer paid the
        line's claim at the claim level (its CLP04 more than its lines pay: a secondary paying the coinsurance on the claim);
        the bill was answered since for the date under another code the payer paid, one billed in its place (the same kind
        of service, or a code corrected after the payer's coding remark on this line); or the payer has no rate for the code
        (it never allowed or paid it: a payer in a group of many payers by its own name)."""
        cands = self.__dict__.pop("_co45_cands", None) or []
        if not cands:
            return
        cands = self._co45_not_denials(cands)
        if not cands:
            return
        want = {(l.get("service_date"), l.get("procedure_code")) for l, _r in cands}
        answered: dict[tuple, list] = defaultdict(list)
        for x in self.lines:
            k = (x.get("service_date"), x.get("procedure_code"))
            if k in want and ((x.get("allowed_cents") or 0) > 0 or (x.get("paid_cents") or 0) > 0):
                answered[k].append(x)
        who = self._patient_of_claims({x["claim_key"] for v in answered.values() for x in v} | {l["claim_key"] for l, _r in cands})
        # the first payer's answers that allowed nothing for the line, on claims it forwarded: (bill, date, code) -> lines. A
        # payer may write the modifier into the code ('99213 57'): the code is its first word
        base = lambda code: (code or "").split(" ")[0]          # noqa: E731
        svc_of = lambda x: (bill_of(x.get("patient_control_number")), x.get("service_date"), base(x.get("procedure_code")))     # noqa: E731
        wanted = {svc_of(l) for l, _r in cands}
        forwarded: dict[tuple, list] = defaultdict(list)
        for x in self.lines:
            k = svc_of(x)
            if k in wanted and not (x.get("paid_cents") or 0) and not (x.get("allowed_cents") or 0) and (
                    x.get("claim_status_code") == "19" or (x.get("claim_status_code") == "1" and self.crossover_carrier(x["claim_key"]))):
                forwarded[k].append(x)
        at = lambda x: x.get("payment_date") or x.get("txn_payment_date") or ""      # noqa: E731
        payer = lambda x: x.get("payer_id") or x.get("payer_name")                   # noqa: E731

        def first_denial(l: dict) -> dict | None:
            """The first payer's own answer of the line that this payer's CO-45 follows, if any."""
            k = svc_of(l)
            if any(e[3] in ("1", "19") for code in {l["procedure_code"], k[2]} for e in self.paid_events.get((k[0], code, k[1]), [])):
                return None             # a primary payment of the service stands: the second payer may owe its share (rule 2)
            return next((x for x in forwarded.get(k, []) if x["claim_key"] != l["claim_key"] and payer(x) != payer(l)
                         and x.get("payer_group") != l.get("payer_group") and at(x) <= at(l)), None)
        follows = {l["line_key"]: x for l, _r in cands for x in [first_denial(l)] if x is not None}
        if follows:
            # a person's work is never dropped: an item someone worked stays listed, the first payer's denial named on it
            from .archive_rules import worked_ids
            kept = worked_ids(self.conn, [_fid("DENIED_OTHER", lk) for lk in follows])
        for l, rarc in cands:
            me = who.get(l["claim_key"])
            if me and any(x["line_key"] != l["line_key"] and who.get(x["claim_key"]) == me and x.get("payer_group") == l.get("payer_group")
                          for x in answered.get((l.get("service_date"), l.get("procedure_code")), [])):
                self.ema_counts["co45_answered_elsewhere"] += 1
                continue
            first = follows.get(l["line_key"])
            if first is not None and _fid("DENIED_OTHER", l["line_key"]) not in kept:
                self.ema_counts["co45_follows_first_payers_denial"] += 1
                continue
            code = l["procedure_code"]
            exp, basis = self.expected_allowed(l, code)
            if exp is not None:
                # what the line's denial is worth: the rate per unit times the units billed, never above the charge
                # (line_expected), as every other denial is sized; one unit's rate for 13 units was a twelfth of it
                exp, basis = self.line_expected(l, code)
            self._emit("DENIED_OTHER", "denial", "low", "review",
                       f"{code} paid $0: the whole charge was taken as a contractual reduction (CO-45), nothing allowed",
                       l, exp, exp, "45", rarc, {"basis": basis, "whole_charge_co45": True},
                       resolved_by=self.later_payment(l, expected=exp))
            f = self.findings.get(_fid("DENIED_OTHER", l["line_key"]))
            if first is not None and f is not None and not f.get("auto_status"):
                f["hint"] = (f"{first.get('payer_group') or 'The first payer'} answered this service first and paid nothing on it, then sent "
                             f"the claim on to this payer: this CO-45 follows that answer, which is the one to work. Kept listed because a "
                             "person worked it.")

    def _outranked(self, l: dict, rate: int) -> bool:
        """Whether a reducible procedure the payer cut as a second one (ranked below another on its claim by what was
        allowed) rightly was the second one. The payer's own ranking decides: it was the payer's error only when the same
        payer, on its other claims that billed this code with each code it paid ahead of it here, ranked this code first on
        at least 80% of at least 5 of them (Anthem paid 17313 in full beside the repair on 39 of 40 other dates: PM case
        C28). The same code twice, or too few claims to tell, counts as rightly second (no item on a guess)."""
        order = self._pair_order()
        above = [o for o in self.lines_by_claim.get(l["claim_key"]) or []
                 if o["line_key"] != l["line_key"] and is_surgical(o.get("procedure_code")) and (o.get("allowed_cents") or 0) > 0
                 and 0 < (o.get("surg_rank") or 0) < (l.get("surg_rank") or 0)]
        if not above:
            return True
        for o in above:
            if o.get("procedure_code") == l.get("procedure_code"):
                return True
            n, first = order.get((l.get("payer_group"), l.get("procedure_code"), o.get("procedure_code")), (0, 0))
            if n < PAIR_ORDER_MIN or first < PAIR_ORDER_SHARE * n:
                return True
        return False

    def _pair_order(self) -> dict[tuple, tuple[int, int]]:
        """{(payer group, code A, code B): (claims that billed both, claims on which the payer allowed A more than B)}, over
        the claims with both reducible procedures allowed (its ranking of the two)."""
        got = self.__dict__.get("_pair_order_cache")
        if got is not None:
            return got
        cnt: dict[tuple, list] = defaultdict(lambda: [0, 0])
        for claim_lines in self.lines_by_claim.values():
            surg = [x for x in claim_lines if is_surgical(x.get("procedure_code")) and (x.get("allowed_cents") or 0) > 0]
            if len(surg) < 2:
                continue
            best: dict[str, int] = {}
            for x in surg:
                best[x["procedure_code"]] = max(best.get(x["procedure_code"], 0), x.get("allowed_cents") or 0)
            grp = surg[0].get("payer_group")
            for a, va in best.items():
                for b, vb in best.items():
                    if a != b:
                        k = cnt[(grp, a, b)]
                        k[0] += 1
                        k[1] += 1 if va > vb else 0
        self._pair_order_cache = got = {k: (v[0], v[1]) for k, v in cnt.items()}
        return got

    def _emit_underpaid(self, l, allowed: int, r: dict, one_off: tuple, old_schedule: bool = True) -> None:
        """A one-off underpayment, or a line of a payer's routine lower amount (grouped by emit_second_rates), or a line of a
        clinician's own discount no rule explains (one review per payer and clinician, emit_provider_gaps), or a service
        the payer priced on its previous year's schedule (one repricing request per payer and year, emit_old_schedules,
        which hands back the services dated before the new rate took effect: `old_schedule` False)."""
        worked = l["line_key"] in self.worked_underpaid
        if not worked and old_schedule:
            old = self._old_schedule_of(l, allowed, r)
            if old is not None:
                if not self._gone(l):
                    self.old_schedule[(l["payer_group"], l["year"])].append((l, one_off, r, old))
                    return
                # priced on the old schedule, then taken back by the payer (fix7c-r2-1): no claim of the repricing request.
                # Repriced since (paid again): a one-off the archive rules close as recovered, its recovery counted once, on
                # its own line, as before the request existed; the request names it. Not paid again: the usual rules.
                if self._note_gone(l, r):
                    self._emit(*one_off)
                    return
        disc = r.get("discount")
        if not worked and disc and abs(allowed - disc["mode"]) <= 1:
            self.provider_gaps[(l["payer_group"], disc["npi"])].append((l, one_off, r))
            return
        key = r.get("key")
        # grouped only against the line's own year: lines measured by the year before's rate stay one-offs of their year
        if not worked and key and key[1] == l["year"] and self.yard_obs.get(r.get("payer_key"), {}).get(allowed, 0) >= SECOND_RATE_MIN_LINES:
            self.yard_by_key[key] = r
            self.second_rates[(key, allowed)].append((l, one_off))       # one routine-rate item, with its reason if one is found
            return
        if not worked and r.get("np_unknown") and r.get("mode") and in_band(allowed / r["mode"], NP_BAND):
            # a PA or NP paid the PA/NP 85 percent of the payer's rate, where neither the clinician's own payments nor the
            # payer's other lines say what it pays PAs and NPs: what many plans pay them, no shortfall to appeal on its own
            # (five lines or more at one amount are one routine-rate review, with that reason, above)
            self.ema_counts["pa_np_85_rate_unknown"] += 1
            return
        self._emit(*one_off)

    def _settle_mppr_candidates(self) -> None:
        """Reducible procedures first on their own claim and paid below the payer's usual rate, ranked with the patient's
        other reducible procedures of that day and clinician that the same payer answered on other claims (a surgery
        center's claim, a split one). The payer's own rate of each procedure in its own setting ranks the day (fix7c: a
        repair at the surgery center is worth its facility rate, not what it was allowed; Medicare ranks the same way), unit
        by unit (a line of three lesions is three procedures), in one fixed order (worth, then what was allowed, then the
        line), so two procedures of equal worth are never both the first (_rank_day):
        - another procedure that day is the higher-valued: this one is the payer's second procedure. At the payer's own
          multiple-procedure share of its rate (within $1) it is that reduction, not an underpayment (Mohs 17311 at exactly
          half: 101 items on real data); deeper than that share it is MPPR_EXCESS; otherwise it stays an underpayment.
        - this one is the highest-valued and was paid at that share (the payer ranked the day by processing order): it was
          owed in full, less what a correct ranking takes back from the others the payer paid above their share (the
          cheaper repair it paid in full). Listed with that net amount and the day's payments as its evidence.
        A procedure of the day with no rate of the payer's own is worth at least what it was allowed. The day also holds the
        reducible procedures the 837s show billed for the patient by the clinician that day on another bill whose answer is
        not on file (_billed_same_day): each is worth the payer's rate in the 837's place of service, and, its payment being
        unknown, is counted as paid in full (what a correct ranking takes back from it is then the most it can be, so the
        amount listed is the least owed). Without them a Mohs cut beside a surgery center's claim the feed lacks would be
        listed whole, though the 837 shows a larger repair made it the second procedure (fix7c, measured on the world).
        The payer's answers since (fix7c-r2-7): this service paid again since (its own later payment) is never another
        procedure of its day, and the item it is owed is closed by that payment (later_payment). A day the payer
        reprocessed since (a procedure of it taken back and paid again) that it owed a missing half when this service was
        answered and owes nothing now is listed closed, with the new money as its recovery."""
        cands = self.__dict__.pop("_mppr_cands", None) or []
        if not cands:
            return
        cand_keys = {l["line_key"] for l, *_x in cands}
        billed = self._billed_same_day(cands)
        want = {(l.get("service_date"), l.get("rendering_provider_npi")) for l, *_x in cands}
        same_day: dict[tuple, list] = defaultdict(list)
        for x in self.lines:
            k = (x.get("service_date"), x.get("rendering_provider_npi"))
            if k in want and (x.get("allowed_cents") or 0) > 0 and is_surgical(x.get("procedure_code")) \
                    and x.get("claim_status_code") in ("1", "2", "3", "19", "20", "21"):
                same_day[k].append(x)       # a payment taken back since too: the day as the payer first answered it
        keys = sorted({x["claim_key"] for v in same_day.values() for x in v} | {l["claim_key"] for l, *_x in cands}
                      | {a["claim_key"] for v in billed.values() for b in v for a in b[5] if a.get("claim_key")})
        who: dict[str, str | None] = {}
        ident: dict[str, tuple] = {}
        norm = lambda v: "".join(ch for ch in (v or "").upper() if ch.isalnum())     # noqa: E731
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            for ck, pid, last, first in self.conn.execute(f"SELECT claim_key, patient_id, patient_last, patient_first FROM claims "
                                                          f"WHERE claim_key IN ({','.join('?' * len(part))})", part):
                ks = patient_keys(pid, last, first)
                who[ck] = ks[0] or ks[1]
                ident[ck] = (norm(pid), norm(last), norm(first))
        for l, rate, basis, one_off, r in cands:
            me = who.get(l["claim_key"])
            allowed = l.get("allowed_cents") or 0
            at = l.get("payment_date") or l.get("txn_payment_date") or ""
            mine = (bill_of(l.get("patient_control_number")), l.get("procedure_code"), l.get("service_date"))
            others, own_later = [], []
            for x in same_day.get((l.get("service_date"), l.get("rendering_provider_npi")), []):
                if x["line_key"] == l["line_key"] or x["claim_key"] == l["claim_key"] or not me or who.get(x["claim_key"]) != me \
                        or x.get("payer_group") != l.get("payer_group"):
                    continue
                if (bill_of(x.get("patient_control_number")), x.get("procedure_code"), x.get("service_date")) == mine:
                    if (x.get("payment_date") or x.get("txn_payment_date") or "") > at:
                        if not self._gone(x):
                            own_later.append(x)     # this very service paid again since: its own answer, not another procedure
                        continue
                    if self._gone(x):
                        continue                    # its own earlier answer, taken back
                others.append(x)
            extra = self._billed_lines(l, billed.get(l["line_key"]) or [], others, ident.get(l["claim_key"]), ident)
            v = self._rank_day(l, rate, allowed, basis, one_off, [x for x in others if not self._gone(x)] + extra, cand_keys)
            if v[0] not in ("highest", "every_cut"):
                # the payer reprocessed the day since this service was answered (a procedure of it taken back and paid again):
                # what the day owed then and owes no more now came in
                then = [x for x in others if (x.get("payment_date") or x.get("txn_payment_date") or "") <= at]
                gone = [x for x in then if self._gone(x)]
                if gone:
                    v0 = self._rank_day(l, rate, allowed, basis, one_off, then + extra, cand_keys)
                    if v0[0] in ("highest", "every_cut") and self._close_fixed_day(l, rate, one_off, v0, gone):
                        continue
            if v[0] in ("highest", "every_cut"):
                lp = self.later_payment(l, l["procedure_code"], expected=rate, min_paid=(l.get("paid_cents") or 0) + 100) \
                    if own_later else None
                self._emit_day(l, rate, one_off, v, resolved_by=lp)
            elif v[0] == "excess":
                _k, rank, pct, learned, exp = v
                code = l["procedure_code"]
                self._emit("MPPR_EXCESS", "underpayment", "medium", "appeal",
                           f"{code} paid at {allowed / rate:.0%} of the usual rate as procedure #{rank} that day "
                           f"({'this payer normally allows' if learned else 'standard policy'} {pct:.0%})",
                           l, exp, exp - allowed, one_off[8], one_off[9],
                           {"basis": basis, "rank": rank, "pct_of_usual": round(allowed / rate, 3), "policy_pct": pct,
                            "policy_learned": learned, "ranked_with": "another claim of the same patient and clinician that day"})
            elif v[0] == "usual":
                self._emit_underpaid(l, allowed, r, one_off)
            elif v[0] == "nets_out":
                self.ema_counts["mppr_day_nets_out"] += 1
            else:
                self.ema_counts["mppr_other_claim"] += 1     # the payer's multiple-procedure share, as for any second procedure

    def _day_units(self, x) -> int:
        """How many procedures a line of a split day is (a line of three lesions is three): its units paid (the 837's units
        for a line whose answer is not on file), a whole count from 1 to MAX_UNITS, else 1."""
        u = x.get("units") if not x.get("claim_key") else self.paid_units_of(x)
        try:
            u = float(u or 1)
        except (TypeError, ValueError):
            return 1
        return int(u) if 1 <= u <= MAX_UNITS and u == int(u) else 1

    def _rank_day(self, l, rate: int, allowed: int, basis: str, one_off: tuple, day: list, cand_keys: set) -> tuple:
        """The verdict on a candidate (_settle_mppr_candidates) from its day's other procedures: ('usual',) (no other
        procedure, or not paid at the payer's multiple-procedure share: the usual rules), ('second',) (rightly a later
        procedure), ('excess', rank, share, learned, expected), ('nets_out',), ('highest', title, net, evidence) or
        ('every_cut', title, least, evidence). Units count one by one, each at the share of its rank."""
        if not day:
            return ("usual",)
        grp = l.get("payer_group")
        worth = {x["line_key"]: self._procedure_worth(x) for x in day}
        units = {x["line_key"]: self._day_units(x) for x in day}

        def order(x) -> tuple:
            """The day's fixed order: the higher worth (per unit), then the more allowed per unit, then the line."""
            u = units[x["line_key"]]
            return (-worth[x["line_key"]][0], -int(round((x.get("allowed_cents") or 0) / u)), x["line_key"])
        me = (-rate, -allowed, l["line_key"])
        above = [x for x in day if order(x) < me]
        if above:
            n_above = sum(units[x["line_key"]] for x in above)
            rank = 2 if n_above == 1 else 3
            pct, learned = self.mppr_pct({"payer_group": grp, "surg_rank": rank})
            if abs(allowed - rate * pct) <= 100:
                return self._every_procedure_cut(l, rate, allowed, one_off, day, worth, units, order, cand_keys) or ("second",)
            if allowed / rate < pct - 0.07:
                return ("excess", rank, pct, learned, int(round(rate * pct)))
            return ("usual",)
        pct2, _learned = self.mppr_pct({"payer_group": grp, "surg_rank": 2})
        if abs(allowed - rate * pct2) > 100:
            return ("usual",)       # not the multiple-procedure share: the usual rules
        # the highest-valued procedure of the day, paid as a second one: owed in full, less what a correct ranking takes back
        # from the others (each unit at the payer's share for its rank, never more than the line was paid)
        back, rows, i = 0, [], 0
        for x in sorted(day, key=order):
            w, known = worth[x["line_key"]]
            u = units[x["line_key"]]
            due, shares = 0, []
            for _k in range(u):
                pct, _l = self.mppr_pct({"payer_group": grp, "surg_rank": 2 if i == 0 else 3})
                due += int(w * pct + 0.5)
                shares.append(pct)
                i += 1
            unknown = x.get("allowed_cents") is None       # billed (837), its answer not on file: counted as paid in full
            got = w * u if unknown else (x.get("allowed_cents") or 0)
            back += max(0, got - due)
            rows.append({"code": x.get("procedure_code"), "claim": x.get("patient_control_number"), "setting": x.get("pos_class") or "office",
                         "allowed_cents": None if unknown else got, "rate_cents": w if known else None, "share": shares[0], "due_cents": due,
                         **({"units": u} if u > 1 else {}), **({"answer": "not on file"} if unknown else {})})
        net = (rate - allowed) - back
        if net < 200 or net / rate < 0.05:
            return ("nets_out",)
        code = l["procedure_code"]
        grp = grp or "The payer"
        others = ", ".join(f"{d['code']}{' x' + str(d['units']) if d.get('units') else ''} (its answer is not on file: counted as paid in full, "
                           f"{_usd(d['rate_cents'] or d['due_cents'])})"
                           if d["allowed_cents"] is None else
                           f"{d['code']}{' x' + str(d['units']) if d.get('units') else ''} "
                           f"({'paid in full' if d['allowed_cents'] > d['due_cents'] else 'also cut'}, allowed {_usd(d['allowed_cents'])})"
                           for d in rows)
        unseen = any(d["allowed_cents"] is None for d in rows)
        note = (f"{grp} paid {code} at its share for a second procedure ({_usd(allowed)} of {_usd(rate)}), although it was the "
                f"highest-valued procedure of the day: the patient's other procedures that day were billed on another claim "
                f"({others}), each worth less at {grp}'s own rate for its setting. The {code} is owed in full: {_usd(rate - allowed)} more"
                + (f", less {_usd(back)} a correct ranking takes back from the others it paid above their share: {_usd(net)} for the day"
                   if back else "")
                + (". That is the least owed: if the payer cut the other claim too, the whole difference is owed" if unseen else ""))
        ev = dict(one_off[10], highest_valued=True, same_day=rows, net_cents=net, back_cents=back, note=note,
                  ranked_with="the patient's other claims of the day, by the payer's own rate in each setting")
        title = (f"{code} allowed {_usd(allowed)} vs usual {_usd(rate)} ({grp}): cut as a second procedure although it is the "
                 f"highest-valued one that day" + (f"; {_usd(net)} net with the other claim" if back else ""))
        return ("highest", title, net, ev)

    def _every_procedure_cut(self, l, rate: int, allowed: int, one_off: tuple, day: list, worth: dict, units: dict, order,
                             cand_keys: set) -> tuple | None:
        """A procedure rightly second on a split day (another procedure that day is worth more), paid at the payer's share,
        on a day on which the payer paid no procedure as the first: the higher-valued one, on the other claim, was cut to
        the same share (Humana MA cut a Mohs and the surgery center's repair alike: the world showed 7 such days, about
        $2,000). One of the day's procedures is owed its missing half. The higher-valued one is the one by the payer's own
        rates, but its rate in its own setting may rest on a few lines (too few to make it an item of its own), so the item
        is listed on a candidate line, at the least of the two missing halves (the candidate's rests on a firm rate): once a
        day (fix7c-r2-6), on the highest-valued candidate under the top procedure; the day's other candidates are covered by
        it (('covered',)). ('every_cut', title, least, evidence), or None where any procedure of the day has no answer on
        file (the 837's), has no rate of the payer's own, or was paid above its share, or where the higher-valued one is an
        item of its own (it carries the day)."""
        grp = l.get("payer_group")
        pct2, _learned = self.mppr_pct({"payer_group": grp, "surg_rank": 2})
        rows, top = [], None
        for x in sorted(day, key=order):
            if x.get("procedure_code") in ADD_ON_CODES:
                continue
            w, known = worth[x["line_key"]]
            got, u = x.get("allowed_cents"), units[x["line_key"]]
            if got is None or not known or got / u > w * pct2 + 100:
                return None
            if top is None:
                top = x
            rows.append({"code": x.get("procedure_code"), "claim": x.get("patient_control_number"), "setting": x.get("pos_class") or "office",
                         "allowed_cents": got, "rate_cents": w, "share": pct2, **({"units": u} if u > 1 else {})})
        if top is None or top["line_key"] in cand_keys or worth[top["line_key"]][0] <= rate:
            return None
        me = (-rate, -allowed, l["line_key"])
        if any(x["line_key"] in cand_keys and x is not top and order(x) < me for x in day):
            return ("covered",)     # a higher-valued candidate of the day carries its one missing half
        top_w = worth[top["line_key"]][0]
        top_got = int(round((top.get("allowed_cents") or 0) / units[top["line_key"]]))
        least = min(rate - allowed, top_w - top_got)
        if least < 200 or least / rate < 0.05:
            return None
        code, tcode = l["procedure_code"], top.get("procedure_code")
        grp = grp or "The payer"
        mine = rate - allowed <= top_w - top_got
        # the year of the rate the other procedure is measured by: a rate the payer paid in another year (none on file for the
        # procedure's own) is named as such, never passed off as this year's (2026 cut facility rates)
        _r, _b, trec = self._yardstick(top, tcode or "", top.get("pos_class") or "office", component_of(top.get("modifiers_list")))
        ty, own_y = ((trec or {}).get("key") or (None, None))[1], str(top.get("year") or "")
        other_year = bool(ty and own_y and ty != own_y)
        whose = f"{grp}'s {ty} rate for its setting, the latest on file" if other_year else f"{grp}'s own rate for its setting"
        note = (f"{grp} paid every procedure of the day at its share for a second procedure: {code} here ({_usd(allowed)} of "
                f"{_usd(rate)}) and {tcode} on the patient's other claim that day ({_usd(top_got)} of {_usd(top_w)}, {whose}). "
                f"No procedure that day was paid as the first. The {tcode} is the higher-valued: it was owed in full, "
                f"{'about ' if other_year else ''}{_usd(top_w - top_got)} more. "
                f"{_usd(least)} is the least owed for the day"
                + (f" (the {code}'s own missing half, at its firm rate)" if mine else "")
                + ". Ask the payer to reprocess the day's claims together")
        ev = dict(one_off[10], every_cut=True, same_day=rows, net_cents=least, note=note,
                  ranked_with="the patient's other claims of the day, by the payer's own rate in each setting")
        title = (f"{code} allowed {_usd(allowed)} vs usual {_usd(rate)} ({grp}): every procedure of the day was paid as a second one; "
                 f"{_usd(least)} owed at the least")
        return ("every_cut", title, least, ev)

    def _emit_day(self, l, rate: int, one_off: tuple, v: tuple, resolved_by=None) -> None:
        """The item of a split day's verdict ('highest' or 'every_cut', _rank_day), on the candidate's line."""
        kind, title, amount, ev = v
        self.ema_counts["mppr_highest_cut_other_claim" if kind == "highest" else "mppr_every_procedure_cut"] += 1
        self._emit("UNDERPAID_VS_OWN_RATE", "underpayment", "medium", "appeal", title, l, rate, amount, one_off[8], one_off[9], ev,
                   resolved_by=resolved_by)

    def _close_fixed_day(self, l, rate: int, one_off: tuple, v: tuple, gone: list) -> bool:
        """A split day that owed a missing half when the candidate was answered (verdict `v`, the day as answered) and owes
        nothing now: the payer took back procedures of it (`gone`) and paid them again since. Listed closed, its recovery
        the new money on those procedures (their later standing payments less what each had been paid), at most what the
        item asked for, each payment counted once. False (nothing listed) where no new money came in."""
        rec, when, paid = 0, None, []
        for x in gone:
            xa = x.get("payment_date") or x.get("txn_payment_date") or ""
            later = [e for e in self.paid_cover(bill_of(x.get("patient_control_number")), x.get("procedure_code"), x.get("service_date"))
                     if e[0] and e[0] > xa and (e[0], e[1], e[4], x.get("procedure_code"), x.get("service_date")) not in self.used_payments]
            got = sum(e[2] or 0 for e in later) - (x.get("paid_cents") or 0)
            if got > 0:
                rec += got
                for e in later:
                    self.used_payments.add((e[0], e[1], e[4], x.get("procedure_code"), x.get("service_date")))
                    paid.append({"date": e[0], "amount_cents": e[2], "kind": "payment", "claim": e[4], "trace": e[1],
                                 "code": x.get("procedure_code")})
                    when = max(when or "", e[0])
        if rec <= 0:
            return False
        self._emit_day(l, rate, one_off, v)
        f = self.findings.get(_fid("UNDERPAID_VS_OWN_RATE", l["line_key"]))
        if f is None:
            return True
        ev = json.loads(f["evidence"]) if f.get("evidence") else {}
        got = min(rec, f.get("shortfall_cents") or rec)
        note = (f"{l.get('payer_group') or 'The payer'} reprocessed the day since: it took back and paid again "
                + ", ".join(sorted({p["code"] for p in paid})) + f" ({_usd(rec)} more), so the day's missing half came in")
        last = max(paid, key=lambda p: (p["date"] or "", p["claim"] or ""))
        ev["paid_later"] = {"how": "the payer reprocessed the day", "proof": paid, "paid_cents": rec, "payment_date": when,
                            "claim": last["claim"], "trace": last["trace"]}
        f["evidence"] = json.dumps(ev, default=str)
        f["auto_status"], f["auto_notes"] = "resolved", note
        f["recovered_cents"], f["recovered_date"] = got, when
        self.ema_counts["mppr_day_reprocessed"] += 1
        return True

    def _billed_same_day(self, cands: list) -> dict[str, list[tuple]]:
        """The reducible procedures the 837s show billed by each candidate's clinician, on its date of service, under its
        patient's own member number (the 835 claim's NM1*QC as the 837's subscriber id; never the NM1*IL subscriber's alone,
        which a family shares: fix7c-r2-8), on another bill: {candidate line key: [(claim id, code, modifiers, place of
        service, units, the 835 lines of that bill, code and date)]}, one per bill and code (an 837 sent again repeats its
        lines). Their answers are read where the 835s hold them (_billed_lines): a procedure the patient key the day is
        matched by misses (a name spelled another way under the same member number), a payment taken back since, a
        denial. Empty where the 837 tables are absent."""
        c = self.conn
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "claims_837" not in tables or "lines_837" not in tables:
            return {}
        keys = sorted({l["claim_key"] for l, *_x in cands})
        ids: dict[str, set] = {}
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            for ck, pid in c.execute(f"SELECT claim_key, patient_id FROM claims WHERE claim_key IN ({','.join('?' * len(part))})", part):
                ids[ck] = {pid.strip()} if pid and pid.strip() else set()
        want: dict[tuple, list] = defaultdict(list)
        for l, *_x in cands:
            if l.get("service_date") and l.get("rendering_provider_npi"):
                for m in ids.get(l["claim_key"], ()):
                    want[(m, l["service_date"], l["rendering_provider_npi"])].append(l)
        if not want:
            return {}
        found: dict[str, dict] = defaultdict(dict)
        for pcn, sub, pos, cnpi, code, mods, lnpi, ldate, cdate, units in c.execute(
                """SELECT c.patient_control_number, TRIM(c.subscriber_id), TRIM(c.place_of_service), c.rendering_npi, l.procedure_code,
                          l.modifiers, l.rendering_npi, l.service_date, c.service_date, l.units
                   FROM lines_837 l JOIN claims_837 c ON c.claim_837_key = l.claim_837_key
                   WHERE c.subscriber_id IS NOT NULL AND l.procedure_code IS NOT NULL"""):
            for l in want.get((sub, ldate or cdate, lnpi or cnpi), ()):
                b = bill_of(pcn)
                if is_surgical(code) and code not in ADD_ON_CODES and b != bill_of(l.get("patient_control_number")):
                    found[l["line_key"]].setdefault((b, code), (pcn, code, mods, pos, units))
        if not found:
            return {}
        # the 835 lines of those bills (any payer), by bill, code and date of service
        answers: dict[tuple, list] = defaultdict(list)
        for b in sorted({b for v in found.values() for b, _code in v}):
            for ck, pcn in c.execute("SELECT claim_key, patient_control_number FROM claims WHERE patient_control_number >= ? "
                                     "AND patient_control_number < ?", (b, b + "~")):
                if bill_of(pcn) == b:
                    for x in self.lines_by_claim.get(ck, ()):
                        answers[(b, x.get("procedure_code"), x.get("service_date"))].append(x)
        out = {}
        for lk, v in found.items():
            day = next(l.get("service_date") for l, *_x in cands if l["line_key"] == lk)
            out[lk] = [x + (answers.get((b, code, day)) or [],) for (b, code), x in sorted(v.items())]
        self.ema_counts["mppr_billed_837"] = sum(len(v) for v in out.values())
        return out

    @staticmethod
    def _same_person(a: tuple | None, b: tuple | None) -> bool:
        """Two claims' patients ((member number, last name, first name), letters and digits only) are one person: the same
        member number and first name (a last name spelled another way), or the same full name. A family member under the
        same subscriber's number has a first name of their own."""
        if not a or not b:
            return False
        if a[1] and a[2] and (a[1], a[2]) == (b[1], b[2]):
            return True
        return len(a[0]) >= 6 and a[0] == b[0] and bool(a[2]) and a[2] == b[2]

    def _billed_lines(self, l, billed: list[tuple], day: list, me: tuple | None = None, ident: dict | None = None) -> list[dict]:
        """The procedures _billed_same_day found for line `l` that the day does not already hold (by bill and code), as lines
        the ranking can price: the claim's payer, plan, clinician and date with the 837's code, modifiers, units and place of
        service. What it was allowed: none when no 835 on file answers it ('not on file': the ranking counts it as paid in
        full), else what the same payer's standing answer allows. An answer for another person (a family member under the
        same number, _same_person: fix7c-r2-8) shows the 837 was theirs: no part of the day. A procedure the payer denied,
        or whose payment it took back since, is no part of the day, as for the 835 lines (_settle_mppr_candidates); one
        another payer answered is that payer's business: both left out."""
        have = {(bill_of(x.get("patient_control_number")), x.get("procedure_code")) for x in day}
        out = []
        for i, (pcn, code, mods, pos, units, answers) in enumerate(billed):
            if (bill_of(pcn), code) in have:
                continue
            if answers and ident is not None:
                answers = [x for x in answers if self._same_person(me, ident.get(x.get("claim_key")))]
                if not answers:
                    continue            # answered for another person under the same number: not this patient's procedure
            mine = [x for x in answers if x.get("payer_group") == l.get("payer_group")]
            if answers and not mine:
                continue
            allowed = None
            if mine:
                allowed = max(((x.get("allowed_cents") or 0) for x in mine
                               if x.get("claim_status_code") in ("1", "2", "3", "19", "20", "21") and (x.get("allowed_cents") or 0) > 0
                               and not ((x.get("paid_cents") or 0) > 0 and x["line_key"] not in self.standing)), default=0)
                if not allowed:
                    continue
            ml = [m.strip() for m in (mods or "").split(",") if m.strip()]
            out.append({"line_key": f"837:{pcn}:{code}:{i}", "claim_key": None, "patient_control_number": pcn, "procedure_code": code,
                        "modifiers_list": ml, "pos": pos or None, "pos_class": pos_class(pos), "allowed_cents": allowed, "units": units,
                        "payer_group": l.get("payer_group"), "payer_kind": l.get("payer_kind"), "year": l.get("year"),
                        "plan": l.get("plan"), "group_number": l.get("group_number"), "provider_tier": l.get("provider_tier"),
                        "rendering_provider_npi": l.get("rendering_provider_npi"), "service_date": l.get("service_date")})
        return out

    def _procedure_worth(self, x) -> tuple[int, bool]:
        """What a procedure is worth when the day's procedures are ranked, per unit: the payer's own rate for it in its own
        setting (and its plan), firm or not (the multiple-procedure halves confirm a full rate a few lines show), as
        usual_rate reads it (a line of no named setting paid exactly the payer's facility rate is a facility claim:
        fix7c-r2-9), its full amount where its usual payment is the half of an amount the payer also paid (fix7c-r1-1: a
        surgery center's repair is nearly always the cut one of a split day), else its usual-rate fallback; never less than
        what it was allowed per unit. (cents, whether it is the payer's own rate)."""
        code = x.get("procedure_code") or ""
        got = int(round((x.get("allowed_cents") or 0) / self._day_units(x)))
        rate, _b, rr = self.usual_rate(x, code)
        if rr is not None and rate:
            rec = self.yard.get(rr.get("payer_key"))
            cnt = self.yard_obs.get(rr.get("payer_key"))
            if rec and cnt and is_surgical(code):
                full = [b for b in cnt if b > rec["mode"] and in_band(rec["mode"] / b, HALF_BAND)]
                if full:
                    rate = int(round(rate * max(full) / rec["mode"]))
            return max(rate, got), True
        return max(rate or 0, got), False

    # ------------------------------------------------------------------ claim rules
    def run_claim_rules(self) -> None:
        """Whole claims denied (CLP02 = 4) and still unpaid. A denied claim counts only for the units of its lines that the
        bill has not been paid for, at any date, by payments still standing (primary payments, or secondary ones covering
        half the expected amount; a secondary's share of units the primary paid adds none), and is sized by those units. An
        undated line counts as paid when a primary payment of its code on the bill came after the denial. A claim with no
        service lines counts unless a primary payment on the bill came after it, or it is a duplicate's denial and the bill
        stands paid. One payer's repeated denials of a bill are one unpaid service: one submission carries the finding and
        lists the others, which add only lines it does not have. A submission someone worked always keeps its own finding;
        the carrier is otherwise the submission that already has a finding, else the newest; a submission denied after the
        last change someone made on a worked carrier is listed on its own, so a repeat denial is seen. A finding someone
        worked whose lines the bill has since paid is closed by that payment, so the recovery is counted. A code and date
        the denying payer has since answered itself, as secondary after saying another payer covers the claim first or
        onto the patient's share (_settled_by_answer), is settled by that answer: a finding whose every line is settled
        or paid so is closed with that answer as its proof, worked or not; a claim never listed makes no finding."""
        from . import runtime
        c = self.conn
        c.row_factory = sqlite3.Row
        by_bill: dict[tuple, list[dict]] = defaultdict(list)
        for r in c.execute("""SELECT cl.*, t.payment_date AS txn_payment_date, COALESCE(t.payer_id, t.payer_name) AS txn_payer
                              FROM claims cl JOIN transactions t ON t.transaction_key=cl.transaction_key
                              WHERE t.superseded=0 AND cl.claim_status_code='4'"""):
            cl = dict(r)
            if cl["claim_key"] in self.void_claims:
                continue            # the payer denied a claim the practice voided: nothing is owed (T-N5)
            by_bill[(bill_of(cl["patient_control_number"]), cl.get("txn_payer") or cl.get("payer_id") or cl.get("payer_name"))].append(cl)
        bills = {b for b, _p in by_bill}
        bill_lines: dict[str, list] = defaultdict(list)                 # lines of processed (not wholly denied) claims, per bill
        answers: dict[str, list] = defaultdict(list)                    # every line of the bill, any claim status (_settled_by_answer)
        for l in self.lines:
            b = bill_of(l["patient_control_number"])
            if b in bills:
                answers[b].append(l)
                if l.get("claim_status_code") in ("1", "19", "2", "3"):
                    bill_lines[b].append(l)

        at = lambda o: o.get("payment_date") or o.get("txn_payment_date") or ""          # noqa: E731
        who = lambda o: o.get("payer_id") or o.get("payer_name")                          # noqa: E731
        listed_lines = {f["line_key"] for f in self.findings.values() if f.get("line_key")} - self.closed_lines
        billed_as: dict[tuple, str] = {}        # (bill, date, billed code) -> claim id of a finding saying that code was billed (a downcode)
        for f in self.findings.values():
            if f.get("line_key") and f.get("billed_code") and f["billed_code"] != f.get("procedure_code"):
                billed_as.setdefault((bill_of(f["patient_control_number"]), f.get("service_date"), f["billed_code"]), f["patient_control_number"])
        reversed_at: dict[tuple, str] = {}      # a processing its payer reversed since settles nothing
        paid_by_payer: set[tuple] = set()       # (claim id, payer) with a payment still standing
        for l in self.lines:
            if bill_of(l["patient_control_number"]) not in bills:
                continue
            if l.get("claim_status_code") == "22":
                k = (l["patient_control_number"], l.get("procedure_code"), l.get("service_date"), who(l))
                reversed_at[k] = max(reversed_at.get(k, ""), at(l))
            elif l["line_key"] in self.standing:
                paid_by_payer.add((l["patient_control_number"], who(l)))

        def settled(bill: str, code: str, dos: str | None, payer: str | None, when: str, pcn: str, dates: set,
                    dup: bool = False) -> tuple | None:
            """How another claim of the bill settled this code and date other than by a payment still standing, if it did:
            - processed to the patient (copay, coinsurance, deductible) by the same payer after this denial (a corrected
              claim), on the same remittance under the same claim id (the payer processed the claim and denied a copy of
              it), or at any time when this is a duplicate's denial (the payer says it processed that service already: PM
              case C12); or by another payer that paid something on this same claim id (a crossover the primary paid and
              the secondary denied); a secondary owes what a primary put to the deductible, so a primary that paid nothing
              on the claim settles nothing;
            - denied as a primary line that has its own finding, which no person dismissed or marked lost;
            - paid under another code (SVC06, or a finding saying this code was billed), its own downcode finding;
            - billed again under another date, for a code this claim has on one date only.
            A processing its payer reversed since counts for nothing."""
            for o in bill_lines.get(bill, []):
                if o.get("service_date") != dos or o.get("claim_status_code") not in ("1", "19"):
                    continue
                if reversed_at.get((o["patient_control_number"], o.get("procedure_code"), o.get("service_date"), who(o)), "") > at(o):
                    continue
                if o.get("procedure_code") == code and not (o.get("paid_cents") or 0):
                    if (o.get("allowed_cents") or 0) > 0 or any(g == "PR" for g, _rc, _a in self.adj.get(o["line_key"], [])):
                        same_payer = who(o) == payer and (at(o) > when or dup
                                                          or (at(o) == when and o["patient_control_number"] == pcn))
                        if same_payer or (who(o) != payer and (pcn, who(o)) in paid_by_payer):
                            return ("processed to the patient", o["patient_control_number"])
                    elif o["line_key"] in listed_lines:
                        return ("denied again as a line", o["patient_control_number"])
                if o.get("original_procedure_code") == code and at(o) > when and o["line_key"] in listed_lines:
                    return ("paid under another code", o["patient_control_number"])
            if (bill, dos, code) in billed_as:
                return ("paid under another code", billed_as[(bill, dos, code)])
            corrected = [o for o in bill_lines.get(bill, []) if at(o) > when and o.get("service_date") == dos and o.get("claim_status_code") in ("1", "19")
                         and o.get("procedure_code") not in (code, None) and o["line_key"] in self.standing
                         and code_category(o.get("procedure_code")) == code_category(code)
                         and code_category(code) in ("em", "biopsy", "destruction", "excision", "repair", "mohs")]   # a level or size corrected
            if corrected and not any(o.get("procedure_code") == code and o.get("service_date") == dos and at(o) > when for o in bill_lines.get(bill, [])):
                return (f"billed again as {corrected[0]['procedure_code']}", corrected[0]["patient_control_number"])
            if dos and len(dates) == 1:
                later = [o for o in bill_lines.get(bill, []) if at(o) > when and o.get("claim_status_code") in ("1", "19") and o.get("procedure_code") == code]
                moved = [o for o in later if o.get("service_date") not in dates]
                if moved and not any(o.get("service_date") == dos for o in later):
                    return ("billed again under another date", moved[0]["patient_control_number"])
            return None

        bill_primary: dict[str, list[str]] = defaultdict(list)          # dates of primary payments standing, per bill
        for (b, _code), evs in self.paid_by_code.items():
            if b in bills:
                bill_primary[b] += [e[0] for e in evs if e[3] in ("1", "19")]
        for r in c.execute("""SELECT cl.patient_control_number, COALESCE(cl.payment_date, t.payment_date) FROM claims cl
                              JOIN transactions t ON t.transaction_key = cl.transaction_key
                              WHERE t.superseded = 0 AND cl.claim_status_code IN ('1','19') AND cl.paid_cents > 0
                                AND NOT EXISTS (SELECT 1 FROM lines l WHERE l.claim_key = cl.claim_key)
                                AND NOT EXISTS (SELECT 1 FROM claims r JOIN transactions rt ON rt.transaction_key = r.transaction_key
                                                WHERE rt.superseded = 0 AND r.claim_status_code = '22' AND r.patient_control_number = cl.patient_control_number
                                                  AND COALESCE(rt.payer_id, rt.payer_name) IS COALESCE(t.payer_id, t.payer_name)
                                                  AND COALESCE(r.payment_date, rt.payment_date) > COALESCE(cl.payment_date, t.payment_date))"""):
            if bill_of(r[0]) in bills:
                bill_primary[bill_of(r[0])].append(r[1] or "")          # a payment sent without service lines
        for (bill, _payer), claims in by_bill.items():
            claims.sort(key=lambda cl: (cl.get("payment_date") or cl.get("txn_payment_date") or "", cl["patient_control_number"]), reverse=True)
            claims.sort(key=lambda cl: 0 if self.prev_claims.get(cl["claim_key"], {}).get("worked") else 1 if cl["claim_key"] in self.prev_claims else 2)
            reported: set[tuple] = set()
            carrier: dict | None = None
            decided = ""            # when someone last changed the carrier's finding, if they worked it
            repeats: set[tuple] = set()         # what the denials after that decision already list
            repeat_carrier: dict | None = None
            for cl in claims:
                when = cl.get("payment_date") or cl.get("txn_payment_date") or ""
                prev = self.prev_claims.get(cl["claim_key"])
                grp, kind = payer_group_kind(cl.get("payer_name"), cl.get("claim_filing_indicator"))
                cl["payer_group"], cl["payer_kind"] = grp, kind
                cl["year"] = (cl.get("statement_from") or cl.get("payment_date") or "")[:4]
                cl["rp_name"] = cl.get("rendering_provider_name")
                lines = self.lines_by_claim.get(cl["claim_key"], [])
                adj = self.claim_adj.get(cl["claim_key"], []) + [(g, rc, a) for ln in lines for g, rc, a in self.adj.get(ln["line_key"], [])]
                carcs = Counter(rc for _g, rc, _a in adj if rc not in IGNORE_CARCS)
                top = top_of(carcs)[0] if carcs else None
                covering = None         # the payment that covers it, when it is covered
                if not lines:
                    later = sorted(d for d in bill_primary.get(bill, []) if d > when)
                    crossed = any(p != (cl.get("txn_payer") or cl.get("payer_id") or cl.get("payer_name")) for c_, p in paid_by_payer if c_ == cl["patient_control_number"])
                    if crossed and not (prev and later):
                        continue
                    if later or (top in DUP_CARCS and bill_primary.get(bill)):
                        if prev and later:
                            covering = later[0]
                        else:
                            continue
                need: dict[tuple, list] = {}            # (code, date) -> [units billed, expected]
                for ln in lines:
                    if not (ln.get("charge_cents") or 0) and not self.adj.get(ln["line_key"]):
                        continue                        # a $0.00 line with no adjustments bills nothing (T-N7)
                    ln2 = dict(ln, payer_group=grp, payer_kind=kind, year=cl["year"], provider_tier=self.provider_tier.get(ln.get("rendering_provider_npi") or "", "unknown"))
                    # each line at the payer's rate for its component, times the units it billed, never above its charge (T-C7)
                    e, _ = self.line_expected(ln2, ln["procedure_code"])
                    k = need.setdefault((ln["procedure_code"], ln.get("service_date")), [0, 0])
                    k[0] += self.billed_units_of(ln)
                    k[1] += e or 0
                unpaid: dict[tuple, list] = {}          # (code, date) -> [units unpaid, expected for them]
                pays: list[tuple] = []
                wants: list[tuple] = []                 # every code and date the claim lists, with its later payments (_recover)
                elsewhere: dict[tuple, tuple] = {}
                noted: dict[tuple, tuple] = {}
                by_answer: dict[tuple, tuple] = {}      # (code, date) -> how the denying payer's own later answer settled it
                payer = cl.get("txn_payer") or cl.get("payer_id") or cl.get("payer_name")
                remarks = set(self.claim_rem.get(cl["claim_key"], [])) | {x for ln in lines for x in self.rem.get(ln["line_key"], [])}
                cob = bool({rc for _g, rc, _a in adj} & OTHER_PAYER_CARCS or remarks & COB_RARCS)
                for (code, dos), (units, exp_k) in need.items():
                    if dos:
                        evs = self.paid_cover(bill, code, dos, exp_k // max(units, 1) if exp_k else None)
                        got = self.units_paid(evs)
                    else:               # an undated line: a primary payment of the code on the bill after the denial
                        evs = [e for e in self.paid_by_code.get((bill, code), []) if e[3] in ("1", "19") and e[0] > when]
                        got = min(units, self.units_paid(evs))
                    pays += [e for e in evs if e[0] > when]
                    wants.append(((bill, code, dos), units, self.with_second_shares(bill, code, dos, [e for e in evs if e[0] > when])))
                    if got < units:
                        # the payer that denied the claim answered the code and date again since: processed it as secondary
                        # after saying another payer covers the claim first (T-C3, T-N3), or put it on the patient's share
                        # (T-N6). Settled, with that answer as the proof, whether or not someone is working the denial
                        ans = self._settled_by_answer(answers.get(bill, []), code, dos, (payer, grp, cl.get("payer_name")), when,
                                                      cl["patient_control_number"], cob)
                        if ans:
                            by_answer[(code, dos)] = ans
                            elsewhere[(code, dos)] = ("processed as secondary by the same payer" if ans[0] == "as_secondary"
                                                      else "processed to the patient's share", ans[1][0]["patient_control_number"])
                            continue
                        how = settled(bill, code, dos, payer, when, cl["patient_control_number"],
                                      {ln.get("service_date") for ln in lines if ln.get("procedure_code") == code},
                                      dup=top in DUP_CARCS)
                        if how and not (prev and prev["worked"]):
                            elsewhere[(code, dos)] = how
                            continue
                        if how:         # someone is working this denial: it stays listed until a payment covers it
                            noted[(code, dos)] = how
                        unpaid[(code, dos)] = [units - got, int(round(exp_k * (units - got) / units)) if units else exp_k]
                # as on a line: a post-op visit, G2211 or a token charge is nothing to appeal
                trivial = lambda code: code in ("99024", "G2211") or sum(                                 # noqa: E731
                    ln.get("charge_cents") or 0 for ln in lines if ln.get("procedure_code") == code) <= 100
                if lines and prev and by_answer and all(trivial(code) for code, _d in unpaid):
                    # listed before and since settled by the denying payer's own answer (what it left is nothing to appeal):
                    # closed with that answer as its proof (as secondary first, then to the patient; a claim never listed
                    # makes no item, as when paid)
                    how = "as_secondary" if any(v[0] == "as_secondary" for v in by_answer.values()) else "to_patient"
                    exp_all = sum(v[1] for v in need.values()) or None
                    self._emit("CLAIM_DENIED_UNPAID", "denial", "medium", "review",
                               f"Claim denied ({top or 'no CARC'}) and since "
                               + ("processed as secondary by the same payer" if how == "as_secondary" else "processed to the patient's share"),
                               None, exp_all, exp_all, top, None, {"codes": sorted({code for code, _d in need})},
                               key_parts=(cl["claim_key"],), claim=cl)
                    f = self.findings.get(_fid("CLAIM_DENIED_UNPAID", cl["claim_key"]))
                    if f is not None:
                        # what the payer paid since on every code of the claim counts as recovered: a code its payment rules
                        # already counted as paid (a secondary's payment of half the expected amount) was paid by it too
                        paid_rows = [o for o in answers.get(bill, []) if (o.get("procedure_code"), o.get("service_date")) in need
                                     and self._one_payer(o, (payer, grp, cl.get("payer_name"))) and self.standing.get(o["line_key"], 0) > 0
                                     and (at(o) > when or (at(o) == when and o["patient_control_number"] != cl["patient_control_number"]))]
                        self._close_by_answer(f, how, [o for v in by_answer.values() if v[0] == how for o in v[1]],
                                              sorted({code for (code, _d), v in by_answer.items() if v[0] == how}), answers.get(bill, []),
                                              paid_rows)
                    continue
                if lines and not unpaid:
                    if prev and pays:           # listed before and since paid on the bill: closed by that payment, so it counts
                        # its recovery is every listed code's payments, not the first one's (trace finding T-C5)
                        covering = LaterPayment(min(pays, key=lambda e: (e[0], e[1] or "", e[4] or "", e[2])))
                        covering.wants = wants
                    else:
                        continue
                if covering is not None:
                    # someone worked this denial and the bill has since paid it: close it by that payment, so it counts
                    exp_all = sum(v[1] for v in need.values()) or None
                    self._emit("CLAIM_DENIED_UNPAID", "denial", "medium", "review", f"Claim denied ({top or 'no CARC'}) and since paid on the bill",
                               None, exp_all, exp_all, top, None, {"codes": sorted({code for code, _d in need})},
                               key_parts=(cl["claim_key"],), claim=cl,
                               resolved_by=covering if isinstance(covering, tuple) else (covering, None, None, "1"))
                    continue
                # a submission a person dismissed, marked lost or recovered keeps its own finding, but neither carries the
                # bill's other denials nor counts as listing their lines
                closed = bool(prev and prev["worked"] and prev.get("status") in ("dismissed", "lost", "resolved"))
                # the payer's date against the practice's day of the person's change (a UTC moment): an appeal at 9:30 PM
                # Eastern on June 19 came before a denial the payer dated June 20, as the states count it
                again = bool(decided) and when > runtime.practice_date(decided) and not (prev and prev["worked"])
                if again:               # denied again after someone acted: listed apart from their finding, the repeats together
                    novel = {k: v for k, v in unpaid.items() if k not in repeats} if lines else ({} if repeat_carrier else {("*", None): [1, 0]})
                    home = repeat_carrier
                elif prev and prev["worked"]:
                    novel = unpaid if lines else {("*", None): [1, 0]}       # someone worked this one: it keeps its own finding
                    home = None
                else:
                    novel = {k: v for k, v in unpaid.items() if k not in reported} if lines else ({} if carrier else {("*", None): [1, 0]})
                    home = carrier
                if not novel:
                    if home is not None:
                        home.setdefault("_also", []).append(cl["patient_control_number"])
                    continue
                unpaid_codes = {code for code, _d in novel if code != "*"}
                if unpaid_codes and all(trivial(code) for code in unpaid_codes):
                    continue        # as on a line: a post-op visit, G2211 or a token charge is nothing to appeal
                if not closed:
                    (repeats if again else reported).update(novel)
                rarcs = self.claim_rem.get(cl["claim_key"], []) + [x for ln in lines for x in self.rem.get(ln["line_key"], [])]
                rarc = ",".join(sorted(set(rarcs))) if rarcs else None
                codes = [code for code, _d in novel if code != "*"]
                exp = sum(v[1] for v in novel.values())
                sev = "high" if exp >= 20000 else "medium"
                if top in TIMELY_CARCS:
                    act = "review"
                elif top in INFO_CARCS or top in CODING_CARCS:
                    act = "correct_and_resubmit"
                elif top in NONCOV_CARCS or top in DUP_CARCS or top in FORWARD_CARCS:
                    act = "review"
                else:
                    act = "appeal"
                paid_codes = sorted({code for (code, _d) in need if (code, _d) not in unpaid and (code, _d) not in elsewhere})
                ev = {"codes": codes, "unpaid": sorted(([code, d] for code, d in novel if code != "*"), key=lambda p: (p[0], p[1] or "")),
                      "claim_charge_cents": cl.get("charge_cents"), "payer_claim_control_number": cl.get("payer_claim_control_number")}
                if elsewhere:
                    ev["settled_elsewhere"] = {code: f"{how} (claim {other})" for (code, _d), (how, other) in elsewhere.items()}
                rebilled = sorted({o["patient_control_number"] for o in bill_lines.get(bill, [])
                                   if (o.get("payment_date") or o.get("txn_payment_date") or "") > when and o["patient_control_number"] != cl["patient_control_number"]})
                if paid_codes:
                    ev["codes_paid_on_the_bill"] = paid_codes
                part = {code: v[0] for (code, _d), v in novel.items() if code != "*" and v[0] < need.get((code, _d), [0])[0]}
                if part:
                    ev["units_unpaid"] = part
                # every denial reason of the claim reaches the window rules (codes 'carcs'): a clinical line among other denials
                # dates the item by its own shorter window (fx-windows review r2-2)
                self._emit("CLAIM_DENIED_UNPAID", "denial", sev, act, f"Claim denied ({top or 'no CARC'}) and never paid: {', '.join(codes[:6]) or 'no service lines'}",
                           None, exp or None, exp or None, top, rarc, ev, key_parts=(cl["claim_key"],), claim=dict(cl, carcs=sorted(carcs)))
                f = self.findings.get(_fid("CLAIM_DENIED_UNPAID", cl["claim_key"]))
                if f is not None:
                    later_lines = [o for o in bill_lines.get(bill, []) if at(o) > when and o["patient_control_number"] != cl["patient_control_number"]]
                    dropped = [k for k in novel if k[0] != "*" and not any(o.get("procedure_code") == k[0] and o.get("service_date") == k[1] for o in later_lines)]
                    words = {"processed to the patient": "Processed to the patient", "denied again as a line": "Denied again as a line",
                             "paid under another code": "Paid under another code", "billed again under another date": "Billed again under another date"}
                    if noted:
                        (how, other) = next(iter(noted.values()))
                        f["hint"] = f"{words.get(how, how)} on claim {other}; kept listed while it is being worked"
                    elif dropped and later_lines:
                        code0, d0 = dropped[0]
                        instead = [o for o in later_lines if o.get("service_date") == d0 and o.get("procedure_code") != code0
                                   and code_category(o.get("procedure_code")) == code_category(code0) and o.get("procedure_code") not in codes]
                        f["hint"] = (f"Billed again on claim {instead[0]['patient_control_number']} as {instead[0]['procedure_code']} instead of {code0}"
                                     if instead else f"Billed again on claim {rebilled[0]} without {', '.join(k[0] for k in dropped[:3])}; check whether it was dropped on purpose")
                    elif novel and later_lines and all(k[0] != "*" for k in novel):
                        code0, d0 = next(iter(novel))
                        billed_units = need.get((code0, d0), [0])[0]
                        f["hint"] = f"Partly paid on claim {rebilled[0]}: {billed_units - novel[(code0, d0)][0]:g} of {billed_units:g} units"
                if again and repeat_carrier is None and f is not None:
                    repeat_carrier = f
                elif not again and carrier is None and f is not None and not closed:
                    carrier = f
                    decided = prev["changed"] if prev and prev["worked"] else ""
            for home in (carrier, repeat_carrier):
                if home is not None and home.get("_also"):
                    ev = json.loads(home["evidence"])
                    ev["other_denied_submissions"] = home.pop("_also")
                    home["evidence"] = json.dumps(ev, default=str)

    # ------------------------------------------------------------------ claims EMA shows open that the payer never answered
    # ------------------------------------------------------------------ the 837s: what was sent, and when
    def _load_837(self) -> dict:
        """What the 837 claim files show was sent, read once a run (claims_837: one row per claim in a file; BHT04, the day
        the file was made, is the day its claims went out). Kept, to spare memory, only for the claim ids EMA's weekly
        exports list and for the claims of bills no payer answered as its primary (no other claim can be chased):
          sends[claim id] = [newest send, first send, the newest send's frequency (1 an original, 7 a replacement, 8 a
                             void), whether the newest send is a void (a void and a new claim the same day is a replacement,
                             not a void), payer id, payer name, billed total, the newest send's claim_837_key, member id
                             (NM1*IL), number of sends]
          bills[bill]     = [newest submission number sent (voids left out), newest send day (voids left out), newest
                             void day]
        with the claim ids EMA's weekly exports list (ema_ids) and the bills a payer answered as primary (prim_bills)."""
        got = self.__dict__.get("_s837")
        if got is not None:
            return got
        c = self.conn
        out: dict = {"on": False, "sends": {}, "bills": {}, "ema_ids": set(), "prim_bills": set()}
        self._s837 = out
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "claims_837" not in tables:
            return out
        out["on"] = True
        prim: set[str] = set()
        for (pcn,) in c.execute(f"""SELECT DISTINCT cl.patient_control_number FROM claims cl JOIN transactions t ON t.transaction_key = cl.transaction_key
                                    WHERE COALESCE(t.superseded,0)=0 AND cl.patient_control_number IS NOT NULL
                                      AND cl.claim_status_code IN ({','.join("'" + s + "'" for s in PRIMARY_STATUSES)})"""):
            prim.add(bill_of(pcn))
        prim -= set(self._cob_only())      # a coordination denial its payer then answered as secondary is not a primary answer
        ema_ids: set[str] = set()
        if "ema_rows" in tables:
            ema_ids = {r[0] for r in c.execute("SELECT DISTINCT claim_id FROM ema_rows WHERE claim_id IS NOT NULL")}
        ema_bills = {bill_of(x) for x in ema_ids}
        pool: dict = {}
        sends: dict[str, list] = {}
        bills: dict[str, list] = {}
        for pcn, bht, freq, pid, pname, chg, key, member in c.execute(
                """SELECT patient_control_number, bht_created, TRIM(COALESCE(frequency_code, '')), payer_id, payer_name,
                          total_charge_cents, claim_837_key, subscriber_id
                   FROM claims_837 WHERE patient_control_number IS NOT NULL AND bht_created IS NOT NULL"""):
            pcn = str(pcn).strip()
            bht = real_day(bht)
            if not pcn or not bht:
                continue
            b = bill_of(pcn)
            if b in prim and b not in ema_bills:
                continue                    # a bill a payer answered as primary: nothing on it is chased from here
            bht = pool.setdefault(bht, bht)
            void = freq == "8"
            bi = bills.get(b)
            if bi is None:
                bi = bills[b] = [0, "", ""]
            if void:
                bi[2] = max(bi[2], bht)
            else:
                bi[0] = max(bi[0], sub_no(pcn))
                bi[1] = max(bi[1], bht)
            if b in prim and pcn not in ema_ids:
                continue
            pid = pool.setdefault(pid, pid)
            pname = pool.setdefault(pname, pname)
            r = sends.get(pcn)
            if r is None:
                sends[pcn] = [bht, bht, freq, void, pid, pname, chg, key, member, 1]
                continue
            r[9] += 1
            if bht < r[1]:
                r[1] = bht
            if bht > r[0]:
                r[0:9] = [bht, r[1], freq, void, pid, pname, chg, key, member]
            elif bht == r[0] and r[3] and not void:
                r[2:9] = [freq, False, pid, pname, chg, key, member]
        out.update(sends=sends, bills=bills, ema_ids=ema_ids, prim_bills=prim)
        return out

    def _837_claim_sends(self) -> dict[str, list]:
        """Every send (voids left out) of each claim id the 837s show sent more than once, read once a run: {claim id: [(day,
        payer id, payer name)]}. One claim id may go to more than one payer (the primary, then the secondary or the right
        payer, under the same claim id), and _load_837 keeps only the newest send's payer."""
        got = self.__dict__.get("_s837_all")
        if got is not None:
            return got
        out: dict[str, list] = {}
        self._s837_all = out
        if not self._load_837().get("on"):
            return out
        for pcn, bht, pid, pname in self.conn.execute(
                """SELECT TRIM(patient_control_number), bht_created, payer_id, payer_name FROM claims_837
                   WHERE TRIM(patient_control_number) IN (SELECT TRIM(patient_control_number) FROM claims_837
                                                          WHERE patient_control_number IS NOT NULL AND bht_created IS NOT NULL
                                                          GROUP BY 1 HAVING COUNT(*) > 1)
                     AND bht_created IS NOT NULL AND TRIM(COALESCE(frequency_code, '')) <> '8'"""):
            day = real_day(bht)
            if pcn and day:
                out.setdefault(pcn, []).append((day, pid, pname))
        return out

    def _first_send_to(self, pcn: str, row: list | None, pid: str | None, pname: str | None) -> str | None:
        """The first day the 837s show claim id pcn sent to this payer (_one_payer), its send row as _load_837 keeps it
        standing for a claim sent once; None when none of its sends went to that payer. Only a send to the payer that has the
        claim now is proof of timely filing with it (fx-837-r1-2)."""
        sends = self._837_claim_sends().get(pcn)
        if sends is None:
            return row[1] if row is not None and row[1] and _one_payer(row[4], row[5], pid, pname) else None
        return min((d for d, i, n in sends if _one_payer(i, n, pid, pname)), default=None)

    def _cob_only(self) -> dict[str, dict]:
        """Bills whose every remittance is one payer's: it denied the claim as the primary payer saying another payer covers
        it first (a reason of OTHER_PAYER_CARCS or a remark of COB_RARCS, nothing paid), then processed it as secondary
        (claim status 2 or 3, on or after that denial), and never paid it as primary. Its denial closes on that answer
        (_settled_by_answer), but no remittance from the payer that covers the claim first is on file: the primary's
        money is unanswered as for a claim only a secondary answered (T-C2), though the payer that answered as secondary is
        the one the claim was sent to. Worked out once a run, after load():
        {bill: {'payer': group, 'payer_id', 'denied_on', 'reason', 'second_on', 'paid_cents', 'answers': {claim id:
        [(date, group, paid, status)]}}}, the answers being its processings as secondary."""
        got = self.__dict__.get("_cob")
        if got is not None:
            return got
        c = self.conn
        out: dict[str, dict] = {}
        self._cob = out
        c.create_function("rw_837_bill", 1, bill_of, deterministic=True)
        prim = ",".join("'" + s + "'" for s in PRIMARY_STATUSES)
        bills = [r[0] for r in c.execute(
            f"""SELECT rw_837_bill(cl.patient_control_number) AS b FROM claims cl JOIN transactions t ON t.transaction_key = cl.transaction_key
                WHERE COALESCE(t.superseded,0)=0 AND cl.patient_control_number IS NOT NULL
                GROUP BY b HAVING COUNT(DISTINCT COALESCE(t.payer_id, t.payer_name)) = 1 AND SUM(cl.claim_status_code IN ('2','3')) > 0
                   AND SUM(cl.claim_status_code IN ({prim}) AND COALESCE(cl.paid_cents,0) = 0) > 0
                   AND SUM(cl.claim_status_code IN ({prim}) AND COALESCE(cl.paid_cents,0) <> 0) = 0""")]
        if not bills:
            return out
        rows: dict[str, list] = defaultdict(list)
        for i in range(0, len(bills), 500):
            c.execute("DROP TABLE IF EXISTS temp.d837_cb")
            c.execute("CREATE TEMP TABLE d837_cb (bill TEXT PRIMARY KEY, hi TEXT)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_cb VALUES (?, ?)", [(b, b + "~") for b in bills[i:i + 500]])
            for b, ck, pcn, st, paid_on, pname, pid, cfi in c.execute(
                    """SELECT x.bill, cl.claim_key, cl.patient_control_number, cl.claim_status_code, t.payment_date, t.payer_name, t.payer_id,
                              cl.claim_filing_indicator
                       FROM temp.d837_cb x JOIN claims cl ON cl.patient_control_number >= x.bill AND cl.patient_control_number < x.hi
                       JOIN transactions t ON t.transaction_key = cl.transaction_key WHERE COALESCE(t.superseded,0)=0"""):
                if bill_of(pcn) == b:
                    rows[b].append((ck, pcn, st or "", paid_on or "", payer_group_kind(pname, cfi)[0], pid or pname))
        c.execute("DROP TABLE IF EXISTS temp.d837_cb")
        for b, rs in rows.items():
            denials = [r for r in rs if r[2] in PRIMARY_STATUSES]
            seconds = [r for r in rs if r[2] in ("2", "3")]
            if not denials or not seconds or len({payer_company(r[4]) for r in rs}) != 1:
                continue
            why = []
            for ck, *_rest in denials:
                lines = self.lines_by_claim.get(ck, [])
                reasons = {(g, rc) for g, rc, _a in self.claim_adj.get(ck, [])} | {(g, rc) for ln in lines for g, rc, _a in self.adj.get(ln["line_key"], [])}
                remarks = set(self.claim_rem.get(ck, [])) | {x for ln in lines for x in self.rem.get(ln["line_key"], [])}
                if any((ln.get("paid_cents") or 0) for ln in lines):
                    break
                hit = sorted(f"{g}-{rc}" for g, rc in reasons if rc in OTHER_PAYER_CARCS) or sorted(remarks & COB_RARCS)
                if not hit:
                    break               # a denial as primary for another reason: the claim was answered as primary
                why.append(hit[0])
            else:
                first = min(r[3] for r in denials)
                later = [r for r in seconds if r[3] >= first]
                if not later:
                    continue
                answers: dict[str, list] = defaultdict(list)
                paid = 0
                for ck, pcn, st, day, grp, _pid in later:
                    p = sum(self.standing.get(ln["line_key"], 0) for ln in self.lines_by_claim.get(ck, []))
                    paid += p
                    answers[pcn].append((day, grp, p, st))
                out[b] = {"payer": denials[0][4], "payer_id": denials[0][5], "denied_on": first, "reason": why[0],
                          "second_on": min(r[3] for r in later), "paid_cents": paid, "answers": dict(answers)}
        return out

    def _answer_days_837(self, newest: str) -> dict:
        """Each payer group's answer time after a send (ANSWER_DAYS_META): for each 837 claim sent in the two years before
        the newest remittance, the days to the first remittance on or after the send that answered its claim id (any
        processing but a reversal), by that remittance's payer group; the 95th percentile where 20 answers at least are on
        file (else all payers'), never under NO_ANSWER_DAYS. Written to meta at once: the states of this run's save read
        it (a bill sent again waits that long for its answer)."""
        c = self.conn
        since = _add_days(newest, -730) or ""
        first: dict[tuple, tuple] = {}
        for pcn, sent, paid_on, pname, cfi in c.execute(
                """SELECT c8.patient_control_number, c8.bht_created, t.payment_date, t.payer_name, cl.claim_filing_indicator
                   FROM claims_837 c8 JOIN claims cl ON cl.patient_control_number = c8.patient_control_number
                   JOIN transactions t ON t.transaction_key = cl.transaction_key
                   WHERE COALESCE(t.superseded,0)=0 AND c8.bht_created >= ? AND t.payment_date >= c8.bht_created
                     AND cl.claim_status_code IN ('1','2','3','4','19','20','21')""", (since,)):
            k = (pcn, sent)
            if k not in first or (paid_on or "") < first[k][0]:
                first[k] = (paid_on or "", pname, cfi)
        by: dict[str, list] = defaultdict(list)
        everything: list = []
        groups: dict = {}
        for (pcn, sent), (paid_on, pname, cfi) in first.items():
            d = _days_between(sent, paid_on)
            if d is None or d < 0 or d > 730:
                continue
            gk = (pname, cfi)
            if gk not in groups:
                groups[gk] = payer_group_kind(pname, cfi)[0]
            by[groups[gk]].append(d)
            everything.append(d)

        def p95(v: list) -> int:
            v = sorted(v)
            return max(NO_ANSWER_DAYS, v[min(len(v) - 1, max(0, (len(v) * 95 + 99) // 100 - 1))])
        out = {"groups": {g: {"p95": p95(v), "n": len(v)} for g, v in by.items() if len(v) >= 20},
               "all": {"p95": p95(everything), "n": len(everything)} if len(everything) >= 20 else None, "newest": newest}
        try:
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (ANSWER_DAYS_META, json.dumps(out, sort_keys=True)))
            c.commit()
        except sqlite3.Error:
            c.rollback()
        return out

    def _claim_companies(self, cd: dict) -> set[str]:
        """The companies a claim's payer stands for (payer_company): the remittances' group for it, the group EMA gives its
        payer's name, and that name's own group (the VA's claims carry the VA and its administrator, under whose name EMA
        posts the VA's payments)."""
        groups = {cd.get("grp"), cd.get("match_group"), self._ema_group(cd.get("ema_payer"))}
        if cd.get("ema_payer"):
            groups.add(normalize_payer(cd["ema_payer"])[0])
        return {payer_company(g) for g in groups if g} - {""}

    def _posting_companies(self, payer: str | None, grp: str | None) -> set[str]:
        """The companies an EMA posting's payer can be: its name's own group and the group EMA gives that name (EMA's summary
        can carry an alias's group on a posting, so the name decides), else the posting's group."""
        if payer:
            return {payer_company(normalize_payer(payer)[0]), payer_company(self._ema_group(payer))} - {""}
        return ({payer_company(grp)} - {""}) if grp else set()

    def _patient_keys(self, cds: list[dict], answers: dict, member_names: bool = True, persons: dict | None = None,
                      ema_names: dict | None = None) -> dict[str, set]:
        """EMA's keyed names for each claim's patient ({claim id: {name keys}}), from everything that names the patient: the
        patient's names on the remittances for the claim's own bill (any payer's answer to it); the member ids the bill's
        837s name, when the remittances give a member id one name only (a subscriber's id also covers the family); the EMA
        claim a bridge found for it (answers); and, for a claim of EMA's weekly exports, EMA's claims for the same EMA
        patient (cd['ema_patients']) that a claim id pair or the bill map ties to an EMA claim. member_names False leaves
        the member ids' names out (only the claim's own evidence names its patient: a dependent's 837 carries the
        subscriber's id). persons, when given, is filled with {claim id: {keyed name and birth date}} from the same EMA
        claims (ema_lines.person); ema_names, when given, with {claim id: {keyed names}} from those EMA claims alone (EMA's
        own spelling of the patient's name: a remittance may spell it otherwise, with a middle name or without)."""
        c = self.conn
        out: dict[str, set] = defaultdict(set)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        bills = sorted({cd.get("b") for cd in cds if cd.get("b")})
        name_sql = """rw_name_key(cl.patient_last, cl.patient_first),
                      CASE WHEN cl.corrected_patient_last IS NOT NULL OR cl.corrected_patient_first IS NOT NULL
                           THEN rw_name_key(COALESCE(cl.corrected_patient_last, cl.patient_last),
                                            COALESCE(cl.corrected_patient_first, cl.patient_first)) END"""
        bill_names: dict[str, set] = defaultdict(set)
        bill_members: dict[str, set] = defaultdict(set)
        for i in range(0, len(bills), 500):
            c.execute("DROP TABLE IF EXISTS temp.d837_pb")
            c.execute("CREATE TEMP TABLE d837_pb (bill TEXT PRIMARY KEY, hi TEXT)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_pb VALUES (?, ?)", [(b, b + "~") for b in bills[i:i + 500]])
            for b, pcn, nk, cnk in c.execute(f"""SELECT x.bill, cl.patient_control_number, {name_sql} FROM temp.d837_pb x
                                                 JOIN claims cl ON cl.patient_control_number >= x.bill AND cl.patient_control_number < x.hi"""):
                if bill_of(pcn) == b:
                    bill_names[b] |= {nk, cnk} - {None}
            if member_names and "claims_837" in tables:
                for b, pcn, member in c.execute("""SELECT x.bill, c8.patient_control_number, c8.subscriber_id FROM temp.d837_pb x
                                                   JOIN claims_837 c8 ON c8.patient_control_number >= x.bill AND c8.patient_control_number < x.hi
                                                   WHERE c8.subscriber_id IS NOT NULL AND c8.subscriber_id <> ''"""):
                    if bill_of(pcn) == b:
                        bill_members[b].add(member)
        c.execute("DROP TABLE IF EXISTS temp.d837_pb")
        members = sorted({m for ms in bill_members.values() for m in ms}
                         | {cd["k"]["member"] for cd in cds if (cd.get("k") or {}).get("member")}) if member_names else []
        names_of: dict[str, set] = defaultdict(set)
        for i in range(0, len(members), 500):
            part = set(members[i:i + 500])
            c.execute("DROP TABLE IF EXISTS temp.d837_bm")
            c.execute("CREATE TEMP TABLE d837_bm (member TEXT PRIMARY KEY)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_bm VALUES (?)", [(m,) for m in part])
            for pid, iid, nk in c.execute("""SELECT patient_id, insured_id, rw_name_key(patient_last, patient_first) FROM claims
                                             WHERE patient_id IN (SELECT member FROM temp.d837_bm) OR insured_id IN (SELECT member FROM temp.d837_bm)"""):
                if nk is not None:
                    for m in {pid, iid} & part:
                        names_of[m].add(nk)
        c.execute("DROP TABLE IF EXISTS temp.d837_bm")
        links = {(answers.get(cd["pcn"]) or {}).get("link") for cd in cds} - {None}
        # the weekly exports' patients: their other EMA claims a claim id pair or the bill map ties to an EMA claim
        pats = sorted({p for cd in cds for p in (cd.get("ema_patients") or ())})
        claims_of: dict[str, set] = defaultdict(set)
        if pats and "ema_rows" in tables:
            c.execute("DROP TABLE IF EXISTS temp.d837_pt")
            c.execute("CREATE TEMP TABLE d837_pt (pid TEXT PRIMARY KEY)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_pt VALUES (?)", [(p,) for p in pats])
            for pid, cid in c.execute("""SELECT DISTINCT patient_id, claim_id FROM ema_rows
                                         WHERE patient_id IN (SELECT pid FROM temp.d837_pt) AND claim_id IS NOT NULL"""):
                claims_of[pid].add(cid)
            c.execute("DROP TABLE IF EXISTS temp.d837_pt")
        link_of_claim: dict[str, set] = defaultdict(set)
        all_claims = sorted({x for xs in claims_of.values() for x in xs})
        for i in range(0, len(all_claims), 500):
            part = all_claims[i:i + 500]
            q = ",".join("?" * len(part))
            for cid, lk in c.execute(f"SELECT claim_id, link FROM ema_claim_links WHERE claim_id IN ({q}) AND link IS NOT NULL", part):
                link_of_claim[cid].add(lk)
            bq = sorted({bill_of(x) for x in part})
            by_bill: dict[str, list] = defaultdict(list)
            for x in part:
                by_bill[bill_of(x)].append(x)
            for b, lk in c.execute(f"""SELECT bill, link FROM ema_link_map WHERE bill IN ({','.join('?' * len(bq))})
                                       AND COALESCE(conflict, 0) = 0""", bq):
                for x in by_bill.get(b) or ():
                    link_of_claim[x].add(lk)
        links |= {lk for ls in link_of_claim.values() for lk in ls}
        keys_of_link: dict[int, set] = defaultdict(set)
        person_of_link: dict[int, set] = defaultdict(set)
        lks = sorted(links)
        for i in range(0, len(lks), 500):
            part = lks[i:i + 500]
            if persons is None:
                for lk, nk in c.execute(f"""SELECT DISTINCT link, name_key FROM ema_lines WHERE link IN ({','.join('?' * len(part))})
                                            AND name_key IS NOT NULL""", part):
                    keys_of_link[lk].add(nk)
                continue
            for lk, nk, person in c.execute(f"""SELECT DISTINCT link, name_key, person FROM ema_lines
                                                WHERE link IN ({','.join('?' * len(part))})
                                                  AND (name_key IS NOT NULL OR person IS NOT NULL)""", part):
                if nk is not None:
                    keys_of_link[lk].add(nk)
                if person is not None:
                    person_of_link[lk].add(person)
        for cd in cds:
            b = cd.get("b")
            got = set(bill_names.get(b) or ())
            ms = (set(bill_members.get(b) or ()) | ({cd["k"]["member"]} if (cd.get("k") or {}).get("member") else set())
                  if member_names else set())
            for m in ms:
                if len(names_of.get(m) or ()) == 1:
                    got |= names_of[m]
            mine = set()
            lk = (answers.get(cd["pcn"]) or {}).get("link")
            if lk is not None:
                mine.add(lk)
            for p in cd.get("ema_patients") or ():
                for x in claims_of.get(p) or ():
                    mine |= link_of_claim.get(x) or set()
            for lk2 in mine:
                got |= keys_of_link.get(lk2) or set()
            if ema_names is not None:
                en = {nk for lk2 in mine for nk in keys_of_link.get(lk2) or ()}
                if en:
                    ema_names[cd["pcn"]] = en
            if persons is not None:
                ps = {p for lk2 in mine for p in person_of_link.get(lk2) or ()}
                if ps:
                    persons[cd["pcn"]] = ps
            if got:
                out[cd["pcn"]] = got
        return out

    def _answered_by_name(self, cds: list[dict], keys_of: dict) -> dict[str, set]:
        """The services (date, code) of these claims a payer answered as its primary (claim status 1, 19, 4 or 22) on another
        bill for the same patient, by the patient's keyed names (_patient_keys) against the remittances' patient name (as
        ema_checks.unanswered_claims checks its claims): a bill made again, which the member id cannot always link. The
        answering payer must be the claim's own (payer_company: a visit another payer answered may still owe this one's
        share), except for a claim only a secondary answered, whose primary is the payer it lacks (T-C2); and for a claim
        that never reached its payer (cd['after']) the answer must come after its send. An answer counts for its own part of
        the service only (part_fits, T-C1): the TC answered never answers a claim for the reading (26), and a claim for the
        whole service needs the whole service answered, or both parts. {claim id: {(date, code, part)}}."""
        want = [(cd["pcn"], cd.get("b"), nk, x[1], x[0], item_part(x)) for cd in cds for x in cd["items"] if x[1] and x[0]
                for nk in keys_of.get(cd["pcn"]) or ()]
        out: dict[str, set] = defaultdict(set)
        if not want:
            return out
        c = self.conn
        keys = {(w[2], w[3], w[4]) for w in want}
        seen: dict[tuple, list] = defaultdict(list)     # (name key, date, code) -> [(bill, payer group, paid on, part)]
        c.execute("DROP TABLE IF EXISTS temp.d837_nd")
        c.execute("CREATE TEMP TABLE d837_nd (svc TEXT PRIMARY KEY)")
        c.executemany("INSERT OR IGNORE INTO temp.d837_nd VALUES (?)", [(d,) for d in sorted({k[1] for k in keys})])
        codes = sorted({k[2] for k in keys})
        cur = c.execute(f"""SELECT rw_name_key(cl.patient_last, cl.patient_first),
                                   CASE WHEN cl.corrected_patient_last IS NOT NULL OR cl.corrected_patient_first IS NOT NULL
                                        THEN rw_name_key(COALESCE(cl.corrected_patient_last, cl.patient_last),
                                                         COALESCE(cl.corrected_patient_first, cl.patient_first)) END,
                                   l.service_date, l.procedure_code, l.original_procedure_code, cl.patient_control_number,
                                   t.payer_name, cl.claim_filing_indicator, t.payment_date, l.modifiers
                            FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key
                            JOIN transactions t ON t.transaction_key = l.transaction_key
                            WHERE COALESCE(t.superseded,0)=0 AND l.service_date IN (SELECT svc FROM temp.d837_nd)
                              AND cl.claim_status_code IN ({','.join("'" + s + "'" for s in PRIMARY_STATUSES)})
                              AND (l.procedure_code IN ({','.join('?' * len(codes))}) OR l.original_procedure_code IN ({','.join('?' * len(codes))}))""",
                        codes + codes)
        for nk, cnk, svc, code, ocode, pcn, pname, cfi, paid_on, mods in cur:
            for n in {nk, cnk} - {None}:
                for cd_ in {code, ocode} - {None}:
                    if (n, svc, cd_) in keys:
                        seen[(n, svc, cd_)].append((bill_of(pcn), payer_group_kind(pname, cfi)[0], paid_on or "", component_of(mods)))
        c.execute("DROP TABLE IF EXISTS temp.d837_nd")
        info = {cd["pcn"]: cd for cd in cds}
        comps: dict[str, set] = {}
        got: dict[tuple, set] = defaultdict(set)        # (claim id, date, code, part) -> the parts answered on other bills
        for cid, b, nk, svc, cpt, part in want:
            cd = info[cid]
            after = cd.get("after")
            for ob, grp, paid_on, op in seen.get((nk, svc, cpt)) or ():
                if ob == b or (after and paid_on <= after):
                    continue
                if cid not in comps:
                    comps[cid] = ({payer_company(x[1]) for x in cd.get("secondary") or [] if x[1]} if cd.get("secondary")
                                  else self._claim_companies(cd))
                if cd.get("secondary"):
                    # only a payer other than the ones that answered as secondary answers for the primary (a payer that
                    # denied it as primary for coordination answered as secondary too)
                    if payer_company(grp) not in comps[cid]:
                        got[(cid, svc, cpt, part)].add(op)
                elif payer_company(grp) in comps[cid]:
                    got[(cid, svc, cpt, part)].add(op)
        for (cid, svc, cpt, part), parts in got.items():
            if part_fits(part, parts):
                out[cid].add((svc, cpt, part))
        return out

    def _ema_by_patient(self, cds: list[dict], answers: dict, keys_of: dict) -> dict[str, set]:
        """The services (date, code, part) of these claims that EMA's postings show answered for their patient, never by a
        payment under the surgery center's separate-payee checks: on the claim's own EMA claim (answers' link) an insurance
        payment or a $0 answer (a $0 row with its proof: ema_checks.zero_answer_sql; a bare $0 row answers nothing), or a
        line EMA shows settled (settled_line_sql); on another of the patient's EMA claims
        (by the patient's keyed names, _patient_keys: a visit paid on another of EMA's claims, a bill made again or a claim
        id the exports never listed, is answered there) a payment that still stands or a settled line, by the claim's own
        payer (payer_company: a visit another payer paid may still owe this one's share; a $0 answer to another claim
        leaves this one's money owed), for the claim's own part of the service (part_fits, T-C1: EMA's lines carry no
        modifiers, so the other claim's part is the one its 837, remittance or weekly export names, ema_checks.line_parts;
        one nothing names answers only a claim for the whole service). For a claim only a secondary answered
        (T-C2, or its own payer as secondary after a coordination denial), only another payer's posting is the primary's
        answer. Any day (a payment before the send is the visit's payment all the same), except for a claim that never
        reached its payer (rejected, or in EMA's print batch: cd['after']), which only a posting after its send answers.
        {claim id: {(date, code, part)}}."""
        from . import ema_checks as ec
        c = self.conn
        out: dict[str, set] = defaultdict(set)
        ec._prepare(c)                      # the keyed name and the other SQL functions the checks use
        want: dict[tuple, set] = defaultdict(set)        # (link, date, code) or (name key, date, code) -> claim ids
        by_link: set[tuple] = set()
        by_name: set[tuple] = set()
        own: dict[str, int | None] = {}
        charge_of: dict[tuple, Counter] = defaultdict(Counter)    # (claim id, date, code) -> {the part its items bill: charge}
        for cd in cds:
            lk = own[cd["pcn"]] = (answers.get(cd["pcn"]) or {}).get("link")
            for x in cd["items"]:
                if not x[1]:
                    continue
                charge_of[(cd["pcn"], x[1], x[0])][item_part(x)] += int(x[2] or 0)
                if lk is not None:
                    want[("l", lk, x[1], x[0])].add(cd["pcn"])
                    by_link.add((lk, x[1], x[0]))
                for nk in keys_of.get(cd["pcn"]) or ():
                    want[("n", nk, x[1], x[0])].add(cd["pcn"])
                    by_name.add((nk, x[1], x[0]))
        if not want:
            return out
        seconds = {cd["pcn"]: [x[1] for x in cd.get("secondary") or [] if x[1]] for cd in cds}
        sep = ("AND NOT EXISTS (SELECT 1 FROM ema_checks s WHERE s.check_no = y.check_no AND s.payer = y.payer AND s.cause = 'separate_payee')"
               if c.execute("SELECT 1 FROM sqlite_master WHERE name='ema_checks'").fetchone() else "")
        hits: list[tuple] = []          # (key, EMA claim number, payer group, payer name, settled, posted)
        for kind, keys, col in (("l", sorted(by_link), "link"), ("n", sorted(by_name), "name_key")):
            for i in range(0, len(keys), 500):
                c.execute("DROP TABLE IF EXISTS temp.d837_ek")
                c.execute("CREATE TEMP TABLE d837_ek (k INTEGER, svc TEXT, cpt TEXT)")
                c.executemany("INSERT INTO temp.d837_ek VALUES (?, ?, ?)", keys[i:i + 500])
                for k, svc, cpt, lk, grp, payer, posted, net in c.execute(
                        f"""SELECT y.{col}, y.svc, y.cpt, y.link, MAX(y.payer_group), y.payer,
                                   MIN(CASE WHEN COALESCE(y.ins_paid, 0) > 0 THEN y.posted END), SUM(COALESCE(y.ins_paid, 0))
                            FROM temp.d837_ek x JOIN ema_line_payments y ON y.{col} = x.k AND y.svc = x.svc AND y.cpt = x.cpt
                            WHERE {ec.answer_row_sql('y')} {sep}
                            GROUP BY y.{col}, y.svc, y.cpt, y.link, y.payer""" if kind == "n" else
                        f"""SELECT y.{col}, y.svc, y.cpt, y.link, y.payer_group, y.payer, y.posted, NULL FROM temp.d837_ek x
                            JOIN ema_line_payments y ON y.{col} = x.k AND y.svc = x.svc AND y.cpt = x.cpt
                            WHERE {ec.answer_row_sql('y')} {sep}"""):
                    if kind == "n" and (net or 0) <= 0:
                        continue            # on another of the patient's claims only a payment that stands shows the visit
                                            # paid: a $0 answer there (a rebill denied) leaves this claim's money owed
                    hits.append(((kind, k, svc, cpt), lk, grp, payer, False, posted))
                # a line EMA shows settled (settled_line_sql), dated by the payer's first answer posted on it
                for k, svc, cpt, lk, grp, payer, posted in c.execute(
                        f"""SELECT l.{col}, l.svc, l.cpt, l.link, l.payer_group, l.primary_payer,
                                   COALESCE(l.first_ins_posting, l.last_posted) FROM temp.d837_ek x
                            JOIN ema_lines l ON l.{col} = x.k AND l.svc = x.svc AND l.cpt = x.cpt
                            WHERE {settled_line_sql('l')}"""):
                    hits.append(((kind, k, svc, cpt), lk, grp, payer, True, posted))
        c.execute("DROP TABLE IF EXISTS temp.d837_ek")
        names = self._secondary_names({h[1] for h in hits if h[1] is not None})
        info = {cd["pcn"]: cd for cd in cds}
        comps: dict[str, set] = {}
        # the parts of a service the other EMA claims' lines bill (EMA's lines carry no modifiers), and their charges: one
        # whose part nothing names, charged what the claim's own line is, is that part billed again
        hit_keys = {(h[1], h[0][2], h[0][3]) for h in hits if h[1] is not None}
        lparts = self._line_parts(hit_keys)
        lcharge = ec.line_charges(c, hit_keys) if any(k[2] in (self._split_codes or ()) for k in hit_keys) else {}
        got: dict[tuple, set] = defaultdict(set)    # (claim id, date, code) -> the parts answered (OWN: its own EMA claim)
        for key, lk, grp, payer, settled, posted in hits:
            for cid in want.get(key, ()):
                after = info[cid].get("after")
                if after and str(posted or "") <= after:
                    continue                # a claim that never reached its payer: only an answer after its send
                mine = lk is not None and lk == own.get(cid)
                ch = lcharge.get((lk, key[2], key[3]))
                alike = next((p for p, v in charge_of.get((cid, key[2], key[3]), {}).items() if ch and v == ch), None)
                answered = {_OWN} if mine else ec.other_parts(lparts.get((lk, key[2], key[3])), alike)
                if seconds.get(cid):
                    if settled or not self._is_secondary_posting(lk, payer, grp, seconds[cid], names):
                        got[(cid, key[2], key[3])] |= answered
                    continue
                if not mine:
                    if cid not in comps:
                        comps[cid] = self._claim_companies(info[cid])
                    if not self._posting_companies(payer, grp) & comps[cid]:
                        continue            # another payer's answer on another EMA claim: not this claim's
                got[(cid, key[2], key[3])] |= answered
        for (cid, svc, cpt), parts in got.items():
            for p in charge_of.get((cid, svc, cpt)) or ():
                if _OWN in parts or part_fits(p, parts):
                    out[cid].add((svc, cpt, p))
        return out

    def _answered_rule(self, step: str, cds: list[dict], answers: dict) -> dict[str, list]:
        """The one answered rule for every never-answered or rejected claim, whatever listed it (the 837s, EMA's weekly
        exports, EMA's reports). A claim is answered when EMA's postings on its own EMA claim answer it after its send
        (answers, from ema_checks.claim_answers and the send-day twins); for a claim only a secondary answered, when another
        payer's posting on it is the primary's answer (_primary_posted). A service is answered when EMA's postings for the
        patient show it paid or settled (_ema_by_patient), or a payer answered it as primary on another bill
        (_answered_by_name), each for the service's own part (26, TC or the whole: T-C1). A $0 row EMA posted under a check
        answers only with its proof (ema_checks.zero_answer_sql: an allowed amount, or the payer's reason that day): a bare
        one answers nothing. cds: [{'pcn', 'b', 'items', 'sent', 'after', 'grp', 'ema_payer', 'secondary', 'k'?,
        'ema_patients'?}]. {claim id: the services left unanswered} ([] when it is answered)."""
        keys_of = self._ema_step(step + "_patient_keys", self._patient_keys, cds, answers) or {}
        primary = self._ema_step(step + "_primary_posted", self._primary_posted, [
            (cd["pcn"], (answers.get(cd["pcn"]) or {}).get("link"), cd["sent"], sorted({x[1] for x in cd["secondary"]}))
            for cd in cds if cd.get("secondary")]) or set()
        by_patient = self._ema_step(step + "_by_patient", self._ema_by_patient, cds, answers, keys_of) or {}
        by_name = self._ema_step(step + "_by_name", self._answered_by_name, cds, keys_of) or {}
        out: dict[str, list] = {}
        for cd in cds:
            a = answers.get(cd["pcn"]) or {}
            if (cd["pcn"] in primary) if cd.get("secondary") else a.get("answered"):
                out[cd["pcn"]] = []
                continue
            done = (by_patient.get(cd["pcn"]) or set()) | (by_name.get(cd["pcn"]) or set())
            out[cd["pcn"]] = [x for x in cd["items"] if (x[1], x[0], item_part(x)) not in done]
        return out

    def _secondary_names(self, links) -> dict[int, set]:
        """Each EMA claim's payer names on the postings EMA matched to a remittance on file (in_835): on a claim only a
        secondary answered (T-C2, or its own payer as secondary after a coordination denial), the secondary's own names."""
        out: dict[int, set] = defaultdict(set)
        lks = sorted({int(x) for x in links if x is not None})
        c = self.conn
        for i in range(0, len(lks), 500):
            c.execute("DROP TABLE IF EXISTS temp.d837_sn")
            c.execute("CREATE TEMP TABLE d837_sn (link INTEGER PRIMARY KEY)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_sn VALUES (?)", [(x,) for x in lks[i:i + 500]])
            for lk, payer in c.execute("""SELECT DISTINCT y.link, y.payer FROM ema_line_payments y
                                          WHERE y.link IN (SELECT link FROM temp.d837_sn) AND COALESCE(y.in_835, 0) = 1
                                            AND COALESCE(y.payer, '') <> ''"""):
                out[lk].add(payer)
        c.execute("DROP TABLE IF EXISTS temp.d837_sn")
        return out

    @staticmethod
    def _is_secondary_posting(link, payer: str | None, grp: str | None, seconds: list, names: dict) -> bool:
        """An EMA posting on a claim only a secondary answered is that secondary's own when its payer is a name the claim's
        postings matched to the remittances carry (only the secondary's remittance is on file); under any other name it is
        another payer's, the primary's (EMA's groups cannot tell a state's Medicaid from a Medicaid plan: both are
        'Medicaid (other)'). With no posting matched to a remittance, the payer's group decides, by its family."""
        from . import ema_checks as ec
        own = names.get(link) or set()
        if payer and payer in own:
            return True
        if own:
            return False
        g = (normalize_payer(payer)[0] if payer else None) or grp
        return not g or any(g == s or ec._same_payer(g, s) or payer_company(g) == payer_company(s) for s in seconds)

    def _primary_posted(self, claims: list[tuple]) -> set[str]:
        """The claims among these that EMA's postings show answered by a payer other than the ones that answered them as
        secondary: [(claim id, EMA claim number, sent, the secondary payers' groups)]. A payment or a $0 answer (a $0 row with
        its proof: ema_checks.zero_answer_sql) on the claim's EMA claim (never one under the surgery center's separate-payee
        checks) by another payer is the primary's answer, whatever its day (the primary answers before the claim goes to the
        secondary): then nothing is left to chase (the coordinator's addition to T-I2; T-C2). Whose a posting is:
        _is_secondary_posting."""
        from . import ema_checks as ec
        rows = [(cid, lk, sent) for cid, lk, sent, _g in claims if lk is not None and sent]
        if not rows:
            return set()
        groups = {cid: [g for g in gs if g] for cid, _lk, _s, gs in claims}
        c = self.conn
        got: set[str] = set()
        names = self._secondary_names([lk for _cid, lk, _s in rows])
        c.execute("DROP TABLE IF EXISTS temp.d837_pp")
        c.execute("CREATE TEMP TABLE d837_pp (cid TEXT, link INTEGER, sent TEXT)")
        c.executemany("INSERT INTO temp.d837_pp VALUES (?, ?, ?)", rows)
        sep = ("AND NOT EXISTS (SELECT 1 FROM ema_checks s WHERE s.check_no = y.check_no AND s.payer = y.payer AND s.cause = 'separate_payee')"
               if c.execute("SELECT 1 FROM sqlite_master WHERE name='ema_checks'").fetchone() else "")
        for cid, lk, grp, payer in c.execute(
                f"""SELECT x.cid, y.link, y.payer_group, y.payer FROM temp.d837_pp x JOIN ema_line_payments y ON y.link = x.link
                    WHERE {ec.answer_row_sql('y')} {sep}"""):
            if not self._is_secondary_posting(lk, payer, grp, groups.get(cid, []), names):
                got.add(cid)
        c.execute("DROP TABLE IF EXISTS temp.d837_pp")
        return got

    def _lines_837(self, claims: dict[str, str]) -> dict[str, list[tuple]]:
        """The service lines of these 837 claims ({claim_837_key: claim id}): {claim_837_key: [(code, date, charge, units,
        modifiers)]}, by the claim id's index (lines_837 has none on its own key)."""
        out: dict[str, list[tuple]] = defaultdict(list)
        c = self.conn
        items = sorted(claims.items())
        for i in range(0, len(items), 500):
            part = items[i:i + 500]
            c.execute("DROP TABLE IF EXISTS temp.d837_k")
            c.execute("CREATE TEMP TABLE d837_k (k TEXT PRIMARY KEY, pcn TEXT)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_k VALUES (?, ?)", part)
            for key, code, dos, chg, units, mods in c.execute(
                    """SELECT l.claim_837_key, l.procedure_code, l.service_date, l.charge_cents, l.units, l.modifiers
                       FROM temp.d837_k x JOIN lines_837 l ON l.patient_control_number = x.pcn AND l.claim_837_key = x.k
                       ORDER BY l.claim_837_key, l.line_seq"""):
                if code:
                    out[key].append((code, real_day(dos), abs(chg or 0), units, mods))
        c.execute("DROP TABLE IF EXISTS temp.d837_k")
        return out

    def run_ema_open_claims(self) -> None:
        """Claims EMA shows sent to a payer and unpaid that no payer answered: no 835 for the claim, none for its bill since
        it was sent, no ERA line posted in EMA for it, nothing paid by insurance in EMA, and not voided, self-pay, denied or
        (by EMA's own status) settled or not yet sent. Only the latest submission of a bill that went out counts (an earlier
        one was superseded). When in doubt a claim stays listed: a visit leaves it only for a payment that still stands on
        that visit's own line under another bill, or because the same visit is chased under a newer bill. One counts once
        NO_ANSWER_DAYS have passed since it was sent and the remittances on file reach that day, so a feed that stopped does
        not make every newer claim look unanswered (NO_ANSWER, "Chase the payer"). One EMA calls rejected never reached the
        payer and counts at once (REJECTED_PRE_PAYER, "Fix and resubmit"). One EMA shows closed or written off is listed
        all the same, marked written off in EMA. Each is sized by the payer's usual rate for its codes and units, and dated
        by the payer's first-filing limit from the date of service; a claim sent on time runs the payer's appeal window past
        that limit (except Medicare, whose year is firm). Both signals of one claim share its id, so the work people record
        follows the claim from one to the other; save() hands it to the claim's own answer or its later submission."""
        c = self.conn
        self.payers_behind = []
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "ema_rows" not in tables:
            return
        newest, oldest = c.execute("SELECT MAX(payment_date), MIN(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()
        if not newest:
            return
        # the day the remittances on file reach, for most of the volume: one stray newer file (or one dated in the future)
        # from one payer says nothing of the others
        newest = feed_date(c) or newest
        # as far back as an item can still be open (a claim sent on time runs the longest appeal window, 730 days, past the
        # longest first-filing limit, 365); and nothing sent before the remittances on file begin can be judged unanswered
        since = _add_days(newest, -(365 + NO_ANSWER_DAYS + 730 + 5))
        recent = _add_days(newest, -400) or ""
        # answered: a remittance in which a payer processed the claim as its primary (CLP02 1, 19, 4 or 22), or the payer's
        # answer posted in EMA (its 835 may never have reached us). A claim that only another payer answered, processed as
        # secondary (CLP02 2 or 3, a crossover) or otherwise, still waits for its primary's remittance (trace finding T-C2):
        # it counts as answered only when that payer is the one EMA billed, which is known once EMA's rows are read (below).
        answered: set[str] = set()
        bill_last: dict[str, str] = {}          # each bill's newest answer of any kind (a submission sent again went out after it)
        bill_answered: dict[str, str] = {}      # each bill's newest answer that counts
        # a bill whose only answers are one payer's denial as primary because another payer covers it first and then its
        # processing as secondary: the primary's remittance is missing (the coordinator's addition to T-I2), so neither of
        # those answers is the claim's own, whoever EMA billed
        cob = self._cob_only()
        for pcn, paid_on, primary in c.execute("""SELECT cl.patient_control_number, MAX(t.payment_date), MAX(cl.claim_status_code IN ('1','19','4','22'))
                                                  FROM claims cl JOIN transactions t ON t.transaction_key = cl.transaction_key
                                                  WHERE COALESCE(t.superseded,0)=0 AND cl.patient_control_number IS NOT NULL GROUP BY 1"""):
            b = bill_of(pcn)
            bill_last[b] = max(bill_last.get(b, ""), paid_on or "")
            if primary and b not in cob:
                answered.add(pcn)
                bill_answered[b] = max(bill_answered.get(b, ""), paid_on or "")
        second: dict[str, list[tuple]] = defaultdict(list)     # claim id -> the other payers' answers: (date, payer group, paid, CLP02)
        for pcn, st, paid_on, name, cfi, paid in c.execute(
                """SELECT cl.patient_control_number, cl.claim_status_code, t.payment_date, t.payer_name, cl.claim_filing_indicator, cl.paid_cents
                   FROM claims cl JOIN transactions t ON t.transaction_key = cl.transaction_key
                   WHERE COALESCE(t.superseded,0)=0 AND cl.patient_control_number IS NOT NULL
                     AND COALESCE(cl.claim_status_code, '') NOT IN ('1','19','4','22')"""):
            if pcn not in answered:
                second[pcn].append((paid_on or "", payer_group_kind(name, cfi)[0], paid or 0, st or ""))
        for cid, payer, when in c.execute("""SELECT claim_id, payer, MAX(COALESCE(transaction_date, posted_date, '')) FROM ema_rows
                                             WHERE family='era_lines' AND claim_id IS NOT NULL GROUP BY 1, 2"""):
            cb = cob.get(bill_of(cid))
            if cb and payer and payer_company(normalize_payer(payer)[0]) == payer_company(cb["payer"]):
                continue                    # EMA's posting of that payer's own answers, as secondary: not the primary's
            answered.add(cid)
            b = bill_of(cid)
            bill_last[b] = max(bill_last.get(b, ""), when or "")
            bill_answered[b] = max(bill_answered.get(b, ""), when or "")
        # EMA payer names that stand for the VA's network: at least 20 rows carry a payer id, and 90 percent of those are
        # the VA's (the rule ema_reports.learn_payer_aliases uses), so a claim of that name with no id of its own is the VA's
        ids_of: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for payer, pid, n in c.execute("""SELECT payer, UPPER(TRIM(payer_id)), COUNT(*) FROM ema_rows
                                          WHERE payer IS NOT NULL AND payer_id IS NOT NULL AND TRIM(payer_id) <> '' GROUP BY 1, 2"""):
            t = ids_of[" ".join(payer.split()).upper()]
            t[0] += n
            t[1] += n if pid in VA_PAYER_IDS else 0
        va_names = {name for name, (n, va) in ids_of.items() if n >= 20 and va >= 0.9 * n}
        del ids_of
        # the visit lines a payment still stands on: on the remittances (takebacks applied), or in EMA's ERA lines
        paid_lines: set[int] = set()
        for cid, cpt, dos in c.execute("""SELECT claim_id, cpt, service_date FROM ema_rows WHERE family='era_lines' AND claim_id IS NOT NULL
                                          GROUP BY 1, 2, 3 HAVING SUM(COALESCE(payments_cents,0)) > 0"""):
            paid_lines.add(hash((cid, cpt, dos)))
        payer_last: dict[str, str] = {}
        pcn_payer: dict[str, tuple[str, str]] = {}
        for l in self.lines:
            if (self.standing.get(l["line_key"]) or 0) > 0:
                paid_lines.add(hash((l["patient_control_number"], l.get("original_procedure_code") or l.get("procedure_code"),
                                     l.get("service_date") or l.get("statement_from"))))
            g, d = l.get("payer_group"), l.get("payment_date") or l.get("txn_payment_date") or ""
            if g and d > payer_last.get(g, ""):
                payer_last[g] = d
            # what the remittances call each claim's payer: its primary answer (a crossover to a supplement is not it)
            if l.get("claim_status_code") in ("1", "4", "19"):
                pcn_payer.setdefault(l["patient_control_number"], (l.get("payer_group") or "Unknown", l.get("payer_kind") or "other"))
        name_last: dict[str, str] = {}
        for name, d in c.execute("SELECT payer_name, MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0 GROUP BY 1"):
            g = normalize_payer(name)[0]
            name_last[g] = max(name_last.get(g, ""), d or "")
        # when each export was pulled: the newest says what a claim's status and payments are now. A spreadsheet's own creation
        # time where it has one; else (a CSV) the newest date among its rows, which no export can be older than and which does
        # not depend on the order files were uploaded in; then when it was recorded
        made: dict[str, tuple[str, str]] = {}
        if "ema_sources" in tables:
            made = {sha: (at or "", rec or "") for sha, at, rec in c.execute("SELECT sha256, created_at, recorded_at FROM ema_sources")}
        src_at: dict[str, tuple[str, str]] = {}
        for sha, newest_in in c.execute("""SELECT source_sha256, MAX(MAX(COALESCE(claim_submission_date,'')), MAX(COALESCE(denial_date,'')),
                                                  MAX(COALESCE(bill_created,'')), MAX(COALESCE(transaction_date,'')),
                                                  MAX(COALESCE(posted_date,'')), MAX(COALESCE(service_date,'')))
                                           FROM ema_rows WHERE family IN ('charge_lines', 'claim_status') GROUP BY 1"""):
            at, rec = made.get(sha, ("", ""))
            src_at[sha] = (at or newest_in or "", rec)
        # the day EMA's exports reach (the newest one pulled, never after today): what a claim still in the print batch is
        # dated against (trace finding T-N11)
        export_day = min(max((str(v[0])[:10] for v in src_at.values() if v[0]), default="") or self.today, self.today)
        votes: dict[str, Counter] = defaultdict(Counter)
        voted: set[str] = set()
        # each visit (patient, date, code) a payment still stands on, and under which bill, by the part of the service the
        # paid line bills (26, TC or the whole: a payment for one part pays nothing of the other, T-C1)
        paid_visits: dict[int, dict[str, set[str]]] = {}

        def visit_paid(patient: str, x: tuple, b: str) -> bool:
            """Whether a payment stands on the visit of this line (code, date, charge, units, item id, modifiers) under
            another bill than b, for the line's own part of the service (part_fits)."""
            got = paid_visits.get(hash((patient, x[1], x[0])))
            return bool(got) and part_fits(item_part(x), {p for p, bills in got.items() if bills - {b}})
        claims: dict[str, dict] = {}
        for (cid, fam, act, item_id, cpt, mods, units, charges, ins_paid, paid, payer, payer_id, patient, provider, dos, created,
             status, _state, sent, denied_on, sha, row, method) in c.execute(
                """SELECT claim_id, family, activity_type, bill_item_id, cpt, modifiers, units, charges_cents, insurance_payments_cents,
                          payments_cents, payer, payer_id, patient_id, provider, service_date, bill_created, claim_status, claims_state,
                          claim_submission_date, denial_date, source_sha256, row_index, payment_method
                   FROM ema_rows WHERE claim_id IS NOT NULL AND family IN ('charge_lines', 'claim_status')
                     AND (service_date IS NULL OR service_date >= ?)""", (since,)):
            act = (act or "").lower()
            billing = fam == "claim_status" or act == "charge"       # a row that says who the claim is billed to
            if cid in answered:
                if cid not in voted and payer and billing and cid in pcn_payer:
                    votes[payer][pcn_payer[cid]] += 1
                    voted.add(cid)
                if act == "charge" and cpt and patient and dos and hash((cid, cpt, dos)) in paid_lines:
                    paid_visits.setdefault(hash((patient, dos, cpt)), {}).setdefault(component_of(mods), set()).add(bill_of(cid))
                continue
            k = claims.get(cid)
            if k is None:
                k = claims[cid] = {"charges": {}, "void_ids": set(), "void_sigs": set(), "ins": {}, "paid_rows": 0, "pat_paid": 0, "amb_paid": 0,
                                   "status_items": {}, "payers": Counter(), "payer_ids": Counter(), "patients": Counter(),
                                   "providers": Counter(), "days": set(), "created": "", "sent": "", "status": None, "denied": "",
                                   "status_at": ("", "", -1)}
            at = src_at.get(sha, ("", "")) + (row if row is not None else -1,)
            if billing:
                if payer:
                    k["payers"][payer] += 1
                if payer_id:
                    k["payer_ids"][payer_id] += 1
            if patient:
                k["patients"][patient] += 1
            # the claim's status as the newest export that gives one says (its Claims State is a US state, not a status,
            # and a row carrying only that says nothing about the claim)
            if status and at >= k["status_at"]:
                k["status"], k["denied"], k["status_at"] = status, denied_on or "", at
            if sent:
                k["sent"] = max(k["sent"], sent)
            if dos:
                k["days"].add(dos)
            if fam == "claim_status":
                if cpt:     # a claim known from the status report alone is still a claim
                    k["status_items"][(cpt, dos)] = (cpt, dos, abs(charges or 0), None, mods, units)
                    if charges is None:     # its charge is not known: never read as a line billed at $0 (answers-3)
                        k.setdefault("no_charge", set()).add((cpt, dos))
                continue
            sig = (cpt, dos, mods, abs(units) if units is not None else None, abs(charges or 0))
            if charges is None and act == "charge":
                k.setdefault("no_charge", set()).add(sig)
            if "void" in act and "payment" not in act and "adjust" not in act:     # a voided charge (not a voided payment)
                if item_id:
                    k["void_ids"].add(item_id)
                else:
                    k["void_sigs"].add(sig)
                continue
            if "payment" in act:
                # the insurer's when the row's Insurance Payments says so, or (a layout without that column) its activity or payment
                # method does; a copay or a card payment is the patient's; a row naming only the payer is not decided either way.
                # Signed: a payment taken back (a voided or reversed one) cancels it
                if ins_paid is not None:
                    k["paid_rows"] += ins_paid
                    k["pat_paid"] += (paid or 0) - ins_paid
                elif paid and "patient" not in act and INSURER_PAYMENT.search(f"{act} {method or ''}"):
                    k["paid_rows"] += paid
                elif paid and "patient" not in act and payer and not SELF_PAY.search(payer):
                    k["amb_paid"] += paid
                elif paid:
                    k["pat_paid"] += paid
                continue
            if act != "charge" or not cpt:
                continue
            key = item_id or sig
            k["charges"][key] = (cpt, dos, abs(charges or 0), item_id, mods, units)
            if ins_paid is not None or paid is not None:
                was = k["ins"].get(sig)             # by the charge itself: the two export layouts key it differently
                if was is None or at >= was[0]:     # the newest export's figures for this charge
                    k["ins"][sig] = (at, ins_paid or 0, (paid or 0) - (ins_paid or 0))
            if provider:
                k["providers"][provider] += 1
            if created:
                k["created"] = max(k["created"], created[:10])

        def group_of(k: dict) -> tuple[str, str]:
            """The payer group and kind of the payer EMA billed a claim to: the VA's network by its payer id (EMA names it after
            the network's administrator), else what the remittances call EMA's name for it (votes), else the name's own group."""
            ema_payer = k["payers"].most_common(1)[0][0]
            payer_id = k["payer_ids"].most_common(1)[0][0] if k["payer_ids"] else None
            if (payer_id or "").strip().upper() in VA_PAYER_IDS or (not payer_id and " ".join(ema_payer.split()).upper() in va_names):
                return VA_GROUP
            return votes[ema_payer].most_common(1)[0][0] if votes.get(ema_payer) else normalize_payer(ema_payer)

        # a claim only another payer answered is answered when that payer is the one EMA billed (the practice sent the claim
        # to it); otherwise the primary's remittance is missing, and the claim is chased like any claim no payer answered
        for cid in [x for x in claims if x in second and claims[x]["payers"]]:
            k = claims[cid]
            if group_of(k)[0] not in {g for _d, g, _p, _s in second[cid]} or bill_of(cid) in cob:
                continue                    # (a payer that denied it as primary for coordination answered as secondary only)
            answered.add(cid)
            b = bill_of(cid)
            bill_answered[b] = max([bill_answered.get(b, "")] + [d for d, _g, _p, _s in second[cid]])
            pat = k["patients"].most_common(1)[0][0] if k["patients"] else None
            for v in k["charges"].values():
                if pat and v[0] and v[1] and hash((cid, v[0], v[1])) in paid_lines:
                    paid_visits.setdefault(hash((pat, v[1], v[0])), {}).setdefault(component_of(v[4]), set()).add(b)
            del claims[cid]

        def went_out(k: dict) -> bool:
            """EMA shows the claim sent (a rejected claim went out and came back), not on hold, ready, voided or cancelled."""
            st = k["status"] or ""
            if re.search(r"VOID|CANCEL", st, re.I):
                return False
            return bool(EMA_REJECTED.search(st)) or not EMA_NOT_SENT.search(st)

        def live_items(k: dict, token: bool = False) -> list[tuple]:
            """One charge for each, though two export layouts list it (with and without its bill item id), less the voids:
            (code, date, charge, units, item id or None, modifiers as EMA gives them). A line billed at $0, or at a token
            charge under $1 (a post-operative visit EMA bills at a penny so the line goes out), is nothing owed, as in the
            837 pass (answers-3); a line whose charge is not known is not such a line. token: the lines billed above $0 and
            under $1 instead, which are part of the claim's content all the same (_claim_content)."""
            groups: dict[tuple, list] = {}
            for v in k["charges"].values():
                sig = (v[0], v[1], v[4], abs(v[5]) if v[5] is not None else None, v[2])
                g = groups.setdefault(sig, [set(), False])
                if v[3]:
                    g[0].add(v[3])
                else:
                    g[1] = True
            unknown = k.get("no_charge") or ()
            out = []
            for sig, (ids, loose) in groups.items():
                small = sig[4] < 100 and sig not in unknown
                if (not small or not sig[4]) if token else small:
                    continue
                keep = sorted(ids - k["void_ids"]) if ids else ([None] if loose else [])
                if sig in k["void_sigs"]:   # a void with no bill item id cancels one, unless a void by id already did
                    keep = keep[:max(0, len(ids) - max(len(ids & k["void_ids"]), 1))] if ids else []
                out += [(sig[0], sig[1], sig[4], sig[3], item, sig[2]) for item in keep]
            if not out and not k["charges"]:
                out = [(v[0], v[1], v[2], v[5], None, v[4]) for v in k["status_items"].values()
                       if ((0 < v[2] < 100 and (v[0], v[1]) not in unknown) if token else (v[2] >= 100 or (v[0], v[1]) in unknown))]
            return out

        def billed_nothing(k: dict) -> bool:
            """Every line EMA lists for the claim is billed at $0 or at a token charge under $1, and none has a charge that is
            not known (answers-3): nothing is owed on it."""
            unknown = k.get("no_charge") or ()
            sigs = [(v[0], v[1], v[4], abs(v[5]) if v[5] is not None else None, v[2]) for v in k["charges"].values()]
            if sigs:
                return all(s[4] < 100 and s not in unknown for s in sigs)
            st = list(k["status_items"].values())
            return bool(st) and all(v[2] < 100 and (v[0], v[1]) not in unknown for v in st)

        def order(pcn: str, k: dict) -> tuple:
            # a bill's submissions in the order they went out: its suffix (C017, C024, ...) first. A bill's creation date
            # belongs to every submission of it, so it can never say which came later
            m = _SUBMISSION.search(pcn)
            return (int(m.group(1)) if m else 0, k["sent"] or k["created"] or "", pcn)
        latest: dict[str, str] = {}
        before: dict[str, str] = {}             # the newest date the bill was sent, or EMA recorded the payer's denial of it
        # what the 837 claim files show sent: a claim's own newest send dates it, and a bill's newer submission the exports
        # do not list yet supersedes theirs (trace finding T-I4)
        s8 = self._load_837()
        sends837, bills837 = s8["sends"], s8["bills"]
        for pcn, k in claims.items():
            b = bill_of(pcn)
            for d in (k["sent"], k["denied"], (bills837.get(b) or [0, ""])[1]):
                if d:
                    before[b] = max(before.get(b, ""), d[:10])
            if not went_out(k):
                continue
            if b not in latest or order(pcn, k) > order(latest[b], claims[latest[b]]):
                latest[b] = pcn
        resent: dict[str, tuple[str, int]] = {}    # bill -> (day, number) of a newer submission nothing else chases
        for bx in list(latest):
            bi = bills837.get(bx)
            if not bi:
                continue
            if bi[2] and bi[2] > bi[1]:
                del latest[bx]              # the bill's newest send is a void: the practice cancelled it (T-N5)
            elif bi[0] > sub_no(latest[bx]):
                if bx in s8["prim_bills"]:
                    # a newer submission went out, but a payer answered an earlier one as its primary, so the 837 pass leaves
                    # the bill: nothing else chases the newer submission. The exports' submission stays listed, dated by the
                    # newer send (answers-5); a person's work follows to the newer one's item if one is listed later
                    resent[bx] = (bi[1], bi[0])
                else:
                    del latest[bx]          # a newer submission went out: it is chased or answered on its own (the 837 pass)
        # each bill's first and last submission numbers seen, answered or not: a higher one is a submission sent again, and
        # an answered submission newer than every unanswered one supersedes them all (its answer is worked from its remittance)
        first_sub: dict[str, int] = {}
        last_answered: dict[str, int] = {}
        for x in list(claims) + [x for x in answered if bill_of(x) in latest]:
            m = _SUBMISSION.search(x)
            if m:
                bx, n = bill_of(x), int(m.group(1))
                first_sub[bx] = min(first_sub.get(bx, n), n)
                if x in answered:
                    last_answered[bx] = max(last_answered.get(bx, n), n)
        for bx in list(latest):
            m = _SUBMISSION.search(latest[bx])
            if m and last_answered.get(bx, -1) > int(m.group(1)):
                del latest[bx]                  # sent again and answered: nothing on this bill is left to chase
        # EMA's own insurance payments on a visit line (the newest export's): that visit is paid under that bill
        for pcn, k in claims.items():
            pat = k["patients"].most_common(1)[0][0] if k["patients"] else None
            if not pat:
                continue
            for key, v in k["charges"].items():
                got = k["ins"].get((v[0], v[1], v[4], abs(v[5]) if v[5] is not None else None, v[2]))
                if v[1] and got and got[1] > 0:
                    paid_visits.setdefault(hash((pat, v[1], v[0])), {}).setdefault(component_of(v[4]), set()).add(bill_of(pcn))

        bill_svcs: dict[str, dict] = {}

        def answered_svcs(bill: str) -> dict[tuple, set]:
            """The services (date, code: {parts}) some remittance on file answered for a bill (any of its submissions), for
            a bill sent again after a payer answered it as primary (answers-5)."""
            if not bill_svcs:
                for l in self.lines:
                    bx = bill_of(l["patient_control_number"])
                    if bx in resent:
                        svc = l.get("service_date") or l.get("statement_from")
                        for code in {l.get("procedure_code"), l.get("original_procedure_code")} - {None, ""}:
                            bill_svcs.setdefault(bx, {}).setdefault((svc, code), set()).add(component_of(l.get("modifiers")))
                bill_svcs.setdefault("", {})
            return bill_svcs.get(bill) or {}
        cands: list[dict] = []
        behind: dict[str, dict] = {}        # payer group -> the claims due after its last remittance (PAYERS_BEHIND_META)
        for pcn, k in claims.items():
            b = bill_of(pcn)
            if latest.get(b) != pcn or not k["payers"]:
                continue                    # an earlier submission of a bill sent again, or one that never went out
            if sum(v[1] for v in k["ins"].values()) + k["paid_rows"] > 0:
                continue                    # EMA shows insurance paid it, net of anything taken back (its 835 may be missing)
            ema_payer = k["payers"].most_common(1)[0][0]
            if SELF_PAY.search(ema_payer):
                continue
            status = k["status"] or ""
            if k["denied"] or EMA_DENIED.search(status) or EMA_SETTLED.search(status):
                continue                    # denied (the payer answered), or settled, voided or with collections
            r8 = sends837.get(pcn)          # what the 837s show sent for this claim id
            if r8 is not None and r8[3]:
                continue                    # its newest 837 is a void: the practice cancelled the claim (T-N5)
            rejected = bool(EMA_REJECTED.search(status))
            seen_on = str(k["status_at"][0] or "")[:10]         # the day of the newest export that gave its status
            if rejected and r8 is not None and seen_on and r8[0] > seen_on:
                rejected = False            # sent again (the 837s show it) after EMA's export last called it rejected
            # in EMA's print batch with no submission date: never sent, unless an 837 shows it went out electronically (T-I4).
            # It is listed to be sent, not chased, once it has waited NO_ANSWER_DAYS since the bill was made (a print batch
            # goes out every few days)
            batched = not rejected and not k["sent"] and bool(EMA_PRINT_BATCH.search(status))
            unsent = batched and r8 is None
            gave_up = bool(EMA_GAVE_UP.search(status))
            items, tokens = live_items(k), live_items(k, token=True)
            if not items and billed_nothing(k):
                self.ema_counts["export_billed_nothing"] += 1
                continue                    # every line billed at $0 or a token charge: nothing is owed (answers-3)
            if not items and r8 is not None:    # EMA lists no line of it: the lines its 837 billed, as the 837 pass reads them
                l8 = self._lines_837({r8[7]: pcn}).get(r8[7], [])
                items = [(code, dos, chg, units, None, mods) for code, dos, chg, units, mods in l8 if (chg or 0) >= 100]
                tokens = [(code, dos, chg, units, None, mods) for code, dos, chg, units, mods in l8 if 0 < (chg or 0) < 100]
            patient = k["patients"].most_common(1)[0][0] if k["patients"] else None
            if patient:                     # a visit a payment still stands on under another bill is paid, for its own part
                items = [x for x in items if not (x[1] and visit_paid(patient, x, b))]
            if not items:
                continue                    # voided, or every visit on it paid under another bill
            days = sorted({x[1] for x in items if x[1]} or {d for d in k["days"] if d})
            if not days:
                continue
            sent, basis = ((k["sent"], SENT_SUBMITTED) if k["sent"] else (k["created"], SENT_BILLED) if k["created"] else (days[-1], SENT_DOS))
            m = _SUBMISSION.search(pcn)
            again_after = False
            if not k["sent"] and m and int(m.group(1)) > first_sub.get(b, int(m.group(1))):
                # a later submission EMA's exports give no date for yet: it went out after the bill's previous submission, after
                # the payer's answer to it, and after any denial EMA recorded, whatever date the bill was created
                nxt = _add_days(max(before.get(b, ""), bill_last.get(b, "")), 1)
                if nxt and nxt > sent:
                    sent, basis, again_after = nxt, SENT_AGAIN, True
            if r8 is not None and (not k["sent"] or r8[0] > k["sent"][:10]):
                # the day its newest 837 went out: where EMA gives no date, or the claim was sent again since (T-I4)
                sent, basis, again_after = r8[0], SENT_837, False
            again_837 = resent.get(b)
            if again_837 and again_837[0] > (sent or ""):
                # the bill went out again as a newer submission (the 837s show it) that nothing else chases: the payer's answer
                # is due from that send (answers-5). What it chases are the services no remittance of the bill answered (a
                # line added in the resend), as the 837 pass sizes a resend (fx-exports-r2-2)
                sent, basis, again_after = again_837[0], SENT_837, False
                done = answered_svcs(b)
                items = [x for x in items if not (x[1] and part_fits(item_part(x), done.get((x[1], x[0])) or set()))]
                if not items:
                    self.ema_counts["export_resent_all_answered"] += 1
                    continue                # every service on it was answered on an earlier submission of the bill
                days = sorted({x[1] for x in items if x[1]} or {d for d in k["days"] if d})
            else:
                again_837 = None
            if not sent or (sent < recent and sent < (oldest or "")):
                continue                    # sent before the remittances on file begin: nothing here can say it went unanswered
            grp, kind = self._open_claim_group(k, ema_payer, votes, va_names)
            due = _add_days(sent, NO_ANSWER_DAYS)
            if self.ema_on and due and not unsent and not rejected:
                # a payer that usually takes longer than that from the visit (EMA's reports measure it) is not late yet
                usual = _add_days(days[0], self._turnaround_days(grp))
                if usual and usual > due:
                    due = usual
            # the day this payer's answers are known through: the newest remittance, or EMA's postings when Production is
            # fresh and the payer posts there (ema_checks.answers_through, never before the feed's newest remittance)
            through = self._answers_through(grp, newest) if self.ema_on else newest
            if unsent:
                # never sent: it waits on the practice, not the payer. Dated against the day EMA's exports reach, not the
                # remittance feed (trace finding T-N11)
                if not due or due > export_day:
                    continue
            elif not rejected and not cob.get(b) and (not due or due > (through or newest)):
                # not due yet, or the answers on file do not reach the day it was due (a payer that answered as secondary
                # after a coordination denial applied the primary's): on the waiting view with its due day, where the 837
                # pass keeps one (_note_waiting, the fix wave's F3a; blindness is loud)
                note = getattr(self, "_note_waiting", None)
                if note is not None:
                    note(pcn, grp, sent, due, sum(x[2] or 0 for x in items))
                continue
            # Its own payer's answers must reach that day too (misuse check CH-8): its remittances, or EMA's postings for it when
            # they reach past the feed (through). When they stop before it while the feed goes on, nothing on file says whether
            # that payer answered. The claim is counted under its payer for the Data tab, not listed. A payer with no remittance
            # on file at all is judged by the feed date, with its own hint.
            last = payer_last.get(grp) or name_last.get(normalize_payer(grp)[0]) or name_last.get(normalize_payer(ema_payer)[0])
            known = max(last, through) if (last and through and through > newest) else last
            if not rejected and not unsent and last and due > known and not cob.get(b):
                held = behind.setdefault(grp, {"payer": grp, "since": last, "claims": 0, "billed_cents": 0})
                held["claims"] += 1
                held["billed_cents"] += sum(x[2] or 0 for x in items)
                continue
            cands.append({"pcn": pcn, "k": k, "b": b, "items": items, "patient": patient, "sent": sent, "basis": basis, "due": due,
                          "again_after": again_after, "rejected": rejected, "unsent": unsent, "gave_up": gave_up, "status": status,
                          "ema_payer": ema_payer, "group": (grp, kind), "grp": grp, "kind": kind, "through": through,
                          "payer_last": last, "s837": r8, "batched": batched, "cob": cob.get(b),
                          "token_lines": [(x[0], x[1], x[2]) for x in tokens],
                          "secondary": second.get(pcn) or [x for xs in (cob.get(b) or {}).get("answers", {}).values() for x in xs],
                          "resent_837": bool(r8 is not None and k["sent"] and basis == SENT_837) or bool(again_837),
                          "bill_resent_837": again_837,
                          "bill_answered": bill_answered.get(b) if (bill_answered.get(b) or "") >= sent else None})
        # one visit chased under two bills (a bill made again): only the newest carries it. The two parts of a service, the
        # reading (26) and the technical part (TC), billed on two bills are two services, each chased on its own. The patient
        # is EMA's patient id, and one person under two of EMA's patient ids (a second chart) is one patient by the keyed
        # name (_export_patients: the residual of fix7b)
        same = self._ema_step("export_patients", self._export_patients, cands) or {}
        newest_for: dict[int, tuple] = {}
        for cd in cands:
            who = same.get(cd["pcn"], cd["patient"])
            for x in cd["items"]:
                if who and x[1]:
                    h = hash((who, x[1], x[0], item_part(x)))
                    mark = (cd["sent"], cd["pcn"])
                    if h not in newest_for or mark > newest_for[h][0]:
                        newest_for[h] = (mark, cd["b"])
        final = []
        carried: dict[str, set] = {}        # claim id -> the newer claims that carry all its visits
        for cd in cands:
            b, patient = cd["b"], same.get(cd["pcn"], cd["patient"])
            items = [x for x in cd["items"]
                     if not (patient and x[1] and newest_for.get(hash((patient, x[1], x[0], item_part(x))), (None, b))[1] != b)]
            if not items:
                carried[cd["pcn"]] = {newest_for[hash((patient, x[1], x[0], item_part(x)))][0][1] for x in cd["items"]
                                      if patient and x[1]}
                continue                    # chased under the newer bill made for the same visit
            final.append((cd, items))
        if self.ema_on and final:
            # dated by the same feed date as every other step of this pass (answers-2: never one stray newer remittance)
            final = self._ema_check_open_claims(final, newest)
        for cd, items in final:
            self._emit_open_claim(cd, items, newest)
            if cd.get("bill_resent_837"):
                self._note_resent_bill(cd)
        for old, tos in sorted(carried.items()):
            # where the older claim's chase went, for the self-check that keeps an item no rule explains (folded)
            for to in sorted(tos):
                if _fid("EMA_OPEN_CLAIM", to) in self.findings:
                    self._fold_into(_fid("EMA_OPEN_CLAIM", to), _fid("EMA_OPEN_CLAIM", old), "exports_one_visit",
                                    f"The same visit was billed before on claim {old}: only this newer bill is chased")
                    break
        # what was listed, for the one-visit rule across the two passes once the 837 pass has listed its own claims
        # (_exports_yield_to_newer_bills, answers-1)
        self._export_final = (final, newest)
        self.payers_behind = sorted(behind.values(), key=lambda h: (-h["billed_cents"], h["payer"]))

    def _export_patients(self, cands: list[dict]) -> dict[str, str]:
        """One person under two of EMA's patient ids: for the weekly exports' claims that share a visit (date, code and part)
        under different patient ids, who each claim's patient is (_who: the remittances' names for its bill, EMA's patient
        through the claims the A/R or the bill map ties to an EMA claim, and the keyed name and birth date there). Patient
        ids whose claims are one person (_one_patient: the same keyed name and birth date, or the same keyed name under the
        same member id, as the 837 pass matches them) are one patient. Never the keyed name alone (a father and a son of one
        name), nor a member id's one remitted name (family members on one subscriber id): fx-exports-r2-3, r2-4. {claim id:
        the patient they share} for the claims so joined; every other claim keeps its own patient id."""
        visits: dict[tuple, set] = defaultdict(set)
        by = {cd["pcn"]: cd for cd in cands}
        for cd in cands:
            for x in cd["items"]:
                if cd["patient"] and x[1]:
                    visits[(x[1], x[0], item_part(x))].add(cd["pcn"])
        want = sorted({p for ps in visits.values() if len({by[q]["patient"] for q in ps}) > 1 for p in ps})
        if not want:
            return {}
        from . import ema_reports as er
        er.register_functions(self.conn, create=False)     # the keyed name (rw_name_key) the patient keys are read with
        who = self._who([dict(by[p], ema_patients={by[p]["patient"]}) for p in want], {})
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                x = parent[x]
            return x
        for ps in visits.values():
            named = sorted(p for p in ps if (who.get(p) or {}).get("names") or (who.get(p) or {}).get("persons"))
            for i, a in enumerate(named):
                for b in named[i + 1:]:
                    pa, pb = find(by[a]["patient"]), find(by[b]["patient"])
                    if pa != pb and self._one_patient(who[a], who[b]):
                        parent[max(pa, pb)] = min(pa, pb)
        return {p: find(by[p]["patient"]) for p in want}

    def _note_resent_bill(self, cd: dict) -> None:
        """An export claim kept because its bill went out again as a newer submission that nothing else chases (answers-5):
        its item names that submission and the day it went out (evidence and hint), so the biller asks the payer about it."""
        f = self.findings.get(_fid(cd.get("id_signal") or "EMA_OPEN_CLAIM", cd["pcn"]))
        if f is None:
            return
        day, n = cd["bill_resent_837"]
        newer = f"{cd['b']}C{n:03d}"
        try:
            ev = json.loads(f.get("evidence") or "{}")
        except ValueError as exc:
            from . import runlog
            runlog.caught(exc, "export_resent_bill", level="warning")
            ev = {}
        ev["bill_sent_again_837"] = {"day": day, "claim_id": newer}
        f["evidence"] = json.dumps(ev, default=str)
        note = (f"The 837s show this bill sent again on {day} as claim {newer}, after the payer answered an earlier submission of "
                f"it. No remittance answers {newer} and nothing else chases it: ask the payer about {newer}")
        f["hint"] = f"{f['hint']}. {note}" if f.get("hint") else note

    def price_unanswered_lines(self, grp: str | None, kind: str | None, year: str, lines) -> list[list]:
        """What each billed line of a claim no payer answered is worth at its payer, for NO_ANSWER and REJECTED_PRE_PAYER
        (and for any pass that prices billed lines the same way, such as one reading the 837s): from (code, date, charge,
        units, modifiers) per line, [code, date, charge, cents or None] in date and code order. Each line at the payer's own
        rate per unit for the part it bills (a 26 or TC line at the component's rate: trace finding T-N9), times its units,
        a second and later surgical procedure at the payer's multiple-procedure share, never above the line's own charge (the
        payer allows the lesser of the two); a code with no rate of the payer's own nor of its product class at the payer's
        own share of the practice's charges (no_rate_size). The modifiers may be a list, EMA's JSON list or a comma list."""
        priced = []
        for cpt, dos, charge, units, mods in sorted(lines, key=lambda x: (x[1] or "", x[0] or "")):
            if isinstance(mods, str):
                mods = re.findall(r"[A-Za-z0-9]{2}", mods)      # EMA's JSON list or a comma list: modifiers are two characters
            line = {"payer_group": grp, "payer_kind": kind, "year": year, "provider_tier": "unknown", "pos_class": "office",
                    "modifiers_list": list(mods or [])}
            rate, _basis, _r = self.usual_rate(line, cpt)
            priced.append([cpt, dos, charge, rate, max(1, int(abs(units))) if units else 1, line])
        # a second and later surgical procedure at this payer's usual share, as the payer would price them
        surg = sorted((p for p in priced if p[3] and is_surgical(p[0]) and not _no_charge(p[2])), key=lambda p: -p[3])
        for rank, p in enumerate(surg, 1):
            if rank >= 2:
                p[3] = int(round(p[3] * self.mppr_pct({"payer_group": grp, "surg_rank": 2 if rank == 2 else 3})[0]))
        # a line whose known charge is $0, or a token under $1 (a post-operative visit EMA bills at a penny so the line goes
        # out), is worth nothing: the payer allows the lesser of its rate and the charge (answers-3). Only a line whose charge
        # is not known keeps the payer's rate.
        return [[cpt, dos, charge, 0 if _no_charge(charge) else
                 (min(rate * units, charge) if charge is not None else rate * units) if rate
                 else self.no_rate_size(line, cpt, charge, units)[0]]
                for cpt, dos, charge, rate, units, line in priced]

    def _emit_open_claim(self, cd: dict, items: list[tuple], newest: str) -> bool:
        """One claim no payer answered (run_ema_open_claims): its payer group, size, filing date and item. A claim still in
        EMA's print batch (cd["unsent"]) was never sent: it is listed under Fix and resubmit, dated by the first-filing limit.
        A claim only another payer answered (cd["secondary"]: its answers, processed as secondary or otherwise) says so, and
        is sized at what is left once that payer's payment is taken off. Whether an item was made: a claim the patient paid
        in full after EMA closed it, a claim billed at nothing (every line under $1) and one a second payer paid all of are
        not listed."""
        k, pcn, sent, due, rejected, gave_up, status, ema_payer = (cd["k"], cd["pcn"], cd["sent"], cd["due"], cd["rejected"], cd["gave_up"],
                                                                   cd["status"], cd["ema_payer"])
        unsent = bool(cd.get("unsent"))
        if cd.get("source") != "837" and not k.get("charges"):
            # a claim EMA's weekly exports show only in the claim-status export has no charge on file: its lines' charge is
            # not known (the exports' pass carries it as 0), so it is priced at the payer's rate, never as a line billed at
            # nothing (answers-3)
            items = [(x[0], x[1], x[2] or None) + tuple(x[3:]) for x in items]
        days = sorted({x[1] for x in items if x[1]} or {d for d in k["days"] if d})
        payer_id = k["payer_ids"].most_common(1)[0][0] if k["payer_ids"] else None
        grp, kind = cd["group"]             # the VA's network by its payer id, though EMA names its administrator: the VA's limits apply
        billed = sum(x[2] or 0 for x in items)
        pat_paid = max(0, k["pat_paid"] + sum(v[2] for v in k["ins"].values()))
        if gave_up and billed and pat_paid >= billed:
            return False                    # closed in EMA because the patient paid it all
        year = days[0][:4]
        # one pricing for every never-answered pass (the weekly exports' and the 837s'): each line at the payer's own rate for
        # the part it bills (26, TC or the whole: T-N9), times its units, the multiple-procedure share, capped at its charge
        billed_lines = sorted(((cpt, dos, charge, units, more[0] if more else None) for cpt, dos, charge, units, _item, *more in items),
                              key=lambda x: (x[1] or "", x[0] or ""))
        lines = self.price_unanswered_lines(grp, kind, year, billed_lines)
        for ln, src in zip(lines, billed_lines):
            comp = component_of(src[4])
            if comp:
                ln.append(comp)             # the part the line bills, for its story and the checks
        # the modifiers that make a line a separate service (59, XS, RT, ...): the one-visit rule compares two bills' lines by
        # them, and an exports' claim's item is the only record of its own when its 837 is not on file (fx-837-r2-4)
        # (as [the line's place in 'lines', '59,RT']: the priced lines keep the billed lines' order)
        sep = [[i, ",".join(_visit_line((src[0], src[1], None, None, None, src[4]))[3])] for i, src in enumerate(billed_lines)]
        sep = [x for x in sep if x[1]]
        if all(_no_charge(x[2]) for x in items):
            # every line billed at $0 or a token under $1: nothing is owed on the claim, so it is not chased (answers-3);
            # counted for the maintainers (meta ema:wiring)
            self.ema_counts["open_claim_billed_nothing"] += 1
            return False
        expected = sum(x[3] for x in lines if x[3]) if any(x[3] for x in lines) else None
        if expected and billed and expected > billed:
            expected = billed               # never valued above what was billed for it
        codes = sorted({x[0] for x in items})
        top = max(items, key=lambda x: x[2] or 0)[0]
        claim = {"patient_control_number": pcn, "payer_name": ema_payer, "payer_group": grp, "payer_kind": kind,
                 "payer_id": payer_id, "service_date": days[0],
                 "year": year, "procedure_code": top, "charge_cents": billed or None,
                 "rendering_provider_name": k["providers"].most_common(1)[0][0] if k["providers"] else None}
        last = cd.get("payer_last")         # its payer's last remittance, or none (a claim sent is due on or before it: CH-8)
        ev = {"codes": codes, "billed_cents": billed or None, "sent": sent, "sent_basis": cd["basis"], "answer_due": due,
              "newest_remittance": newest, "ema_status": status or None, "payer_in_ema": ema_payer, "lines": lines,
              "days_waiting": _days_between(sent, newest), "payer_last_remittance": last}
        if unsent:
            ev["not_sent"] = True
        if cd.get("ema_bare_zero"):
            ev["ema_bare_zero"] = cd["ema_bare_zero"]       # the $0 row on a check EMA shows, which is no answer
        if sep:
            ev["line_modifiers"] = sep
        r8 = cd.get("s837")
        if r8 is not None:
            # the 837 behind the send date: its day, frequency, payer, how many times the claim id was sent, and the store's
            # key of the claim in its file (a person finds the file by it)
            ev["sent_837"] = {"day": r8[0], "first": r8[1], "frequency": r8[2] or None, "payer_id": r8[4], "payer": r8[5],
                              "sends": r8[9], "claim_837_key": r8[7]}
            got = (self._837_send_info([r8[7]]).get(r8[7]) or {}) if r8[7] else {}
            resp = (got.get("payer_resp") or "").strip().upper()
            if got.get("read_version"):
                ev["sent_837"].update(payer_resp=resp or None, filing_indicator=got.get("filing_indicator"))
            from .x12_837 import later_payers_send
            if later_payers_send(resp, got):
                # sent to this payer after another payer answered it (its secondary or later payer: SBR01, and the loop of
                # the payer before it): it owes at most what that payer left to the patient (AMT*EAF, else that payer's PR
                # adjustments), never its own full rate. A claim sent to its primary (SBR01 P) that lists the secondary in
                # its other-payer loop, as the guide asks, is the primary's (fx-837-r1-4, r2-1)
                left = got["other_left_cents"] if got.get("other_left_cents") is not None else got.get("other_pr_cents")
                ev["sent_as"] = {"place": resp or None, "other_payer": got.get("other_payer_name"),
                                 "other_paid_cents": got.get("other_paid_cents"), "left_cents": left}
                if left is not None and expected is not None and left < expected:
                    expected = max(0, left)
                if left is not None and left <= 0:
                    # the payer before it left the patient nothing: this payer owes nothing on the claim
                    self.ema_counts["open_claim_second_send_nothing_left"] += 1
                    return False
        # only another payer answered (trace finding T-C2): what it paid comes off the item's size, and the item says that the
        # primary's remittance is missing
        sec = cd.get("secondary") or []
        other, short = "", expected
        if sec:
            sec_paid, sec_on, sec_payer = sum(p for _d, _g, p, _s in sec), max(sec)[0], max(sec)[1]
            as_second = all(s in ("2", "3", "20", "21") for _d, _g, _p, s in sec)
            ev["secondary_answer"] = {"payer": sec_payer, "date": sec_on, "paid_cents": sec_paid, "processed_as": "secondary" if as_second
                                      else "not as the primary payer (CLP02 " + ", ".join(sorted({s for _d, _g, _p, s in sec})) + ")"}
            other = f"; only {sec_payer} answered, " + ("as secondary" if as_second else "not as the primary payer")
            if expected and sec_paid > 0:
                # what is left once that payer's payment is taken off (T-C2), never below nothing (answers-6)
                short = max(0, expected - sec_paid)
                if short == 0:
                    # the second payer paid all the claim's payer would have allowed (its whole allowed amount went to the
                    # patient's deductible, say): nothing of the visit is left to collect, so it is not listed as owed money.
                    # The primary's remittance is still missing from the books: counted with its dollars (meta ema:wiring)
                    self.ema_counts["open_claim_second_paid_all"] += 1
                    self.ema_counts["open_claim_second_paid_all_cents"] += sec_paid
                    return False
        oe = cd.get("own_earlier")
        if oe and oe[2] > 0 and short:
            # its own payer already paid on an older submission of the bill, as a later payer: that money came in for the visit,
            # so it comes off what is left, as another payer's does (T-C2), never below nothing (fx-837-r2-3)
            short = max(0, short - oe[2])
            if short == 0:
                self.ema_counts["open_claim_own_payer_paid_all"] += 1
                return False
        cob = cd.get("cob")
        if cob and sec:
            # the payer it was sent to denied it as primary because another payer covers it first, then answered as secondary:
            # the money the primary payer owes is what is chased (the coordinator's addition to T-I2)
            ev["secondary_answer"].update(after_coordination_denial=True, denied_as_primary_on=cob["denied_on"], reason=cob["reason"])
        if cd.get("ema_bridge"):
            ev["ema_bridge"] = cd["ema_bridge"]         # how EMA's claim for it was found: its claim id, its bill or its content
        if cd.get("through") and cd["through"] != newest:
            ev["answers_through"] = cd["through"]       # EMA's postings show this payer's answers through this day
        label = f"{', '.join(codes[:5])}{'…' if len(codes) > 5 else ''}"
        # the visit's first send: on an older bill a newer one carries (cd visit_first), or this claim's own first send when
        # it went out again since (the 837s show every send of a claim id): sent on time there, the visit keeps its timely
        # filing (fx-837-r1-2). Only a send to the payer its newest send went to counts (one claim id may go to two payers).
        # A claim rejected before it reached the payer may have been rejected on that first send too, so only an older
        # bill's send counts for it
        own_first = None
        if r8 is not None and r8[1] and sent and r8[1] < sent and not rejected:
            d = self._first_send_to(pcn, r8, r8[4], r8[5])
            own_first = (d, pcn) if d and d < sent else None
        visit_first = min((x for x in (cd.get("visit_first"), own_first) if x), default=None)
        dt = self._open_claim_dates(grp, kind, ema_payer, payer_id, days[0], sent, due, cd["basis"], rejected, unsent,
                                    visit_first, pcn)
        timely, deadline, dbasis, file_legal = dt["timely"], dt["deadline"], dt["basis"], dt["legal"]
        soon = bool(dt["first"]) and dt["first"] <= _add_days(newest, 30)
        if rejected or unsent:
            made = f" (bill made {sent})" if cd["basis"] == SENT_BILLED else ""
            title = (f"In EMA's print batch, never sent to {grp}{made}: {label}" if unsent else
                     f"Rejected before reaching {grp} (EMA: {status}): {label}")
            self._emit("REJECTED_PRE_PAYER", "denial", "high" if soon else "medium", "correct_and_resubmit", title, None, expected,
                       short, None, None, ev, key_parts=(pcn,), claim=claim, id_signal=cd.get("id_signal") or "EMA_OPEN_CLAIM")
        else:
            said = f" (EMA: {status})" if gave_up else ""
            waited = (f"the primary payer's remittance is missing ({sec_payer} denied it as primary, another payer covers it first, "
                      f"then answered as secondary){said}: {label}" if cob and sec else
                      f"no answer from {grp} in {ev['days_waiting']} days{other}{said}: {label}")
            if cd["basis"] == SENT_837:
                # the 837 shows the day it went out: sent again since the exports' date, or (still in EMA's print batch)
                # electronically before EMA batched it for printing (trace finding T-I4)
                title = (f"Sent electronically {sent}, before EMA batched it for printing; {waited}" if cd.get("batched") else
                         f"Sent again electronically {sent}, {waited}" if cd.get("resent_837") else
                         f"Sent electronically {sent}, {waited}")
            else:
                title = (f"Sent again after {_add_days(sent, -1)}, no answer from {grp} yet{other}{said}: {label}" if cd["again_after"] else
                         f"Visit of {sent}, no answer from {grp} in {ev['days_waiting']} days, and EMA gives no date it was sent{other}{said}: {label}"
                         if cd["basis"] == SENT_DOS else
                         f"Sent {sent}, {waited}")
            self._emit("NO_ANSWER", "denial", "high" if soon else "medium", "review", title, None, expected, short,
                       None, None, ev, key_parts=(pcn,), claim=claim, id_signal=cd.get("id_signal") or "EMA_OPEN_CLAIM")
        f = self.findings[_fid(cd.get("id_signal") or "EMA_OPEN_CLAIM", pcn)]
        f["appeal_deadline"], f["deadline_basis"] = deadline, dbasis
        if not timely:
            # dated by the first-filing limit: the payer's own last day beside it where later (42 CFR 424.44(c): a weekend or a
            # Federal holiday moves Medicare's to the next workday; Ambetter's 180 days for a participating provider)
            f["legal_deadline"] = file_legal if file_legal and deadline and file_legal > deadline else None
        f["source"] = cd.get("source") or "ema"
        if cd.get("ema_link") is not None:
            f["ema_link"] = cd["ema_link"]
        self._export_claims.add(pcn)
        if gave_up and billed > pat_paid:   # given up in EMA though no payer answered: what the patient did not pay, written off
            owed = billed - pat_paid
            f["written_off_cents"] = min(expected, owed) if expected else owed
            f["written_off_on"] = (k["status_at"][0] or "")[:10] or None
        hints = []
        if sec and cob:
            hints.append(f"{sec_payer} denied this claim as the primary payer on {cob['denied_on']} (another payer covers it first: "
                         f"{cob['reason']}) and processed it as secondary on {sec_on}, "
                         + (f"paying ${sec_paid/100:,.2f}" if sec_paid > 0 else "paying nothing") + ". The primary payer's remittance is "
                         f"not on file: find which payer covers the patient first (EMA's coverage, or ask {sec_payer}), then get its "
                         "remittance from its portal or ask it to send it again")
        elif sec and cd.get("sec_bills"):
            # only another payer answered the visit, on the bill the practice made again (fx-837-r1-1)
            again_on = ", ".join(sorted(cd["sec_bills"]))
            hints.append(f"The practice billed this visit again as claim {again_on}, and {sec_payer} answered that claim on {sec_on} "
                         + ("as secondary" if as_second else "but not as its primary payer")
                         + (f", paying ${sec_paid/100:,.2f} for these services" if sec_paid > 0 else ", paying nothing")
                         + f". The primary's answer, from {grp}, is not on file: look this claim up on the payer's portal, or ask the "
                         "payer to send its remittance again, before sending the claim again")
        elif sec:
            hints.append((f"{sec_payer} processed this claim as secondary on {sec_on}" + (f" and paid ${sec_paid/100:,.2f}, its share" if sec_paid > 0 else ", paying nothing")
                          if as_second else f"{sec_payer} answered this claim on {sec_on}, but not as its primary payer"
                          + (f", and paid ${sec_paid/100:,.2f}" if sec_paid > 0 else ""))
                         + f". The primary's remittance, from {grp}, is not on file: look it up on the payer's portal, or ask the "
                         "payer to send it again, before sending the claim again")
        if cd.get("status_context"):
            hints.append(cd["status_context"] + ", but no payment, $0 answer or payer's reason is posted on it: check the payer's portal "
                         "before you send it again")
        if cd.get("ema_bare_zero"):
            from . import ema_checks as ec
            hints.append(ec.bare_zero_note(cd["ema_bare_zero"]))
        if unsent:
            hints.append("EMA shows it waiting in its print batch, with no date it was sent: print and mail it, or send it electronically")
        if cd.get("added_after_answer"):
            hints.append(f"This bill was answered on {cd['bill_answered']}; it went out again on {sent} with "
                         f"{'this service' if len(codes) == 1 else 'these services'} ({label}), which no remittance of the bill has "
                         "answered: look the resent claim up on the payer's portal")
        elif cd.get("bill_answered"):
            hints.append(f"Another submission of this bill was answered on {cd['bill_answered']}: if that answer was for this claim, "
                         "work it from that remittance")
        if oe:
            # its own payer's answer to an older submission of the bill, or to this claim before it went out again, is neither
            # this send's answer nor another payer's (fx-837-r2-3): said, so the biller sees the payer has the bill on file
            how = (" as secondary" if oe[3] in ("2", "3", "20", "21") else f" (claim status {oe[3]})" if oe[3] else "")
            what = (f"this claim on {oe[0]}{how}, before it was sent again" if oe[1] == pcn else
                    f"an earlier submission of this bill, claim {oe[1]}, on {oe[0]}{how}")
            hints.append(f"{grp} answered {what}" + (f", paying ${oe[2]/100:,.2f} (taken off this item)" if oe[2] > 0 else ", paying nothing")
                         + f"; nothing answered the claim sent {sent}: look it up on the payer's portal")
        if cd.get("older_bills"):
            older = ", ".join(f"{p} (sent {d}{' to ' + g if g else ''})" for d, p, g in sorted(cd["older_bills"], key=lambda t: (t[0], t[1])))
            hints.append(f"This visit was billed before on claim {older}, which no payer answered as primary: the practice made the "
                         "bill again, so only this newer bill is chased")
        sa = ev.get("sent_as")
        if sa:
            place = {"S": "secondary", "T": "tertiary"}.get(sa.get("place") or "", "second or later")
            who_ = sa.get("other_payer") or "the payer before it"
            paid_ = f" (paid ${sa['other_paid_cents'] / 100:,.2f})" if sa.get("other_paid_cents") is not None else ""
            hints.append(f"Sent to {grp} as its {place} payer after {who_} answered{paid_}: " + (
                f"{grp} owes at most what {who_} left to the patient, ${sa['left_cents'] / 100:,.2f}" if sa.get("left_cents") is not None else
                f"the 837 does not say what {who_} left to the patient, so the size is {grp}'s own rate, more than a {place} payer pays"))
        if k["amb_paid"]:
            hints.append(f"EMA shows ${k['amb_paid']/100:,.2f} paid on this claim by a payment row that names {ema_payer} but is "
                         "not marked insurance or patient: if it is the insurer's, the claim was answered")
        # (a claim due after its payer's last remittance is not listed at all: run_ema_open_claims counts it for the Data tab)
        if not rejected and not unsent and not last:
            hints.append(f"No remittance from {grp} is on file at all: check its portal, and whether its ERAs are enrolled, "
                         "before sending the claim again")
        if hints:
            f["hint"] = ". ".join(hints)
        return True

    def _open_claim_dates(self, grp: str | None, kind: str | None, ema_payer: str | None, payer_id: str | None, dos: str, sent: str,
                          due: str | None, basis: str, rejected: bool, unsent: bool, visit_first: tuple | None = None,
                          pcn: str | None = None) -> dict:
        """The date a never-answered claim's item is due by, and why ({'timely', 'deadline', 'basis', 'legal', 'first'}):
        sent on time, past the first-filing date the payer still owes a claim it has, and most take proof of timely filing
        for one they have no record of (Medicare's year from the date of service is firm). Its date runs the payer's appeal
        window past the first-filing limit, so it never expires on that limit, whatever day the page is opened. The date of
        service says nothing of when a claim went out, so a claim EMA gives no date for is dated by the limit; where the
        payer's limit is not known, only a claim sent within the shortest common limit was surely on time. visit_first:
        (day, claim id) of the visit's first send to this payer on an older bill a newer bill now carries (the one-visit
        rule), or of this claim's (pcn) own first send to it before it went out again: sent on time there, the visit keeps
        its timely filing, and the basis names that send (fx-837-r1-2; a send to another payer proves nothing to this one)."""
        rule = filing_rule(grp, kind, payer_name=ema_payer, payer_id=payer_id)
        file_by = window_end(dos, rule)
        file_legal = legal_deadline(dos, rule)      # the payer's own last day where later (Medicare's next workday)
        # a payer whose limit is not known: the item is dated by the longest common limit, and names the shortest
        early_by = _add_days(dos, rule["earliest"]) if rule.get("earliest") else None
        on_time_by = early_by or file_by
        timely = bool(not rejected and not unsent and kind != "medicare" and file_by and basis != SENT_DOS and sent <= on_time_by)
        prior = (visit_first if (not timely and visit_first and visit_first[0] and not unsent and kind != "medicare" and file_by
                                 and visit_first[0] <= on_time_by and visit_first[0] < (sent or "9999")) else None)
        limit = rule["label"] + ("" if rule["verified"] else ", not confirmed against the payer's current manual")
        if timely or prior:
            ap = appeal_rule(grp, kind, payer_name=ema_payer, payer_id=payer_id)
            deadline = window_end(max(file_by, due or file_by), ap)
            by = f"by {early_by} where you can, and by {file_by} at the latest" if early_by else f"by {file_by}"
            if prior:
                again = (f"it was sent again {sent}" if pcn and prior[1] == pcn else f"the bill was made again and went out {sent}")
                dbasis = (f"First sent on time ({prior[0]}, as claim {prior[1]}, which no payer answered); {again}, past the "
                          f"first-filing limit ({limit}). Most payers take proof of timely filing: "
                          f"the clearinghouse's acceptance report for the {prior[0]} send, or EMA's submission date. The date shown "
                          f"runs the payer's appeal window past that limit ({ap['label']}), not confirmed for a claim the payer "
                          "never answered")
            else:
                dbasis = (f"Sent on time ({sent}). If the payer has no record of it, send it again {by}, the first-filing "
                          f"limit ({limit}); after that, most payers take proof of timely filing (EMA's submission date, or the "
                          f"clearinghouse's acceptance report). The date shown runs the payer's appeal window past that limit "
                          f"({ap['label']}), not confirmed for a claim the payer never answered")
        else:
            deadline = file_by
            if unsent:
                lead = "EMA shows the claim waiting in its print batch, with no date it was sent. Print and mail it, or send it electronically"
                dbasis = (f"{lead}, by {early_by} where you can; it must reach the payer by this date at the latest: {limit}" if early_by
                          else f"{lead}, so that it reaches the payer by this date: {limit}")
            else:
                dbasis = (f"If the payer has no record of the claim, send it again by {early_by} where you can; it must reach the payer "
                          f"by this date at the latest: {limit}" if early_by else
                          f"If the payer has no record of the claim, it must reach the payer by this date: {limit}")
        on_time = timely or bool(prior)
        return {"timely": on_time, "deadline": deadline, "basis": dbasis, "legal": file_legal,
                "first": early_by or (file_by if on_time else deadline), "prior": prior}

    def run_837_open_claims(self) -> None:
        """Claims the 837s show sent that no payer answered and that EMA's weekly exports do not list (the catch-up trace's
        T-I2: run_ema_open_claims reads only the exports; the claims they list are its own, dated by the 837s there).
        For each bill no payer answered as its primary, its newest submission, counted from its newest send (a void as the
        newest send ends it, T-N5). It is answered by an 835 for it from the payer its 837 names (by payer id, else the
        company the remittances' votes give that id or the 837's payer name), by EMA's postings (ema_checks.claim_answers:
        a payment, a $0 answer or a payer's reason posted after the send; a status alone never answers), and a service a
        primary payer paid under another bill of the same member is paid (T-N10). A bill some payer answered as its primary
        has nothing chased here: a submission sent again after that answer is the answer's work (states.py waits for it).
        It is due NO_ANSWER_DAYS after the send (or the payer's usual time from the visit, where EMA's reports measure it)
        and listed once the payer's answers reach that day: a payer whose remittances stop before it has the claim counted
        under it on the Data tab (CH-8), and a payer whose 837 claims never get an ERA is judged by EMA's postings alone
        (counted on the Data tab when they cannot show it). A claim only another payer answered stays listed and says the
        primary's remittance is missing (T-C2). One NO_ANSWER item per claim, id signal 837_OPEN_CLAIM, sized and dated
        as the exports' items are (_emit_open_claim)."""
        try:
            self._run_837()
        finally:
            self._finish_waiting(self.__dict__.get("_wait_since") or "")

    def _run_837(self) -> None:
        """The body of run_837_open_claims (its docstring says what it does)."""
        self.no_era = []
        self.waiting_837 = {}
        s8 = self._load_837()
        if not s8["on"]:
            return
        c = self.conn
        newest, oldest = c.execute("SELECT MAX(payment_date), MIN(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()
        if not newest:
            return                          # no remittance at all: nothing can say a claim went unanswered
        self._answer_days_837(newest)
        sends, bills, ema_ids = s8["sends"], s8["bills"], s8["ema_ids"]
        ema_sub: dict[str, int] = {}
        for x in ema_ids:
            ema_sub[bill_of(x)] = max(ema_sub.get(bill_of(x), 0), sub_no(x))
        # bills a payer answered as primary whose newest submission went out again after that answer, with services no
        # remittance of the bill answered (answers-5: a line added in a resend after the bill was paid)
        resends = self._837_resends(s8["prim_bills"], ema_ids, ema_sub)
        if not s8["sends"] and not resends:
            return
        newest = feed_date(c) or newest
        recent = _add_days(newest, -400) or ""
        # the claims a send week's never-answered claims are measured against: every send since the oldest a claim of this
        # pass may be listed for (sent within 400 days, or since the remittances on file begin: fx-837-r2-7)
        self._wait_since = min(recent, oldest) if oldest else ""
        # the newest submission of each bill no payer answered as primary, that EMA's weekly exports do not list
        cand: dict[str, list] = {}
        for pcn, r in sends.items():
            b = bill_of(pcn)
            if pcn in ema_ids or b in s8["prim_bills"] or r[3]:
                continue                    # the exports' own claim; a bill a primary answered; voided by its newest send
            bi = bills.get(b) or [0, "", ""]
            if sub_no(pcn) < max(bi[0], ema_sub.get(b, 0)) or (bi[2] and bi[2] > bi[1]):
                continue                    # a newer submission went out, or the bill's newest send is a void
            cand[pcn] = r
        for pcn, (r, _first, _done) in resends.items():
            cand[pcn] = r
        # these claims are this pass's own: the Production check (ema_checks.unanswered_claims) leaves them to it, so that a
        # claim sent again is not chased from the day EMA posted its charge
        self._837_claims = set(cand)
        if not cand:
            return
        # the 835 claims of those bills: no payer processed any as its primary (prim_bills), so each is another payer's
        # answer (a secondary, a crossover) or the one the 837 named (a claim sent to a payer as its secondary)
        rows835: dict[str, list] = defaultdict(list)
        want = sorted({bill_of(p) for p in cand})
        for i in range(0, len(want), 500):
            c.execute("DROP TABLE IF EXISTS temp.d837_b")
            c.execute("CREATE TEMP TABLE d837_b (bill TEXT PRIMARY KEY, hi TEXT)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_b VALUES (?, ?)", [(b, b + "~") for b in want[i:i + 500]])
            for b, pcn, st, paid_on, pname, pid, cfi, paid in c.execute(
                    """SELECT x.bill, cl.patient_control_number, cl.claim_status_code, t.payment_date, t.payer_name, t.payer_id,
                              cl.claim_filing_indicator, cl.paid_cents
                       FROM temp.d837_b x JOIN claims cl ON cl.patient_control_number >= x.bill AND cl.patient_control_number < x.hi
                       JOIN transactions t ON t.transaction_key = cl.transaction_key WHERE COALESCE(t.superseded,0)=0"""):
                if bill_of(pcn) == b:
                    rows835[b].append((paid_on or "", payer_group_kind(pname, cfi)[0], paid or 0, st or "", pid, pcn))
        c.execute("DROP TABLE IF EXISTS temp.d837_b")
        # who each 837 payer id is: the payer groups that answered claims sent under it as their primary (the remittances'
        # votes), and whether its claims ever get an ERA
        id_votes: dict[str, Counter] = defaultdict(Counter)
        for pid, pname, cfi, n in c.execute(
                f"""SELECT c8.payer_id, t.payer_name, cl.claim_filing_indicator, COUNT(*) FROM claims_837 c8
                    JOIN claims cl ON cl.patient_control_number = c8.patient_control_number
                    JOIN transactions t ON t.transaction_key = cl.transaction_key
                    WHERE COALESCE(t.superseded,0)=0 AND cl.claim_status_code IN ({','.join("'" + s + "'" for s in PRIMARY_STATUSES)})
                    GROUP BY 1, 2, 3"""):
            id_votes[pid][payer_group_kind(pname, cfi)] += n
        due_by = _add_days(newest, -(NO_ANSWER_DAYS + 15)) or ""
        no_era_ids: set[str] = set()
        for pid, n, got in c.execute("""SELECT payer_id, COUNT(*), SUM(EXISTS (SELECT 1 FROM claims cl WHERE cl.patient_control_number = x.pcn))
                                        FROM (SELECT patient_control_number AS pcn, MAX(payer_id) AS payer_id FROM claims_837
                                              WHERE bht_created <= ? GROUP BY patient_control_number) x GROUP BY payer_id""", (due_by,)):
            if pid and n >= NO_ERA_MIN_CLAIMS and (got or 0) <= NO_ERA_MAX_SHARE * n:
                no_era_ids.add(pid)
        # the same member's other claims to the same payer, answered as primary, name the payer group (its product)
        members = sorted({(r[4], r[8]) for r in cand.values() if r[4] and r[8]})
        sub_votes: dict[tuple, Counter] = defaultdict(Counter)
        for i in range(0, len(members), 500):
            c.execute("DROP TABLE IF EXISTS temp.d837_m")
            c.execute("CREATE TEMP TABLE d837_m (pid TEXT, member TEXT, PRIMARY KEY (pid, member))")
            c.executemany("INSERT OR IGNORE INTO temp.d837_m VALUES (?, ?)", members[i:i + 500])
            for pid, member, pname, cfi, n in c.execute(
                    """SELECT c8.payer_id, c8.subscriber_id, t.payer_name, cl.claim_filing_indicator, COUNT(*)
                        FROM claims_837 c8 JOIN temp.d837_m m ON m.pid = c8.payer_id AND m.member = c8.subscriber_id
                        JOIN claims cl ON cl.patient_control_number = c8.patient_control_number
                        JOIN transactions t ON t.transaction_key = cl.transaction_key
                        WHERE COALESCE(t.superseded,0)=0 AND cl.claim_status_code IN ('1','19','4')
                        GROUP BY 1, 2, 3, 4"""):
                sub_votes[(pid, member)][payer_group_kind(pname, cfi)] += n
        c.execute("DROP TABLE IF EXISTS temp.d837_m")

        def group_of(r: list) -> tuple[str, str]:
            """The payer group and kind a claim went to: the VA's network by its payer id (named after its administrator),
            else what the same member's answered claims to that payer id were, else what most claims sent under the id
            were answered as, else the 837's payer name."""
            pid = (r[4] or "").strip().upper()
            if pid in VA_PAYER_IDS:
                return VA_GROUP
            sv = sub_votes.get((r[4], r[8]))
            if sv:
                return sv.most_common(1)[0][0]
            v = id_votes.get(r[4])
            if v:
                top, n = v.most_common(1)[0]
                if n >= 3 and n >= 0.5 * sum(v.values()):
                    return top
            return normalize_payer(r[5]) if r[5] else ("Unknown", "other")

        def named(r: list) -> set[str]:
            """The companies the 837's payer stands for: its payer name's, and those the remittances' votes give its id."""
            out = {payer_company(group_of(r)[0])}
            if r[5]:
                out.add(payer_company(normalize_payer(r[5])[0]))
            v = id_votes.get(r[4]) or Counter()
            tot = sum(v.values())
            out |= {payer_company(g[0]) for g, n in v.items() if n >= 3 and n >= 0.2 * tot}
            return out - {""}

        payer_last: dict[str, str] = {}
        for l in self.lines:
            g, d = l.get("payer_group"), l.get("payment_date") or l.get("txn_payment_date") or ""
            if g and d > payer_last.get(g, ""):
                payer_last[g] = d
        name_last: dict[str, str] = {}
        for name, d in c.execute("SELECT payer_name, MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0 GROUP BY 1"):
            g = normalize_payer(name)[0]
            name_last[g] = max(name_last.get(g, ""), d or "")
        lines = self._lines_837({r[7]: pcn for pcn, r in cand.items()})
        behind: dict[str, dict] = {p["payer"]: p for p in self.payers_behind}
        no_era: dict[str, dict] = {}
        cds: list[dict] = []
        cob = self._cob_only()
        # 1. each claim's own answer and the services it chases, before the one-visit rule: a newer bill of the visit that a
        # payer answered as non-primary carries an older bill's services only when that payer is the older bill's own
        # (fx-837-r1-1)
        answered_by: dict[str, set] = {}   # claim id -> the companies (and payer ids) of its own payer that answered it
        answered_rows: dict[str, list] = {}  # claim id -> those answers (rows835's shape)
        own_items: dict[str, list] = {}
        for pcn, r in sorted(cand.items()):
            ans = rows835.get(bill_of(pcn)) or []
            mine = named(r)
            # an answer is this send's only when it came on or after the send, to this claim id or to a newer submission of
            # the bill (answers-4: a payer's answer to an earlier submission, before the bill went out again, is not it)
            after = [a for a in ans if a[0] >= r[0] and (a[5] == pcn or sub_no(a[5]) > sub_no(pcn))]
            hit = [a for a in after if (a[4] and r[4] and a[4] == r[4]) or payer_company(a[1]) in mine]
            if not cob.get(bill_of(pcn)) and hit:
                self.ema_counts["837_answered_by_its_payer"] += 1
                answered_by[pcn] = {payer_company(a[1]) for a in hit} | {("id", a[4]) for a in hit if a[4]}
                answered_rows[pcn] = hit
                continue                    # the payer the 837 named answered it (as its secondary, say)
            # a line billed at $0, or at a token charge under $1 (a post-operative visit EMA bills at a penny so the line
            # goes out), is nothing owed
            items = [(code, dos, chg, units, None, mods) for code, dos, chg, units, mods in lines.get(r[7], []) if (chg or 0) >= 100]
            again = resends.get(pcn)        # a resend of a bill answered as primary (answers-5)
            if again:
                # a resend of a bill answered as primary: only the services no remittance of the bill answered are its own;
                # the rest are the answer's work (states.py waits for the resend's answer to them)
                items = [x for x in items if not again[2](x)]
                if not items:
                    self.ema_counts["837_resend_all_answered"] += 1
                    continue
            own_items[pcn] = items
        # 2. who each claim's patient is (the 837's own patient, else the one the bill's remittances or EMA's claim name), and
        # one visit billed again under a new bill: only the newest bill carries it (answers-1). The weekly exports' own
        # never-answered claims carry a visit too (answers-1 across the passes: an older bill only the 837s know yields to a
        # newer bill the exports chase), and so does a newer bill a payer answered as primary: its answer is the visit's
        # (fx-837-r2-6)
        ext = self._837_export_carriers(sends) if self._export_claims else {}
        who = self._837_patients({**cand, **{p: v["row"] for p, v in ext.items() if p not in cand}})
        done = self._837_answered_carriers(cand, lines, who, s8["prim_bills"])
        if done:
            who.update(self._837_patients({p: v["row"] for p, v in done.items()}))

        def may_carry(x: str, y: str) -> bool:
            """Whether newer bill x may carry older bill y's service: it is chased, waits for its answer, is the exports' or
            was answered as primary; a bill its own payer answered only as a later payer carries it only when that payer
            is y's own (y's payer answered the visit), never for another payer's secondary answer (fx-837-r1-1)."""
            got = answered_by.get(x)
            if got is None:
                return True
            ry = cand[y]
            return bool(got & (named(ry) | ({("id", ry[4])} if ry[4] else set())))
        carriers = {p: (v["sent"], v["lines"], 1) for p, v in ext.items()}       # an exports' bill wins a same-day tie
        carriers.update({p: (v["sent"], v["lines"], 0) for p, v in done.items() if p not in carriers})
        carrier, refused = self._837_one_visit(cand, lines, who, carriers, may_carry)
        heirs: dict[str, list] = defaultdict(list)
        # newer claim id -> [(first send, older claim id, its first send to the newer bill's payer, or None and the payer it
        # went to)] it carries: only a send to the payer that has the visit now is proof of timely filing with it
        # (fx-837-r1-2); a bill first sent to another payer is named in the hint with that payer
        older_of: dict[str, list] = defaultdict(list)

        def payer_of(x: str) -> tuple:
            """The payer a carrier bill went to: its newest 837 send's (id, name), else the payer EMA gives the exports' item."""
            rx = cand.get(x) or (ext.get(x) or done.get(x) or {}).get("row")
            if rx is not None and (rx[4] or rx[5]):
                return rx[4], rx[5]
            fx = self.findings.get(ext[x]["fid"]) if x in ext else None
            return None, (fx or {}).get("payer_group")

        for pcn, items in own_items.items():
            for nb in {carrier.get(pcn, {}).get(_visit_line(x)) for x in items} - {None}:
                same = self._first_send_to(pcn, cand[pcn], *payer_of(nb))
                older_of[nb].append((cand[pcn][1], pcn, same, None if same else group_of(cand[pcn])[0]))
        # the answers of the newer bills that may not carry a visit: another payer answered them only as a later payer
        sec_lines = self._837_answer_lines({x: answered_rows[x] for ref in refused.values() for xs in ref.values() for x in xs
                                            if x in answered_rows})
        # 3. each claim's item
        for pcn, items in sorted(own_items.items()):
            r = cand[pcn]
            b = bill_of(pcn)
            ans = rows835.get(b) or []
            cb = cob.get(b)                 # its payer denied it as primary for coordination, then answered as secondary
            again = resends.get(pcn)
            mine = named(r)
            if carrier:
                kept = [x for x in items if not carrier.get(pcn, {}).get(_visit_line(x))]
                if len(kept) < len(items):
                    for x in items:
                        nb = carrier.get(pcn, {}).get(_visit_line(x))
                        if nb:
                            heirs[nb].append(pcn)
                            if nb in ext:
                                self.ema_counts["837_visit_under_exports_bill"] += 1
                            elif nb in done:
                                self.ema_counts["837_visit_under_answered_bill"] += 1
                    self.ema_counts["837_visit_under_newer_bill"] += len(items) - len(kept)
                items = kept
            if not items:
                continue
            sent = r[0]
            if sent < recent and sent < (oldest or ""):
                continue                    # sent before the remittances on file begin
            days = sorted({x[1] for x in items if x[1]})
            if not days:
                continue
            grp, kind = group_of(r)
            due = _add_days(sent, NO_ANSWER_DAYS)
            if self.ema_on and due:
                usual = _add_days(days[0], self._turnaround_days(grp))
                if usual and usual > due:
                    due = usual
            through = self._answers_through(grp, newest) if self.ema_on else newest
            if not cb and (not due or due > (through or newest)):
                # not due yet, or the answers on file do not reach the day it was due (a payer that answered as secondary
                # after a coordination denial applied the primary's): on no list until then, so shown waiting with its due day
                self._note_waiting(pcn, grp, sent, due, sum(x[2] or 0 for x in items))
                continue
            billed = sum(x[2] or 0 for x in items)
            pid = (r[4] or "").strip()
            last = None
            if pid in no_era_ids:
                # its 837 claims never get an ERA: EMA's postings are the only source of its answers. Counted for the Data
                # tab; without EMA's reports nothing can show the answer, so it is not listed (never as paid either)
                ne = no_era.setdefault(pid, {"payer": grp, "payer_id": pid, "claims": 0, "billed_cents": 0, "since": sent,
                                              "listed": 0, "answered_in_ema": 0, "unchecked": 0})
                ne["claims"] += 1
                ne["billed_cents"] += billed
                ne["since"] = min(ne["since"], sent)
                if not self.ema_on:
                    ne["unchecked"] += 1
                    continue
            else:
                last = payer_last.get(grp) or name_last.get(normalize_payer(grp)[0]) or (name_last.get(normalize_payer(r[5])[0]) if r[5] else None)
                known = max(last, through) if (last and through and through > newest) else last
                if last and due > known and not cb:
                    held = behind.setdefault(grp, {"payer": grp, "since": last, "claims": 0, "billed_cents": 0})
                    held["claims"] += 1
                    held["billed_cents"] += billed
                    continue                # its payer's remittances stop before its due day (CH-8): counted, not listed
            # only another payer answered it (T-C2). Its own payer's answers to an older submission of the bill are neither
            # this send's answer nor another payer's (fx-837-r2-3): they are named in the hint. (A resend of a bill answered
            # as primary: its new services have no answer at all, and the bill's answers were the primary's own, so no other
            # payer answered them as secondary.)
            own_pay = [a for a in ans if (a[4] and r[4] and a[4] == r[4]) or payer_company(a[1]) in mine]
            others_ = [a for a in ans if a not in own_pay]
            sec = [] if again else ([x for xs in cb["answers"].values() for x in xs] if cb else
                                    [(a[0], a[1], a[2], a[3]) for a in others_ if a[5] == pcn] or [(a[0], a[1], a[2], a[3]) for a in others_])
            sec_bills: set[str] = set()
            if not again and not cb:
                # the visit billed again on a newer bill another payer answered only as a later payer (fx-837-r1-1): its answer
                # is this claim's T-C2, what it paid for these services taken off
                for x in sorted({x for xx in items for x in (refused.get(pcn, {}).get(_visit_line(xx)) or ())}):
                    got = sec_lines.get(x)
                    if not got:
                        continue
                    paid = sum(c for d, code, part, c in got["lines"]
                               if any(d == xx[1] and code == xx[0] and part_fits(item_part(xx), {part}) for xx in items))
                    sec.append((got["date"], got["group"], paid, got["status"]))
                    sec_bills.add(x)
            k = {"payer_ids": Counter({r[4]: 1}) if r[4] else Counter(), "pat_paid": 0, "ins": {}, "providers": Counter(),
                 "status_at": ("", "", -1), "amb_paid": 0, "days": set(days), "member": r[8]}
            firsts = sorted(older_of.get(pcn) or (), key=lambda t: (t[0], t[1]))
            cds.append({"pcn": pcn, "k": k, "b": b, "items": items, "sent": sent, "basis": SENT_837, "due": due, "again_after": False,
                        "rejected": False, "unsent": False, "gave_up": False, "status": None, "ema_payer": r[5] or grp,
                        "group": (grp, kind), "grp": grp, "kind": kind, "through": through, "payer_last": last, "secondary": sec,
                        "s837": r, "batched": False, "resent_837": bool(again), "bill_answered": again[1] if again else None,
                        "added_after_answer": bool(again), "id_signal": OPEN_837_ID,
                        "source": "837", "no_era": pid if pid in no_era_ids else None, "cob": cb, "who": who.get(pcn),
                        # EMA's claims are found under the group EMA gives the 837's payer (_ema_group)
                        "match_group": self._ema_group(r[5]),
                        # the older bills of the visit this one carries (each with the payer it went to when not this
                        # claim's), and the visit's first send on them to this claim's payer (fx-837-r1-2)
                        "older_bills": [(d, p, g) for d, p, _s, g in firsts],
                        "visit_first": min(((x, p) for _d, p, x, _g in firsts if x and x < sent), default=None),
                        "sec_bills": sec_bills,
                        # its own payer's answers to an older submission (its latest, and what they paid in all)
                        "own_earlier": ((max(own_pay)[0], max(own_pay)[5], sum(a[2] for a in own_pay), max(own_pay)[3])
                                        if own_pay and not again and not cb else None)})
        # a service a primary payer paid under another bill of the same member (a bill made again) is paid (T-N10)
        cds = self._837_paid_elsewhere(cds)
        if self.ema_on and cds:
            cds = self._837_ema_answers(cds, newest, no_era)
        for cd in cds:
            if self._emit_open_claim(cd, cd["items"], newest):
                self.ema_counts["837_no_answer"] += 1
                if cd.get("no_era"):
                    no_era[cd["no_era"]]["listed"] += 1
            # EMA claims that are this claim or another listed one (send-day twins): the Production check leaves them
            self._export_links.update(cd.get("ema_twins") or ())
            fid = _fid(OPEN_837_ID, cd["pcn"])
            if cd.get("ema_link") is not None:
                self._export_links.add(cd["ema_link"])
                # the Production check's item for the same EMA claim, keyed by its number before the A/R knew its claim id,
                # hands its work to this one (save: heir keys)
                self.__dict__.setdefault("_heirs", {}).setdefault(fid, []).append(("EMA_OPEN_CLAIM", f"link:{cd['ema_link']}"))
            for old in sorted(set(heirs.get(cd["pcn"]) or ())):
                # the older bill of the same visit, chased before this bill was made: its work passes on (answers-1)
                self.__dict__.setdefault("_heirs", {}).setdefault(fid, []).append((OPEN_837_ID, old))
            self._fold_837_into(fid, heirs.get(cd["pcn"]))
        for e in sorted(set(heirs) & set(ext)):
            # an older bill only the 837s know whose visit a newer bill of the exports' carries: the exports' item is the one
            # chased, and takes the older bill's work (save: heir keys)
            olds = sorted(set(heirs[e]))
            self.__dict__.setdefault("_heirs", {}).setdefault(ext[e]["fid"], []).extend((OPEN_837_ID, old) for old in olds)
            self._fold_837_into(ext[e]["fid"], olds)
            f = self.findings.get(ext[e]["fid"])
            if f is not None:
                note = (f"This visit was billed before on claim {', '.join(olds)}, which no payer answered either: the practice made "
                        "the bill again, so only this newer bill is chased")
                f["hint"] = f"{f['hint']}. {note}" if f.get("hint") else note
                self._redate_carrier(f, min(((x, p) for _d, p, x, _g in older_of.get(e) or () if x), default=("", "")))
        # an older bill whose visit a newer bill a payer answered as primary carries: that answer's item takes its work
        # (fx-837-r2-6)
        self._hand_to_answers({e: sorted(set(heirs[e])) for e in set(heirs) & set(done)}, done)
        self.payers_behind = sorted(behind.values(), key=lambda h: (-h["billed_cents"], h["payer"]))
        self.no_era = sorted(no_era.values(), key=lambda h: (-h["billed_cents"], h["payer"]))

    def _note_waiting(self, pcn: str, grp: str | None, sent: str, due: str | None, billed: int) -> None:
        """A claim sent that no payer answered yet and that is not due yet (WAITING_837_META): any never-answered pass may
        note one."""
        w = self.__dict__.setdefault("_waiting", {})
        w[pcn] = [pcn, grp or "Unknown", sent, due, int(billed or 0)]

    def _finish_waiting(self, since: str) -> None:
        """WAITING_837_META from the claims noted waiting, with every claim the 837s show sent since `since` by week (the
        never-answered claims of a send week are measured against it: insights.unanswered_weeks)."""
        rows = sorted((self.__dict__.get("_waiting") or {}).values(), key=lambda r: (r[3] or "9999", r[0]))
        out: dict = {"n": len(rows), "billed_cents": sum(r[4] for r in rows), "first_due": min((r[3] for r in rows if r[3]), default=None),
                     "last_due": max((r[3] for r in rows if r[3]), default=None), "by_group": {}, "by_due_week": {},
                     "claims": rows[:WAITING_LIST_MAX], "at": self.now}
        for pcn, grp, sent, due, billed in rows:
            g = out["by_group"].setdefault(grp, {"n": 0, "billed_cents": 0, "first_due": due})
            g["n"] += 1
            g["billed_cents"] += billed
            if due and (not g["first_due"] or due < g["first_due"]):
                g["first_due"] = due
            wk = week_of(due)
            if wk:
                d = out["by_due_week"].setdefault(wk, {"n": 0, "billed_cents": 0})
                d["n"] += 1
                d["billed_cents"] += billed
        weeks: Counter = Counter()
        # a store no 837 file was ever read into has no claims_837 table: no send week to count, and nothing went wrong
        # (logged as an error, it made every such run read 'partial')
        has_837 = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='claims_837'").fetchone()
        try:
            sends = self.conn.execute("""SELECT bht_created, COUNT(DISTINCT patient_control_number) FROM claims_837
                                         WHERE bht_created >= ? AND patient_control_number IS NOT NULL
                                           AND TRIM(COALESCE(frequency_code, '')) <> '8' GROUP BY bht_created""",
                                      (since or "",)) if has_837 else ()
            for bht, n in sends:
                wk = week_of(real_day(bht))
                if wk:
                    weeks[wk] += n
        except sqlite3.Error as exc:
            from . import runlog
            runlog.caught(exc, "detect.finish_waiting")       # the send weeks are left without counts; the run's log says why
            weeks = Counter()
        out["sent_by_week"] = dict(sorted(weeks.items()))
        self.waiting_837 = out

    def _837_send_info(self, keys) -> dict[str, dict]:
        """What the 837 reader keeps since READ_VERSION 2 for these sends ({claim_837_key: {...}}): the patient's keyed name,
        keyed name and birth date and (since READ_VERSION 3) keyed first name, the relationship, the payer's place on the
        claim (SBR01), the claim filing indicator and the other payer's answer (x12_837). A send read before has none of
        them (read_version 0)."""
        c = self.conn
        cols = {r[1] for r in c.execute("PRAGMA table_info(claims_837)")}
        want = tuple(k for k in ("patient_key", "person_key", "relationship", "payer_resp", "filing_indicator", "other_payer_name",
                                 "other_payer_id", "other_payer_resp", "other_paid_cents", "other_left_cents", "other_pr_cents",
                                 "read_version", "first_key") if k in cols)
        if "read_version" not in cols:
            return {}
        cache = self.__dict__.setdefault("_s837_info", {})
        need = sorted({k for k in keys if k and k not in cache})
        for i in range(0, len(need), 500):
            part = need[i:i + 500]
            for row in c.execute(f"SELECT claim_837_key, {', '.join(want)} FROM claims_837 WHERE claim_837_key IN ({','.join('?' * len(part))})",
                                 part):
                cache[row[0]] = dict(zip(want, row[1:]))
        return {k: cache[k] for k in keys if k in cache}

    def _837_patients(self, cand: dict[str, list]) -> dict[str, dict]:
        """Who each never-answered 837 claim's patient is ({claim id: {'names': {keyed names}, 'persons': {keyed name and birth
        date}, 'firsts': {keyed first names}, 'member': member id, 'from': where}}): the patient its 837 names (the patient
        loop, else the subscriber, read since READ_VERSION 2; the first name since 3) where it does; else the patient the
        remittances of its own bill name (any payer's answer to any submission of it), and EMA's patient for its bill (the
        claim id the A/R pairs, or the bill map; EMA keys no first name alone). Never the member id alone: a subscriber's id
        covers the family, so a claim nothing names has no patient here."""
        c = self.conn
        out: dict[str, dict] = {pcn: {"names": set(), "persons": set(), "firsts": set(), "member": r[8], "from": None}
                                for pcn, r in cand.items()}
        info = self._837_send_info([r[7] for r in cand.values()])
        for pcn, r in cand.items():
            i = info.get(r[7]) or {}
            if i.get("patient_key") is not None:
                out[pcn]["names"].add(i["patient_key"])
                if i.get("person_key") is not None:
                    out[pcn]["persons"].add(i["person_key"])
                if i.get("first_key") is not None:
                    out[pcn]["firsts"].add(i["first_key"])
                out[pcn]["from"] = "837"
        rest = {pcn: bill_of(pcn) for pcn, w in out.items() if w["from"] is None}
        if not rest:
            return out
        from . import ema_reports as er
        er.register_functions(c, create=False)      # rw_name_key with the store's salt (None where it has none)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        by_bill: dict[str, list] = defaultdict(list)
        for pcn, b in rest.items():
            by_bill[b].append(pcn)
        names: dict[str, set] = defaultdict(set)
        persons: dict[str, set] = defaultdict(set)
        firsts: dict[str, set] = defaultdict(set)
        bills = sorted(by_bill)
        for i in range(0, len(bills), 500):
            c.execute("DROP TABLE IF EXISTS temp.d837_wb")
            c.execute("CREATE TEMP TABLE d837_wb (bill TEXT PRIMARY KEY, hi TEXT)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_wb VALUES (?, ?)", [(b, b + "~") for b in bills[i:i + 500]])
            for b, pcn, nk, cnk, fk, cfk in c.execute(
                    """SELECT x.bill, cl.patient_control_number, rw_name_key(cl.patient_last, cl.patient_first),
                              CASE WHEN cl.corrected_patient_last IS NOT NULL OR cl.corrected_patient_first IS NOT NULL
                                   THEN rw_name_key(COALESCE(cl.corrected_patient_last, cl.patient_last),
                                                    COALESCE(cl.corrected_patient_first, cl.patient_first)) END,
                              rw_name_key(NULL, cl.patient_first), rw_name_key(NULL, cl.corrected_patient_first)
                       FROM temp.d837_wb x JOIN claims cl ON cl.patient_control_number >= x.bill
                            AND cl.patient_control_number < x.hi"""):
                if bill_of(pcn) == b:
                    names[b] |= {nk, cnk} - {None}
                    firsts[b] |= {fk, cfk} - {None}
            if "ema_claim_links" in tables and "ema_lines" in tables:
                links: dict[int, set] = defaultdict(set)
                for b, cid, lk in c.execute("""SELECT x.bill, e.claim_id, e.link FROM temp.d837_wb x JOIN ema_claim_links e
                                               ON e.claim_id >= x.bill AND e.claim_id < x.hi WHERE e.link IS NOT NULL"""):
                    if bill_of(cid) == b:
                        links[lk].add(b)
                if "ema_link_map" in tables:
                    for b, lk in c.execute("""SELECT x.bill, m.link FROM temp.d837_wb x JOIN ema_link_map m ON m.bill = x.bill
                                              WHERE COALESCE(m.conflict, 0) = 0 AND m.link IS NOT NULL"""):
                        links[lk].add(b)
                lks = sorted(links)
                for j in range(0, len(lks), 500):
                    part = lks[j:j + 500]
                    for lk, nk, person in c.execute(f"""SELECT DISTINCT link, name_key, person FROM ema_lines
                                                        WHERE link IN ({','.join('?' * len(part))})""", part):
                        for b in links[lk]:
                            if nk is not None:
                                names[b].add(nk)
                            if person is not None:
                                persons[b].add(person)
        c.execute("DROP TABLE IF EXISTS temp.d837_wb")
        for b, pcns in by_bill.items():
            for pcn in pcns:
                if names.get(b) or persons.get(b):
                    out[pcn].update(names=set(names.get(b) or ()), persons=set(persons.get(b) or ()), firsts=set(firsts.get(b) or ()),
                                    **{"from": "bill"})
        return out

    def _837_one_visit(self, cand: dict[str, list], lines: dict, who: dict, carriers: dict | None = None,
                       may_carry=None) -> tuple[dict[str, dict], dict[str, dict]]:
        """One visit billed again under a new bill (answers-1): for each never-answered 837 claim, its services that a newer
        bill of the same patient carries ({claim id: {visit line: the newer claim id}}), and the newer bills of the same
        patient's visit that may not carry a service ({claim id: {visit line: {newer claim ids}}}: may_carry(newer, older)
        said no). The same patient is the same keyed name and birth date, or the same keyed name under the same member id
        when one of the two bills gives no birth date (never the member id alone, so family members on one subscriber id
        stay apart; two bills that both give a birth date are one patient's only when name and birth date agree: a father
        and his son of one name on one id are two, fx-837-r1-6); the same service is the same date, code, part and
        distinct-service modifiers (_visit_line); the newer bill is the one sent later (then the higher claim id), the
        newest that may carry it. The surgery center's facility claims and the clinicians' claims are never one bill of
        each other. carriers: other bills that carry a visit ({claim id: (sent, its services as (code, date, charge, units,
        item, modifiers), tie)}): the weekly exports' never-answered claims (_837_export_carriers; tie 1: they win a tie with
        an 837 bill sent the same day, as the exports' pass never makes its claim yield to a bill sent the same day) and
        the bills a payer answered as primary (_837_answered_carriers; tie 0)."""
        from .ema_reports import is_facility_claim
        marks: dict[tuple, list] = defaultdict(list)       # visit key -> [(mark, claim id)]
        keys_of: dict[str, list] = {}

        def ids_of(pcn: str) -> list:
            w = who.get(pcn) or {}
            ids = [("p", p) for p in w.get("persons") or ()]
            if w.get("member"):
                ids += [("n", n, w["member"]) for n in w.get("names") or ()]
            return ids

        for pcn, r in cand.items():
            ids = ids_of(pcn)
            if not ids:
                continue
            fac = bool(is_facility_claim(pcn))
            mark = (r[0], 0, pcn)
            ks = []
            for code, dos, chg, units, mods in lines.get(r[7], []):
                x = (code, dos, chg, units, None, mods)
                if (chg or 0) < NO_CHARGE_CENTS or not dos:
                    continue
                vl = _visit_line(x)
                for i in ids:
                    vk = (i, fac) + vl
                    ks.append((vk, vl, mark))
                    marks[vk].append((mark, pcn))
            keys_of[pcn] = ks
        for pcn, (sent, xs, tie) in sorted((carriers or {}).items()):
            ids = ids_of(pcn)
            if not ids or not sent:
                continue
            fac = bool(is_facility_claim(pcn))
            for x in xs:
                if _no_charge(x[2]) or not x[1]:
                    continue
                for i in ids:
                    vk = (i, fac) + _visit_line(x)
                    if vk in marks:
                        marks[vk].append(((sent, tie, pcn), pcn))      # only a visit an 837 claim here carries matters
        out: dict[str, dict] = {}
        refused: dict[str, dict] = {}
        for pcn, ks in keys_of.items():
            dated = bool((who.get(pcn) or {}).get("persons"))
            best: dict[tuple, tuple] = {}
            for vk, vl, mark in ks:
                for m, x in marks[vk]:
                    if m <= mark or x == pcn or bill_of(x) == bill_of(pcn):
                        continue
                    if vk[0][0] == "n" and dated and (who.get(x) or {}).get("persons"):
                        continue            # both bills give the birth date: their keyed names and birth dates decide
                    if may_carry is not None and not may_carry(x, pcn):
                        refused.setdefault(pcn, {}).setdefault(vl, set()).add(x)
                        continue
                    if vl not in best or m > best[vl]:
                        best[vl] = m
            if best:
                out[pcn] = {vl: m[2] for vl, m in best.items()}
        return out, refused

    def _837_export_carriers(self, sends: dict) -> dict[str, dict]:
        """The weekly exports' never-answered claims their pass listed (run_ema_open_claims runs first), as carriers of a
        visit for the one-visit rule (answers-1 across the passes): an older bill only the 837s know yields to a newer bill
        the exports chase. {claim id: {'fid': its item, 'sent': the day it went out, 'row': its newest 837 send (_load_837's
        shape; one with no 837 when none is on file), 'lines': the services its item chases as (code, date, charge, units,
        item, modifiers)}}: the item's own lines (the part each bills), and, where the claim's 837 is on file, that 837's
        lines of the same date, code and part with their modifiers. A claim still in EMA's print batch went nowhere and
        carries nothing."""
        out: dict[str, dict] = {}
        keys: dict[str, str] = {}
        for pcn in sorted(self._export_claims):
            fid = _fid("EMA_OPEN_CLAIM", pcn)
            f = self.findings.get(fid)
            if f is None or f.get("signal") not in ("NO_ANSWER", "REJECTED_PRE_PAYER"):
                continue
            try:
                ev = json.loads(f.get("evidence") or "{}")
            except ValueError as exc:
                from . import runlog
                runlog.caught(exc, "detect.837_export_carriers")
                continue
            if ev.get("not_sent") or not ev.get("sent"):
                continue
            # each line with the part it bills and the modifiers that make it a separate service (_emit_open_claim keeps
            # them in 'line_modifiers', by the line's place in 'lines': fx-837-r2-4)
            sep = {i: m for i, m in (x for x in ev.get("line_modifiers") or [] if isinstance(x, list) and len(x) == 2)
                   if isinstance(i, int) and isinstance(m, str)}
            xs = [(ln[0], ln[1], ln[2], None, None, ([ln[4]] if len(ln) > 4 and ln[4] else []) + [m for m in sep.get(i, "").split(",") if m])
                  for i, ln in enumerate(ev.get("lines") or []) if isinstance(ln, list) and len(ln) > 2 and ln[0] and ln[1]]
            xs = [x[:5] + (x[5] or None,) for x in xs]
            if not xs:
                continue
            r = sends.get(pcn)
            out[pcn] = {"fid": fid, "sent": ev["sent"], "lines": xs,
                        "row": r if r is not None else [ev["sent"], ev["sent"], "", False, None, None, None, None, None, 0]}
            if r is not None and r[7]:
                keys[r[7]] = pcn
        for key, ls in self._lines_837(keys).items():
            pcn = keys[key]
            want = {(x[1], x[0], item_part(x)) for x in out[pcn]["lines"]}
            own = [(code, dos, chg, units, None, mods) for code, dos, chg, units, mods in ls if (dos, code, component_of(mods)) in want]
            if own:
                # the 837 is what was sent: its lines, with their modifiers, stand for the services they bill (an exports'
                # 17110-59 is another lesion's service, never the plain 17110 an older bill carries: fx-837-r2-4)
                got = {(x[1], x[0], item_part(x)) for x in own}
                out[pcn]["lines"] = [x for x in out[pcn]["lines"] if (x[1], x[0], item_part(x)) not in got] + own
        return out

    def _837_answered_carriers(self, cand: dict[str, list], lines: dict, who: dict, prim: set) -> dict[str, dict]:
        """The newer bills of a never-answered claim's visit that a payer answered as primary, as carriers of the visit for
        the one-visit rule (fx-837-r2-6: the visit's answer is on the newest bill, so an older bill keeps giving way once
        the newer one is answered, with a denial say, and is not chased beside that answer). The bills in prim (a payer
        processed one of their claims as primary) whose newest submission the 837s show sent on or after the oldest of
        these claims' sends, for the same member id or, where the 837 names the patient, the same patient; a bill whose
        newest send is a void carries nothing. {claim id: {'sent': its newest send, 'row': that send (_load_837's shape),
        'lines': its services on the dates and codes of these claims' own, as (code, date, charge, units, item,
        modifiers)}}."""
        c = self.conn
        if not cand or not prim or not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='claims_837'").fetchone():
            return {}
        cols = {r[1] for r in c.execute("PRAGMA table_info(claims_837)")}
        members = sorted({r[8] for r in cand.values() if r[8]})
        names = sorted({n for p in cand for n in ((who.get(p) or {}).get("names") or ()) if (who.get(p) or {}).get("from") == "837"}
                       if "patient_key" in cols else ())
        if not members and not names:
            return {}
        since = min(r[0] for r in cand.values())
        own_bills = {bill_of(p) for p in cand}
        days = {(dos, code) for r in cand.values() for code, dos, _chg, _units, _mods in lines.get(r[7], [])}
        c.execute("DROP TABLE IF EXISTS temp.d837_am")
        c.execute("CREATE TEMP TABLE d837_am (k TEXT PRIMARY KEY)")
        c.executemany("INSERT OR IGNORE INTO temp.d837_am VALUES (?)", [(m,) for m in members])
        c.execute("DROP TABLE IF EXISTS temp.d837_an")
        c.execute("CREATE TEMP TABLE d837_an (k INTEGER PRIMARY KEY)")
        c.executemany("INSERT OR IGNORE INTO temp.d837_an VALUES (?)", [(n,) for n in names])
        who_sql = "subscriber_id IN (SELECT k FROM temp.d837_am)" + (" OR patient_key IN (SELECT k FROM temp.d837_an)" if names else "")
        rows: dict[str, list] = {}          # bill -> [claim id, send row]: its newest submission's newest send
        voided: dict[str, str] = {}
        for pcn, bht, freq, pid, pname, chg, key, member in c.execute(
                f"""SELECT patient_control_number, bht_created, TRIM(COALESCE(frequency_code, '')), payer_id, payer_name,
                           total_charge_cents, claim_837_key, subscriber_id
                    FROM claims_837 WHERE patient_control_number IS NOT NULL AND bht_created IS NOT NULL AND ({who_sql})"""):
            pcn = str(pcn).strip()
            bht = real_day(bht)
            b = bill_of(pcn)
            if not pcn or not bht or b not in prim or b in own_bills:
                continue
            if freq == "8":
                voided[b] = max(voided.get(b, ""), bht)
                continue
            got = rows.get(b)
            if got is None or sub_no(pcn) > sub_no(got[0]) or (sub_no(pcn) == sub_no(got[0]) and pcn > got[0]):
                rows[b] = [pcn, [bht, bht, freq, False, pid, pname, chg, key, member, 1]]
            elif pcn == got[0]:
                r = got[1]
                r[9] += 1
                r[1] = min(r[1], bht)
                if bht > r[0] or (bht == r[0] and (key or "") > (r[7] or "")):
                    r[0], r[2], r[4], r[5], r[6], r[7], r[8] = bht, freq, pid, pname, chg, key, member
        c.execute("DROP TABLE IF EXISTS temp.d837_am")
        c.execute("DROP TABLE IF EXISTS temp.d837_an")
        keep = {pcn: r for b, (pcn, r) in rows.items() if r[0] >= since and not (voided.get(b) and voided[b] >= r[0])}
        out: dict[str, dict] = {}
        got_lines = self._lines_837({r[7]: pcn for pcn, r in keep.items() if r[7]})
        for pcn, r in keep.items():
            xs = [(code, dos, chg, units, None, mods) for code, dos, chg, units, mods in got_lines.get(r[7], []) if (dos, code) in days]
            if xs:
                out[pcn] = {"sent": r[0], "row": r, "lines": xs}
        return out

    def _837_answer_lines(self, hits: dict[str, list]) -> dict[str, dict]:
        """What the answers of the newer bills of a visit that another payer answered only as a later payer paid, service
        by service (the T-C2 of the older bill they may not carry, fx-837-r1-1): {claim id: {'date': its latest answer,
        'group': that answer's payer group, 'status': its CLP02, 'lines': [(date, code, part, what still stands of the line's
        payment in cents)]}} from each claim's answers (rows835's shape: date, group, paid, status, payer id, claim id)."""
        out: dict[str, dict] = {}
        if not hits:
            return out
        c = self.conn
        ids = sorted({a[5] for rows in hits.values() for a in rows if a[5]})
        by_claim: dict[str, list] = defaultdict(list)
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for pcn, lk, code, ocode, dos, mods in c.execute(
                    f"""SELECT cl.patient_control_number, l.line_key, l.procedure_code, l.original_procedure_code,
                               COALESCE(l.service_date, cl.statement_from), l.modifiers
                        FROM claims cl JOIN transactions t ON t.transaction_key = cl.transaction_key
                        JOIN lines l ON l.claim_key = cl.claim_key
                        WHERE COALESCE(t.superseded,0)=0 AND cl.patient_control_number IN ({','.join('?' * len(part))})""", part):
                cents = int(self.standing.get(lk) or 0)
                for cd_ in {code, ocode} - {None, ""}:
                    by_claim[pcn].append((dos, cd_, component_of(mods), cents))
        for x, rows in hits.items():
            top = max(rows)
            out[x] = {"date": top[0], "group": top[1], "status": top[3],
                      "lines": [ln for p in sorted({a[5] for a in rows if a[5]}) for ln in by_claim.get(p, [])]}
        return out

    def _redate_carrier(self, f: dict, first: tuple) -> None:
        """An exports' item that carries a visit first sent to its payer on an older bill only the 837s know (the one-visit
        rule): when that send was inside the first-filing limit and the item's own was not, the visit keeps its timely filing
        (fx-837-r1-2), so the item is dated as _emit_open_claim dates one sent on time, with the basis naming that send."""
        if not first or not first[0]:
            return
        try:
            ev = json.loads(f.get("evidence") or "{}")
        except ValueError as exc:
            from . import runlog
            runlog.caught(exc, "detect.redate_carrier")
            return
        sent, dos = ev.get("sent"), f.get("service_date")
        if not sent or not dos or first[0] >= sent:
            return
        dt = self._open_claim_dates(f.get("payer_group"), f.get("payer_kind"), ev.get("payer_in_ema"), f.get("payer_id"), dos, sent,
                                    ev.get("answer_due"), ev.get("sent_basis"), f.get("signal") == "REJECTED_PRE_PAYER",
                                    bool(ev.get("not_sent")), first)
        if dt["prior"]:
            f["appeal_deadline"], f["deadline_basis"], f["legal_deadline"] = dt["deadline"], dt["basis"], None

    def _fold_837_into(self, fid: str, olds) -> None:
        """The never-answered items of the older bills a carrier item now stands for (the one-visit rule), named in its
        evidence's 'folded' list as every fold names the items it carries: the self-check's vanished test reads the list as
        the route those items left the list by (without it, it keeps an older item a person worked listed beside its
        carrier: the visit chased twice), the decision log as where they went, the Results tab as duplicates spared."""
        f = self.findings.get(fid)
        if f is None or not olds:
            return
        try:
            ev = json.loads(f.get("evidence") or "{}")
        except ValueError as exc:
            from . import runlog
            runlog.caught(exc, "detect.fold_837_into")
            return
        if not isinstance(ev, dict):
            return
        folded = list(ev.get("folded") or [])
        have = {x.get("finding_id") if isinstance(x, dict) else x for x in folded}
        add = [{"finding_id": _fid(OPEN_837_ID, o), "rule": "837_one_visit"} for o in sorted(set(olds)) if _fid(OPEN_837_ID, o) not in have]
        if add:
            ev["folded"] = folded + add
            f["evidence"] = json.dumps(ev, default=str)

    def _hand_to_answers(self, olds_of: dict[str, list], done: dict) -> None:
        """An older bill whose visit a newer bill a payer answered as primary carries (fx-837-r2-6) hands its work to that
        answer's item (save: heir keys): the newer bill's item on a date and code of the visit (a denial, an underpayment),
        else the newer bill's claim-level item; none when the answer left nothing to work (paid in full)."""
        if not olds_of:
            return
        bills = {bill_of(x) for x in olds_of}
        idx: dict[str, list] = defaultdict(list)
        for fid, f in self.findings.items():
            p = f.get("patient_control_number")
            if p and bill_of(p) in bills and f.get("signal") not in ("NO_ANSWER", "REJECTED_PRE_PAYER"):
                idx[bill_of(p)].append((fid, f.get("service_date"), f.get("procedure_code"), bool(f.get("line_key"))))
        for x, olds in sorted(olds_of.items()):
            items = sorted(idx.get(bill_of(x)) or [])
            want = {(dos, code) for code, dos, *_rest in done[x]["lines"]}
            hit = [i for i in items if (i[1], i[2]) in want] or [i for i in items if not i[3]]
            if hit:
                self.__dict__.setdefault("_heirs", {}).setdefault(hit[0][0], []).extend((OPEN_837_ID, o) for o in olds)
                self._fold_837_into(hit[0][0], olds)

    def _837_resends(self, prim: set, ema_ids: set, ema_sub: dict) -> dict[str, tuple]:
        """Bills a payer answered as primary (prim) whose newest submission the 837s show sent again after the bill's first
        primary answer (answers-5: a line added in a resend after the bill was paid): {claim id: (its send row as
        _load_837 keeps sends, the bill's first primary answer, a test of whether a line (code, date, charge, units, item,
        modifiers) is a service some remittance of the bill answered)}. Left out: a bill whose newest send is a void, a
        submission EMA's weekly exports list or show a newer one of (theirs), and a bill one of whose remittances answered the
        whole claim with no service line (the answer covers every service)."""
        c = self.conn
        if not prim or not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='claims_837'").fetchone():
            return {}
        # one row a bill, to spare memory (a serverless run holds every answered bill here): its newest submission's newest
        # send, in _load_837's shape, and the bill's newest void
        rows: dict[str, list] = {}          # bill -> [claim id, send row]
        voided: dict[str, str] = {}
        sent_first: dict[str, str] = {}     # bill -> its first send on file (any submission)
        for pcn, bht, freq, pid, pname, chg, key, member in c.execute(
                """SELECT patient_control_number, bht_created, TRIM(COALESCE(frequency_code, '')), payer_id, payer_name,
                          total_charge_cents, claim_837_key, subscriber_id
                   FROM claims_837 WHERE patient_control_number IS NOT NULL AND bht_created IS NOT NULL"""):
            pcn = str(pcn).strip()
            bht = real_day(bht)
            b = bill_of(pcn)
            if not pcn or not bht or b not in prim:
                continue
            if freq == "8":
                voided[b] = max(voided.get(b, ""), bht)
                continue
            if bht < sent_first.get(b, "9999"):
                sent_first[b] = bht
            got = rows.get(b)
            if got is None or sub_no(pcn) > sub_no(got[0]) or (sub_no(pcn) == sub_no(got[0]) and pcn > got[0]):
                rows[b] = [pcn, [bht, bht, freq, False, pid, pname, chg, key, member, 1]]
                continue
            if pcn != got[0]:
                continue                    # an older submission of the bill
            r = got[1]
            r[9] += 1
            r[1] = min(r[1], bht)
            if bht > r[0] or (bht == r[0] and (key or "") > (r[7] or "")):
                r[0], r[2], r[4], r[5], r[6], r[7], r[8] = bht, freq, pid, pname, chg, key, member
        best = {b: pcn for b, (pcn, r) in rows.items() if pcn not in ema_ids and sub_no(pcn) >= ema_sub.get(b, 0)
                and not (voided.get(b) and voided[b] >= r[0])}
        rows = {pcn: rows[b][1] for b, pcn in best.items()}
        if not best:
            return {}
        first: dict[str, str] = {}
        whole: set[str] = set()
        bills = sorted(best)
        prim_sql = ",".join("'" + s + "'" for s in PRIMARY_STATUSES)
        for i in range(0, len(bills), 500):
            c.execute("DROP TABLE IF EXISTS temp.d837_rs")
            c.execute("CREATE TEMP TABLE d837_rs (bill TEXT PRIMARY KEY, hi TEXT)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_rs VALUES (?, ?)", [(b, b + "~") for b in bills[i:i + 500]])
            # (+cl.claim_status_code: the status index must not lead, or SQLite reads every answered claim and probes the
            # list for each, once per 500 bills; the list is walked and claims looked up by claim id)
            for b, pcn, paid_on in c.execute(f"""SELECT x.bill, cl.patient_control_number, t.payment_date FROM temp.d837_rs x
                                                 JOIN claims cl ON cl.patient_control_number >= x.bill AND cl.patient_control_number < x.hi
                                                 JOIN transactions t ON t.transaction_key = cl.transaction_key
                                                 WHERE COALESCE(t.superseded,0)=0 AND +cl.claim_status_code IN ({prim_sql})"""):
                if bill_of(pcn) == b and paid_on and (b not in first or paid_on < first[b]):
                    first[b] = paid_on
        c.execute("DROP TABLE IF EXISTS temp.d837_rs")
        keep = sorted(b for b in bills if first.get(b) and rows[best[b]][0] > first[b])
        # a bill the payer answered before any send of it on file went out before the 837 files on file begin: its first
        # submission and the answers to it may be older than the files (a CO-97 on the visit says its procedure was
        # answered elsewhere), so the archive cannot tell that a service the resend carries was never answered
        older = [b for b in keep if sent_first.get(b, "9999") > first[b]]
        if older:
            self.ema_counts["837_resend_bill_older_than_837s"] += len(older)
            keep = [b for b in keep if sent_first.get(b, "9999") <= first[b]]
        answered: dict[str, dict] = defaultdict(lambda: defaultdict(set))
        for i in range(0, len(keep), 500):
            c.execute("DROP TABLE IF EXISTS temp.d837_rs")
            c.execute("CREATE TEMP TABLE d837_rs (bill TEXT PRIMARY KEY, hi TEXT)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_rs VALUES (?, ?)", [(b, b + "~") for b in keep[i:i + 500]])
            for b, pcn, code, ocode, dos, mods in c.execute(
                    """SELECT x.bill, cl.patient_control_number, l.procedure_code, l.original_procedure_code,
                              COALESCE(l.service_date, cl.statement_from), l.modifiers
                       FROM temp.d837_rs x JOIN claims cl ON cl.patient_control_number >= x.bill AND cl.patient_control_number < x.hi
                       JOIN transactions t ON t.transaction_key = cl.transaction_key
                       LEFT JOIN lines l ON l.claim_key = cl.claim_key
                       WHERE COALESCE(t.superseded,0)=0"""):
                if bill_of(pcn) != b:
                    continue
                if code is None and ocode is None:
                    whole.add(b)            # a claim answered with no service line: the answer covers the whole claim
                    continue
                for cd_ in {code, ocode} - {None, ""}:
                    answered[b][(dos, cd_)].add(component_of(mods))
        c.execute("DROP TABLE IF EXISTS temp.d837_rs")
        out: dict[str, tuple] = {}
        for b in keep:
            if b in whole:
                continue
            done = answered.get(b) or {}

            def is_answered(x: tuple, done: dict = done) -> bool:
                got = done.get((x[1], x[0]))
                return bool(got) and part_fits(item_part(x), got)
            out[best[b]] = (rows[best[b]], first[b], is_answered)
        return out

    def _837_paid_elsewhere(self, cds: list[dict]) -> list[dict]:
        """The never-answered claims from the 837s less each service a primary payer paid (a payment that still stands)
        under another bill of the same member, by the 837's member id against the 835's patient and insured ids (T-N10:
        a bill made again for the same visit), for the service's own part (part_fits, T-C1: the TC paid is no payment of the
        reading, 26). Where the claim's patient is known (cd['who']: the 837's patient loop, its bill's remittances or EMA's
        claim), only a payment whose remittance names that patient counts: a subscriber's member id covers the family, and
        a sibling's paid visit is no payment of this child's (the verifier's adjacent case). The same patient is told by the
        first name where both sides give one (keyed alone: a payer that writes O'TESTER as OTESTER, or drops an accent, still
        names the same person; a sibling has another first name, fx-837-r2-5), else by the whole keyed name. A claim with
        every service paid so is not listed."""
        members = sorted({cd["k"].get("member") for cd in cds if cd["k"].get("member")})
        if not members:
            return cds
        c = self.conn
        from . import ema_reports as er
        er.register_functions(c, create=False)      # rw_name_key with the store's salt (None where it has none)
        # (member, date, code) -> {part: {(bill, the paid claim's patient keyed names, its keyed first names)}}
        paid: dict[tuple, dict] = defaultdict(lambda: defaultdict(set))
        c.execute("DROP TABLE IF EXISTS temp.d837_p")
        c.execute("CREATE TEMP TABLE d837_p (member TEXT PRIMARY KEY)")
        c.executemany("INSERT OR IGNORE INTO temp.d837_p VALUES (?)", [(m,) for m in members])
        for mid, iid, pcn, lk, code, ocode, dos, mods, nk, cnk, fk, cfk in c.execute(
                """SELECT cl.patient_id, cl.insured_id, cl.patient_control_number, l.line_key, l.procedure_code, l.original_procedure_code,
                          COALESCE(l.service_date, cl.statement_from), l.modifiers, rw_name_key(cl.patient_last, cl.patient_first),
                          CASE WHEN cl.corrected_patient_last IS NOT NULL OR cl.corrected_patient_first IS NOT NULL
                               THEN rw_name_key(COALESCE(cl.corrected_patient_last, cl.patient_last),
                                                COALESCE(cl.corrected_patient_first, cl.patient_first)) END,
                          rw_name_key(NULL, cl.patient_first), rw_name_key(NULL, cl.corrected_patient_first)
                   FROM claims cl JOIN transactions t ON t.transaction_key = cl.transaction_key
                   JOIN lines l ON l.claim_key = cl.claim_key
                   WHERE COALESCE(t.superseded,0)=0 AND cl.claim_status_code IN ('1','19')
                     AND (cl.patient_id IN (SELECT member FROM temp.d837_p) OR cl.insured_id IN (SELECT member FROM temp.d837_p))"""):
            if (self.standing.get(lk) or 0) <= 0:
                continue
            who = frozenset({nk, cnk} - {None})
            first = frozenset({fk, cfk} - {None})
            for m in {mid, iid} - {None, ""}:
                for cd_ in {code, ocode} - {None, ""}:
                    paid[(m, dos, cd_)][component_of(mods)].add((bill_of(pcn), who, first))
        c.execute("DROP TABLE IF EXISTS temp.d837_p")

        def same_patient(names: set, firsts: set, pn: frozenset, pf: frozenset) -> bool:
            if not names or not pn:
                return True                 # one side names no patient: the member's
            if firsts and pf:
                return bool(firsts & pf)    # both give a first name: it decides (one name written two ways)
            return bool(names & pn)

        def paid_elsewhere(m: str | None, x: tuple, b: str, names: set, firsts: set) -> bool:
            got = paid.get((m, x[1], x[0])) if m and x[1] else None
            if not got:
                return False
            # a payment under another bill, for this patient where both remittance and claim name one (else the member's)
            parts = {p for p, hits in got.items() if any(ob != b and same_patient(names, firsts, pn, pf) for ob, pn, pf in hits)}
            return part_fits(item_part(x), parts)
        out = []
        for cd in cds:
            m, b = cd["k"].get("member"), cd["b"]
            names = set((cd.get("who") or {}).get("names") or ())
            firsts = set((cd.get("who") or {}).get("firsts") or ())
            items = [x for x in cd["items"] if not paid_elsewhere(m, x, b, names, firsts)]
            if not items:
                self.ema_counts["837_paid_under_another_bill"] += 1
                continue
            if len(items) < len(cd["items"]):
                cd = dict(cd, items=items)
            out.append(cd)
        return out

    def _ema_group(self, name: str | None) -> str | None:
        """The payer group EMA's own reports give a payer name (ema_reports.payer_group_of: the owner's aliases, the VA's
        pattern, then codes.normalize_payer): the group its lines for that payer carry. EMA writes the 837s and the weekly
        exports, so the payer names in both are EMA's own."""
        if not name:
            return None
        cache = self.__dict__.setdefault("_ema_groups", {})
        if name not in cache:
            from . import ema_reports as er
            cache[name] = er.payer_group_of(self.conn, name)[0]
        return cache[name]

    def _ema_claims_found(self, step: str, cds: list[dict]) -> dict:
        """EMA's claim for each never-answered claim, and whether EMA's postings answered it after its send
        (ema_checks.claim_answers: by the claim id the A/R pairs, by its bill, else by its content). The content is matched
        under the group EMA gives the claim's payer name (_ema_group), else under the remittances' group for it (the only
        one used before: a claim to a payer the remittances name otherwise was never found). A claim still not found is
        looked for among its send-day twins (_send_day_twins), which are used one to one only: no more of these claims than
        twins share them, and the 837s and EMA's exports show no more claims sent that day with those lines to that payer
        group (with no EMA claim tied to their bill) than twins (_sent_that_day). A claim whose own EMA claim was charged
        on another day would otherwise take another patient's twin. Then one twin is its EMA claim; several, all answered,
        answer it whichever is its own.
        cds: [{'pcn', 'sent', 'items' [(code, date, charge, ...)], 'grp', 'ema_payer', 'match_group'?, 's837'?}].
        {claim id: claim_answers' entry; 'twins' lists the twins when each twin is one of these claims}."""
        from . import ema_checks as ec
        claims, alt = [], {}
        # the claim's content is every line it charged, a token charge included (_claim_content): EMA's claim carries it
        content = self._ema_step(step + "_content", self._claim_content, cds) or {}
        cds = [dict(cd, content=content.get(cd["pcn"]) or [(x[0], x[1], x[2]) for x in cd["items"]]) for cd in cds]
        for cd in cds:
            own = cd.get("match_group") or self._ema_group(cd.get("ema_payer")) or cd.get("grp")
            claims.append({"claim_id": cd["pcn"], "sent": cd["sent"], "payer_group": own,
                           "lines": [(x[1], x[0], x[2]) for x in cd["content"] if x[1]]})
            old = (normalize_payer(cd["ema_payer"])[0] if cd.get("grp") == VA_GROUP[0] and cd.get("ema_payer") else cd.get("grp"))
            if old and old != own:
                alt[cd["pcn"]] = old
        answers = self._ema_step(step, ec.claim_answers, self.conn, claims) or {}
        again = [dict(c, payer_group=alt[c["claim_id"]]) for c in claims
                 if c["claim_id"] in alt and (answers.get(c["claim_id"]) or {}).get("link") is None]
        if again:
            for cid, a in (self._ema_step(step + "_alt", ec.claim_answers, self.conn, again) or {}).items():
                if a.get("link") is not None:
                    answers[cid] = a
        groups = {c["claim_id"]: {c["payer_group"], alt.get(c["claim_id"])} - {None} for c in claims}
        found = self._ema_step(step + "_twins", self._send_day_twins, cds, answers, groups) or {}
        shared = Counter(tuple(f[0]) for f in found.values())
        sent = self._ema_step(step + "_sent_that_day", self._sent_that_day,
                              {(f[1], f[2], frozenset(groups.get(cid) or ())) for cid, f in found.items()})
        if sent is None:
            return answers                  # the count failed (recorded): no twin serves
        twins = {cid: f[0] for cid, f in found.items()
                 if (len(f) > 3 and f[3])           # the patient's name chose it among several twins
                 or (shared[tuple(f[0])] <= len(f[0]) and sent.get((f[1], f[2], frozenset(groups.get(cid) or ())), 0) <= len(f[0]))}
        if twins:
            sent_of = {cd["pcn"]: cd["sent"] for cd in cds}
            done = self._ema_step(step + "_twins_answered", self._links_answered,
                                  [(cid, lk, sent_of[cid]) for cid, lks in twins.items() for lk in lks]) or set()
            for cid, lks in twins.items():
                if len(lks) == 1:
                    answers[cid] = {"answered": (cid, lks[0]) in done, "how": "send_day", "on": None, "paid_cents": 0,
                                    "link": lks[0], "bridge": "send_day"}
                elif all((cid, lk) in done for lk in lks):
                    answers[cid] = {"answered": True, "how": "send_day_twins", "on": None, "paid_cents": 0, "link": None,
                                    "bridge": "send_day_twins"}
                elif shared[tuple(lks)] == len(lks):
                    # each twin is one of these claims, which stay listed: EMA's open ones are on the list through them
                    answers.setdefault(cid, {"answered": False, "how": None, "on": None, "paid_cents": 0, "link": None,
                                             "bridge": None})["twins"] = list(lks)
        # a claim EMA charged days before it went out (held for coding or an authorization) is no send-day twin: its EMA
        # claim is the only one of its lines no payer answered, charged before the send (answers-7). One claim, one item:
        # the claim keeps that EMA claim, and Production's never-answered check (G8) leaves it
        lone = self._ema_step(step + "_unanswered_before_send", self._unanswered_before_send, cds, answers, groups) or {}
        for cid, lk in lone.items():
            answers[cid] = {"answered": False, "how": None, "on": None, "paid_cents": 0, "link": lk,
                            "bridge": "unanswered_before_send"}
        return answers

    def _claim_content(self, cds: list[dict]) -> dict[str, list[tuple]]:
        """Each never-answered claim's charged lines as its EMA claim holds them, to find that claim by its content: the
        lines it is listed for and the lines billed above $0 and under $1 (a token charge: a code EMA bills at a penny so
        the line goes out). A token line is nothing owed, so it is never listed or priced (answers-3), but EMA's claim
        carries it as a charge line: left out, the claim's content equals no EMA claim, and a claim EMA shows paid is listed
        as never answered. The token lines are the claim's own (cd['token_lines'], from EMA's weekly exports), else those of
        the 837 send its lines were read from (cd['s837']). {claim id: [(code, date, charge cents)]}."""
        out: dict[str, list[tuple]] = {}
        want: dict[str, str] = {}
        for cd in cds:
            out[cd["pcn"]] = [(x[0], x[1], int(x[2] or 0)) for x in cd["items"]]
            r8 = cd.get("s837")
            if any(0 < (x[2] or 0) < 100 for x in cd["items"]):
                continue                    # the pass kept its token lines among the claim's lines: they are its content
            if cd.get("token_lines") is not None:
                out[cd["pcn"]] += [(x[0], x[1], int(x[2] or 0)) for x in cd["token_lines"]]
            elif r8 is not None and len(r8) > 7 and r8[7]:
                want[r8[7]] = cd["pcn"]
        if want:
            for key, lines in self._lines_837(want).items():
                out[want[key]] += [(code, dos, int(chg)) for code, dos, chg, _units, _mods in lines if 0 < (chg or 0) < 100]
        return out

    def _unanswered_before_send(self, cds: list[dict], answers: dict, groups: dict) -> dict[str, int]:
        """The EMA claim of a never-answered claim that no bridge and no send-day twin found, when it is the only EMA claim
        of the claim's lines that no payer answered (answers-7): the same day, code and charge on every line and no other line
        with a charge, a payer group of the claim's (groups: claim id -> its groups), charges first posted on or before the
        day the claim first went out (EMA posts a claim's charges before it sends it, days before when the claim is held
        for coding or an authorization), a balance still open, nothing a payer answered on any line (Production's own
        never-answered rule, ema_checks.line_answered_sql), and no bill the link map or the A/R ties to it. The claim's
        patient must be named (_who), and the EMA claim must name that patient: with nothing naming the patient the claim's
        own EMA claim may be an answered one (no candidate), and the one left another patient's, which Production's check
        must keep listing (fx-exports-r1-4, r2-6). An answered EMA claim of the lines that names the patient may be the
        claim's own: then none is taken. A member id's one remitted name only confirms a lone candidate, never chooses among
        several. Used one to one only: no other of these claims takes the same EMA claim, and the claim's bill is the only
        bill on file (the 837s and EMA's weekly exports, less those tied to an EMA claim) with those lines to that payer
        group (_bills_with_lines). {claim id: EMA claim number}."""
        from . import ema_checks as ec
        c = self.conn
        taken = {a["link"] for a in answers.values() if a.get("link") is not None}
        taken |= {lk for a in answers.values() for lk in (a.get("twins") or ())}
        want, n_rows, info = [], {}, {}
        for cd in cds:
            a = answers.get(cd["pcn"]) or {}
            if a.get("link") is not None or a.get("twins") or a.get("answered"):
                continue
            first = str((cd.get("s837") or [None, None])[1] or cd.get("first_sent") or "")[:10]
            agg: dict[tuple, int] = defaultdict(int)       # EMA keeps one line per date and code
            for x in cd.get("content") or cd["items"]:
                if x[0] and x[1]:
                    agg[(x[1], x[0])] += int(x[2] or 0)
            if not first or not agg:
                continue
            n_rows[cd["pcn"]] = len(agg)
            info[cd["pcn"]] = (cd.get("b") or bill_of(cd["pcn"]), tuple(sorted((svc, cpt, chg) for (svc, cpt), chg in agg.items())), cd)
            want += [(cd["pcn"], svc, cpt, chg, first) for (svc, cpt), chg in sorted(agg.items())]
        if not want:
            return {}
        c.execute("DROP TABLE IF EXISTS temp.d837_ub")
        c.execute("CREATE TEMP TABLE d837_ub (cid TEXT, svc TEXT, cpt TEXT, charge INTEGER, hi TEXT)")
        c.executemany("INSERT INTO temp.d837_ub VALUES (?, ?, ?, ?, ?)", want)
        hits = c.execute(f"""SELECT x.cid, l.link, COUNT(*), MAX(l.payer_group), MAX(l.bill), MAX(l.claim_id), MAX(l.name_key),
                                    MAX({ec.line_answered_sql('l')}), SUM(COALESCE(l.balance, 0))
                             FROM temp.d837_ub x JOIN ema_lines l ON l.svc = x.svc AND l.cpt = x.cpt AND l.charges = x.charge
                             WHERE COALESCE(l.facility, 0) = 0 AND (COALESCE(l.flags, 0) & 17) = 0
                               AND SUBSTR(l.first_charge_posted, 1, 10) <= x.hi
                             GROUP BY x.cid, l.link ORDER BY x.cid, l.link""").fetchall()
        c.execute("DROP TABLE IF EXISTS temp.d837_ub")
        hits = [h for h in hits if h[2] == n_rows[h[0]] and h[1] not in taken]
        if not hits:
            return {}
        links = sorted({h[1] for h in hits})
        per_link: dict[int, int] = {}
        paired: dict[int, set] = defaultdict(set)
        for i in range(0, len(links), 500):
            part = links[i:i + 500]
            q = ",".join("?" * len(part))
            for lk, n in c.execute(f"SELECT link, COUNT(*) FROM ema_lines WHERE link IN ({q}) AND COALESCE(charges, 0) > 0 GROUP BY link",
                                   part):
                per_link[lk] = n
            for lk, cid in c.execute(f"SELECT link, claim_id FROM ema_claim_links WHERE link IN ({q}) AND claim_id IS NOT NULL", part):
                paired[lk].add(bill_of(cid))
        cand: dict[str, list] = defaultdict(list)          # claim id -> [(EMA claim number, its patient's name key)]
        done: dict[str, set] = defaultdict(set)            # claim id -> the patients' names on answered EMA claims of its lines
        for cid, lk, _n, grp, bill, claim, nk, answered, balance in hits:
            mine = {info[cid][0]}
            if per_link.get(lk) != n_rows[cid]:
                continue                    # EMA's claim has lines this claim does not
            if grp and not any(ec._same_payer(grp, g) for g in groups.get(cid) or ()):
                continue
            if (bill and bill not in mine) or (claim and bill_of(claim) not in mine) or (paired.get(lk, set()) - mine):
                continue                    # the link map or the A/R ties it to another bill
            if answered or (balance or 0) <= 0:
                if nk is not None:
                    done[cid].add(nk)
                continue                    # a payer answered it, or nothing is open on it: not a claim no payer answered
            cand[cid].append((lk, nk))
        if not cand:
            return {}
        who = self._who([info[cid][2] for cid in sorted(cand)], answers)
        pick: dict[str, int] = {}
        for cid, xs in sorted(cand.items()):
            w = who.get(cid) or {}
            own, weak = w.get("names") or set(), w.get("weak") or set()
            keys = own or weak
            if not keys:
                continue                    # nothing names the patient: the claim's own EMA claim may be an answered one,
                                            # which is no candidate, and the one left another patient's (fx-exports-r1-4, r2-6)
            if done.get(cid, set()) & keys:
                continue                    # an EMA claim of its lines that names its patient was answered: maybe its own
            if not own and len(xs) > 1:
                continue                    # a member id's one remitted name never chooses among several
            xs = [x for x in xs if x[1] is not None and x[1] in keys]
            if len(xs) == 1:
                pick[cid] = xs[0][0]
        uses = Counter(pick.values())
        pick = {cid: lk for cid, lk in pick.items() if uses[lk] == 1}
        if not pick:
            return {}
        bills = self._bills_with_lines({cid: (info[cid][1], frozenset(groups.get(cid) or ())) for cid in pick})
        return {cid: lk for cid, lk in sorted(pick.items()) if not ((bills.get(cid) or set()) - {info[cid][0]})}

    def _bills_with_lines(self, keys: dict) -> dict[str, set]:
        """The bills on file whose lines are exactly these, billed to a payer of these groups: keys {claim id: (lines
        ((date, code, charge cents), ...), groups)}. Read from the 837s (every send but a void, each claim with all its lines)
        and EMA's weekly exports (each claim's charge lines), each bill once, less the bills the link map or the A/R ties to
        an EMA claim (their EMA claim is known). {claim id: {bills}}."""
        from . import ema_checks as ec
        c = self.conn
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        by_lines: dict[tuple, list] = defaultdict(list)
        for cid, (lines, gs) in keys.items():
            by_lines[lines].append((cid, gs))
        days = sorted({ln[0] for lines in by_lines for ln in lines})
        out: dict[str, set] = defaultdict(set)

        def count(bill: str, payer: str | None, rows: dict) -> None:
            lines = tuple(sorted((svc, code, chg) for (svc, code), chg in rows.items() if chg > 0))
            g = self._ema_group(payer)
            for cid, gs in by_lines.get(lines) or ():
                if g and any(ec._same_payer(g, x) for x in gs):
                    out[cid].add(bill)
        if "claims_837" in tables and days:
            sends: set[tuple] = set()       # (claim id, claim key) of the 837 claims with a line on one of these days
            for i in range(0, len(days), 200):
                part = days[i:i + 200]
                sends |= set(tuple(r) for r in c.execute(f"""SELECT DISTINCT c8.patient_control_number, c8.claim_837_key FROM lines_837 l
                                             JOIN claims_837 c8 ON c8.claim_837_key = l.claim_837_key
                                             WHERE SUBSTR(COALESCE(l.service_date, c8.service_date), 1, 10) IN ({','.join('?' * len(part))})
                                               AND COALESCE(c8.frequency_code, '1') <> '8'
                                               AND c8.patient_control_number IS NOT NULL""", part))
            per: dict[str, list] = {}
            ordered = sorted(sends)
            for i in range(0, len(ordered), 500):
                c.execute("DROP TABLE IF EXISTS temp.d837_bl")
                c.execute("CREATE TEMP TABLE d837_bl (pcn TEXT, k TEXT, PRIMARY KEY (pcn, k))")
                c.executemany("INSERT OR IGNORE INTO temp.d837_bl VALUES (?, ?)", ordered[i:i + 500])
                for pcn, key, payer, code, svc, chg in c.execute(
                        """SELECT x.pcn, x.k, c8.payer_name, l.procedure_code, SUBSTR(COALESCE(l.service_date, c8.service_date), 1, 10),
                                  l.charge_cents
                           FROM temp.d837_bl x JOIN lines_837 l ON l.patient_control_number = x.pcn AND l.claim_837_key = x.k
                           JOIN claims_837 c8 ON c8.claim_837_key = x.k"""):
                    p = per.setdefault(key, [pcn, payer, defaultdict(int)])
                    if code and svc:
                        p[2][(svc, code)] += chg or 0
            c.execute("DROP TABLE IF EXISTS temp.d837_bl")
            for pcn, payer, rows in per.values():
                count(bill_of(pcn), payer, rows)
        if "ema_rows" in tables and days:
            q = ",".join("?" * len(days))
            ids = sorted({cid for (cid,) in c.execute(
                f"""SELECT DISTINCT claim_id FROM ema_rows WHERE claim_id IS NOT NULL AND family = 'charge_lines'
                    AND SUBSTR(service_date, 1, 10) IN ({q})""", days)})
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                ex: dict[str, list] = {}            # claim id -> [Counter(payers), {(date, code): charge}]
                for cid, act, payer, svc, code, chg in c.execute(
                        f"""SELECT claim_id, activity_type, payer, SUBSTR(service_date, 1, 10), cpt, charges_cents FROM ema_rows
                            WHERE claim_id IN ({','.join('?' * len(part))}) AND family = 'charge_lines'""", part):
                    e = ex.setdefault(cid, [Counter(), defaultdict(int)])
                    if payer:
                        e[0][payer] += 1
                    if (act or "").lower() == "charge" and svc and code:
                        e[1][(svc, code)] += abs(chg or 0)
                for cid, (payers, rows) in ex.items():
                    if payers:
                        count(bill_of(cid), payers.most_common(1)[0][0], rows)
        every = sorted({b for bs in out.values() for b in bs})
        tied: set[str] = set()
        for i in range(0, len(every), 500):
            part = every[i:i + 500]
            q = ",".join("?" * len(part))
            tied |= {b for (b,) in c.execute(f"SELECT bill FROM ema_link_map WHERE bill IN ({q}) AND COALESCE(conflict, 0) = 0", part)}
            tied |= {b for (b,) in c.execute(f"SELECT bill FROM ema_claim_links WHERE bill IN ({q})", part)}
        return {cid: bs - tied for cid, bs in out.items()}

    def _send_day_twins(self, cds: list[dict], answers: dict, groups: dict) -> dict[str, list[int]]:
        """EMA's claims that can be a never-answered claim whose own EMA claim no bridge found (two EMA claims of the same
        content, say): the same day, code and charge on every line and no other line, a payer group of the claim's
        (groups: claim id -> its groups), and charges first posted on the day the claim first went out or up to
        SEND_DAY_SLACK days before (EMA writes the 837s: it posts a claim's charges and sends it the same day). An EMA claim
        the link map or the A/R ties to another bill, or a bridge gave another claim, is not a twin.
        {claim id: ([EMA claim numbers], the day it first went out, its lines ((date, code, charge), ...))}, for the claims
        with any."""
        from . import ema_checks as ec
        c = self.conn
        taken = {a["link"] for a in answers.values() if a.get("link") is not None}
        rows, n_rows, info = [], {}, {}
        for cd in cds:
            if (answers.get(cd["pcn"]) or {}).get("link") is not None:
                continue
            first = str((cd.get("s837") or [None, None])[1] or cd.get("first_sent") or "")[:10]
            lo = _add_days(first, -SEND_DAY_SLACK) if first else None
            agg: dict[tuple, int] = defaultdict(int)       # EMA keeps one line per date and code
            for x in cd.get("content") or cd["items"]:
                if x[0] and x[1]:
                    agg[(x[1], x[0])] += int(x[2] or 0)
            if not lo or not agg:
                continue
            n_rows[cd["pcn"]] = len(agg)
            info[cd["pcn"]] = dict(cd, _first=first, _rows=tuple(sorted((svc, cpt, chg) for (svc, cpt), chg in agg.items())))
            rows += [(cd["pcn"], svc, cpt, chg, lo, first) for (svc, cpt), chg in sorted(agg.items())]
        if not rows:
            return {}
        c.execute("DROP TABLE IF EXISTS temp.d837_tw")
        c.execute("CREATE TEMP TABLE d837_tw (cid TEXT, svc TEXT, cpt TEXT, charge INTEGER, lo TEXT, hi TEXT)")
        c.executemany("INSERT INTO temp.d837_tw VALUES (?, ?, ?, ?, ?, ?)", rows)
        hits = c.execute("""SELECT x.cid, l.link, COUNT(*), MAX(l.payer_group), MAX(l.bill), MAX(l.claim_id)
                            FROM temp.d837_tw x JOIN ema_lines l ON l.svc = x.svc AND l.cpt = x.cpt AND l.charges = x.charge
                            WHERE COALESCE(l.facility, 0) = 0 AND (COALESCE(l.flags, 0) & 17) = 0
                              AND SUBSTR(l.first_charge_posted, 1, 10) BETWEEN x.lo AND x.hi
                            GROUP BY x.cid, l.link""").fetchall()
        c.execute("DROP TABLE IF EXISTS temp.d837_tw")
        hits = [h for h in hits if h[2] == n_rows[h[0]] and h[1] not in taken]
        links = sorted({h[1] for h in hits})
        per_link: dict[int, int] = {}
        paired: dict[int, set] = defaultdict(set)
        for i in range(0, len(links), 500):
            part = links[i:i + 500]
            q = ",".join("?" * len(part))
            for lk, n in c.execute(f"SELECT link, COUNT(*) FROM ema_lines WHERE link IN ({q}) AND COALESCE(charges, 0) > 0 GROUP BY link",
                                   part):
                per_link[lk] = n
            for lk, cid in c.execute(f"SELECT link, claim_id FROM ema_claim_links WHERE link IN ({q}) AND claim_id IS NOT NULL", part):
                paired[lk].add(bill_of(cid))
        out: dict[str, list] = defaultdict(list)
        for cid, lk, _n, grp, bill, claim in hits:
            mine = {info[cid].get("b") or bill_of(cid)}
            if per_link.get(lk) != n_rows[cid]:
                continue                    # EMA's claim has lines this claim does not
            if grp and not any(ec._same_payer(grp, g) for g in groups.get(cid) or ()):
                continue
            if (bill and bill not in mine) or (claim and bill_of(claim) not in mine) or (paired.get(lk, set()) - mine):
                continue                    # the link map or the A/R ties it to another bill
            out[cid].append(lk)
        named = self._twins_by_name(out, info, answers, groups) if out else set()
        return {k: (sorted(v), info[k]["_first"], info[k]["_rows"], k in named) for k, v in out.items()}

    def _twins_by_name(self, out: dict, info: dict, answers: dict, groups: dict | None = None) -> set[str]:
        """The patient's name and the send-day twins (the residual of fix7b; fx-exports-r1-3, r1-5): out {claim id: [EMA
        claim numbers]} is narrowed in place, and the claims whose twin the name chose are returned (named: their twin is
        their EMA claim whatever else went out that day). Who the claim's patient is (_who), and whose each twin is:
        - the claim's: its EMA claim names the patient the claim's own evidence names (its bill's remittances, the EMA
          claim a bridge found, EMA's patient), or its patient's other bills on file are the claim's (_twin_kin: one of
          them is the claim's own bill, their 837s carry the claim's member id, or their remittances name the claim's
          patient, own or by its member id's one remitted name: remittances compared with remittances);
        - another patient's: EMA's own name for the claim's patient (EMA's patient, a bridged EMA claim) names someone else,
          or its patient's other bills on file carry member ids and remitted names, none of them the claim's;
        - otherwise not known. A remittance and EMA can spell one person's name otherwise (a middle name, a nickname), so
          a twin whose EMA name differs from a remitted name is set aside only when another EMA claim of the claim's
          content that no bill holds may be the claim's own (_spare_ema_claims); else it is the only place the claim's EMA
          claim can be (on the real data: six claims EMA shows paid were listed when such twins were set aside).
        A twin of another patient is never the claim's. Of several twins, the one the claim's own evidence names is the
        claim's (named), one to one: two claims that name the same twin both fall back to the twins rule, and a twin a
        name gave one claim is no other claim's. A member id's one remitted name never chooses (a subscriber's id covers
        the family, and a dependent never remitted takes that name): it only keeps a twin. Named by nothing: the twins
        rule alone (_ema_claims_found)."""
        c = self.conn
        groups = groups or {}
        who = self._who([info[cid] for cid in sorted(out)], answers)
        lks = sorted({lk for v in out.values() for lk in v})
        nk_of: dict[int, set] = defaultdict(set)
        for i in range(0, len(lks), 500):
            part = lks[i:i + 500]
            for lk, nk in c.execute(f"""SELECT DISTINCT link, name_key FROM ema_lines WHERE link IN ({','.join('?' * len(part))})
                                        AND name_key IS NOT NULL""", part):
                nk_of[lk].add(nk)
        kin = self._twin_kin(lks) if any((who.get(cid) or {}).get("names") or (who.get(cid) or {}).get("weak") for cid in out) else {}
        named: set[str] = set()
        before: dict[str, list] = {}
        loose: dict[str, tuple] = {}          # claim id -> (its twins, those set aside when another EMA claim may be its own)
        for cid in sorted(out):
            w = who.get(cid) or {}
            own, own_ema, weak = w.get("names") or set(), w.get("names_ema") or set(), w.get("weak") or set()
            if not (own or weak):
                continue                    # nothing names the patient: the twins rule alone
            b = info[cid].get("b") or bill_of(cid)
            members = w.get("members") or set()
            twins, keep, differs = list(out[cid]), [], []
            for lk in twins:
                nks, k = nk_of.get(lk) or set(), kin.get(lk) or {}
                if own_ema and nks and not nks & own_ema:
                    continue                # EMA's own name for the patient is another chart's: another patient's
                if nks & own or b in k.get("bills", ()) or k.get("members", set()) & members or k.get("names", set()) & (own | weak):
                    keep.append(lk)         # the claim's patient's
                elif k.get("members") or k.get("names"):
                    continue                # its patient's bills on file carry other member ids and names: another patient's
                else:
                    keep.append(lk)
                    if nks and not nks & (own | weak):
                        differs.append(lk)  # a name of its own, nothing else known
            mine = [lk for lk in keep if (nk_of.get(lk) or set()) & own]
            if len(twins) > 1 and len(mine) == 1:
                before[cid] = keep
                keep = mine
                named.add(cid)
            elif differs:
                loose[cid] = (twins, differs)
            out[cid] = keep
        if loose:
            taken = {a["link"] for a in answers.values() if a.get("link") is not None}
            spare = self._spare_ema_claims({cid: (info[cid]["_rows"], groups.get(cid) or set(), set(t)) for cid, (t, _d) in loose.items()},
                                           taken)
            for cid, (_t, differs) in loose.items():
                if spare.get(cid):
                    out[cid] = [lk for lk in out[cid] if lk not in differs]
        # one to one: a twin two claims' names chose is neither's; a twin a name gave one claim is no other's
        uses = Counter(out[cid][0] for cid in named)
        for cid in sorted(named):
            if uses[out[cid][0]] > 1:
                named.discard(cid)
                out[cid] = before[cid]
        given = {out[cid][0]: cid for cid in named}
        for cid in list(out):
            if cid not in named:
                out[cid] = [lk for lk in out[cid] if lk not in given]
            if not out[cid]:
                del out[cid]
        return named

    def _twin_kin(self, links: list[int]) -> dict[int, dict]:
        """What the bills on file say of the patients of these EMA claims: each one's other EMA claims (by EMA's patient,
        its name and birth date, else its keyed name) that a bill holds (EMA's lines, the bill map, the A/R's pairs), and
        those bills' member ids (their 837s' subscriber ids) and patient names (their remittances' keyed names).
        {EMA claim number: {'bills', 'members', 'names'}}."""
        from . import ema_reports as er
        c = self.conn
        er.register_functions(c, create=False)          # the keyed name (rw_name_key) the remittances' names are read with
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        who_of: dict[int, tuple] = {}                    # EMA claim -> ('p', persons) or ('n', name keys)
        for i in range(0, len(links), 500):
            part = links[i:i + 500]
            got: dict[int, list] = defaultdict(lambda: [set(), set()])
            for lk, person, nk in c.execute(f"""SELECT DISTINCT link, person, name_key FROM ema_lines
                                                WHERE link IN ({','.join('?' * len(part))})""", part):
                if person is not None:
                    got[lk][0].add(person)
                if nk is not None:
                    got[lk][1].add(nk)
            for lk, (ps, nks) in got.items():
                if ps or nks:
                    who_of[lk] = ("p", ps) if ps else ("n", nks)
        others: dict[int, set] = defaultdict(set)        # EMA claim -> the patient's other EMA claims
        for col, kind in (("person", "p"), ("name_key", "n")):
            keys = sorted({k for t, ks in who_of.values() if t == kind for k in ks})
            by_key: dict[int, set] = defaultdict(set)
            for i in range(0, len(keys), 500):
                part = keys[i:i + 500]
                for lk, k in c.execute(f"SELECT DISTINCT link, {col} FROM ema_lines WHERE {col} IN ({','.join('?' * len(part))})", part):
                    by_key[k].add(lk)
            for lk, (t, ks) in who_of.items():
                if t == kind:
                    others[lk] |= {o for k in ks for o in by_key.get(k) or ()} - {lk}
        olks = sorted({o for v in others.values() for o in v})
        bills_of: dict[int, set] = defaultdict(set)
        for i in range(0, len(olks), 500):
            part = olks[i:i + 500]
            q = ",".join("?" * len(part))
            for lk, b in c.execute(f"SELECT DISTINCT link, bill FROM ema_lines WHERE link IN ({q}) AND bill IS NOT NULL", part):
                bills_of[lk].add(b)
            for lk, cid in c.execute(f"SELECT link, claim_id FROM ema_claim_links WHERE link IN ({q}) AND claim_id IS NOT NULL", part):
                bills_of[lk].add(bill_of(cid))
            if "ema_link_map" in tables:
                for lk, b in c.execute(f"SELECT link, bill FROM ema_link_map WHERE link IN ({q}) AND COALESCE(conflict, 0) = 0", part):
                    bills_of[lk].add(b)
        bills = sorted({b for bs in bills_of.values() for b in bs if b})
        member_of: dict[str, set] = defaultdict(set)
        names_of: dict[str, set] = defaultdict(set)
        name_sql = """rw_name_key(cl.patient_last, cl.patient_first),
                      CASE WHEN cl.corrected_patient_last IS NOT NULL OR cl.corrected_patient_first IS NOT NULL
                           THEN rw_name_key(COALESCE(cl.corrected_patient_last, cl.patient_last),
                                            COALESCE(cl.corrected_patient_first, cl.patient_first)) END"""
        for i in range(0, len(bills), 500):
            c.execute("DROP TABLE IF EXISTS temp.d837_tk")
            c.execute("CREATE TEMP TABLE d837_tk (bill TEXT PRIMARY KEY, hi TEXT)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_tk VALUES (?, ?)", [(b, b + "~") for b in bills[i:i + 500]])
            if "claims_837" in tables:
                for b, pcn, member in c.execute("""SELECT x.bill, c8.patient_control_number, c8.subscriber_id FROM temp.d837_tk x
                                                   JOIN claims_837 c8 ON c8.patient_control_number >= x.bill AND c8.patient_control_number < x.hi
                                                   WHERE c8.subscriber_id IS NOT NULL AND c8.subscriber_id <> ''"""):
                    if bill_of(pcn) == b:
                        member_of[b].add(member)
            for b, pcn, nk, cnk in c.execute(f"""SELECT x.bill, cl.patient_control_number, {name_sql} FROM temp.d837_tk x
                                                JOIN claims cl ON cl.patient_control_number >= x.bill AND cl.patient_control_number < x.hi"""):
                if bill_of(pcn) == b:
                    names_of[b] |= {nk, cnk} - {None}
        c.execute("DROP TABLE IF EXISTS temp.d837_tk")
        out: dict[int, dict] = {}
        for lk in links:
            bs = {b for o in others.get(lk) or () for b in bills_of.get(o) or () if b}
            out[lk] = {"bills": bs, "members": {m for b in bs for m in member_of.get(b) or ()},
                       "names": {n for b in bs for n in names_of.get(b) or ()}}
        return out

    def _spare_ema_claims(self, want: dict, taken: set) -> dict[str, int]:
        """How many EMA claims, besides a claim's send-day twins, may be the claim's own: each holds exactly the claim's
        charged lines (every line's day, code and charge, and no other charged line), under a payer group of the claim's,
        and no bill holds it (EMA's lines, the bill map, the A/R's pairs) nor did a bridge give it to another claim. want
        {claim id: (lines ((date, code, charge cents), ...), groups, twins)}. {claim id: number}."""
        from . import ema_checks as ec
        c = self.conn
        rows, n_rows = [], {}
        for cid, (lines, _g, _t) in want.items():
            n_rows[cid] = len(lines)
            rows += [(cid, svc, cpt, chg) for svc, cpt, chg in lines]
        if not rows:
            return {}
        c.execute("DROP TABLE IF EXISTS temp.d837_sp")
        c.execute("CREATE TEMP TABLE d837_sp (cid TEXT, svc TEXT, cpt TEXT, charge INTEGER)")
        c.executemany("INSERT INTO temp.d837_sp VALUES (?, ?, ?, ?)", rows)
        hits = c.execute("""SELECT x.cid, l.link, COUNT(*), MAX(l.payer_group), MAX(l.bill) FROM temp.d837_sp x
                            JOIN ema_lines l ON l.svc = x.svc AND l.cpt = x.cpt AND l.charges = x.charge
                            WHERE COALESCE(l.facility, 0) = 0 AND (COALESCE(l.flags, 0) & 17) = 0
                            GROUP BY x.cid, l.link""").fetchall()
        c.execute("DROP TABLE IF EXISTS temp.d837_sp")
        hits = [h for h in hits if h[2] == n_rows[h[0]] and h[1] not in taken and h[1] not in want[h[0]][2] and not h[4]]
        links = sorted({h[1] for h in hits})
        per_link: dict[int, int] = {}
        tied: set[int] = set()
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for i in range(0, len(links), 500):
            part = links[i:i + 500]
            q = ",".join("?" * len(part))
            for lk, n in c.execute(f"SELECT link, COUNT(*) FROM ema_lines WHERE link IN ({q}) AND COALESCE(charges, 0) > 0 GROUP BY link", part):
                per_link[lk] = n
            tied |= {lk for (lk,) in c.execute(f"SELECT link FROM ema_claim_links WHERE link IN ({q}) AND claim_id IS NOT NULL", part)}
            if "ema_link_map" in tables:
                tied |= {lk for (lk,) in c.execute(f"SELECT link FROM ema_link_map WHERE link IN ({q}) AND COALESCE(conflict, 0) = 0", part)}
        out: dict[str, int] = defaultdict(int)
        for cid, lk, _n, grp, _bill in hits:
            if per_link.get(lk) != n_rows[cid] or lk in tied:
                continue
            if grp and want[cid][1] and not any(ec._same_payer(grp, g) for g in want[cid][1]):
                continue
            out[cid] += 1
        return dict(out)

    def _who(self, cds: list[dict], answers: dict) -> dict[str, dict]:
        """Who each claim's patient is, for this pass's rules that tell people apart (the send-day twins, the before-send
        bridge, the one-visit rules): {claim id: {'names': keyed names from the claim's own evidence, 'names_ema': those of
        them EMA itself gives (its patient, a bridged EMA claim: EMA's spelling), 'persons': keyed name and birth date,
        'members': the member ids its 837s (or cd['k']['member']) name, 'weak': the one name the remittances give such a
        member id}}. Own evidence (_patient_keys without the member ids' names): the remittances of
        its own bill, the EMA claim a bridge found for it, and EMA's patient (a claim of the weekly exports: the patient's
        EMA claims the A/R or the bill map ties). A member id's one remitted name is never own evidence: a subscriber's id
        covers the family, and a dependent never remitted takes the subscriber's (or a sibling's) name there."""
        persons: dict[str, set] = {}
        ema_names: dict[str, set] = {}
        own = self._patient_keys(cds, answers, member_names=False, persons=persons, ema_names=ema_names) or {}
        every = self._patient_keys(cds, answers) or {}
        c = self.conn
        members: dict[str, set] = defaultdict(set)
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='claims_837'").fetchone():
            bills = sorted({cd.get("b") or bill_of(cd["pcn"]) for cd in cds})
            for i in range(0, len(bills), 500):
                c.execute("DROP TABLE IF EXISTS temp.d837_wm")
                c.execute("CREATE TEMP TABLE d837_wm (bill TEXT PRIMARY KEY, hi TEXT)")
                c.executemany("INSERT OR IGNORE INTO temp.d837_wm VALUES (?, ?)", [(b, b + "~") for b in bills[i:i + 500]])
                for b, pcn, member in c.execute("""SELECT x.bill, c8.patient_control_number, c8.subscriber_id FROM temp.d837_wm x
                                                   JOIN claims_837 c8 ON c8.patient_control_number >= x.bill AND c8.patient_control_number < x.hi
                                                   WHERE c8.subscriber_id IS NOT NULL AND c8.subscriber_id <> ''"""):
                    if bill_of(pcn) == b:
                        members[b].add(member)
            c.execute("DROP TABLE IF EXISTS temp.d837_wm")
        out: dict[str, dict] = {}
        for cd in cds:
            b = cd.get("b") or bill_of(cd["pcn"])
            ms = set(members.get(b) or ()) | ({cd["k"]["member"]} if (cd.get("k") or {}).get("member") else set())
            mine = set(own.get(cd["pcn"]) or ())
            out[cd["pcn"]] = {"names": mine, "names_ema": set(ema_names.get(cd["pcn"]) or ()),
                              "persons": set(persons.get(cd["pcn"]) or ()), "members": ms,
                              "weak": set(every.get(cd["pcn"]) or ()) - mine}
        return out

    @staticmethod
    def _one_patient(a: dict, b: dict) -> bool:
        """Whether two claims' patients (_who) are one person, as the 837 pass matches them (_837_one_visit): the same keyed
        name and birth date, or the same keyed name under the same member id, each name from the claim's own evidence.
        Never the keyed name alone (a father and a son of one name: JR and SR are dropped from it), and never a member id
        alone or its one remitted name (family members on one subscriber id)."""
        if a.get("persons") and b.get("persons") and a["persons"] & b["persons"]:
            return True
        return bool(a.get("names") and b.get("names") and a["names"] & b["names"]
                    and a.get("members") and b.get("members") and a["members"] & b["members"])

    def _sent_that_day(self, keys: set) -> dict:
        """How many bills went out on a day with these lines to a payer of these groups: the 837s' claims on the day each
        first went out, and EMA's weekly exports' claims on their submission date (else the day their bill was made), each
        bill once, leaving out a bill the link map or the A/R ties to an EMA claim (its EMA claim is no send-day twin
        either). keys {(day, lines, groups)} with lines ((date, code, charge cents), ...). {key: number of bills}."""
        from . import ema_checks as ec
        c = self.conn
        if not keys:
            return {}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        want: dict[tuple, list] = defaultdict(list)
        for day, lines, gs in keys:
            want[(day, lines)].append(gs)
        days = sorted({k[0] for k in want})
        day_set = set(days)
        bills: dict[tuple, set] = defaultdict(set)

        def count(bill: str, payer: str | None, day: str, rows: dict) -> None:
            lines = tuple(sorted((svc, code, chg) for (svc, code), chg in rows.items() if chg > 0))
            g = self._ema_group(payer)
            for gs in want.get((day, lines)) or ():
                if g and any(ec._same_payer(g, x) for x in gs):
                    bills[(day, lines, gs)].add(bill)
        per: dict[str, list] = {}                   # claim key -> [claim id, payer name, day, {(date, code): charge}]
        for i in range(0, len(days) if "claims_837" in tables else 0, 200):
            part = days[i:i + 200]
            for key, pcn, payer, day, code, svc, chg in c.execute(
                    f"""SELECT c8.claim_837_key, c8.patient_control_number, c8.payer_name, SUBSTR(c8.bht_created, 1, 10),
                               l.procedure_code, SUBSTR(COALESCE(l.service_date, c8.service_date), 1, 10), l.charge_cents
                        FROM claims_837 c8 JOIN lines_837 l ON l.claim_837_key = c8.claim_837_key
                        WHERE SUBSTR(c8.bht_created, 1, 10) IN ({','.join('?' * len(part))})
                          AND COALESCE(c8.frequency_code, '1') <> '8'""", part):
                p = per.setdefault(key, [pcn, payer, day, defaultdict(int)])
                if code and svc:
                    p[3][(svc, code)] += chg or 0
        # a claim counts on the day it first went out
        pcns = sorted({p[0] for p in per.values() if p[0]})
        first: dict[str, str] = {}
        for i in range(0, len(pcns), 500):
            part = pcns[i:i + 500]
            for pcn, d in c.execute(f"""SELECT patient_control_number, MIN(SUBSTR(bht_created, 1, 10)) FROM claims_837
                                        WHERE patient_control_number IN ({','.join('?' * len(part))})
                                          AND COALESCE(frequency_code, '1') <> '8' GROUP BY 1""", part):
                first[pcn] = d
        for pcn, payer, day, rows in per.values():
            if first.get(pcn) == day:
                count(bill_of(pcn), payer, day, rows)
        # EMA's weekly exports: a claim's charge lines, its payer, and the day it went out (or its bill was made)
        if "ema_rows" in tables:
            q = ",".join("?" * len(days))
            ids = sorted({cid for (cid,) in c.execute(
                f"""SELECT DISTINCT claim_id FROM ema_rows WHERE claim_id IS NOT NULL
                    AND (SUBSTR(claim_submission_date, 1, 10) IN ({q}) OR SUBSTR(bill_created, 1, 10) IN ({q}))""", days + days)})
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                ex: dict[str, list] = {}            # claim id -> [submitted, bill made, Counter(payers), {(date, code): charge}]
                for cid, fam, act, payer, sub_on, made, svc, code, chg in c.execute(
                        f"""SELECT claim_id, family, activity_type, payer, SUBSTR(claim_submission_date, 1, 10), SUBSTR(bill_created, 1, 10),
                                   SUBSTR(service_date, 1, 10), cpt, charges_cents FROM ema_rows
                            WHERE claim_id IN ({','.join('?' * len(part))}) AND family IN ('charge_lines', 'claim_status')""", part):
                    e = ex.setdefault(cid, ["", "", Counter(), defaultdict(int)])
                    e[0], e[1] = max(e[0], sub_on or ""), max(e[1], made or "")
                    if payer:
                        e[2][payer] += 1
                    if fam == "charge_lines" and (act or "").lower() == "charge" and svc and code:
                        e[3][(svc, code)] += chg or 0
                for cid, (sub_on, made, payers, rows) in ex.items():
                    day = sub_on or made
                    if day in day_set and payers:
                        count(bill_of(cid), payers.most_common(1)[0][0], day, rows)
        every = sorted({b for bs in bills.values() for b in bs})
        tied: set[str] = set()
        for i in range(0, len(every), 500):
            part = every[i:i + 500]
            q = ",".join("?" * len(part))
            tied |= {b for (b,) in c.execute(f"SELECT bill FROM ema_link_map WHERE bill IN ({q}) AND COALESCE(conflict, 0) = 0", part)}
            tied |= {b for (b,) in c.execute(f"SELECT bill FROM ema_claim_links WHERE bill IN ({q})", part)}
        return {k: len(bs - tied) for k, bs in bills.items()}

    def _links_answered(self, rows: list[tuple]) -> set[tuple]:
        """Which EMA claims EMA's postings show answered after a claim's send: rows [(claim id, EMA claim number, sent)].
        As ema_checks.claim_answers counts an answer (a net insurance payment, a $0 answer with its proof, never a bare $0
        row: ema_checks.zero_answer_sql; a payer's non-routine reason, posted after the send; never a payment under the
        surgery center's separate-payee checks), or every line with a charge settled (settled_line_sql).
        {(claim id, EMA claim number)}"""
        from . import ema_checks as ec
        if not rows:
            return set()
        c = self.conn
        c.execute("DROP TABLE IF EXISTS temp.d837_la")
        c.execute("CREATE TEMP TABLE d837_la (cid TEXT, link INTEGER, sent TEXT)")
        c.executemany("INSERT INTO temp.d837_la VALUES (?, ?, ?)", rows)
        sep = ("AND NOT EXISTS (SELECT 1 FROM ema_checks s WHERE s.check_no = y.check_no AND s.payer = y.payer AND s.cause = 'separate_payee')"
               if c.execute("SELECT 1 FROM sqlite_master WHERE name='ema_checks'").fetchone() else "")
        got: set[tuple] = set()
        for cid, lk, paid, zero in c.execute(
                f"""SELECT x.cid, x.link, SUM(COALESCE(y.ins_paid, 0)), MAX({ec.zero_answer_sql('y')})
                    FROM temp.d837_la x JOIN ema_line_payments y ON y.link = x.link AND y.posted > x.sent
                    WHERE 1 = 1 {sep} GROUP BY x.cid, x.link"""):
            if (paid or 0) > 0 or zero:
                got.add((cid, lk))
        for cid, lk in c.execute(f"""SELECT DISTINCT x.cid, x.link FROM temp.d837_la x JOIN ema_line_reasons z ON z.link = x.link
                                     WHERE z.first_posted > x.sent AND z.grp NOT IN ('', 'PR')
                                       AND z.reason_class NOT IN ({ec._routine_sql()})"""):
            got.add((cid, lk))
        for cid, lk, n, settled in c.execute(f"""SELECT x.cid, x.link, COUNT(*), SUM({settled_line_sql('l')})
                                                 FROM temp.d837_la x JOIN ema_lines l ON l.link = x.link
                                                 WHERE COALESCE(l.charges, 0) > 0 GROUP BY x.cid, x.link"""):
            if n and settled == n:
                got.add((cid, lk))
        c.execute("DROP TABLE IF EXISTS temp.d837_la")
        return got

    def _837_ema_answers(self, cds: list[dict], newest: str, no_era: dict) -> list[dict]:
        """The never-answered claims from the 837s, checked against EMA's reports as the exports' claims are
        (_ema_check_open_claims): one EMA's postings show answered after it was sent leaves the list (ema_checks.claim_answers);
        a newer status in the A/R or Production (one dated after the send) is context only, except rejected (it never
        reached the payer: fix and resubmit) and never sent (EMA's own check lists it); one due only by EMA's postings whose
        EMA claim cannot be found waits, counted with its dollars (meta ema:unanswered_unseen, and the Data tab's line for
        a payer that sends no ERA)."""
        from . import ema_checks as ec
        answers = self._ema_claims_found("claim_answers_837", cds)
        links = sorted({a["link"] for a in answers.values() if a.get("link") is not None})
        statuses = self._ema_step("claim_statuses_837", ec.claim_statuses, self.conn, claim_ids=[cd["pcn"] for cd in cds],
                                  links=links) or {}
        # the one answered rule every never-answered claim meets, whatever listed it (_answered_rule)
        rule = self._answered_rule("answered_837", cds, answers)
        out = []
        for cd in cds:
            a = answers.get(cd["pcn"]) or {}
            ne = no_era.get(cd.get("no_era") or "")
            left = rule.get(cd["pcn"], cd["items"])
            if not left:
                self.ema_counts["837_answered_in_ema"] += 1
                if ne is not None:
                    ne["answered_in_ema"] += 1
                continue
            if len(left) < len(cd["items"]):
                cd = dict(cd, items=left)      # the services EMA shows answered for this patient leave the item
            # the claim's newest status: by its claim id, else by the EMA claim it was matched to
            st = statuses.get(cd["pcn"]) or (statuses.get(a["link"]) if a.get("link") is not None else None)
            if st and str(st.get("as_of") or "")[:10] > cd["sent"]:
                cls = st.get("class")
                if cls == "not_sent":
                    self.ema_counts["837_ema_not_sent"] += 1
                    self._837_claims.discard(cd["pcn"])     # EMA's own check (ema_checks.not_sent) lists it
                    continue
                if cls == "rejected":
                    cd = dict(cd, rejected=True, status=st.get("status"))
                elif cls == "gave_up":
                    cd = dict(cd, gave_up=True, status=st.get("status"))
                elif cls in ("denied", "paid", "settled", "void", "answered"):
                    # a status alone never answers a claim (amendment E6): the item stays, with the status as context
                    cd = dict(cd, status_context=(f"EMA's {'A/R' if st.get('source') == 'ar' else 'Production'} report calls the "
                                                  f"claim {st.get('status') or cls} ({str(st.get('as_of') or '')[:10]})"),
                              status_class=cls)
            if a.get("link") is None and ((cd.get("due") or "") > newest or ne is not None):
                # due only by EMA's postings (or a payer that sends no ERA), and EMA's own claim for it was not found: its
                # answer cannot be seen. Never a silent drop: counted with its dollars and due day
                self.ema_counts["837_waits_unfound"] += 1
                if ne is not None:
                    ne["unchecked"] += 1
                w = self.__dict__.setdefault("_unseen", {"n": 0, "billed_cents": 0, "due_from": None, "due_to": None,
                                                         "overdue_30": 0, "overdue_30_cents": 0, "by_group": {}})
                billed = sum(x[2] or 0 for x in cd["items"])
                w["n"] += 1
                w["billed_cents"] += billed
                w["due_from"] = min(w["due_from"] or cd["due"], cd["due"])
                w["due_to"] = max(w["due_to"] or cd["due"], cd["due"])
                g = w["by_group"].setdefault(cd.get("grp") or "Unknown", {"n": 0, "billed_cents": 0})
                g["n"] += 1
                g["billed_cents"] += billed
                if (_days_between(cd["due"], self.today) or 0) > 30:
                    w["overdue_30"] += 1
                    w["overdue_30_cents"] += billed
                continue
            out.append(dict(cd, ema_bridge=a.get("bridge"), ema_link=a.get("link"), ema_twins=a.get("twins"),
                            ema_bare_zero=a.get("bare_zero")))
        return out

    def _send_weeks(self) -> dict:
        """Weeks one feed shows claims sent and the other none (trace finding T-N13), inside the weeks both reach: EMA's
        claim-status exports (each claim's submission date) against the 837 files (each claim's send day). A week counts
        on the side that sent SEND_WEEK_MIN claims at least. Saved as SEND_WEEKS_META in run()."""
        c = self.conn
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "claims_837" not in tables or "ema_rows" not in tables:
            return {}
        ema: Counter = Counter()
        for cid, d in c.execute("""SELECT DISTINCT claim_id, SUBSTR(claim_submission_date, 1, 10) FROM ema_rows
                                   WHERE family = 'claim_status' AND claim_id IS NOT NULL AND claim_submission_date IS NOT NULL"""):
            w = week_of(d)
            if w:
                ema[w] += 1
        s837: Counter = Counter()
        for pcn, d in c.execute("SELECT DISTINCT patient_control_number, bht_created FROM claims_837 WHERE patient_control_number IS NOT NULL"):
            w = week_of(d)
            if w:
                s837[w] += 1
        if not ema or not s837:
            return {}
        lo, hi = max(min(ema), min(s837)), min(max(ema), max(s837))
        out = {"from": lo, "to": hi, "ema_no_837": [], "no_ema_837": []}
        w = lo
        while w and w <= hi:
            if ema[w] >= SEND_WEEK_MIN and not s837[w]:
                out["ema_no_837"].append({"week": w, "claims": ema[w]})
            if s837[w] >= SEND_WEEK_MIN and not ema[w]:
                out["no_ema_837"].append({"week": w, "claims": s837[w]})
            w = _add_days(w, 7)
        return out

    # ------------------------------------------------------------------ EMA's A/R and Production reports (amendment 4.5)
    # The checks live in remitwatch.ema_checks, which never imports this module: the Detector passes its own rules in
    # (rate, ratio, code_once, has_835_finding). Every step runs only when the store holds EMA's reports, and a step that
    # fails is recorded (meta ema:wiring) and rolled back: the items from the remittances are produced all the same. A
    # stop at the serverless deadline (every statement interrupted) is never swallowed.
    def _ema_step(self, name: str, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except MemoryError:
            raise
        except Exception as exc:        # noqa: BLE001  (recorded; the 835 items still come out)
            if isinstance(exc, sqlite3.OperationalError) and "interrupt" in str(exc).lower():
                raise
            if os.environ.get("REMITWATCH_EMA_STRICT") == "1":
                raise
            from . import runlog
            runlog.caught(exc, "ema:" + name)
            try:
                self.conn.rollback()
            except sqlite3.Error as exc2:
                runlog.caught(exc2, "ema:rollback", level="warning")
            self.ema_errors.append({"step": name, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
            return None

    def _line_parts(self, keys) -> dict:
        """The parts of a service EMA claim lines (link, date, code) bill (ema_checks.line_parts), with the codes the practice
        bills in parts worked out once a run."""
        from . import ema_checks as ec
        if self.__dict__.get("_split_codes") is None:
            self._split_codes = ec.split_codes(self.conn)
        return ec.line_parts(self.conn, keys, codes=self._split_codes)

    def _has_ema_reports(self) -> bool:
        """Whether EMA's A/R or Production reports have reached this store (their summaries are what the checks read)."""
        c = self.conn
        try:
            if c.execute("SELECT 1 FROM ema_report_files WHERE status IN ('current','partly_replaced') LIMIT 1").fetchone():
                return True
            return bool(c.execute("SELECT 1 FROM ema_lines LIMIT 1").fetchone() or c.execute("SELECT 1 FROM ema_ar LIMIT 1").fetchone())
        except sqlite3.Error:
            return False

    def _ema_prepare(self) -> None:
        """Steps 2 and 3: the map of EMA claims to remittance bills brought up to date when new 835s or an A/R arrived
        (Part B, store only), then the causes of the checks no 835 carries (C1), the payers' answer times and EMA's posting
        lag, which the later checks read."""
        self.ema_on = self._has_ema_reports()
        if not self.ema_on:
            return
        from . import ema_checks as ec
        if self.conn.execute("SELECT 1 FROM meta WHERE key='ema:map_dirty'").fetchone():
            from . import ema_ledger
            self._ema_step("map", ema_ledger.refresh, self.store, None, parts="B", log=lambda *_a: None)
        self._ema_step("classify_checks", ec.classify_checks, self.conn)
        self._ema_step("turnaround", ec.turnaround, self.conn, today=self.today)
        self._ema_step("posting_lag", ec.posting_lag, self.conn)

    # the Detector's rules, as ema_checks takes them
    def _rate(self, payer_group: str | None, code: str | None, year: str | None) -> int | None:
        """The payer's usual allowed amount for a code in a year (the office setting, any provider type); for a code it has
        paid only at a facility (a surgery center's claims, known as such from the 837 or the claim's location since fix7c),
        its facility amount; else its product class's typical amount across settings. A claim with no remittance is never
        sized at nothing (its charge, in EMA's checks) for want of one setting's lines. The class's lines of one setting too
        few for a typical amount, or of every setting together, are read only in the last pass: the payer's own facility
        amount comes first (fix7c)."""
        if not code:
            return None
        for pos in ("office", "facility", "any"):
            rate, _b, _r = self.usual_rate({"payer_group": payer_group, "year": str(year or ""), "provider_tier": "unknown",
                                            "pos_class": pos, "pool": pos == "any"}, code)
            if rate is not None:
                return rate
        return None

    def _ratio(self, payer_group: str | None, code: str | None) -> float | None:
        """What a payer group paid of the charge for a code over the newest 12 months of remittances (10 paid lines at least)."""
        if self.__dict__.get("_ratios") is None:
            newest = max((l.get("payment_date") or l.get("txn_payment_date") or "" for l in self.lines), default="")
            since = _add_days(newest[:10], -365) if newest else ""
            agg: dict[tuple, list] = defaultdict(lambda: [0, 0, 0])
            for l in self.lines:
                if l.get("claim_status_code") not in ("1", "19") or (l.get("charge_cents") or 0) <= 0:
                    continue
                if (l.get("payment_date") or l.get("txn_payment_date") or "") < (since or ""):
                    continue
                a = agg[(l["payer_group"], l.get("procedure_code"))]
                a[0] += l.get("paid_cents") or 0
                a[1] += l.get("charge_cents") or 0
                a[2] += 1 if (l.get("paid_cents") or 0) > 0 else 0
            self._ratios = {k: max(0.0, min(1.0, v[0] / v[1])) for k, v in agg.items() if v[2] >= 10 and v[1] > 0}
        return self._ratios.get((payer_group, code))

    def _paid_share(self, payer_group: str | None, code: str | None) -> tuple[int, int] | None:
        """(lines paid, lines answered) for a payer group and code on the 835s of services since 2025, answered as the
        primary payer (a payment still standing counts as paid), or None when fewer than 20 are on file. EMA's vocabulary names some groups apart
        ('UnitedHealthcare Medicare Advantage' beside 'UHC Medicare Advantage'): a group of the same family and kind counts."""
        if self.__dict__.get("_share") is None:
            from . import ema_checks as ec
            share: dict[tuple, list] = defaultdict(lambda: [0, 0])
            for l in self.lines:
                # its answers as the primary payer (paid, or denied as primary): a second payer's payments of the cost share
                # say nothing of whether the payer pays the code itself
                if (l.get("service_date") or "") < ec.POLICY_SINCE or not l.get("procedure_code") \
                        or l.get("claim_status_code") not in ("1", "4", "19"):
                    continue
                a = share[(l["payer_group"], l["procedure_code"])]
                a[1] += 1
                a[0] += 1 if (self.standing.get(l["line_key"]) or 0) > 0 else 0
            self._share = dict(share)
        got = self._share.get((payer_group, code))
        if not got:
            from . import ema_checks as ec
            fam = ec._family(payer_group)
            kinds = lambda g: ("Medicare Advantage" in (g or ""), "Medicaid" in (g or ""))      # noqa: E731
            hits = [v for (g, c), v in self._share.items() if c == code and ec._family(g) == fam and kinds(g) == kinds(payer_group)]
            got = [sum(v[0] for v in hits), sum(v[1] for v in hits)] if hits else None
        return tuple(got) if got and got[1] >= 20 else None

    def fold_payer_policy(self) -> None:
        """Denials from the remittances of a code the payer pays on 5% or less of at least 20 lines it answered as primary
        since 2025 (UHC's G6001, PM case C18; the answer key's R9), or of a payer and code the research covers
        (remitwatch.rarely_paid, the owner's decision 4: Anthem's 77600 with the 2026 surface radiation codes, whose 2025
        lines it paid): the payer's policy, not an appeal per line. The code is the one the practice billed, so a 77600
        the payer answered as 77436 and paid nothing on is one of them. They make one pattern per payer and code in the
        owner's table (meta ema:payer_policy; one per period where the research splits the pair on a date), counted with
        their dollars. Nothing is accepted by default: they stay listed, pursued, until the owner accepts the pattern.
        Only with EMA's reports on file (the owner's table is built then)."""
        if not self.ema_on:
            return
        from . import ema_checks as ec
        from . import rarely_paid as rp
        decisions = ec._meta(self.conn, ec.POLICY_KEY, {}) or {}
        folded: dict[str, dict] = {}
        for fid, f in list(self.findings.items()):
            if not f.get("line_key") or f.get("auto_status"):
                continue
            got = rp.item_codes(f)          # an unpaid denial line: its billed code and date
            if not got:
                continue
            code, svc = got[0]
            grp = f.get("payer_group")
            share = self._paid_share(grp, code)
            rare = bool(share) and share[0] <= ec.POLICY_MAX_PAID_SHARE * share[1]
            entry = rp.entry_for(grp, code, svc)
            if not (rare or rp.applies(entry, False)):
                continue
            key = rp.pattern_key(grp, code, svc)
            # nothing is accepted by default (PLAN decision 14), and a code a payer almost never pays is pursued in a batch per
            # payer until the owner accepts it (the owner's decision 16: appeal at any win rate when it beats the cost to send).
            # A decision made for the pair at every date (before decision 4 named periods) covers each period
            dec = rp.decision_for(decisions, key)
            decision = (dec or {}).get("decision") or "pursue"
            pt = folded.setdefault(key, {"group": grp, "code": code, "paid": share[0] if share else None,
                                         "answered": share[1] if share else None, "n": 0, "cents": 0, "decision": decision,
                                         "default": dec is None, "source": "835",
                                         "researched": (entry or {}).get("verdict") if rp.applies(entry, rare) else None})
            pt["n"] += 1
            pt["cents"] += f.get("shortfall_cents") or 0
            if decision != "pursue":
                del self.findings[fid]
                self.ema_counts["payer_policy_835"] += 1
        self._policy_835 = folded

    def _rarely_paid_step(self) -> None:
        """explain_rarely_paid, contained: a failure is logged and counted, the old evidence is dropped (never shown as
        today's), and the run goes on; with REMITWATCH_EMA_STRICT it is raised (the tests)."""
        try:
            self.explain_rarely_paid()
        except MemoryError:
            raise
        except Exception as exc:        # noqa: BLE001  (logged; the items are not changed by this step)
            if os.environ.get("REMITWATCH_EMA_STRICT") == "1":
                raise
            from .runlog import caught
            caught(exc, "rarely_paid")
            self.ema_counts["rarely_paid_failed"] += 1
            try:
                from .rarely_paid import WHY_META
                self.conn.execute("DELETE FROM meta WHERE key=?", (WHY_META,))
            except sqlite3.Error as exc2:
                caught(exc2, "rarely_paid.drop")

    def explain_rarely_paid(self) -> None:
        """Why a payer does not pay a code it almost never pays (the owner's decision 4), from this store: for each pair an
        item of this run is about that the research covers for its payer group, or that the payer pays on 5% or less of
        the lines it answered (the remittances here, else EMA's books), and for each pattern in the owner's table, the
        lines it answered as primary and paid by year, the reasons and remarks on the unpaid ones, their modifiers and
        places of service, the codes billed with them, how often it paid another line of the claim, and the code it
        answered instead (remitwatch.rarely_paid.evidence). Kept in meta rarely_paid:why for the item drawer, the policy
        page, the appeal packet and the note review, on both hosts. Counts, codes and years only: no patient data."""
        from . import ema_checks as ec
        from . import rarely_paid as rp
        wanted: dict[str, dict] = {}

        def want(key: str, grp: str, code: str, svc: str | None) -> None:
            if key not in wanted:
                parts = rp.parse_key(key)
                entry = rp.entry_for(grp, code, svc)
                wanted[key] = rp.spec_for(grp, code, entry, parts.get("period"))
        for f in self.findings.values():
            grp = f.get("payer_group")
            for code, svc in rp.item_codes(f):
                entry = rp.entry_for(grp, code, svc)
                share = self._paid_share(grp, code)
                rare = bool(share) and share[0] <= ec.POLICY_MAX_PAID_SHARE * share[1]
                if rare or rp.applies(entry, False):
                    want(rp.pattern_key(grp, code, svc), grp, code, svc)
        # every pattern of the owner's table this run made: the remittances' (fold_payer_policy) and EMA's own (C5)
        keys = dict(self.__dict__.get("_policy_835") or {})
        try:
            lo = json.loads((self.conn.execute("SELECT value FROM meta WHERE key=?", (ec.C5_LEFT_OUT_KEY,)).fetchone() or ["{}"])[0])
            keys.update(((lo.get("payer_policy") or {}).get("patterns") or {}) if isinstance(lo, dict) else {})
        except (ValueError, TypeError, sqlite3.Error) as exc:
            from .runlog import caught
            caught(exc, "rarely_paid.patterns")      # the items' own pairs still get their evidence
        for key, p in keys.items():
            parts = rp.parse_key(key)
            if parts["group"] and parts["code"]:
                day = (parts.get("period") or {}).get("from") or (parts.get("period") or {}).get("to")
                want(key, parts["group"], parts["code"], day)
        pairs = rp.evidence(self.lines, adj=self.adj, rem=self.rem, claim_adj=self.claim_adj, claim_rem=self.claim_rem,
                            standing=self.standing, by_claim=self.lines_by_claim, wanted=wanted, conn=self.conn)
        rp.save(self.conn, pairs, at=self.now, run_id=self.run_id)
        self.ema_counts["rarely_paid_pairs"] = len(pairs)

    def _has_835_finding(self, bill: str | None, svc: str | None, code: str | None) -> bool:
        """The run already holds an item from the remittances for this bill, date and code (or the whole claim that day)."""
        if self.__dict__.get("_f835") is None:
            from .ema_reports import bill_of_claim
            got: set[tuple] = set()
            for f in self.findings.values():
                if not f.get("patient_control_number") or not (f.get("line_key") or f.get("claim_key")):
                    continue
                b = bill_of_claim(f["patient_control_number"])
                if f.get("line_key"):
                    for cd in {f.get("procedure_code"), f.get("original_procedure_code"), f.get("billed_code")} - {None}:
                        got.add((b, f.get("service_date"), cd))
                else:
                    got.add((b, f.get("service_date"), None))
                    try:
                        ev = json.loads(f.get("evidence") or "{}")
                    except ValueError:
                        ev = {}
                    for p in ev.get("unpaid") or []:
                        if isinstance(p, list) and len(p) == 2:
                            got.add((b, p[1] or f.get("service_date"), None))
            self._f835 = got
        return (bill, svc, code) in self._f835 or (bill, svc, None) in self._f835

    def _turnaround_days(self, group: str | None) -> int:
        cache = self.__dict__.setdefault("_tat", {})
        if group not in cache:
            from . import ema_checks as ec
            cache[group] = self._ema_step("turnaround_days", ec.turnaround_days, self.conn, group, "svc_p95") or NO_ANSWER_DAYS
        return cache[group]

    def _answers_through(self, group: str | None, newest: str) -> str:
        cache = self.__dict__.setdefault("_through", {})
        if group not in cache:
            from . import ema_checks as ec
            cache[group] = self._ema_step("answers_through", ec.answers_through, self.conn, group, newest, today=self.today) or newest
        return cache[group]

    def _open_claim_group(self, k: dict, ema_payer: str, votes: dict, va_names) -> tuple[str, str]:
        """Who an unanswered claim went to: the VA's network by its payer id (EMA names the network's administrator), else
        the group the remittances give the claims EMA names after this payer, else the name's own group."""
        payer_id = k["payer_ids"].most_common(1)[0][0] if k["payer_ids"] else None
        if (payer_id or "").strip().upper() in VA_PAYER_IDS or (not payer_id and " ".join(ema_payer.split()).upper() in va_names):
            return VA_GROUP                 # the VA's network, though EMA names its administrator: the VA's limits apply
        return votes[ema_payer].most_common(1)[0][0] if votes.get(ema_payer) else normalize_payer(ema_payer)

    def _ema_check_open_claims(self, final: list[tuple], newest: str | None = None) -> list[tuple]:
        """The weekly exports' unanswered claims, checked against EMA's reports before one is listed: a newer status in the
        A/R or Production (denied, paid, settled, voided, answered: the payer answered; rejected: it never reached the payer;
        never sent: ema_checks.not_sent lists it), and the one answered rule every never-answered claim meets
        (_answered_rule: EMA's postings on its own EMA claim after its send, found by its claim id, its bill, its content
        under the group EMA gives its payer's name, or its send day; the patient's other EMA claims and remittances, by its
        own payer). A claim that never reached its payer (rejected, or still in EMA's print batch) is answered only by a
        posting after its send or its bill's making: an earlier answer, a denial above all, answered an earlier send, and the
        bill made again is the correction that answer asked for (fx-exports-r1-1, r2-1). One still in the print batch is
        also left out when a payment that still stands is on its visit, whatever its day (_print_batch_paid: a duplicate of
        a paid visit, the residual of fix7b). A status alone never answers one."""
        from . import ema_checks as ec
        pcns = [cd["pcn"] for cd, _items in final]
        statuses = self._ema_step("claim_statuses", ec.claim_statuses, self.conn, claim_ids=pcns) or {}
        cds = []
        for cd, items in final:
            k = cd["k"]
            # the day it first went out, for its send-day twins: its 837's first send, else EMA's submission date, else
            # the day EMA made the bill (its charges are posted then); none from the date of service alone
            first = ((cd.get("s837") or [None, None])[1] or str(k.get("sent") or "")[:10] or str(k.get("created") or "")[:10]
                     or None)
            cds.append(dict(cd, items=items, first_sent=first, after=cd["sent"] if (cd["rejected"] or cd.get("unsent")) else None,
                            ema_patients=set(k.get("patients") or ())))
        answers = self._ema_claims_found("claim_answers", cds)
        rule = self._answered_rule("answered_export", cds, answers)
        # a claim still in the print batch whose visit a payment that still stands answers, whatever its day: a duplicate
        batch = [dict(cd, items=rule.get(cd["pcn"], cd["items"])) for cd in cds if cd.get("unsent") and rule.get(cd["pcn"], cd["items"])]
        paid_dup = (self._ema_step("export_print_batch_paid", self._print_batch_paid, batch, answers) or {}) if batch else {}
        for cd in batch:
            done = paid_dup.get(cd["pcn"]) or set()
            if done:
                left = [x for x in cd["items"] if (x[1], x[0], item_part(x)) not in done]
                if not left:
                    self.ema_counts["export_print_batch_paid"] += 1
                rule[cd["pcn"]] = left
        # the day the remittances on file reach, as run_ema_open_claims dated the claims (feed_date): one stray newer file, or
        # one dated in the future, says nothing of whether this claim's payer answered it (answers-2)
        newest = newest or feed_date(self.conn) or self.conn.execute(
            "SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()[0] or ""
        out = []
        for cd in cds:
            a = answers.get(cd["pcn"]) or {}
            items = rule.get(cd["pcn"], cd["items"])
            if not items:
                if not (cd.get("unsent") and paid_dup.get(cd["pcn"])):
                    self.ema_counts["export_answered_in_ema"] += 1
                continue
            # (a claim still in the print batch never went out: its due day is the practice's own, dated against the day
            # EMA's exports reach (run_ema_open_claims), and no payer's answer can be waited for: fx-exports-r2-7)
            if not cd["rejected"] and not cd.get("unsent") and (cd.get("due") or "") > newest and a.get("link") is None:
                # due only by EMA's postings, and EMA's own claim for it was not found: its answer cannot be seen, so the
                # claim waits until the remittances reach its due day. Never a silent drop: counted with its billed dollars
                # and due day (meta ema:unanswered_unseen), which the Data tab and Today show as money no source can check
                self.ema_counts["export_waits_unfound"] += 1
                w = self.__dict__.setdefault("_unseen", {"n": 0, "billed_cents": 0, "due_from": None, "due_to": None,
                                                         "overdue_30": 0, "overdue_30_cents": 0, "by_group": {}})
                billed = sum(x[2] or 0 for x in items)
                w["n"] += 1
                w["billed_cents"] += billed
                w["due_from"] = min(w["due_from"] or cd["due"], cd["due"])
                w["due_to"] = max(w["due_to"] or cd["due"], cd["due"])
                g = w["by_group"].setdefault(cd.get("grp") or "Unknown", {"n": 0, "billed_cents": 0})
                g["n"] += 1
                g["billed_cents"] += billed
                if (_days_between(cd["due"], self.today) or 0) > 30:
                    w["overdue_30"] += 1
                    w["overdue_30_cents"] += billed
                continue
            st = statuses.get(cd["pcn"])
            export_at = str((cd["k"].get("status_at") or ("",))[0] or "")[:10]
            if st and str(st.get("as_of") or "")[:10] > export_at:
                cls = st.get("class")
                if cls in ("denied", "paid", "settled", "void", "answered"):
                    # a status alone never answers a claim (amendment E6): the item stays, with the status as context; a
                    # denial EMA posted answers it through C5's item for the claim (run_ema_denials drops this one then)
                    self.ema_counts["export_newer_status_kept"] += 1
                    cd = dict(cd, status_context=(f"EMA's {'A/R' if st.get('source') == 'ar' else 'Production'} report calls the "
                                                  f"claim {st.get('status') or cls} ({str(st.get('as_of') or '')[:10]})"),
                              status_class=cls)
                elif cls == "not_sent":
                    self.ema_counts["export_not_sent"] += 1
                    continue                    # never left EMA after all: listed by ema_checks.not_sent
                elif cls == "rejected" and not cd["rejected"]:
                    cd = dict(cd, rejected=True, status=st.get("status") or cd["status"])
                elif cls == "gave_up":
                    cd = dict(cd, gave_up=True, status=st.get("status") or cd["status"])
            cd = dict(cd, ema_bridge=a.get("bridge"), ema_link=a.get("link"), ema_twins=a.get("twins"), ema_bare_zero=a.get("bare_zero"))
            if a.get("link") is not None:
                self._export_links.add(a["link"])
            # EMA claims that are this claim or another listed one (send-day twins): the Production check leaves them
            self._export_links.update(a.get("twins") or ())
            out.append((cd, items))
        return out

    def _print_batch_paid(self, cds: list[dict], answers: dict) -> dict[str, set]:
        """The services of claims still in EMA's print batch (never sent) whose visit a payment that still stands answers,
        whatever its day: the bill made again is then a duplicate of a paid visit (the residual of fix7b). Only a payment
        counts, net of what was taken back, by the claim's own payer company (payer_company), for the service's own part
        (part_fits): EMA's postings on the claim's own EMA claim or on another EMA claim of the patient (insurance
        payments summing above $0, never under the surgery center's separate-payee checks), or a remittance's line on
        another bill of the patient that still stands (the ledger, takebacks and reversals applied). The patient is the
        claim's by its own evidence only (_patient_keys without the member ids' names: a subscriber's id covers the
        family). A denial, a $0 answer or a payment taken back never makes such a claim a duplicate: the bill made again
        is the correction they asked for (fx-exports-r1-1, r2-1). {claim id: {(date, code, part)}}."""
        from . import ema_checks as ec
        c = self.conn
        out: dict[str, set] = defaultdict(set)
        keys_of = self._patient_keys(cds, answers, member_names=False) or {}
        info = {cd["pcn"]: cd for cd in cds}
        own = {cd["pcn"]: (answers.get(cd["pcn"]) or {}).get("link") for cd in cds}
        want: dict[tuple, set] = defaultdict(set)         # ('l', link | 'n', name key, date, code) -> claim ids
        parts_of: dict[tuple, set] = defaultdict(set)     # (claim id, date, code) -> the parts its items bill
        for cd in cds:
            for x in cd["items"]:
                if not (x[0] and x[1]):
                    continue
                parts_of[(cd["pcn"], x[1], x[0])].add(item_part(x))
                if own[cd["pcn"]] is not None:
                    want[("l", own[cd["pcn"]], x[1], x[0])].add(cd["pcn"])
                for nk in keys_of.get(cd["pcn"]) or ():
                    want[("n", nk, x[1], x[0])].add(cd["pcn"])
        if not want:
            return out
        comps = {cd["pcn"]: self._claim_companies(cd) for cd in cds}
        got: dict[tuple, set] = defaultdict(set)          # (claim id, date, code) -> the parts a standing payment answers
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "ema_line_payments" in tables:
            sep = ("AND NOT EXISTS (SELECT 1 FROM ema_checks s WHERE s.check_no = y.check_no AND s.payer = y.payer "
                   "AND s.cause = 'separate_payee')" if "ema_checks" in tables else "")
            hits = []
            for kind, col in (("l", "link"), ("n", "name_key")):
                keys = sorted({(k[1], k[2], k[3]) for k in want if k[0] == kind})
                for i in range(0, len(keys), 500):
                    c.execute("DROP TABLE IF EXISTS temp.d837_pb2")
                    c.execute("CREATE TEMP TABLE d837_pb2 (k INTEGER, svc TEXT, cpt TEXT)")
                    c.executemany("INSERT INTO temp.d837_pb2 VALUES (?, ?, ?)", keys[i:i + 500])
                    for k, svc, cpt, lk, payer, grp, net in c.execute(
                            f"""SELECT y.{col}, y.svc, y.cpt, y.link, y.payer, MAX(y.payer_group), SUM(COALESCE(y.ins_paid, 0))
                                FROM temp.d837_pb2 x JOIN ema_line_payments y ON y.{col} = x.k AND y.svc = x.svc AND y.cpt = x.cpt
                                WHERE COALESCE(y.ins_paid, 0) <> 0 {sep}
                                GROUP BY y.{col}, y.svc, y.cpt, y.link, y.payer"""):
                        if (net or 0) > 0:
                            hits.append(((kind, k, svc, cpt), lk, payer, grp))
            c.execute("DROP TABLE IF EXISTS temp.d837_pb2")
            hit_keys = {(h[1], h[0][2], h[0][3]) for h in hits if h[1] is not None}
            lparts = self._line_parts(hit_keys)
            lcharge = ec.line_charges(c, hit_keys) if any(k[2] in (self._split_codes or ()) for k in hit_keys) else {}
            for key, lk, payer, grp in hits:
                for cid in want.get(key, ()):
                    if not self._posting_companies(payer, grp) & comps[cid]:
                        continue            # another payer's payment: this payer's share may still be owed
                    if lk is not None and lk == own.get(cid):
                        got[(cid, key[2], key[3])].add(_OWN)
                        continue
                    ch = lcharge.get((lk, key[2], key[3]))
                    alike = None
                    if ch:
                        alike = next((p for p in parts_of.get((cid, key[2], key[3])) or () if any(
                            int(x[2] or 0) == ch for x in info[cid]["items"] if x[1] == key[2] and x[0] == key[3]
                            and item_part(x) == p)), None)
                    got[(cid, key[2], key[3])] |= ec.other_parts(lparts.get((lk, key[2], key[3])), alike)
        # a remittance's line on another bill of the patient, paid and still standing (the ledger)
        if any(k[0] == "n" for k in want):
            from . import ema_reports as er
            er.register_functions(c, create=False)
            days = sorted({k[2] for k in want if k[0] == "n"})
            c.execute("DROP TABLE IF EXISTS temp.d837_pb3")
            c.execute("CREATE TEMP TABLE d837_pb3 (svc TEXT PRIMARY KEY)")
            c.executemany("INSERT OR IGNORE INTO temp.d837_pb3 VALUES (?)", [(d,) for d in days])
            rows = c.execute("""SELECT l.line_key, rw_name_key(cl.patient_last, cl.patient_first),
                                       CASE WHEN cl.corrected_patient_last IS NOT NULL OR cl.corrected_patient_first IS NOT NULL
                                            THEN rw_name_key(COALESCE(cl.corrected_patient_last, cl.patient_last),
                                                             COALESCE(cl.corrected_patient_first, cl.patient_first)) END,
                                       l.service_date, l.procedure_code, l.original_procedure_code, cl.patient_control_number,
                                       t.payer_name, cl.claim_filing_indicator, l.modifiers
                                FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key
                                JOIN transactions t ON t.transaction_key = l.transaction_key
                                WHERE COALESCE(t.superseded,0)=0 AND l.service_date IN (SELECT svc FROM temp.d837_pb3)""").fetchall()
            c.execute("DROP TABLE IF EXISTS temp.d837_pb3")
            for line_key, nk, cnk, svc, code, ocode, pcn, pname, cfi, mods in rows:
                if (self.standing.get(line_key) or 0) <= 0:
                    continue                # nothing of it stands: a denial, a $0 answer, or a payment taken back
                grp = payer_group_kind(pname, cfi)[0]
                for n in {nk, cnk} - {None}:
                    for cd_ in {code, ocode} - {None}:
                        for cid in want.get(("n", n, svc, cd_), ()):
                            if bill_of(pcn) == info[cid].get("b") or payer_company(grp) not in comps[cid]:
                                continue
                            got[(cid, svc, cd_)].add(component_of(mods))
        for (cid, svc, cpt), parts in got.items():
            for p in parts_of.get((cid, svc, cpt)) or ():
                if _OWN in parts or part_fits(p, parts):
                    out[cid].add((svc, cpt, p))
        return out

    def _g8_one_service(self, protos: list[dict]) -> list[dict]:
        """Production's never-answered claims (G8) as one item per service, priced as the weekly exports' and the 837s' are
        (the residuals of fix7b). EMA's claims of one patient's service (the same person, date and code) billed as the whole
        service and again in its parts (the reading, 26, and the technical part, TC), or twice, are one service: the newest
        charge carries what it covers (a whole covers both parts; both parts cover the whole; a part covers itself), and a
        line an older claim bills that a newer one covers leaves that claim's item (an item with no line left is not
        listed). Each line is then priced by price_unanswered_lines: the payer's rate for the part it bills (EMA's lines
        carry no modifiers: the part is what its claim's 837, remittance or weekly export names, ema_checks.line_parts),
        times its units, the multiple-procedure share, never above its charge; a line billed at $0, or a token charge under
        $1, is nothing owed. Before, each claim was its own item and a part was priced at the whole service's rate."""
        from . import ema_checks as ec
        mine = [p for p in protos if p.get("signal") == "NO_ANSWER" and p.get("ema_link") is not None
                and (p.get("evidence") or {}).get("source") == "production"]
        if not mine:
            return protos

        def lines_of(p: dict) -> list:
            return [ln for ln in (p.get("evidence") or {}).get("lines") or [] if isinstance(ln, list) and len(ln) > 2 and ln[0] and ln[1]]
        c = self.conn
        lks = sorted({p["ema_link"] for p in mine})
        info: dict[tuple, tuple] = {}       # (link, date, code) -> (units, the patient's key, first charge posted)
        for i in range(0, len(lks), 500):
            part = lks[i:i + 500]
            for lk, svc, cpt, units, person, nk, fc in c.execute(
                    f"""SELECT link, svc, cpt, units, person, name_key, first_charge_posted FROM ema_lines
                        WHERE link IN ({','.join('?' * len(part))})""", part):
                info[(lk, svc, cpt)] = (units, person if person is not None else nk, str(fc or "")[:10])
        keys = {(p["ema_link"], ln[1], ln[0]) for p in mine for ln in lines_of(p)}
        found = self._line_parts(keys)
        part_of = {k: ec.own_part(found.get(k)) for k in keys}
        groups: dict[tuple, list] = defaultdict(list)       # (patient, date, code) -> [(first charge, link, part)]
        for k in sorted(keys):
            who = (info.get(k) or (None, None, ""))[1]
            if who is not None:
                groups[(who, k[1], k[2])].append(((info.get(k) or (None, None, ""))[2], k[0], part_of.get(k, "")))

        def covers(got: set, part: str) -> bool:
            return "" in got or (bool(part) and part in got) or (not part and {"26", "TC"} <= got)
        carried: set[tuple] = set()         # (link, date, code) a newer charge of the same service carries
        for (_who, svc, cpt), xs in groups.items():
            if len({x[1] for x in xs}) < 2:
                continue
            got: set = set()
            for _fc, lk, part in sorted(xs, reverse=True):
                if covers(got, part):
                    carried.add((lk, svc, cpt))
                else:
                    got.add(part)
        out = []
        for p in protos:
            if not any(p is q for q in mine):
                out.append(p)
                continue
            lk = p["ema_link"]
            claim = dict(p.get("claim") or {})
            ev = dict(p.get("evidence") or {})
            left = [ln for ln in lines_of(p) if (lk, ln[1], ln[0]) not in carried]
            if not left:
                self.ema_counts["unanswered_claims_one_service"] += 1
                continue
            year = claim.get("year") or str(left[0][1])[:4]
            billed = [(ln[0], ln[1], int(ln[2] or 0), (info.get((lk, ln[1], ln[0])) or (None,))[0],
                       [part_of[(lk, ln[1], ln[0])]] if part_of.get((lk, ln[1], ln[0])) else [])
                      for ln in left if int(ln[2] or 0) >= 100]
            cents = {(x[0], x[1]): x[3] for x in (self.price_unanswered_lines(claim.get("payer_group"), claim.get("payer_kind"), year,
                                                                              billed) if billed else [])}
            new_lines = [[ln[0], ln[1], ln[2], cents.get((ln[0], ln[1])) or 0] + ([part_of[(lk, ln[1], ln[0])]] if part_of.get((lk, ln[1], ln[0])) else [])
                         for ln in left]
            expected = sum(x[3] for x in new_lines) if any(x[3] for x in new_lines) else None
            charges = sum(int(ln[2] or 0) for ln in left)
            if expected and charges and expected > charges:
                expected = charges          # never valued above what was billed for it
            codes = sorted({ln[0] for ln in left})
            ev.update(lines=new_lines, codes=codes)
            title = p.get("title") or ""
            if len(left) < len(lines_of(p)):
                ev["carried_by_newer_charge"] = [[ln[0], ln[1]] for ln in lines_of(p) if (lk, ln[1], ln[0]) in carried]
                claim.update(charge_cents=charges or None, procedure_code=max(left, key=lambda ln: int(ln[2] or 0))[0])
                if ": " in title:
                    title = title.rsplit(": ", 1)[0] + ": " + ", ".join(codes[:5]) + ("…" if len(codes) > 5 else "")
            out.append(dict(p, evidence=ev, claim=claim, title=title, expected_cents=expected, shortfall_cents=expected))
        return out

    def _exports_yield_to_newer_bills(self) -> None:
        """One visit chased under two bills across the two never-answered passes (answers-1, the exports' side): a visit
        (date, code and part of the service) on an item of EMA's weekly exports that the practice billed again under a newer
        bill that only the 837s know, and that the 837 pass lists, is chased under the newer bill only. The patient must be
        one person (_one_patient: the same keyed name and birth date, or the same keyed name under the same member id, each
        from the claim's own evidence; never a member id or its one remitted name: two people on one subscriber id seen the
        same day are two services, fx-exports-r2-3; never the keyed name alone: a father and a son of one name, r2-4). The
        newer bill went out after the older (an earlier or same-day send is not a bill made again). And an exports' item
        kept because its bill went out again as a newer submission (_note_resent_bill, answers-5) yields to the 837 pass's
        item for that bill when there is one: the resend is chased there, once (fx-exports-r2-2).
        The exports' item keeps any other service, sized again; with none left it is not listed, and its work goes to the
        newer bill's item (save: heir keys), which save() hands only to an item new in this run. So an exports' item a person
        worked whose newer item was already listed stays listed, pointing to the newer bill, and the newer item points back:
        the work is never left on an item no list shows (fx-exports-r1-2, r2-5)."""
        got = self.__dict__.pop("_export_final", None)
        if not got:
            return
        final, newest = got
        newer: dict[tuple, list] = defaultdict(list)    # (date, code, part) -> the 837 pass's items that carry it
        by_bill: dict[str, list] = defaultdict(list)    # bill -> the 837 pass's items of it
        n837: dict[str, dict] = {}
        for f in self.findings.values():
            pcn = f.get("patient_control_number")
            if f.get("source") != "837" or not pcn or f["finding_id"] != _fid(OPEN_837_ID, pcn):
                continue
            try:
                ev = json.loads(f.get("evidence") or "{}")
            except ValueError as exc:
                from . import runlog
                runlog.caught(exc, "exports_yield_to_newer_bills", level="warning")
                continue
            svcs = {(ln[1], ln[0], (ln[4] if len(ln) > 4 else "") or "") for ln in ev.get("lines") or []
                    if isinstance(ln, list) and len(ln) > 1 and ln[0] and ln[1]}
            if not svcs or not ev.get("sent"):
                continue
            n837[pcn] = {"f": f, "sent": ev["sent"], "key": (ev.get("sent_837") or {}).get("claim_837_key"), "svcs": svcs}
            by_bill[bill_of(pcn)].append(pcn)
            for sv in svcs:
                newer[sv].append(pcn)
        if not n837:
            return
        pairs: dict[str, set] = {}      # export claim id -> the 837 claim ids that may carry its visits
        resend: dict[str, set] = {}     # export claim id kept for a newer submission -> the 837 pass's items of its bill
        for cd, items in final:
            if cd.get("bill_resent_837") and by_bill.get(cd["b"]):
                resend[cd["pcn"]] = set(by_bill[cd["b"]])
            hits = {p for x in items if x[0] and x[1] for p in newer.get((x[1], x[0], item_part(x))) or ()
                    if bill_of(p) != cd["b"] and n837[p]["sent"] > (cd.get("sent") or "")}
            if hits:
                pairs[cd["pcn"]] = hits
        if not pairs and not resend:
            return
        info = {cd["pcn"]: (cd, items) for cd, items in final}
        same_of: dict[str, set] = {e: set(ps) for e, ps in resend.items()}     # one bill is one patient
        if pairs:
            # who each claim's patient is: the exports' claims as their pass found them, the 837 pass's from their 837
            from . import ema_reports as er
            er.register_functions(self.conn, create=False)     # the keyed name (rw_name_key) the patient keys are read with
            members: dict[str, str] = {}
            keys837 = sorted({n837[p]["key"] for ps in pairs.values() for p in ps if n837[p]["key"]})
            for i in range(0, len(keys837), 500):
                part = keys837[i:i + 500]
                for key, member in self.conn.execute(f"""SELECT claim_837_key, subscriber_id FROM claims_837
                                                          WHERE claim_837_key IN ({','.join('?' * len(part))})""", part):
                    members[key] = member
            cds = [dict(info[e][0], items=info[e][1]) for e in sorted(pairs)]
            cds += [{"pcn": p, "b": bill_of(p), "items": [], "k": {"member": members.get(n837[p]["key"])}}
                    for p in sorted({p for ps in pairs.values() for p in ps})]
            answers = {cd["pcn"]: {"link": cd.get("ema_link") if cd["pcn"] in info else n837[cd["pcn"]]["f"].get("ema_link")}
                       for cd in cds}
            who = self._who(cds, answers)
            for e, ps in pairs.items():
                same = {p for p in ps if self._one_patient(who.get(e) or {}, who.get(p) or {})}
                if same:
                    same_of[e] = same_of.get(e, set()) | same
        if not same_of:
            return
        prior = self._prior_work({_fid(info[e][0].get("id_signal") or "EMA_OPEN_CLAIM", e) for e in same_of}
                                 | {_fid(OPEN_837_ID, p) for ps in same_of.values() for p in ps})
        heirs = self.__dict__.setdefault("_heirs", {})
        for e in sorted(same_of):
            cd, items = info[e]
            same = same_of[e]
            whole = e in resend and bool(resend[e] & same)
            taken = {sv for p in same for sv in n837[p]["svcs"]}
            left = [] if whole else [x for x in items if not (x[0] and x[1] and (x[1], x[0], item_part(x)) in taken)]
            if len(left) == len(items):
                continue
            fid = _fid(cd.get("id_signal") or "EMA_OPEN_CLAIM", e)
            to = max((p for p in same if not whole or p in resend[e]), key=lambda p: (n837[p]["sent"], p))
            to_fid = _fid(OPEN_837_ID, to)
            f = n837[to]["f"]
            if not left and fid in self.findings and prior.get(fid, {}).get("worked") and to_fid in prior:
                # the newer item was listed before this run: save() would leave this item's work on a row no list shows
                self.ema_counts["export_kept_for_its_work"] += 1
                both = prior[to_fid].get("worked")
                g = self.findings[fid]
                note = (f"This visit is also chased on claim {to} (a newer bill, sent {n837[to]['sent']}). The work recorded here "
                        f"stays here: " + ("both items were worked, so check which is the payer's claim and set the other Done"
                                            if both else f"continue it on claim {to}'s item, then set this one Done"))
                g["hint"] = f"{g['hint']}. {note}" if g.get("hint") else note
                back = (f"This visit was billed before on claim {e} (sent {cd.get('sent')}), which is still listed with the work "
                        f"a person recorded on it")
                f["hint"] = f"{f['hint']}. {back}" if f.get("hint") else back
                continue
            self.ema_counts["export_yields_to_newer_bill"] += 1
            if left:
                self._emit_open_claim(cd, left, newest)     # its other services stay chased, sized again
                if cd.get("bill_resent_837"):
                    self._note_resent_bill(cd)
            else:
                self.findings.pop(fid, None)
                heirs.setdefault(to_fid, []).append((cd.get("id_signal") or "EMA_OPEN_CLAIM", e))
                self._fold_into(to_fid, fid, "exports_yield_to_newer_bill",
                                f"The exports' claim {e} of the same visit yields to this newer bill")
            note = (f"This bill went out before as claim {e} (sent {cd.get('sent')}), which EMA's exports list and no payer "
                    f"answered: only this newer submission is chased" if whole else
                    f"This visit was billed before on claim {e} (sent {cd.get('sent')}), which no payer answered either: the "
                    f"practice made the bill again, so only this newer bill is chased")
            f["hint"] = f"{f['hint']}. {note}" if f.get("hint") else note

    def _fold_into(self, into: str, gone: str, rule: str, note: str) -> None:
        """Record on the item that carries a claim's chase now (`into`) the item that no longer lists it (`gone`), in its
        evidence's 'folded' list, as archive_rules' folds do: the self-check that keeps an item no rule explains reads
        it (selfcheck.check_vanished), and so do the decision log and Results."""
        f = self.findings.get(into)
        if f is None:
            return
        try:
            ev = json.loads(f.get("evidence") or "{}")
        except ValueError as exc:
            from . import runlog
            runlog.caught(exc, "exports_fold_into", level="warning")
            return
        folded = [x for x in ev.get("folded") or [] if not (isinstance(x, dict) and x.get("finding_id") == gone)]
        folded.append({"finding_id": gone, "rule": rule, "signal": "NO_ANSWER", "note": note})
        ev["folded"] = folded
        f["evidence"] = json.dumps(ev, default=str)

    def _prior_work(self, fids: set) -> dict[str, dict]:
        """What the store holds for these items before this run's save: {finding id: {'worked': a person recorded something
        on it (a status other than To do, a note, an appeal, a fax sent or being prepared)}}, for the items listed before."""
        c = self.conn
        out: dict[str, dict] = {}
        ids = sorted(fids)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "findings" not in tables:
            return out
        cols = {r[1] for r in c.execute("PRAGMA table_info(findings)")}
        extra = [x for x in ("appealed_at", "auto_note") if x in cols]
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = ",".join("?" * len(part))
            for row in c.execute(f"SELECT finding_id, status, notes{''.join(', ' + x for x in extra)} FROM findings "
                                 f"WHERE finding_id IN ({q})", part):
                r = dict(zip(["finding_id", "status", "notes"] + extra, row))
                st = r.get("status") or "open"
                worked = bool(r.get("notes") or r.get("appealed_at")) or (st not in ("open", "resolved")) \
                    or (st == "resolved" and not r.get("auto_note"))
                out[r["finding_id"]] = {"worked": worked}
            if "faxes" in tables:
                for (fid,) in c.execute(f"""SELECT DISTINCT finding_id FROM faxes WHERE direction='out' AND finding_id IN ({q})""", part):
                    out.setdefault(fid, {})["worked"] = True
            if "meta" in tables:
                for (key,) in c.execute(f"SELECT key FROM meta WHERE key IN ({','.join('?' * len(part))})",
                                        ["fax:prep:" + x for x in part]):
                    out.setdefault(key[len("fax:prep:"):], {})["worked"] = True
        return out

    def _emit_proto(self, p: dict) -> str | None:
        """One proto-finding of ema_checks as an item: _emit, then the deadline, hint, source and EMA claim number the
        check gives. An item already listed under the same id (the weekly exports' path, an earlier check) wins."""
        fid = _fid(p.get("id_signal") or p["signal"], *p["key_parts"])
        if fid in self.findings:
            return None
        ev = dict(p.get("evidence") or {})
        if p.get("ema_heir_keys"):
            ev["ema_heir_keys"] = [list(h) for h in p["ema_heir_keys"]]
        claim = dict(p.get("claim") or {})
        self._emit(p["signal"], p["category"], p["severity"], p["actionable"], p["title"], None, p.get("expected_cents"),
                   p.get("shortfall_cents"), p.get("carc"), p.get("rarc"), ev, key_parts=tuple(p["key_parts"]), claim=claim,
                   id_signal=p.get("id_signal"))
        f = self.findings.get(fid)
        if f is None:
            return None
        f["appeal_deadline"], f["deadline_basis"] = p.get("appeal_deadline"), p.get("deadline_basis")
        legal_p = p.get("legal_deadline")           # the payer's own limit a check worked out beside its safe date
        f["legal_deadline"] = legal_p if (legal_p and f["appeal_deadline"] and legal_p > f["appeal_deadline"]) else None
        # the same window the check dated it by: the denial's reasons and the payer's name pick the plan's (Anthem's clinical
        # appeal, a state's managed care plan named '<plan> Medicaid of Ky'; fx-windows, review r2-1, r2-2)
        rule = (appeal_rule(claim.get("payer_group"), claim.get("payer_kind"), **rule_facts(claim, carc=p.get("carc")))
                if p["signal"] == "EMA_DENIED" else None)
        if rule and f["appeal_deadline"] and not rule.get("no_appeal"):     # a no-appeal payer's item is dated by its new claim's limit
            legal = legal_deadline(_add_days(f["appeal_deadline"], -rule["days"]), rule)
            f["legal_deadline"] = legal if legal and legal != f["appeal_deadline"] else None
        if p.get("hint"):
            f["hint"] = p["hint"]
        f["source"] = p.get("source") or "ema"
        if p.get("ema_link") is not None:
            f["ema_link"] = p["ema_link"]
        if p.get("heir_keys"):
            self.__dict__.setdefault("_heirs", {})[fid] = [tuple(h) for h in p["heir_keys"]]
        self.ema_counts[p["signal"]] += 1
        return fid

    def _emit_protos(self, name: str, protos) -> int:
        n = 0
        for p in protos or []:
            if self._emit_proto(p):
                n += 1
        return n

    def _protos_answered(self, name: str, protos: list[dict]) -> list[dict]:
        """EMA's reports' never-answered (G8) and rejected (C3f) claims less those the one answered rule shows answered
        (_answered_rule): EMA's postings on the claim's own EMA claim, every service paid or settled for the patient on
        another EMA claim by the claim's own payer, or answered as primary by it on another bill. A rejected claim is
        answered only by a posting after its charges were posted (it cannot have gone out before). Each line is for the
        part of the service its claim's 837, remittance or weekly export names (ema_checks.line_parts: EMA's lines carry no
        modifiers), else the whole service."""
        from . import ema_checks as ec
        c = self.conn
        cds, answers, key_of = [], {}, {}
        mine = self._line_parts({(p.get("ema_link"), ln[1], ln[0]) for p in protos if p.get("ema_link") is not None
                                 for ln in (p.get("evidence") or {}).get("lines") or [] if ln and len(ln) > 1 and ln[0] and ln[1]})
        for i, p in enumerate(protos):
            lk = p.get("ema_link")
            if p.get("signal") not in ("NO_ANSWER", "REJECTED_PRE_PAYER") or lk is None:
                continue
            claim = p.get("claim") or {}
            items = [(ln[0], ln[1], ln[2] if len(ln) > 2 else 0, None, None, ec.own_part(mine.get((lk, ln[1], ln[0]))))
                     for ln in (p.get("evidence") or {}).get("lines") or [] if ln and len(ln) > 1 and ln[0] and ln[1]]
            if not items:
                continue
            first = str(c.execute("SELECT MIN(first_charge_posted) FROM ema_lines WHERE link = ?", (lk,)).fetchone()[0] or "")[:10]
            if p["signal"] == "REJECTED_PRE_PAYER":
                # the send EMA turned back is not dated in its reports: only a posting after the day a report read the
                # claim Rejected answers it (a payment before then answered an earlier send, not this one)
                first = max(first, str((p.get("evidence") or {}).get("status_as_of") or "")[:10])
            key = key_of[i] = f"{name}:{lk}"
            cid = claim.get("patient_control_number")
            cds.append({"pcn": key, "b": bill_of(cid) if cid else None, "items": items, "sent": first or "",
                        "after": (first or None) if p["signal"] == "REJECTED_PRE_PAYER" else None,
                        "grp": claim.get("payer_group"), "ema_payer": claim.get("payer_name"), "secondary": [], "k": {}})
            answers[key] = {"answered": False, "how": None, "on": None, "paid_cents": 0, "link": lk, "bridge": "report"}
        if not cds:
            return protos
        own = self._links_answered([(cd["pcn"], answers[cd["pcn"]]["link"], cd["sent"]) for cd in cds])
        for key, lk in own:
            answers[key]["answered"] = True
        rule = self._answered_rule("answered_" + name, cds, answers)
        out = []
        for i, p in enumerate(protos):
            key = key_of.get(i)
            if key is not None and not rule.get(key, [None]):
                self.ema_counts[f"{name}_answered_in_ema"] += 1
                continue
            out.append(p)
        return out

    def run_ema_report_claims(self) -> None:
        """Step 5, from the reports: claims that never left EMA (C3b), claims the reports call Rejected (C3f), and claims
        Production shows no payer answered (G8). A claim the weekly exports' path listed is left to it (that path wins), and
        so is its EMA claim number. First, with or without EMA's reports, the weekly exports' items yield each visit the
        practice billed again under a newer bill that only the 837s know (_exports_yield_to_newer_bills)."""
        self._ema_step("exports_yield_to_newer_bills", self._exports_yield_to_newer_bills)
        if not self.ema_on:
            return
        from . import ema_checks as ec
        skip = set(self._export_claims)
        for name, fn in (("not_sent", ec.not_sent), ("rejected_claims", ec.rejected_claims), ("unanswered_claims", ec.unanswered_claims)):
            if name == "unanswered_claims":
                skip |= self._837_claims    # a claim the 837s show sent is judged from its send there, not from its charge
            # the parts of a service the claims' lines bill: a payment for another part is not theirs (T-C1)
            more = {"parts": self._line_parts} if name in ("not_sent", "unanswered_claims") else {}
            protos = self._ema_step(name, fn, self.conn, today=self.today, rate=self._rate, skip_claim_ids=skip, **more) or []
            if name != "not_sent" and protos:
                # the one answered rule every never-answered claim meets; should it fail, every claim stays listed
                kept = self._ema_step(name + "_answered", self._protos_answered, name, protos)
                if kept is not None:        # None: the step failed (recorded), and every claim stays listed
                    protos = kept
            if name == "unanswered_claims" and protos:
                # one item per service, each line priced as the other never-answered passes price theirs; should it fail,
                # every claim stays listed as Production's check made it
                one = self._ema_step(name + "_one_service", self._g8_one_service, protos)
                if one is not None:
                    protos = one
            for p in protos:
                links = {p.get("ema_link")} | set((p.get("evidence") or {}).get("ema_links") or [])
                if links & self._export_links:
                    continue                # the same EMA claim, listed by the weekly exports' path under its claim id
                if self._emit_proto(p):
                    cid = (p.get("claim") or {}).get("patient_control_number")
                    if cid:
                        skip.add(cid)

    def run_ema_denials(self) -> None:
        """Step 6 (C5): denials only EMA saw, where no remittance and no item from one covers the line."""
        if not self.ema_on:
            return
        from . import ema_checks as ec
        self.__dict__.pop("_f835", None)        # the run's 835 items as they stand now
        protos = self._ema_step("ema_denials", ec.ema_denials, self.conn, today=self.today, rate=self._rate, ratio=self._ratio,
                                has_835_finding=self._has_835_finding, paid_share=self._paid_share) or []
        self._emit_protos("ema_denials", protos)
        # a claim listed as unanswered whose denial EMA posted: C5's item is the claim's answer (it inherits the chase's work
        # through its heir keys), so the chase is not listed beside it
        for p in protos:
            for h in p.get("heir_keys") or []:
                fid = _fid(*h)
                f = self.findings.get(fid)
                if f is not None and f.get("signal") == "NO_ANSWER" and _fid(p.get("id_signal") or p["signal"], *p["key_parts"]) in self.findings:
                    del self.findings[fid]
                    self.ema_counts["export_answered_by_ema_denial"] += 1

    def run_ema_contractual(self) -> None:
        """Step 7 (C7): a payer's denials EMA posts as contractual adjustments (outside the money totals)."""
        if not self.ema_on:
            return
        from . import ema_checks as ec
        self._emit_protos("denial_as_contractual",
                          self._ema_step("denial_as_contractual", ec.denial_as_contractual, self.conn, today=self.today, ratio=self._ratio))

    def _outcome_items(self) -> list[dict]:
        """The items a line outcome is worked out for: the money items from the remittances (not the claim-level chases)
        and the denials only EMA saw. Each carries the status a person last gave it (the checks read it)."""
        from . import ema_checks as ec
        prev = self.__dict__.get("_prev_status")
        if prev is None:
            prev = self._prev_status = {r[0]: r[1] for r in self.conn.execute("SELECT finding_id, status FROM findings")}
        out = []
        for f in self.findings.values():
            if f["signal"] == "EMA_DENIED" or (f["category"] in ec.MONEY_CATEGORIES and f["signal"] not in ec.CLAIM_SIGNALS
                                               and f["signal"] not in EMA_NATIVE_SIGNALS):
                f.setdefault("status", prev.get(f["finding_id"]) or "open")
                f.setdefault("stale", 0)
                out.append(f)
        return out

    def run_ema_outcomes(self) -> None:
        """Step 9, after resolve_elsewhere and the archive rules: each item's line outcome in EMA, once (G3), then what EMA
        shows paid on checks no 835 carries (C6): the same payer paying at least half of what the item is expected to
        bring (95% of a shortfall) closes it with the payment as its proof, and a newer pull that no longer shows that
        payment reopens it; another payer paying a claim that was another payer's to pay (a coordination-of-benefits or
        eligibility denial, or a line EMA moved to that payer) closes it as rebilled and paid; anything less is a hint. A
        person's status is never replaced (save)."""
        if not self.ema_on:
            return
        from . import ema_checks as ec
        items = self._outcome_items()
        if not items:
            return
        oc = self._ema_step("line_outcomes", ec.line_outcomes, self.conn, items, code_once=self._code_once, today=self.today) or {}
        self.ema_outcomes = oc
        pe = self._ema_step("paid_in_ema", ec.paid_in_ema, self.conn, items, oc, closes=EMA_CLOSES) or {}
        rebill = {fid: r for fid, r in pe.items() if r.get("action") == "hint_other_payer"}
        moved = self._ema_line_payers([tuple(oc[fid]["lines"][0]) for fid in rebill if (oc.get(fid) or {}).get("lines")]) if rebill else {}
        for fid, r in pe.items():
            f = self.findings.get(fid)
            if f is None or f.get("auto_status"):
                continue
            o = oc.get(fid) or {}
            act = r.get("action")
            if act == "close" and EMA_CLOSES:
                self._close_from_ema(f, r, o, "already paid before this denial; the later payment looks like a duplicate"
                                     if r.get("duplicate") else "EMA shows it paid", r.get("note") or "")
                self.ema_counts["c6_closed_duplicate" if r.get("duplicate") else "c6_closed"] += 1
            elif act == "hint_other_payer" and EMA_CLOSES and self._rebilled(f, o, moved):
                payer = r.get("payer") or "another payer"
                note = (f"Rebilled to {payer}, paid: EMA shows {_usd(r.get('paid_cents'))} paid by {payer} on {r.get('date')} "
                        f"(check ending {r.get('check_tail') or ''}, no 835). Reopen it if that payment is for something else.")
                self._close_from_ema(f, r, o, f"rebilled to {payer}, paid", note)
                self.ema_counts["c6_rebilled"] += 1
            elif r.get("hint"):
                f["hint"] = r["hint"]
                self.ema_counts["c6_" + str(act)] += 1

    def _close_from_ema(self, f: dict, r: dict, o: dict, how: str, note: str) -> None:
        """Close an item by a payment EMA posted on a check no 835 carries, with that payment as the proof row."""
        payer, cents, when = r.get("payer") or "insurance", r.get("paid_cents") or 0, r.get("date")
        detail = {"payment_date": when, "claim": f.get("patient_control_number"), "paid_cents": cents, "how": how,
                  "trace": f"EMA check ending {r.get('check_tail') or ''}", "source": "EMA Production", "payer": payer,
                  "ema_link": o.get("link"), "postings_through": o.get("as_of"),
                  "proof": [{"date": when, "source": "production", "kind": "payment", "payer": payer, "amount_cents": cents,
                             "codes": [], "text": f"EMA posted {_usd(cents)} from {payer} on a check ending {r.get('check_tail') or ''} "
                                                  "that no 835 carries", "decisive": True}]}
        used = ("ema",) + tuple(r.get("used") or (when, r.get("check_tail"), o.get("link")))
        self._mark_paid(f, when, cents if r.get("recover_cents") is None else max(0, min(cents, r["recover_cents"])), note, detail,
                        used=used)
        f["hint"] = None

    def _ema_line_payers(self, keys: list[tuple]) -> dict[tuple, str | None]:
        """The payer group EMA's line is billed to now (the newest pull), for (EMA claim number, date, code)."""
        out: dict[tuple, str | None] = {}
        keys = sorted({k for k in keys if k and len(k) == 3})
        for i in range(0, len(keys), 300):
            part = keys[i:i + 300]
            q = " OR ".join("(link=? AND svc=? AND cpt=?)" for _k in part)
            for lk, svc, cpt, grp in self.conn.execute(f"SELECT link, svc, cpt, payer_group FROM ema_lines WHERE {q}",
                                                       [x for k in part for x in k]):
                out[(lk, svc, cpt)] = grp
        return out

    def _rebilled(self, f: dict, o: dict, moved: dict) -> bool:
        """An unpaid denial another payer then paid in full was that payer's to pay: the denial said so (coordination of
        benefits, eligibility, sent on), or EMA has moved the line to the payer that paid."""
        from . import ema_checks as ec
        if f.get("category") != "denial" or (f.get("paid_cents") or 0) > 0:
            return False
        if (f.get("carc") or "") in OTHER_PAYER_CARCS:
            return True
        payer_now = moved.get(tuple(o["lines"][0])) if o.get("lines") else None
        # the paying payer's payment came after the denial's day: on an EMA-only denial, a payment posted the same day is the
        # first payer's own, already taken off the second payer's share (C5)
        late = (lambda p: (p.get("posted") or "") > (o.get("anchor") or "")) if f.get("signal") == "EMA_DENIED" else (lambda p: True)
        paying = next((p.get("group") for p in o.get("payments") or [] if p.get("kind") == "ema" and not p.get("same") and late(p)), None)
        return bool(payer_now and paying and ec._same_payer(payer_now, paying) and not ec._same_payer(payer_now, f.get("payer_group")))

    def run_ema_unposted(self) -> None:
        """Step 10 (C4): 835 payments EMA never posted, or posted short (one item per payment; work type post)."""
        if not self.ema_on:
            return
        from . import ema_checks as ec
        self._emit_protos("unposted_remits", self._ema_step("unposted_remits", ec.unposted_remits, self.conn, today=self.today))

    def run_ema_writeoffs(self) -> None:
        """Step 11: what EMA wrote off on each item's line (C2; the larger of the ERA-lines figure and EMA's own, never a
        sum), when and how (an automatic write-off on the posting day, with its evidence), and EMA's overlay columns (W2:
        the claim's newest status as context, the line's balance and outcome, the newest work EMA shows)."""
        if not self.ema_on:
            return
        from . import ema_checks as ec
        oc = self.ema_outcomes
        items = self._outcome_items()
        wo = self._ema_step("written_off_in_ema", ec.written_off_in_ema, self.conn, items, oc) or {}
        for fid, w in wo.items():
            f = self.findings.get(fid)
            if f is None:
                continue
            for k in ("written_off_cents", "written_off_on", "written_off_source", "written_off_kind", "written_off_when"):
                f[k] = w.get(k)
            if w.get("evidence_level"):
                f["ema_writeoff_evidence"] = w["evidence_level"]
            if w.get("written_off_basis"):
                ev = json.loads(f["evidence"]) if f.get("evidence") else {}
                ev["written_off_basis"] = w["written_off_basis"]
                f["evidence"] = json.dumps(ev, default=str)
            self.ema_counts["c2_marked"] += 1
        for fid, o in oc.items():
            f = self.findings.get(fid)
            if f is None:
                continue
            if o.get("outcome") in ec.WRITE_OFF_OUTCOMES and o.get("pattern"):
                ev = json.loads(f["evidence"]) if f.get("evidence") else {}
                ev["writeoff_pattern"], ev["writeoff_carcs"] = o["pattern"], o.get("carcs") or []
                f["evidence"] = json.dumps(ev, default=str)
            if o.get("evidence_level") and not f.get("ema_writeoff_evidence"):
                f["ema_writeoff_evidence"] = o["evidence_level"]
        ov_items = [f for f in self.findings.values() if f.get("patient_control_number") or f.get("ema_link")]
        ov = self._ema_step("overlays", ec.overlays, self.conn, ov_items, oc, today=self.today) or {}
        for fid, o in ov.items():
            f = self.findings.get(fid)
            if f is None:
                continue
            for k, v in o.items():
                if k == "ema_link" and v is None:
                    continue
                f[k] = v

    def run_more_rules(self) -> None:
        """The rules the practice-manager trace and the independent answer key found missing, which need EMA's reports:
        a secondary never billed and credit balances to refund. Short payments on checks no 835 carries are not judged:
        EMA's lines carry no modifier and no provider, and a trial rule agreed with the answer key on 43 of 409 items."""
        if not self.ema_on:
            return
        self._ema_step("secondary_not_billed", self.run_secondary_not_billed)
        self._ema_step("credit_balances", self.run_credit_balances)

    def _ema_lines_for(self, keys: list[tuple]) -> dict[tuple, dict]:
        """EMA's line for each (bill, date, code) of a remittance, through the map (no conflict, never the ASC's facility
        claim): {key: {link, secondary_payer, primary_payer, payer_group, balance, pat_paid, charges, other_paid}}, where
        other_paid is what payers other than the line's primary posted on it."""
        from . import ema_checks as ec
        from . import ema_reports as er
        out: dict[tuple, dict] = {}
        groups_of: dict[str, str | None] = {}
        keys = sorted(set(keys))
        for i in range(0, len(keys), 400):
            part = keys[i:i + 400]
            self.conn.execute("DROP TABLE IF EXISTS temp.dt_keys")
            self.conn.execute("CREATE TEMP TABLE dt_keys (bill TEXT, svc TEXT, cpt TEXT)")
            self.conn.executemany("INSERT INTO temp.dt_keys VALUES (?,?,?)", part)
            self.conn.execute("CREATE INDEX temp.ix_dt_keys ON dt_keys(bill)")
            rows = self.conn.execute("""SELECT k.bill, k.svc, k.cpt, l.link, l.secondary_payer, l.primary_payer, l.payer_group, l.balance,
                                               COALESCE(l.pat_paid_joined, 0), l.charges, l.name_key
                                        FROM temp.dt_keys k JOIN ema_link_map m ON m.bill = k.bill AND COALESCE(m.conflict,0) = 0
                                             AND COALESCE(m.facility,0) = 0
                                        JOIN ema_lines l ON l.link = m.link AND l.svc = k.svc AND l.cpt = k.cpt
                                        WHERE (COALESCE(l.flags,0) & 17) = 0 AND COALESCE(l.facility,0) = 0""").fetchall()
            pays: dict[tuple, list] = defaultdict(list)
            elsewhere: dict[tuple, list] = defaultdict(list)     # (patient key, date, code) -> payments on the patient's other EMA claims
            reasons: dict[tuple, list] = defaultdict(list)       # the payers' own reasons (not PR) on the line, with who and when
            if rows:
                self.conn.execute("DROP TABLE IF EXISTS temp.dt_links")
                self.conn.execute("CREATE TEMP TABLE dt_links (link INTEGER, svc TEXT, cpt TEXT)")
                self.conn.executemany("INSERT INTO temp.dt_links VALUES (?,?,?)", {(r[3], r[1], r[2]) for r in rows})
                for lk, svc, cpt, grp, cents, posted in self.conn.execute(
                        """SELECT y.link, y.svc, y.cpt, y.payer_group, COALESCE(y.ins_paid,0), y.posted FROM temp.dt_links d
                           JOIN ema_line_payments y ON y.link = d.link AND y.svc = d.svc AND y.cpt = d.cpt"""):
                    pays[(lk, svc, cpt)].append((grp, cents, posted))
                for lk, svc, cpt, zg, code, payer, first, cents in self.conn.execute(
                        """SELECT z.link, z.svc, z.cpt, z.grp, z.code, z.payer, z.first_posted,
                                  COALESCE(z.contractual,0) + COALESCE(z.noncontractual,0) FROM temp.dt_links d
                           JOIN ema_line_reasons z ON z.link = d.link AND z.svc = d.svc AND z.cpt = d.cpt
                           WHERE COALESCE(z.grp, '') NOT IN ('', 'PR')"""):
                    reasons[(lk, svc, cpt)].append((zg, code, payer, first, cents))
                self.conn.execute("DROP TABLE IF EXISTS temp.dt_links")
                nks = sorted({(r[10], r[1], r[2]) for r in rows if r[10] is not None})
                if nks:
                    self.conn.execute("DROP TABLE IF EXISTS temp.dt_nk")
                    self.conn.execute("CREATE TEMP TABLE dt_nk (nk INTEGER, svc TEXT, cpt TEXT)")
                    self.conn.executemany("INSERT INTO temp.dt_nk VALUES (?,?,?)", nks)
                    for nk, svc, cpt, lk, grp, cents in self.conn.execute(
                            """SELECT l.name_key, l.svc, l.cpt, l.link, y.payer_group, COALESCE(y.ins_paid,0) FROM temp.dt_nk n
                               JOIN ema_lines l ON l.name_key = n.nk AND l.svc = n.svc AND l.cpt = n.cpt AND COALESCE(l.facility,0) = 0
                               JOIN ema_line_payments y ON y.link = l.link AND y.svc = l.svc AND y.cpt = l.cpt
                               WHERE COALESCE(y.ins_paid,0) > 0"""):
                        elsewhere[(nk, svc, cpt)].append((lk, grp, cents))
                    self.conn.execute("DROP TABLE IF EXISTS temp.dt_nk")
            for b, svc, cpt, lk, sec, prim, grp, bal, pat, chg, nk in rows:
                other = sum(c for g, c, _p in pays.get((lk, svc, cpt), []) if c > 0 and g and grp and not ec._same_payer(g, grp))
                other += sum(c for olk, g, c in elsewhere.get((nk, svc, cpt), []) if olk != lk and c > 0 and g and grp
                             and not ec._same_payer(g, grp))
                # every answer on the line from a payer other than the primary: a payment row ($0 included) or its own reason
                answers = [(p or "", g, c, "payment") for g, c, p in pays.get((lk, svc, cpt), []) if g and grp and not ec._same_payer(g, grp)]
                for zg, code, payer, first, cents in reasons.get((lk, svc, cpt), []):
                    if payer not in groups_of:
                        groups_of[payer] = er.payer_group_of(self.conn, payer)[0] if payer else None
                    g = groups_of[payer]
                    if g and grp and not ec._same_payer(g, grp):
                        answers.append((first or "", g, cents, f"{zg}-{code}"))
                out.setdefault((b, svc, cpt), {"link": lk, "secondary_payer": sec, "primary_payer": prim, "payer_group": grp,
                                               "balance": bal or 0, "pat_paid": pat or 0, "charges": chg or 0, "other_paid": other,
                                               "answers": sorted(answers)})
            self.conn.execute("DROP TABLE IF EXISTS temp.dt_keys")
        return out

    def run_secondary_not_billed(self) -> None:
        """The primary paid and left the patient's share (deductible, coinsurance, copay), a second insurer is on file in
        EMA, and the second payer never answered: no remittance from it as second payer (at any amount, $0 included), no
        posting or reason of its own on EMA's line after the first payer's payment, no payment by another payer in EMA,
        the patient has not paid the share, and EMA still shows a balance. One item per line, sized at the share left (at
        most EMA's balance), dated by the second payer's filing limit as it counts a second claim (codes.second_claim_window). A claim
        the first payer forwarded itself (claim status 19, or a carrier named) is listed only once that second payer's
        usual time has passed, to be chased rather than billed; one whose EMA line the store does not hold is listed from
        the remittance alone (the carrier it names). Medicaid as the second payer is never listed: it pays the share only
        up to its own rate and the member cannot be billed (the answer key's R3). Every line left out is counted (meta
        ema:wiring)."""
        from . import ema_reports as er
        from . import ema_checks as ec
        cands: dict[tuple, Line] = {}
        other_paid: set[tuple] = set()
        second_835: dict[tuple, list] = defaultdict(list)       # (bill, date, code) -> second payers' remittances: (date, payer)
        for l in self.lines:
            code, dos = l.get("procedure_code"), l.get("service_date")
            if not code or not dos or code in ("99024", "G2211"):
                continue
            key = (er.bill_of_claim(l["patient_control_number"]), dos, code)
            st = l.get("claim_status_code")
            if st in ("2", "3", "20", "21", "22"):
                # processed as a second (or third) payer, or reversed: an answer from a second payer, whatever it paid
                second_835[key].append((l.get("payment_date") or l.get("txn_payment_date") or "", l.get("payer_id") or l.get("payer_name")))
                continue
            if st not in ("1", "19") or not (self.standing.get(l["line_key"]) or 0) or l.get("payer_kind") == "supplement":
                continue
            pr = sum(a for g, rc, a in self.adj.get(l["line_key"], []) if g == "PR" and rc in ("1", "2", "3"))
            if pr <= 100:
                continue
            was = cands.get(key)
            if was is None or (l.get("payment_date") or "") > (was.get("payment_date") or ""):
                cands[key] = l
        for key, c in cands.items():
            who = c.get("payer_id") or c.get("payer_name")
            if any(p != who for _d, p in second_835.get(key, [])):
                other_paid.add(key)
                self.ema_counts["snb_second_answered_835"] += 1
        # a second line of the code on the bill and date from another payer: paid by it, or answered by another payer group
        # after the first payer's remittance (a supplement's own $0 answer as if primary, a deductible it does not cover)
        for l in self.lines:
            key = (er.bill_of_claim(l["patient_control_number"]), l.get("service_date"), l.get("procedure_code"))
            c = cands.get(key)
            if c is None or l["line_key"] == c["line_key"] or key in other_paid \
                    or (l.get("payer_id") or l.get("payer_name")) == (c.get("payer_id") or c.get("payer_name")):
                continue
            if (self.standing.get(l["line_key"]) or 0) > 0:
                other_paid.add(key)
            elif l.get("payer_group") != c.get("payer_group") \
                    and (l.get("payment_date") or l.get("txn_payment_date") or "") >= (c.get("payment_date") or c.get("txn_payment_date") or ""):
                other_paid.add(key)
                self.ema_counts["snb_second_answered_835"] += 1
        keys = [k for k in cands if k not in other_paid]
        if not keys:
            return
        ema = self._ema_lines_for(keys)
        groups: dict[str, tuple] = {}
        boxes = (self._ema_step("freshness", ec.freshness, self.conn, self.today) or {}).get("boxes") or []
        for key in keys:
            e = ema.get(key)
            l = cands[key]
            paid_on = l.get("payment_date") or l.get("txn_payment_date") or ""
            crossed = l.get("claim_status_code") == "19" or bool(self.crossover_carrier(l["claim_key"]))
            pr = sum(a for g, rc, a in self.adj.get(l["line_key"], []) if g == "PR" and rc in ("1", "2", "3"))
            if not e:
                # EMA shows no line of this bill for the date and code (the Production reports hold none, or its claim is not
                # mapped to the bill): a claim the first payer crossed over is still a second payer to chase, named by the
                # remittance
                carrier = self.crossover_carrier(l["claim_key"]) if crossed else None
                if not carrier:
                    continue
                if not ec._svc_covered(boxes, key[1]):
                    # EMA's Production report does not reach this date of service: nothing could ever show the carrier's
                    # answer or the patient's payment, so it is counted, not listed
                    self.ema_counts["snb_crossover_not_covered"] += 1
                    continue
                sec, owed, bal, pat, link, wrote_off = carrier, pr, None, None, None, False
            else:
                if not (e["secondary_payer"] or "").strip() or e["other_paid"] > 0:
                    continue
                # the second payer answered in EMA after the first payer's payment: a $0 payment row or a reason of its own
                ans = [a for a in e.get("answers") or [] if (a[0] or "") >= paid_on[:10]]
                if ans:
                    self.ema_counts["snb_second_answered_ema"] += 1
                    continue
                sec = " ".join(str(e["secondary_payer"]).split())
                owed = pr - max(0, e["pat_paid"])
                bal, pat, link = e["balance"], e["pat_paid"], e["link"]
                wrote_off = False
            if SELF_PAY.search(sec):
                continue
            if sec not in groups:
                groups[sec] = er.payer_group_of(self.conn, sec)
            sgrp, skind = groups[sec]
            if not sgrp or ec._same_payer(sgrp, l["payer_group"]) or (e and ec._same_payer(sgrp, e["payer_group"])):
                continue                    # the second payer on file is the first payer itself
            if skind == "medicaid":
                # Medicaid pays the share only up to its own rate (normally nothing after a Medicare or commercial payment)
                # and the member cannot be billed: no money to chase (the answer key's R3)
                self.ema_counts["snb_medicaid_second"] += 1
                self.ema_counts["snb_medicaid_second_cents"] += max(0, owed)
                continue
            if owed <= 100:
                continue                    # the patient paid the share
            if e:
                # EMA's balance closed with nothing from the patient or a second payer: the share was written off, not billed
                wrote_off = bal <= 100
                if not wrote_off:
                    owed = min(owed, bal)
            if crossed and (_days_between(paid_on, self.today) or 0) < 45:
                continue                    # forwarded by the first payer: its usual time has not passed yet
            rule = filing_rule(sgrp, skind, payer_name=sec)
            # the second payer's own clock for a claim it gets second (codes.second_claim_window): from the date of service
            # where its source says so (Medicare, CHAMPVA, TRICARE, GEHA), from the first payer's remittance where it says
            # that (Ambetter, the FEP); a source that is silent gives the safe date from the service and the last day from the
            # remittance. The item is a loss only once its last day has passed (deadlines review r1-2)
            deadline, last, counted = second_claim_window(rule, l.get("service_date"), paid_on[:10] or None,
                                                          first_payer=l["payer_group"])
            if (last or deadline) and (last or deadline) < (_add_days(self.today, -30) or ""):
                # past the second payer's limit (and a month's grace): a loss, not work; counted with its dollars, so a
                # window that closed is never dropped without a trace
                self.ema_counts["snb_past_limit"] += 1
                self.ema_counts["snb_past_limit_cents"] += max(0, owed)
                continue
            share = ", ".join(w for rc, w in (("1", "deductible"), ("2", "coinsurance"), ("3", "copay"))
                              if any(g == "PR" and c == rc for g, c, _a in self.adj.get(l["line_key"], [])))
            title = (f"{code}: crossed over to {sgrp}, nothing paid by it yet ({_usd(owed)} {share})" if crossed else
                     f"{code}: {l['payer_group']} paid and left {_usd(owed)} ({share}); {sgrp} is on file and was never billed"
                     + ("; EMA wrote the share off" if wrote_off else ""))
            ev = {"first_payer": l["payer_group"], "first_paid_cents": self.standing.get(l["line_key"]), "patient_share_cents": pr,
                  "share": share, "second_payer": sgrp, "second_payer_kind": skind, "ema_balance_cents": bal,
                  "ema_patient_paid_cents": pat, "crossover": crossed, "ema_link": link, "ema_wrote_off_share": wrote_off,
                  "ema_line_on_file": bool(e),
                  "basis": ("the patient's share the first payer left, less what the patient paid, at most EMA's balance" if e else
                            "the patient's share the first payer left (EMA's line is not on file yet: what the patient paid is not known)")}
            self._emit("SECONDARY_NOT_BILLED", "denial", "medium" if owed >= 5000 else "low", "review", title, l, owed, owed, None, None, ev)
            f = self.findings.get(_fid("SECONDARY_NOT_BILLED", l["line_key"]))
            if f is None:
                continue
            f["appeal_deadline"], f["legal_deadline"] = deadline, last
            f["deadline_basis"] = (f"{sgrp}'s filing limit, {counted}: {rule['label']}"
                                   + ("" if rule["verified"] else ", not confirmed against the payer's current manual"))
            if link is not None:
                f["ema_link"] = link
            f["source"] = "835"
            f["hint"] = (f"Ask {sgrp} for its payment: the first payer forwarded the claim" if crossed else
                         f"Bill {sgrp} with {l['payer_group']}'s remittance")
            self.ema_counts["SECONDARY_NOT_BILLED"] += 1
            if not e:
                self.ema_counts["snb_from_remittance_only"] += 1

    def run_credit_balances(self) -> None:
        """Credit balances (C8 as items): an EMA claim whose lines hold more than they were owed after every payment and
        write-down, by more than $1 on the claim as a whole (a line's credit that another line of the claim absorbs is a
        posting to the wrong line, not a refund). One item per EMA claim: who overpaid (the payer whose money exceeds what
        was owed, else the patient), how much, the lines. A Medicare credit is due back within 60 days of being found."""
        rows = self.conn.execute("""SELECT link, MIN(svc), SUM(COALESCE(balance,0)), SUM(COALESCE(charges,0)), SUM(COALESCE(ins_paid,0)),
                                           SUM(COALESCE(pat_paid_joined,0)), MAX(payer_group), MAX(payer_kind), MAX(primary_payer),
                                           MAX(claim_id), MAX(bill), MAX(as_of), MIN(COALESCE(balance,0)), MAX(last_posted),
                                           SUM(COALESCE(contractual,0)), SUM(COALESCE(noncontractual,0)), MAX(pat_join = 'several')
                                    FROM ema_lines WHERE (COALESCE(flags,0) & 17) = 0
                                    GROUP BY link HAVING SUM(COALESCE(balance,0)) < -100 AND MIN(COALESCE(balance,0)) < -100""").fetchall()
        # the patient's share the payers' 835s set on these claims' lines (PR 1, 2, 3), where an 835 answered them
        from .ema_reports import bill_of_claim
        want: dict[tuple, int] = {}
        line_keys: dict[int, list] = defaultdict(list)
        for i in range(0, len(rows), 400):
            part = [r[0] for r in rows[i:i + 400]]
            for lk_, b_, s_, c_ in self.conn.execute(f"SELECT link, bill, svc, cpt FROM ema_lines WHERE link IN ({','.join('?' * len(part))})",
                                                     part):
                if b_ and s_ and c_:
                    line_keys[lk_].append((b_, s_, c_))
                    want[(b_, s_, c_)] = -1
        if want:
            for l in self.lines:
                if l.get("claim_status_code") not in ("1", "19"):
                    continue
                k = (bill_of_claim(l["patient_control_number"]), l.get("service_date"), l.get("procedure_code"))
                if k in want:
                    pr = sum(a for g, rc, a in self.adj.get(l["line_key"], []) if g == "PR" and rc in ("1", "2", "3"))
                    want[k] = max(want[k], pr)
        for lk, svc, bal, chg, ins, pat, grp, kind, payer, cid, bill, as_of, _mn, last, contr, nonc, shared in rows:
            credit = -bal
            shares = [want.get(k, -1) for k in line_keys.get(lk, [])]
            share = sum(shares) if shares and all(x >= 0 for x in shares) else 0       # known only where an 835 set every line's
            lines = [list(r) for r in self.conn.execute("""SELECT cpt, svc, COALESCE(balance,0), COALESCE(ins_paid,0) FROM ema_lines
                                                            WHERE link=? AND COALESCE(balance,0) < 0 ORDER BY svc, cpt""", (lk,))]
            pays = self.conn.execute("""SELECT payer_group, payer, SUM(COALESCE(ins_paid,0)), MAX(posted), COUNT(DISTINCT check_no)
                                        FROM ema_line_payments WHERE link=? AND COALESCE(ins_paid,0) > 0 GROUP BY payer_group, payer
                                        ORDER BY 3 DESC""", (lk,)).fetchall()
            # who paid more than their part: insurance owed at most what was left after the write-downs (the charge less the
            # contractual and other adjustments; less still where the patient owed a share), so insurance money above that
            # is its overpayment and the rest of the credit is the patient's. The patient's share is not on EMA's lines, so
            # the insurer's part is a lower bound: the payer is named only for it, and Medicare's 60 days only for it.
            ins_over = min(credit, max(0, ins - max(0, chg - contr - nonc - share)))
            pat_over = credit - ins_over
            insurance = ins_over > 100
            payer_name = (pays[0][0] or pays[0][1]) if pays else (grp or "insurance")
            if insurance and pat_over <= 100:
                who = payer_name
            elif insurance:
                who = f"{payer_name} ({_usd(ins_over)}) and the patient ({_usd(pat_over)})"
            elif shared or not pat:
                who = "check who overpaid"          # the patient's money is shared by several claims, or nothing shows who
            else:
                who = "the patient"
            age = _days_between(last, as_of) if last and as_of else None
            medicare = kind == "medicare" and insurance
            title = ((f"Credit of {_usd(credit)}: check who overpaid this claim" if who == "check who overpaid" else
                      f"Credit of {_usd(credit)}: {who} paid more than this claim was owed")
                     + (f" ({age} days since the last posting)" if age is not None else ""))
            claim = {"patient_control_number": cid or bill, "payer_name": payer_name if insurance else who,
                     "payer_group": grp if insurance else "Patient", "payer_kind": kind if insurance else "other", "service_date": svc,
                     "year": (svc or "")[:4] or None, "procedure_code": max(lines, key=lambda x: -x[2])[0] if lines else None,
                     "charge_cents": chg or None}
            ev = {"credit_cents": credit, "lines": lines, "overpaid_by": who, "insurance_paid_cents": ins, "patient_paid_cents": pat,
                  "insurance_over_cents": ins_over, "patient_over_cents": pat_over,
                  "owed_after_write_downs_cents": max(0, chg - contr - nonc), "patient_share_835_cents": share or None,
                  "payers": [[p[0] or p[1], p[2], p[3], p[4]] for p in pays[:5]], "ema_as_of": as_of, "ema_link": lk,
                  "days_since_last_posting": age,
                  "next_step": ("A refund or a recoupment is the owner's decision (with the compliance lead): check the payments, "
                                "then refund the payer, or let it take the money back from a later payment.")}
            self._emit("CREDIT_BALANCE", "refund", "high" if (medicare and (age or 0) > 60) or credit >= 50000 else "medium", "review",
                       title, None, credit, credit, None, None, ev, key_parts=(lk,), claim=claim)
            f = self.findings.get(_fid("CREDIT_BALANCE", lk))
            if f is None:
                continue
            f["ema_link"], f["source"] = lk, "ema"
            if medicare:
                f["appeal_deadline"], f["legal_deadline"] = _add_days(as_of or self.today, 60), None
                f["deadline_basis"] = (f"Medicare expects its overpayment ({_usd(ins_over)}) back within 60 days of finding it "
                                       "(42 CFR 401.305); counted from the report that shows it")
            self.ema_counts["CREDIT_BALANCE"] += 1

    # ------------------------------------------------------------------ hooks for the archive-only rules (unit B2)
    # remitwatch.archive_rules acts on the run's items at these four points (needs_B2 section 1; hook_hide stays a no-op).
    def hook_expected(self) -> None:
        """After the line and claim rules set expected amounts: a fair expected amount where an item has none (drugs and
        skin substitutes: archive_rules.drug_expected, rule 10)."""
        from .archive_rules import hook_expected
        self.archive_expected = hook_expected(self)        # {'expected_cents': n}

    def hook_archive_rules(self) -> None:
        """Between the second rates and resolve_elsewhere, before every EMA closure, so a closure the 835 archive proves
        comes first: closures with proof, reclassifications and the items rule 8 raises (archive_rules.evaluate, rules 1
        to 8). The folds wait for hook_fold."""
        from .archive_rules import hook_archive_rules
        self.archive_summary = hook_archive_rules(self)    # per-rule counts, applied_counts, rate_errors; sets self.archive_folds

    def hook_fold(self) -> None:
        """Before save: repeat listings of one service folded into the item that carries it (archive_rules fold_into). A
        person's work is read again first: an item a person worked is never folded."""
        from .archive_rules import hook_fold
        self.archive_folded = hook_fold(self)              # {'fold_into': n}

    def hook_hide(self) -> None:
        """After save, where the hide rules are matched: hide rules at payer x code x modifier x reason grain."""
        return None

    # ------------------------------------------------------------------ after the save: what the pages read
    def run_ema_summaries(self) -> None:
        """Step 13, on the saved items: the A/R accounted for (W4, each row's bucket kept for its list), the unworked
        number (W3), the reconcile summary the Data tab and the dashboard's copy read, and the write-off pattern table
        for the owner's policy (O11)."""
        if not self.ema_on:
            return
        from . import ema_checks as ec
        self._ema_step("ar_accounted", ec.ar_accounted, self.conn, None, today=self.today)
        fr = self._ema_step("freshness", ec.freshness, self.conn, self.today) or {}
        through = fr.get("production_as_of") if fr.get("production_fresh") else fr.get("newest_835")
        if through:
            cols = ("finding_id", "category", "status", "stale", "appeal_deadline", "ema_worked_on", "shortfall_cents",
                    "written_off_cents", "written_off_when")
            rows = [dict(zip(cols, r)) for r in self.conn.execute(f"SELECT {', '.join(cols)} FROM findings WHERE COALESCE(stale,0)=0")]
            self._ema_step("unworked", ec.unworked, self.conn, rows, data_through=through)
        self._ema_step("reconcile_summary", ec.reconcile_summary, self.conn, today=self.today)
        items = [f for f in self.findings.values() if f["finding_id"] in self.ema_outcomes]
        pol = self._ema_step("policy_summary", ec.policy_summary, self.conn, items, self.ema_outcomes, today=self.today)
        if pol is not None:
            # the payers' policies EMA-only denials were folded into (a code the payer pays on 5% or less of its answered
            # lines): one row each in the owner's table, 'pursue' until the owner chooses 'accept' (the owner's decision 16)
            try:
                lo = json.loads((self.conn.execute("SELECT value FROM meta WHERE key=?", (ec.C5_LEFT_OUT_KEY,)).fetchone() or ["{}"])[0])
            except (ValueError, TypeError, sqlite3.Error):
                lo = {}
            pats = dict(((lo.get("payer_policy") or {}).get("patterns") or {}))
            for key, p in (self.__dict__.get("_policy_835") or {}).items():
                if key in pats:             # the same payer and code from EMA-only denials and from the remittances
                    pats[key] = dict(pats[key], n=pats[key]["n"] + p["n"], cents=pats[key]["cents"] + p["cents"])
                else:
                    pats[key] = p
            extra = [{"pattern": key, "kind": "payer_policy", "items": p.get("n"), "cents": p.get("cents"), "appealable": None,
                      "appealable_cents": None, "at_posting": None, "auto": None, "rule_proven": None,
                      "later_paid_share": round(p["paid"] / p["answered"], 3) if p.get("answered") else None,
                      "paid": p.get("paid"), "answered": p.get("answered"), "decision": p.get("decision"), "default": p.get("default")}
                     for key, p in pats.items()]
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                              (PAYER_POLICY_KEY, json.dumps({"at": self.now, "patterns": pats}, default=str)))
            pol = list(pol) + sorted(extra, key=lambda x: (-(x["cents"] or 0), x["pattern"]))
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                              (POLICY_SUMMARY_KEY, json.dumps({"at": self.now, "patterns": pol[:500]}, default=str)))
            self.conn.commit()

    def _set_states(self, rows: list[dict]) -> None:
        """Each item's state (remitwatch.states, the one place it is decided: work now, waiting with its follow-up day,
        done, accepted, expired, can't be checked), with the fact that decided it and why a bulk appeal must leave it out.
        Worked out on the status the save just settled; a store without the states module leaves the columns empty."""
        try:
            from . import states
        except ImportError:
            return
        items = [r for r in rows if r.get("finding_id")]
        if not items:
            return
        # each payer group's usual answer time, measured once per run (meta states:response_days; the copy reads it)
        self._ema_step("response_days", states.save_response_days, self.conn)
        outcomes = dict(self.ema_outcomes) if self.ema_on else {}
        reopened = getattr(self, "_overridden", set()) or set()
        for r in items:
            o = outcomes.get(r["finding_id"])
            if o and r["finding_id"] in reopened and r.get("status") != "resolved" and o.get("outcome") in PAYMENT_OUTCOMES:
                # a person moved it off the closure EMA's payment made ("that payment was for another visit"): the state is
                # judged on the rest of the line's facts, and the payment they rejected is passed on as a conflict to show
                outcomes[r["finding_id"]] = dict(o, outcome="open", outcome_on=None, person_reopened=True,
                                                 rejected_payment=o.get("first_paid_after"), paid_after_same=0, paid_after_other=0)
                self.ema_counts["state_person_reopened"] += 1
        got = self._ema_step("states", states.states_of, self.conn, items, outcomes=outcomes, today=self.today) or {}
        for r in items:
            st = got.get(r["finding_id"])
            if not st:
                continue
            r["work_state"], r["follow_up_on"] = st.get("work_state"), st.get("follow_up_on")
            r["state_basis"] = (st.get("state_basis") or "")[:300] or None
            r["appeal_block"] = st.get("appeal_block") or self._never_payable_block(r, st)
            r["state_rule"] = st.get("rule")
            getattr(self, "state_rules", {})[r["finding_id"]] = st.get("rule")      # for the decision log
            self._payer_work_size(r, st, states)
            self.ema_counts["state_" + str(st.get("work_state"))] += 1

    def _payer_work_size(self, r: dict, st: dict, states) -> None:
        """A patient-group denial whose work is with the payer (a corrected claim or a resubmission sent, its follow-up: any
        state but the patient's side, states.PATIENT_SIDE_RULES) is stored at what the payer pays for the service (its rate,
        kept in the evidence by archive rule 8), not at the patient's share, so Work now's dollars and the ranking count what
        that work can bring (review r1-4). The share stays in the evidence for the bill-the-patient step."""
        if st.get("work_state") not in ("work_now", "waiting") or st.get("rule") in states.PATIENT_SIDE_RULES:
            return
        if not states.patient_denial(r):
            return
        rate = int(states._ev(r).get("payer_rate_cents") or 0)
        if rate > 0 and r.get("shortfall_cents") != rate:
            r["shortfall_cents"] = r["expected_cents"] = rate
            self.ema_counts["patient_denial_payer_work"] += 1

    def _never_payable_block(self, r: dict, st: dict) -> str | None:
        """'never_payable' for an item to work now that a payer's published rule never pays (the owner's decision 4, the
        rule that leads its next step: story._with_payer_rule): no appeal goes for it in bulk, and the bulk list's Left
        out line says why. Its state is not changed. A failure is logged and blocks nothing."""
        if st.get("work_state") != "work_now":
            return None
        try:
            from . import rarely_paid as rp
            if st.get("route") not in rp.LEAD_ROUTES:
                return None
            if rp.never_payable_lead(self.conn, r):
                self.ema_counts["never_payable_block"] += 1
                return "never_payable"
        except Exception as exc:        # noqa: BLE001  (logged; the item keeps its state, and its panel still says the rule)
            if os.environ.get("REMITWATCH_EMA_STRICT") == "1":
                raise
            from .runlog import caught
            caught(exc, "rarely_paid.block")
        return None

    def _save_ema_wiring(self) -> None:
        """What the run did with EMA's reports, for the Data tab and the maintainers: counts by step, and any step that failed;
        and the unanswered claims no source can show the answer to yet (meta ema:unanswered_unseen, with their dollars)."""
        try:
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                              (EMA_WIRING_KEY, json.dumps({"at": self.now, "on": self.ema_on, "counts": dict(self.ema_counts),
                                                           "errors": self.ema_errors[:20]}, sort_keys=True)))
            unseen = dict(self.__dict__.get("_unseen") or {"n": 0, "billed_cents": 0})
            # the day the never-answered passes judged these claims against: the feed date (answers-2), not one stray file
            newest = feed_date(self.conn) or self.conn.execute(
                "SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()[0]
            unseen.update(at=self.now, newest_835=newest,
                          why=("Due only by EMA's postings, and EMA's own claim for each cannot be told apart from others with the "
                               "same date, code and charge: no 835 since the newest remittance shows its answer. Check the payer "
                               "portal for these claims."))
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (UNSEEN_KEY, json.dumps(unseen, sort_keys=True)))
            self.conn.commit()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ aggregate rules
    def _taken_back_items(self) -> set[tuple]:
        """A service whose payment the payer's recoupment (PLB WO, FB or J1 naming the claim, as the ledger applied it) took
        back in full, and that no other payment still pays (a primary payment, or a secondary one covering half the expected
        amount, for every unit the payment taken back paid), is listed whatever the amount (the owner's rule: no dollar floor): paid,
        then taken back by the payer on that day; find out why, then dispute or bill the right payer, inside the payer's
        window to dispute counted from the remittance that took the money back. One item per paying line (signal TAKEBACK
        on the line). Not listed:
        - a service another payment still pays: most recoupments recover a double payment (two claims paid one service);
        - a service answered again since the payment: by the same payer (its new answer, a denial or the patient's share, is
          the item to work), or as primary by another payer (the right payer was billed). The payer restating the payment it
          took back (a duplicate denial, B13 or CO-18, on or before the takeback) is no such answer, and neither is another
          payer's answer processed after this one's (CARC 22 or 23, whatever its claim status: a supplement's share)
          (fx-ledger-r1-1, r1-3);
        - a service of the same patient billed again on another claim since the payment (a claim voided and billed again:
          a global billed again as its two parts): the new claim's answer is the work;
        - a claim whose recoupment took a payment only in part (a payer's repricing): the recoupment's own listing stands
          for the claim;
        - a service the practice voided after the payment (_voided_services: a void 837 with no later send, or EMA's charge
          voided): the payer took the money back because the practice withdrew the charge.
        A second payer's share taken back (claim status 2 or 3) is judged against that payer's own standing payments of the
        service, never the primary's: the primary's payment does not pay the coinsurance the second payer took back, so the
        share is listed (bill the patient, or dispute the takeback) unless that payer pays it again or another second payer
        answered the service since (money-2).
        Where the payer's own denial of the service, which the payment had answered, is listed, the service is one item, that
        denial, with the takeback named on it and worked until the payer's window to dispute the takeback closes when that
        is later than the denial's own (_carry_takeback_window; ledger_taken.listed 2 for the payment's other items).
        A primary's payment is covered only by another primary payment or a secondary one of half the expected amount; a
        supplement's share (status 1 with CARC 22 or 23) is a secondary's (fx-ledger-r1-3).
        A service listed before and paid since (after the takeback) is closed by that payment, which is its recovery.
        Returns {(remittance, reference, cents) of a WO entry naming a claim on file: the cents its own listing carries}
        (_wo_listing_sizes: what the ledger could not apply of the claim's netted recoupments; all it set out to take for a
        claim taken from only in part; nothing where they net to nothing: money-6, fx-ledger-r2-1)."""
        took = getattr(self, "plb_took", None) or []
        self.wo_partial_keys: set[tuple] = set()
        if not took:
            return self._wo_listing_sizes(set())
        when = lambda l: l.get("payment_date") or l.get("txn_payment_date") or ""      # noqa: E731
        payer = lambda l: l.get("payer_id") or l.get("payer_name")                     # noqa: E731
        by_line: dict[str, list] = defaultdict(list)
        partial: set[str] = set()           # the claims a recoupment took only part of a payment of
        for p, day, cents, entry in took:
            by_line[p["line_key"]].append((p, day, cents, entry))
            if self.standing.get(p["line_key"], 0) > 0:
                partial.add(entry.get("claim"))
        prev = {r[0] for r in self.conn.execute("SELECT finding_id FROM findings WHERE signal='TAKEBACK' AND line_key IS NOT NULL")}
        bills = {bill_of(p["patient_control_number"]) for p, *_x in took}
        svcs = {(p.get("procedure_code"), p.get("service_date")) for p, *_x in took}
        answers: dict[tuple, list] = defaultdict(list)
        elsewhere: list = []            # answers of the same codes and dates on any bill (the same patient's, sorted out below)
        for l in self.lines:
            if l.get("claim_status_code") == "22":
                continue
            if bill_of(l["patient_control_number"]) in bills:
                answers[(bill_of(l["patient_control_number"]), l.get("procedure_code"), l.get("service_date"))].append(l)
            elif (l.get("procedure_code"), l.get("service_date")) in svcs:
                elsewhere.append(l)
        # the patient of each claim (member number, else name): a service the practice voided and billed again on another
        # claim (a global billed again as its two parts) is answered there
        who = self._patient_of_claims({x["claim_key"] for x in elsewhere} | {p["claim_key"] for p, *_x in took})
        same_svc: dict[tuple, list] = defaultdict(list)
        for x in elsewhere:
            if who.get(x["claim_key"]):
                same_svc[(who[x["claim_key"]], x.get("procedure_code"), x.get("service_date"))].append(x)
        taken_lines = set(by_line)
        line_by_key = {x["line_key"]: x for xs in answers.values() for x in xs}
        voided = self._voided_services({(bill_of(p["patient_control_number"]), p.get("procedure_code"), p.get("service_date"), when(p),
                                         p["patient_control_number"], p.get("claim_key"), p.get("payer_group") or "") for p, *_x in took})
        # the denials of these services still open (the line rules' items), that the payer has not reversed since: (bill,
        # code, date) -> items
        undone: dict[tuple, str] = {}
        for l in self.lines:
            if l.get("claim_status_code") == "22" and bill_of(l["patient_control_number"]) in bills:
                k = (l["patient_control_number"], l.get("procedure_code"), l.get("service_date"))
                undone[k] = max(undone.get(k, ""), when(l))
        listed: dict[tuple, list] = defaultdict(list)
        for f in self.findings.values():
            if f.get("line_key") and f.get("category") in ("denial", "bundling") and not f.get("auto_status") \
                    and bill_of(f.get("patient_control_number")) in bills \
                    and undone.get((f["patient_control_number"], f.get("procedure_code"), f.get("service_date")), "") <= (f.get("payment_date") or ""):
                listed[(bill_of(f.get("patient_control_number")), f.get("procedure_code"), f.get("service_date"))].append(f)
        for lk, takes in sorted(by_line.items()):
            p = takes[0][0]
            if lk in self.standing or not p.get("service_date") or p.get("procedure_code") in ("99024", "G2211"):
                continue            # (a post-op visit or G2211 is nothing to pursue, as on a denial)
            day = max(t[1] for t in takes)
            cents = sum(t[2] for t in takes)
            entry = max(takes, key=lambda t: t[1])[3]
            bill, code, dos = bill_of(p["patient_control_number"]), p.get("procedure_code"), p["service_date"]
            exp, _basis = self.expected_allowed(p, code)
            second = p.get("claim_status_code") in ("2", "3")
            line_of = {x["line_key"]: x for x in answers.get((bill, code, dos), [])}
            if second:
                # a second payer's share: the second payers' standing payments of the service pay it again, that payer's own
                # or another supplement's that paid the same coinsurance, before or after (the recoupment recovers a double
                # payment: fx-ledger-r2-5); never the primary's payment (money-2). A status-1 answer processed after another
                # payer's is a second payer's too (fx-ledger-r1-3)
                cover = [e for e in self.paid_events.get((bill, code, dos), []) if e[8] != lk
                         and (e[3] in ("2", "3") or (e[3] in ("1", "19") and e[8] in line_of and self._after_other_payer(line_of[e[8]])))]
            else:
                # a primary's payment taken back: a payment processed after another payer's (CARC 22 or 23, a supplement
                # answering with claim status 1) is a second payer's share, which covers it only as paid_cover counts a
                # secondary, at half the expected amount (fx-ledger-r1-3)
                cover = [e for e in self.paid_cover(bill, code, dos, exp)
                         if not (e[3] in ("1", "19") and e[8] in line_of and self._after_other_payer(line_of[e[8]]))
                         or (exp and (e[2] or 0) >= 0.5 * exp)]
            # the units the claim's payment of the service paid (its paid lines of the code and date: a payer's restatement of
            # an earlier payment lists more lines of the code, paid nothing, which are no units of their own), still paid by
            # what stands
            need = sum(abs(x.get("paid_units") or 1) for x in self.lines_by_claim.get(p["claim_key"], [])
                       if x.get("procedure_code") == code and x.get("service_date") == dos and (x.get("paid_cents") or 0) > 0) or 1
            if self.units_paid(cover) >= need:
                since = [e for e in cover if e[0] > day]
                if _fid("TAKEBACK", lk) in prev and since and self.units_paid(since) >= need:
                    # listed before, and the service paid since the takeback: closed by that payment, its recovery
                    paid = LaterPayment(min(since))
                    paid.wants = [((bill, code, dos), abs(p.get("original_units") or p.get("paid_units") or 1), since)]
                    self._emit_taken_back(p, day, cents, entry, resolved_by=paid)
                continue
            if entry.get("claim") in partial:
                continue            # the claim's recoupment took another payment only in part: its own listing stands for it
            if (bill, code, dos, p["patient_control_number"]) in voided:
                continue            # the practice voided the charge since: the takeback answers the void
            paid_on = when(p)
            ours = lambda x: payer(x) == payer(p) or x.get("payer_group") == p.get("payer_group")     # noqa: E731
            # the payer restating this payment (a duplicate denial, B13 or CO-18) on or before the day it took the payment back
            # is not the takeback's work: the takeback is, inside its own window (fx-ledger-r1-1)
            again = [x for x in answers.get((bill, code, dos), []) if x["line_key"] not in taken_lines
                     and (when(x) > paid_on or (when(x) == paid_on and x["patient_control_number"] != p["patient_control_number"]))
                     and (ours(x) or (x.get("claim_status_code") in ("1", "19", "4") and not self._after_other_payer(x))
                          or (second and x.get("claim_status_code") in ("2", "3")))
                     and not (ours(x) and when(x) <= day and self._restates(x))]
            me = who.get(p["claim_key"])
            rebilled = [x for x in same_svc.get((me, code, dos), []) if me and bill_of(x["patient_control_number"]) != bill and when(x) > paid_on]
            if again or rebilled:
                continue            # answered again since the payment, or billed again on another claim: that answer is the work
            mine = [f for f in listed.get((bill, code, dos), []) if (f.get("payer_id") == payer(p) or f.get("payer_group") == p.get("payer_group"))
                    and not ((f.get("payment_date") or "") <= day and (str(f.get("carc") or "") in DUP_CARCS
                                                                       or (f["line_key"] in line_by_key and self._restates(line_by_key[f["line_key"]]))))]
            if mine:
                # the payer's own denial of the service, which that payment had answered, is listed: the service is one item,
                # that denial, the takeback named on it (states.paid_proof no longer reads the payment taken back as paying it),
                # worked until the payer's window to dispute the takeback closes (fx-ledger-r1-1). The payment's other items
                # (a restatement of it, another payer's denial) name the takeback and where it is worked (ledger_taken.listed 2)
                for f in mine:
                    ev = json.loads(f["evidence"]) if f.get("evidence") else {}
                    ev["taken_back"] = self._taken_back_detail(p, day, cents, entry)
                    ev["taken_back_lines"] = sorted(set(ev.get("taken_back_lines") or []) | {lk})
                    f["evidence"] = json.dumps(ev, default=str)
                    f["hint"] = (f"Paid on {when(p)} ({_usd(p.get('paid_cents'))}), then taken back by {p.get('payer_group') or 'the payer'} "
                                 f"on {day}: this denial stands")
                    self._carry_takeback_window(f, p, day)
                self.taken_on_denial.add(lk)
                continue
            self._emit_taken_back(p, day, cents, entry)
        self.wo_partial_keys = {key for key, (name, _by) in (getattr(self, "plb_wo_by", None) or {}).items() if name in partial}
        return self._wo_listing_sizes(partial)

    def _wo_listing_sizes(self, partial: set) -> dict:
        """{(remittance, reference, cents) of a WO entry naming a claim on file: what its own listing carries}: of the claim's
        recoupments (its WO, FB, J1, CS and 72 entries of the payer, netted), what the ledger could not apply, the largest WO
        entry first (money-6: every dollar withheld is on an item); for a claim a recoupment took a payment of only in part,
        all the ledger set out to take (the recoupment's own listing stands for the claim). Nothing where the claim's entries
        net to nothing, a balance its reversal had carried absorbs them, or the same remittance's reversal took the claim
        back (fx-ledger-r2-1: a balance carried and recovered later, or a recoupment its own adjustment cancels, withholds
        nothing of its own; the service's items carry the reversal)."""
        wo_by = getattr(self, "plb_wo_by", None) or {}
        claimed = getattr(self, "plb_claimed", None) or {}
        unapplied = getattr(self, "plb_unapplied", None) or {}
        entries = getattr(self, "plb_entries", None) or {}
        out = {key: 0 for key in wo_by}
        for nb in sorted(set(wo_by.values()), key=lambda nb: (nb[0], str(nb[1]))):
            cents = claimed.get(nb, 0) if nb[0] in partial else unapplied.get(nb, 0)
            wos = sorted((e for e in entries.get(nb) or [] if e[3] == "WO"), key=lambda e: (e[1], e[4], e[0][1] or ""), reverse=True)
            for key, amount, _who, _code, _day in wos:
                share = min(amount, cents)
                if share <= 0:
                    break
                out[key] = out.get(key, 0) + share
                cents -= share
        return out

    def _restates(self, l: dict) -> bool:
        """A duplicate denial (CARC 18 or B13 by the payer) that pays nothing: the payer restating a payment it made, never an
        answer of the service of its own (fx-ledger-r1-1). The reason sits on the line, on its claim, or on another $0 line
        of the same code and date on its claim: a payer splits its restatement of a paid line into pieces, the payment
        marked B13 and the original adjustments (CO-45, CO-253, PR-3) on lines of their own, each a piece of the same
        restatement."""
        if (l.get("paid_cents") or 0) > 0:
            return False
        dup = lambda key: any(rc in DUP_CARCS and g != "PR" for g, rc, _a in self.adj.get(key, []))      # noqa: E731
        if dup(l["line_key"]) or any(rc in DUP_CARCS and g != "PR" for g, rc, _a in self.claim_adj.get(l.get("claim_key"), [])):
            return True
        return any(dup(x["line_key"]) for x in self.lines_by_claim.get(l.get("claim_key"), [])
                   if x["line_key"] != l["line_key"] and x.get("procedure_code") == l.get("procedure_code")
                   and x.get("service_date") == l.get("service_date") and (x.get("paid_cents") or 0) <= 0)

    def _carry_takeback_window(self, f: dict, p: dict, day: str) -> None:
        """An item that carries a takeback (the payer's own denial of the service, which the payment taken back had answered)
        is worked until the payer's window to dispute that takeback closes, when that is later than its own: the window a
        TAKEBACK item of the payment would have, counted from the remittance that took the money back (_emit's TAKEBACK rule),
        never lost to the denial's own window while the takeback can still be disputed (fx-ledger-r1-1)."""
        if not day:
            return
        base = dict(p, payment_date=day, txn_payment_date=day)
        plan = base.get("plan") or (base.get("contract_class") or "").strip() or None
        rule = appeal_rule(base.get("payer_group"), base.get("payer_kind", "other"), **rule_facts(base, carc=None, plan=plan))
        end, legal = window_end(day, rule), legal_deadline(day, rule)
        if not end or end <= str(f.get("appeal_deadline") or "")[:10]:
            return
        used = rule_at(day, rule) or rule
        f["appeal_deadline"] = end
        f["legal_deadline"] = legal if legal and legal > end else None
        f["deadline_basis"] = (used["label"] + ("" if used.get("verified") else " (not confirmed against the payer's current manual)")
                               + f". Counted from the remittance that took the payment back ({day}): the payer's window to dispute the "
                               "takeback outlasts this denial's own")

    def _after_other_payer(self, l: dict) -> bool:
        """This answer was processed after another payer's: CARC 22 (covered by another payer) or 23 (the prior payer's
        adjudication) on its line or its claim, by the payer, whatever claim status it reports. A supplement answering with
        claim status 1 pays its share as second payer, never the primary's payment (fx-ledger-r1-3; states._after_other_payer
        reads the same for paid_proof)."""
        return any(rc in AFTER_OTHER_CARCS and g != "PR" for g, rc, _a in self.adj.get(l["line_key"], [])) \
            or any(rc in AFTER_OTHER_CARCS and g != "PR" for g, rc, _a in self.claim_adj.get(l.get("claim_key"), []))

    def _voided_services(self, svcs: set[tuple]) -> set[tuple]:
        """The taken-back payments (bill, code, date, the payment's day, its claim id, its claim, its payer group) whose claim
        the practice voided after the payment, as (bill, code, date, claim id): the newest 837 send since the payment of that
        claim (its claim id, a send whose original reference is its payer claim number, or a send to the same payer) is a void
        (frequency 8) with no other send of it that day or later (a void and a new claim the same day is a replacement); or
        EMA's lines of the service show its charge voided (every line's status a void and the charges netting to nothing: a
        posted void of the charge, not a status alone). A void of another payer's claim of the bill (the wrong second payer
        billed, then voided) withdraws nothing this payer paid (fx-ledger-r2-2). The 835s' own void answers (CLP09 8) make no
        item already (T-N5)."""
        if not svcs:
            return set()
        c = self.conn
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('claims_837', 'ema_lines')")}
        out: set[tuple] = set()
        bills = sorted({s_[0] for s_ in svcs if s_[0]})
        if "claims_837" in tables:
            pccn: dict[str, str] = {}
            cks = sorted({s_[5] for s_ in svcs if s_[5]})
            for i in range(0, len(cks), 500):
                part = cks[i:i + 500]
                pccn.update((ck, n) for ck, n in c.execute(
                    f"SELECT claim_key, payer_claim_control_number FROM claims WHERE claim_key IN ({','.join('?' * len(part))})", part) if n)
            sends: dict[str, list] = defaultdict(list)
            for b in bills:
                for pcn, bht, freq, ref, pname in c.execute(
                        """SELECT patient_control_number, bht_created, TRIM(COALESCE(frequency_code, '')), TRIM(COALESCE(original_reference_number, '')),
                                  payer_name FROM claims_837 WHERE patient_control_number >= ? AND patient_control_number < ?""", (b, b + "~")):
                    if bill_of(pcn) == b and bht:
                        grp = normalize_payer(pname)[0] if pname else ""
                        sends[b].append((real_day(bht) or "", freq, pcn, ref, "" if grp == "Unknown" else grp))
            for b, code, dos, paid_on, pcn, ck, grp in svcs:
                n = pccn.get(ck)
                mine = [(d, f) for d, f, x, ref, g in sends.get(b, []) if x == pcn or (n and ref == n) or (grp and g == grp)]
                voids = [d for d, f in mine if f == "8" and d and d >= (paid_on or "")]
                if voids and not any(f != "8" and d >= max(voids) for d, f in mine):
                    out.add((b, code, dos, pcn))
        if "ema_lines" in tables:
            from .ema_reports import status_class
            for b, code, dos, _paid_on, pcn, _ck, _grp in svcs:
                if (b, code, dos, pcn) in out:
                    continue
                rows = c.execute("SELECT status, charges FROM ema_lines WHERE bill = ? AND svc = ? AND cpt = ?", (b, dos, code)).fetchall()
                if rows and all(status_class(st) == "void" for st, _ch in rows) and sum(ch or 0 for _st, ch in rows) <= 0:
                    out.add((b, code, dos, pcn))
        return out

    @staticmethod
    def _taken_back_detail(p, day: str, cents: int, entry: dict) -> dict:
        """evidence['taken_back']: the recoupment (its day, the cents it took from the line, the payer, its PLB reasons) and the
        payment it took back."""
        paid = {"date": p.get("payment_date") or p.get("txn_payment_date"), "cents": p.get("paid_cents"), "claim": p["patient_control_number"],
                "trace": p.get("trace_number")}
        return {"date": day, "cents": cents, "payer": entry.get("payer") or p.get("payer_name"), "reasons": entry.get("reasons") or [],
                "paid": paid}

    def _emit_taken_back(self, p, day: str, cents: int, entry: dict, resolved_by: tuple | None = None) -> None:
        """The TAKEBACK item of one paying line a recoupment took back in full (_taken_back_items). A second payer's share
        taken back (claim status 2 or 3) says so: the coinsurance it had paid is open again (money-2)."""
        grp = p.get("payer_group") or p.get("payer_name") or "the payer"
        code = p.get("procedure_code")
        base = dict(p, payment_date=day, txn_payment_date=day)
        ev = {"taken_back": self._taken_back_detail(p, day, cents, entry),
              "reference": entry.get("reference"), "reason_code": "/".join(entry.get("reasons") or []) or None}
        if p.get("claim_status_code") in ("2", "3"):
            ev["second_payer"] = True
            title = (f"{code}: {grp} paid its share as second payer, then took it back on {day}: bill the patient, or dispute the "
                     "takeback")
        else:
            title = f"{code} paid, then taken back by {grp} on {day}: find out why, then dispute or bill the right payer"
        self._emit("TAKEBACK", "fees", "high" if cents >= 500000 else "medium", "review", title,
                   None, cents, cents, None, None, ev, key_parts=(p["line_key"],), claim=base, resolved_by=resolved_by)
        self.taken_listed.add(p["line_key"])

    def run_aggregate_rules(self) -> None:
        c = self.conn
        c.row_factory = sqlite3.Row
        judged = self._taken_back_items()
        # the payments each claim's recoupments took (ledger_taken's listed mark, when a recoupment is listed on its own)
        took_of: dict[str, set] = defaultdict(set)
        for p, _day, _cents, entry in getattr(self, "plb_took", None) or []:
            if entry and entry.get("claim"):
                took_of[entry["claim"]].add(p["line_key"])
        rows = c.execute("""SELECT p.payer_name, substr(p.payment_date,1,7) AS month, p.reason_code, p.reference, p.amount_cents, p.transaction_key, p.payment_date
                            FROM plb p JOIN transactions t ON t.transaction_key=p.transaction_key WHERE t.superseded=0""").fetchall()
        fees: dict[tuple, dict] = {}
        for r in rows:
            amt = r["amount_cents"] or 0
            ref = (r["reference"] or "").upper()
            is_fee = (r["reason_code"] == "AH") or (r["reason_code"] in ("CS", "72", "J1") and "FEE" in ref)
            if is_fee and amt > 0:
                grp, kind = normalize_payer(r["payer_name"])
                key = (r["payer_name"], r["month"])
                f = fees.setdefault(key, {"payer_name": r["payer_name"], "payer_group": grp, "payer_kind": kind, "month": r["month"], "n": 0, "cents": 0, "refs": Counter(), "year": (r["month"] or "")[:4], "payment_date": r["payment_date"]})
                f["n"] += 1
                f["cents"] += amt
                f["refs"][ref[:40] or r["reason_code"]] += 1
                if r["payment_date"] and (not f["payment_date"] or r["payment_date"] < f["payment_date"]):
                    f["payment_date"] = r["payment_date"]       # the month's first, whatever order the rows come in
            size = judged.get((r["transaction_key"], r["reference"], amt), amt)
            if r["reason_code"] == "WO" and amt >= 50000 and size > 0:
                # a recoupment of $500 or more, unless it names a claim whose every payment it took back was judged service by
                # service (_taken_back_items: listed when left unpaid, else paid by another payment) and the ledger applied all
                # of it; what it could not apply stays listed here (money-6); one that withheld nothing of its own (the claim's
                # entries net to nothing, or its reversal took the claim back) is not listed (fx-ledger-r2-1)
                grp, kind = normalize_payer(r["payer_name"])
                base = {"payer_name": r["payer_name"], "payer_group": grp, "payer_kind": kind, "transaction_key": r["transaction_key"], "payment_date": r["payment_date"], "year": (r["payment_date"] or "")[:4]}
                ev = {"reference": r["reference"], "reason_code": "WO"}
                title = f"Overpayment recovery of ${amt/100:,.2f} withheld by {r['payer_name']}"
                key = (r["transaction_key"], r["reference"], amt)
                part = key in (getattr(self, "wo_partial_keys", None) or ())
                if part:
                    # a claim the recoupment took payments of only in part: this listing carries what it took, so those
                    # payments still answer the claim's other items (ledger_taken's listed mark)
                    self.taken_listed |= took_of.get((getattr(self, "plb_wo_keys", None) or {}).get(key) or "", set())
                if size < amt and part:
                    ev.update(withheld_cents=amt, net_cents=size)
                    title += f": ${size/100:,.2f} of it net of the payer's adjustments naming the claim"
                elif size < amt:
                    ev.update(withheld_cents=amt, not_applied_cents=size)
                    title += f": ${size/100:,.2f} of it takes back no payment on file"
                self._emit("TAKEBACK", "fees", "high" if size >= 500000 else "medium", "review", title, None, None, size, None, None,
                           ev, key_parts=(r["transaction_key"], r["reference"], amt), claim=base)
        self._mark_taken_listed()
        for key, f in fees.items():
            self._emit("FEE_SKIM", "fees", "medium", "opt_out", f"${f['cents']/100:,.2f} in payment fees withheld by {f['payer_name']} in {f['month']}", None, None, f["cents"], None, None,
                       {"count": f["n"], "references": dict(f["refs"].most_common(5))}, key_parts=key, claim=f)

        g = defaultdict(lambda: {"paid": 0, "zero": 0, "charge": 0, "allowed": 0, "carcs": Counter()})
        for l in self.lines:
            if l.get("procedure_code") == "G2211" and l.get("claim_status_code") in ("1", "19"):
                d = g[(l["payer_group"], l["year"])]
                if (l.get("paid_cents") or 0) > 0:
                    d["paid"] += 1
                    d["allowed"] += l.get("allowed_cents") or 0
                else:
                    d["zero"] += 1
                    _a, top, _pr, _c = self._line_carcs(l)
                    if top:
                        d["carcs"][top] += 1
                d["charge"] += l.get("charge_cents") or 0
                d["kind"] = l["payer_kind"]
        for (grp, year), d in g.items():
            tot = d["paid"] + d["zero"]
            if tot >= 25 and d["zero"] / tot >= 0.5:
                base = {"payer_group": grp, "payer_kind": d.get("kind"), "year": year, "payer_name": grp}
                self._emit("G2211_DENIED", "info", "low", "review", f"{grp} {year}: G2211 denied on {d['zero']} of {tot} visits", None, None, None,
                           top_of(d["carcs"])[0] if d["carcs"] else None, None,
                           {"paid": d["paid"], "zero": d["zero"], "carcs": dict(d["carcs"].most_common(4)), "charge_cents": d["charge"]}, key_parts=(grp, year), claim=base)

        for row in c.execute("""SELECT substr(COALESCE(l.service_date,l.payment_date),1,4) yr, a.reason_code, SUM(a.amount_cents) amt, COUNT(*) n
                                FROM adjustments a JOIN lines l ON l.line_key=a.line_key JOIN transactions t ON t.transaction_key=l.transaction_key
                                WHERE t.superseded=0 AND a.reason_code IN ('144','253') GROUP BY 1,2"""):
            if not row["yr"] or row["yr"] < "2023":
                continue
            amt = row["amt"] or 0
            base = {"year": row["yr"], "payer_group": "Medicare", "payer_kind": "medicare", "payer_name": "Medicare and Medicare Advantage"}
            if row["reason_code"] == "144":
                self._emit("MIPS_ADJUSTMENT", "info", "info", "review", f"{row['yr']}: net MIPS adjustment {'+' if amt < 0 else '-'}${abs(amt)/100:,.0f} across {row['n']} lines",
                           None, None, -amt, "144", None, {"year": row["yr"], "lines": row["n"]}, key_parts=("MIPS", row["yr"]), claim=base)
            else:
                self._emit("SEQUESTRATION", "info", "info", "none", f"{row['yr']}: sequestration withheld ${amt/100:,.0f}", None, None, amt, "253", None,
                           {"year": row["yr"], "lines": row["n"]}, key_parts=("SEQ", row["yr"]), claim=base)

        # a payer whose routine allowance for third and later procedures is below the standard 50 percent
        for (grp, bucket), p in self.mppr.items():
            if bucket == 3 and p["pct"] < 0.5:
                gap, n = 0, 0
                for l in self.lines:
                    if l["payer_group"] != grp or l.get("surg_rank", 0) < 3 or not is_surgical(l.get("procedure_code")) or l.get("procedure_code") in ADD_ON_CODES \
                            or l.get("claim_status_code") not in ("1", "19") or (l.get("paid_cents") or 0) <= 0:
                        continue
                    rate, _b, r = self.usual_rate(l, l["procedure_code"])
                    if rate and r and (l.get("allowed_cents") or 0) < 0.5 * rate:
                        gap += int(0.5 * rate) - (l.get("allowed_cents") or 0)
                        n += 1
                base = {"payer_group": grp, "payer_kind": self.kinds.get(grp), "payer_name": grp, "year": None}
                self._emit("MPPR_POLICY", "contract", "medium", "renegotiate",
                           f"{grp} allows third and later same-day procedures at about {p['pct']:.0%} of its usual rate ({p['n']} lines); the Medicare standard is 50%",
                           None, None, gap, "59", None, {"typical_pct": p["median"], "lines": p["n"], "lines_below_half": n,
                                                          "note": "Check the contract's multiple-procedure terms; this is the payer's routine practice, not a one-off error."},
                           key_parts=("MPPR_POLICY", grp), claim=base)

        # contract-level rate findings: commercial far below Medicare, Medicare Advantage below Medicare
        for (grp, year, code, tier, pos), r in self.rates.items():
            kind = self.kinds.get(grp)
            if pos != "office" or tier == "unknown" or r["n"] < 50 or r["share"] < 0.3:
                continue
            med = self.medicare.get((year, code, tier, pos))
            if not med:
                continue
            pct = r["mode"] / med
            base = {"payer_group": grp, "payer_kind": kind, "year": year, "payer_name": grp, "procedure_code": code, "provider_tier": tier}
            gap = (med - r["mode"]) * r["n"]
            if kind == "commercial" and pct < 0.85:
                self._emit("CONTRACT_RATE_GAP", "contract", "high" if gap >= 2000000 else "medium", "renegotiate",
                           f"{grp} pays {code} at {pct:.0%} of Medicare ({year}, {tier}, {r['n']} lines)", None, med, gap, None, None,
                           {"payer_rate_cents": r["mode"], "medicare_cents": med, "lines": r["n"], "pct_of_medicare": round(pct, 3), "mode_share": round(r["share"], 3)},
                           key_parts=(grp, year, code, tier), claim=base)
            elif kind == "medicare_advantage" and pct < 0.97:
                self._emit("MA_BELOW_MEDICARE", "contract", "medium" if gap < 2000000 else "high", "review",
                           f"{grp} pays {code} at {pct:.0%} of Medicare ({year}, {tier}, {r['n']} lines)", None, med, gap, None, None,
                           {"payer_rate_cents": r["mode"], "medicare_cents": med, "lines": r["n"], "pct_of_medicare": round(pct, 3), "mode_share": round(r["share"], 3),
                            "note": "Contracted MA rates follow the contract; non-contracted providers must receive at least the Medicare amount."},
                           key_parts=(grp, year, code, tier), claim=base)
        self.emit_charge_caps()

    def _newest_fees(self) -> tuple[str | None, dict]:
        """The practice's fee now: the commonest one-unit charge on lines with a date of service in the newest 90 days of
        the archive, for each (payer group, code, provider type, place of service) and for each code. Returns the first
        day of those 90 days and the fees."""
        days = [l["service_date"] for l in self.lines if l.get("service_date")]
        if not days:
            return None, {}
        since = _add_days(max(days), -90)
        seen: dict[tuple, Counter] = defaultdict(Counter)
        for l in self.lines:
            if (l.get("service_date") or "") >= since and (l.get("charge_cents") or 0) > 0 and l.get("procedure_code") \
                    and l.get("paid_units") in (None, 1, 1.0) and not l.get("pricing_mod") and l.get("claim_status_code") != "22":
                code = l["procedure_code"]
                seen[(l["payer_group"], code, l.get("provider_tier") or "unknown", l.get("pos_class") or "office")][l["charge_cents"]] += 1
                seen[(code,)][l["charge_cents"]] += 1
        return since, {k: cnt.most_common(1)[0][0] for k, cnt in seen.items()}

    def _first_answer_of(self, base: dict, kinds: tuple, group: str | None) -> str | None:
        """The day a payer of these kinds (Medicare) first answered the item's bill for its service day: its remittance date,
        from the 835s on file; None when there is none. Built once per run, for the bills the item's payer group answered
        (a Kentucky Medicaid crossover's Medicare adjudication, 907 KAR 3:005), so it costs one pass over the lines."""
        from .ema_reports import bill_of_claim
        cache = self.__dict__.setdefault("_first_answers", {})
        idx = cache.get((tuple(kinds), group))
        if idx is None:
            bills = {bill_of_claim(l.get("patient_control_number")) for l in self.lines if l.get("payer_group") == group}
            idx = {}
            for l in self.lines:
                if l.get("payer_kind") not in kinds or l.get("payer_group") == group:
                    continue
                b = bill_of_claim(l.get("patient_control_number"))
                day = l.get("payment_date") or l.get("txn_payment_date")
                if b in bills and day and l.get("service_date"):
                    k = (b, l["service_date"])
                    if k not in idx or day < idx[k]:
                        idx[k] = day
            cache[(tuple(kinds), group)] = idx
        return idx.get((bill_of_claim(base.get("patient_control_number")), self._first_service_day(base)))

    def _first_service_day(self, base: dict) -> str | None:
        """The first date of service a finding covers, for clocks that count from it: the line's own date or its claim's
        statement date, else the start of the line's service period, else the claim's earliest line."""
        day = base.get("service_date") or base.get("statement_from")
        if day or not base.get("claim_key"):
            return day
        try:
            if base.get("line_key"):
                r = self.conn.execute("""SELECT COALESCE(l.service_date, l.service_period_start, cl.statement_from) FROM lines l
                                         JOIN claims cl ON cl.claim_key = l.claim_key WHERE l.line_key=?""", (base["line_key"],)).fetchone()
            else:
                r = self.conn.execute("""SELECT COALESCE(MIN(COALESCE(l.service_date, l.service_period_start)), cl.statement_from)
                                         FROM claims cl LEFT JOIN lines l ON l.claim_key = cl.claim_key WHERE cl.claim_key=?""",
                                      (base["claim_key"],)).fetchone()
        except sqlite3.Error:
            return None
        return r[0] if r and r[0] else None

    def emit_charge_caps(self) -> None:
        """The practice's own charge below what a payer allows. Payers pay the lesser of the charge and their rate, so
        on such a line the payer paid exactly the charge and the rest is lost, on every line, until the fee is raised.
        Measured within one payer group, year, code, provider type and place of service: the payer's rate is the most
        common amount it allowed on clean lines it did not cap (allowed below the charge, at least five of them), and a
        line counts when it was paid exactly its charge and that charge is below the rate (but at least 40 percent of it and
        $5: a token charge is something else). One finding per group with at least five such lines and $100 between them.
        Nothing here can be appealed: the payer paid what was billed."""
        capped: dict[tuple, list] = defaultdict(list)
        uncapped: dict[tuple, Counter] = defaultdict(Counter)
        since, newest_fee = self._newest_fees()
        for l in self.lines:
            if l.get("claim_status_code") not in ("1", "19") or (l.get("paid_cents") or 0) <= 0:
                continue
            if self.standing.get(l["line_key"]) != l.get("paid_cents"):
                continue        # a payment taken back (or reversed and made again) counts once, on the payment that stands
            allowed, charge = l.get("allowed_cents") or 0, l.get("charge_cents") or 0
            if allowed <= 0 or charge <= 0 or l.get("original_procedure_code") or l.get("paid_units") not in (None, 1, 1.0) \
                    or l.get("pricing_mod") or l.get("surg_rank", 0) > 1 or not l.get("procedure_code"):
                continue
            key = (l["payer_group"], l["year"], l["procedure_code"], l.get("provider_tier") or "unknown", l.get("pos_class") or "office")
            if allowed == charge:
                capped[key].append(l)
            elif allowed < charge:
                uncapped[key][allowed] += 1
        for key in sorted(capped):
            cnt = uncapped.get(key)
            if not cnt or sum(cnt.values()) < 5:
                continue
            rate, _n = usual_amount(cnt)
            # a charge far below the rate is a billing oddity (a balance or a token amount), not a fee set too low; a charge
            # within 5 percent of it is a payer's rate that changed during the year (Medicare's did on March 9, 2024), not a
            # fee to raise
            short = [l for l in capped[key] if 0.4 * rate <= l["charge_cents"] <= 0.95 * rate and l["charge_cents"] >= 500]
            gap = sum(rate - l["charge_cents"] for l in short)
            if len(short) < 5 or gap < 10000:
                continue
            grp, year, code, tier, pos = key
            if since and not any((l.get("service_date") or "") >= since for l in short):
                # nothing charged below the rate lately: a code no longer billed, or a fee now above this payer's rate, leaves
                # nothing to change
                now = newest_fee.get((grp, code, tier, pos)) or newest_fee.get((code,))
                if not now or now >= 0.95 * rate:
                    continue
            charges = Counter(l["charge_cents"] for l in short)
            charge = charges.most_common(1)[0][0]
            base = {"payer_group": grp, "payer_kind": self.kinds.get(grp), "payer_name": grp, "year": year, "procedure_code": code,
                    "provider_tier": tier, "pos_class": pos}
            self._emit("CHARGE_BELOW_RATE", "contract", "high" if gap >= 200000 else "medium", "raise_fee",
                       f"{grp} allows ${rate/100:,.2f} for {code} but paid your charge of ${charge/100:,.2f} on {len(short)} lines ({year})",
                       None, rate, gap, None, None,
                       {"payer_rate_cents": rate, "charge_cents": charge, "charges": {str(k): v for k, v in charges.most_common(5)}, "lines": len(short),
                        "uncapped_lines": sum(cnt.values()),
                        "note": "The payer pays the lesser of your charge and its rate, so these lines were paid what was billed and cannot be appealed. "
                                "Raise your fee for this code above the highest amount any payer allows; claims billed after that are paid in full."},
                       key_parts=("CHARGE_BELOW_RATE", grp, year, code, tier, pos), claim=base)

    # ------------------------------------------------------------------ persistence
    def save(self) -> dict:
        c = self.conn
        c.row_factory = sqlite3.Row
        existing = {r["finding_id"]: dict(r) for r in c.execute("SELECT * FROM findings")}
        # the open items before this run's save, for swing_moves (record_moves); none on a first analysis, which is a load
        self._open_before = _open_items(existing.values()) if existing else None
        # an item a baseline makes (a short payment, a routine rate, a clinician's discount) that is new to this run while its
        # filing window has already closed is no work to do: it is not made (trace finding T-N4: a baseline that moved turns
        # old remittances into items no one can still raise)
        for fid in [k for k, f in self.findings.items() if f.get("signal") in RATE_SIGNALS and k not in existing
                    and f.get("appeal_deadline") and f["appeal_deadline"] < self.today]:
            del self.findings[fid]
        cols = ["finding_id", "signal", "category", "severity", "actionable", "title", "line_key", "claim_key", "transaction_key",
                "patient_control_number", "payer_name", "payer_group", "payer_kind", "payer_id", "rendering_provider_npi",
                "rendering_provider_name", "provider_tier", "service_date", "payment_date", "year", "procedure_code", "modifiers",
                "original_procedure_code", "code_category", "charge_cents", "allowed_cents", "paid_cents", "expected_cents",
                "shortfall_cents", "carc", "carc_text", "rarc", "rarc_text", "evidence", "appeal_deadline", "status", "notes",
                "run_id", "first_seen", "last_seen"] + list(EXTRA_FINDING_COLS)

        # Findings a person moved away from "resolved" at or after the analysis last closed them (the same second counts: a
        # person can only act on a closed finding after it closed), or that inherited such a decision when their signal
        # changed: the analysis leaves their status alone from then on. A slip a person undid before any automatic closure
        # does not count.
        # A reopening by the analysis (the payment was taken back) ends both: what the person did before it was about a
        # closure that no longer holds.
        overridden = {r[0] for r in c.execute("""SELECT finding_id FROM finding_events GROUP BY finding_id
                                                 HAVING MAX(CASE WHEN kind='override_carried' THEN at END) >= COALESCE(MAX(CASE WHEN kind='reopened' THEN at END), '')
                                                     OR (MAX(CASE WHEN kind='paid_on_later_remit' THEN at END) IS NOT NULL
                                                         AND MAX(CASE WHEN kind='manual' AND from_status='resolved' THEN at END)
                                                             >= MAX(CASE WHEN kind='paid_on_later_remit' THEN at END)
                                                         AND MAX(CASE WHEN kind='manual' AND from_status='resolved' THEN at END)
                                                             >= COALESCE(MAX(CASE WHEN kind='reopened' THEN at END), ''))""")}
        # recoveries a person recorded after the analysis had closed the finding (and before anything reopened it): they rest
        # on that payment
        confirmed = {r[0] for r in c.execute("""SELECT finding_id FROM finding_events GROUP BY finding_id
                                               HAVING MAX(CASE WHEN kind='confirmed_carried' THEN at END) >= COALESCE(MAX(CASE WHEN kind='reopened' THEN at END), '')
                                                   OR (MAX(CASE WHEN kind='paid_on_later_remit' THEN at END) IS NOT NULL
                                                       AND MAX(CASE WHEN kind='manual' AND to_status='resolved' THEN at END)
                                                           >= MAX(CASE WHEN kind='paid_on_later_remit' THEN at END)
                                                       AND MAX(CASE WHEN kind='paid_on_later_remit' THEN at END)
                                                           >= COALESCE(MAX(CASE WHEN kind='reopened' THEN at END), ''))""")}
        # Rows from a release whose status action left the automatic note in place (before this change): where a person
        # acted after the analysis closed it, the status is theirs. A recovery they recorded rests on that payment.
        for fid, kind, from_status, to_status, _mx in c.execute("SELECT finding_id, kind, from_status, to_status, MAX(id) FROM finding_events GROUP BY finding_id"):
            r = existing.get(fid)
            if r and r.get("auto_note") and kind == "manual":
                r["auto_note"], r["prev_status"] = None, None
                if to_status == "resolved":
                    confirmed.add(fid)
                elif from_status == "resolved":
                    overridden.add(fid)         # moved off a closure the live release made without logging it

        def still_paid(f: dict) -> bool:
            """A payment for this finding's service still stands on its bill, made after the finding's remittance."""
            bill, after = bill_of(f.get("patient_control_number")), f.get("payment_date") or ""
            codes = [f.get("procedure_code")] if f.get("procedure_code") else (json.loads(f["evidence"]).get("codes") or [] if f.get("evidence") else [])
            return any(e[0] > after and e[3] in ("1", "19") for code in codes for e in
                       (self.paid_events.get((bill, code, f.get("service_date")), []) if f.get("service_date") else self.paid_by_code.get((bill, code), [])))
        label = {"open": "open", "in_progress": "in progress", "appealed": "appealed", "lost": "lost", "dismissed": "dismissed"}
        # A fax sent or going out for an item, or an appeal being prepared for it (on a server, the marker api._prep_take keeps
        # in meta under api.PREP_KEY), is work on it, though the item stays To do until the line takes the fax: when its money
        # gets a new id meanwhile, the fax follows the heir (carry_to_heirs) and so does the send's Sent (actions.heir_of).
        have_tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        faxed = {r[0] for r in c.execute("SELECT DISTINCT finding_id FROM faxes WHERE direction='out' AND finding_id IS NOT NULL")} \
            if "faxes" in have_tables else set()
        if "meta" in have_tables:
            faxed |= {r[0][len("fax:prep:"):] for r in c.execute("SELECT key FROM meta WHERE key LIKE 'fax:prep:%'")}

        def worked(r: dict) -> bool:        # a person recorded something on this finding
            return bool(r.get("notes") or r.get("appealed_at")) or r["status"] in MANUAL_STATUSES or r["status"] == "dismissed" \
                or (r["status"] == "resolved" and not r.get("auto_note")) or r["finding_id"] in overridden or r["finding_id"] in faxed

        # when new information changes a line's signal (and so its id), the work recorded on the old finding follows the line
        orphans: dict[tuple, dict] = {}
        # ... and the notes on a claim no payer had answered (an item no longer found) go to exactly two places: the items its
        # own claim's answer brings, in the run that answer arrives; or the same bill's claim sent again to the same payer, when
        # that one comes due. Otherwise the item waits, stale, and comes back with its own status and notes if its claim id is
        # listed again. A later reprocessing, another payer's claim or a rate finding never takes it.
        own_answer: dict[str, dict] = {}
        sent_again: dict[str, list[dict]] = {}
        # ... and a claim chased under one id that is now chased under the other (the 837s' 837_OPEN_CLAIM and EMA's
        # EMA_OPEN_CLAIM: EMA's exports started, or stopped, listing it) is the same chase: its item passes on whole
        same_claim: dict[str, dict] = {}
        for fid, prev in existing.items():
            if fid in self.findings or not worked(prev):
                continue
            if prev.get("line_key"):
                if not prev.get("stale"):
                    orphans.setdefault((prev["line_key"], prev["category"]), prev)
            elif prev.get("signal") in EMA_CLAIM_SIGNALS and prev.get("patient_control_number"):
                pcn_w = prev["patient_control_number"]
                if pcn_w not in same_claim or (same_claim[pcn_w].get("stale") and not prev.get("stale")):
                    same_claim[pcn_w] = prev
                if prev["status"] != "resolved":
                    # (an item a person marked Paid is a recovery on the record: it stays as it is and hands nothing on to
                    # another matter)
                    own_answer[pcn_w] = prev
                    sent_again.setdefault(bill_of(pcn_w), []).append(prev)
        first_cache: dict[str, str] = {}

        def first_paid(pcn: str) -> str:
            """A claim's first remittance date: its own answer, as against a reprocessing months later (a new matter)."""
            if pcn not in first_cache:
                got = c.execute("""SELECT MIN(t.payment_date) FROM claims cl JOIN transactions t ON t.transaction_key = cl.transaction_key
                                   WHERE cl.patient_control_number=? AND COALESCE(t.superseded,0)=0""", (pcn,)).fetchone()
                first_cache[pcn] = (got[0] if got else None) or ""
            return first_cache[pcn]

        def payer_family(r: dict) -> str | None:
            """Who a claim went to, in a form that does not change between runs: the first word of the payer's usual group
            ('anthem', 'humana', 'medicaid', ...), from the name EMA or the remittance gives, so that EMA's 'Medicaid of
            Kentucky' and a remittance's 'KENTUCKY MEDICAID' are one payer. A group of many payers (the Medicare supplement
            carriers, the self-funded plans' administrators) and a payer the rules do not know are the payer's own name
            instead: Medicare and a Medigap carrier, or two administrators, are never one payer (missing a hand-over is
            safer than handing the work to another payer). VA Community Care is known by its payer id too, as EMA names it
            after the network's administrator. None when nothing names the payer: then it matches nothing."""
            ev = r.get("evidence")
            try:
                ev = json.loads(ev) if isinstance(ev, str) else (ev or {})
            except ValueError:
                ev = {}
            if r.get("payer_group") == VA_GROUP[0] or (r.get("payer_id") or "").strip().upper() in VA_PAYER_IDS:
                return "va"
            name = ev.get("payer_in_ema") or r.get("payer_name") or r.get("payer_group") or ""
            grp, kind = normalize_payer(name) if name else ("", "other")
            if grp in MANY_PAYER_GROUPS or kind == "other":
                own = " ".join(re.findall(r"[A-Z0-9]+", name.upper()))
                return ("own:" + own) if own else None
            return grp.split(" ")[0].lower() or None

        def sub_no(pcn: str | None) -> int:
            m = _SUBMISSION.search(pcn or "")
            return int(m.group(1)) if m else 0
        # EMA's checks name the items whose work a new item takes (heir_keys): one claim keyed by its EMA claim number before
        # the A/R knew its claim id, or the chase of a claim whose denial EMA has since posted (an EMA_DENIED item is that
        # claim's answer). And a denial only EMA saw that a person worked hands its work to the first item from the
        # remittance that then arrives for its line (its ema_heir_keys: bill, date, code); the EMA item stays, stale.
        heirs_of: dict[str, list] = self.__dict__.get("_heirs") or {}
        ema_heirs: dict[tuple, dict] = {}
        for fid, prev in existing.items():
            if fid in self.findings or prev.get("signal") != "EMA_DENIED" or not worked(prev) or prev["status"] == "resolved":
                continue
            try:
                keys = json.loads(prev.get("evidence") or "{}").get("ema_heir_keys") or []
            except ValueError:
                keys = []
            for k in keys:
                if isinstance(k, list) and len(k) == 3:
                    ema_heirs.setdefault(tuple(k), prev)
        ema_taken: set[str] = set()
        inherited: set[str] = set()
        moved: list[tuple[str, str]] = []          # (old id, heir): what was recorded on the old id goes to the heir
        rows, events = [], []
        out_rows: list[dict] = []
        for fid, f in self.findings.items():
            prev = existing.get(fid)
            via_bill = None
            heir = orphans.get((f["line_key"], f["category"])) if (prev is None and f.get("line_key")) else None
            if prev is None and heir is None and not f.get("line_key") and f.get("signal") in EMA_CLAIM_SIGNALS:
                w = same_claim.get(f.get("patient_control_number") or "")
                if w is not None and w["finding_id"] not in inherited:
                    heir = w                # the same claim's chase under the other id: status, notes and records follow
            if prev is None and heir is None and f.get("patient_control_number"):
                pcn_f = f["patient_control_number"]
                paid_on = f.get("payment_date") or f.get("txn_payment_date") or ""
                own = f.get("signal") in EMA_CLAIM_SIGNALS or paid_on == first_paid(pcn_f)    # a claim's chase, or its first answer
                if pcn_f in own_answer and paid_on and paid_on == first_paid(pcn_f) and own_answer[pcn_f]["finding_id"] not in inherited:
                    via_bill = own_answer[pcn_f]
                elif own:                       # the same bill sent again to the same payer: its chase, or its first answer
                    fam_f = payer_family(f)
                    via_bill = next((w for w in sent_again.get(bill_of(pcn_f), [])
                                     if sub_no(pcn_f) > sub_no(w["patient_control_number"]) and fam_f is not None and payer_family(w) == fam_f), None)
            if prev is None and heir is None and via_bill is None and heirs_of.get(fid):
                via_bill = next((w for w in (existing.get(_fid(*h)) for h in heirs_of[fid])
                                 if w is not None and worked(w) and w["status"] != "resolved"), None)
            elif (prev is not None and heir is None and via_bill is None and heirs_of.get(fid) and not worked(prev)
                  and prev["status"] == "open" and not prev.get("status_changed_at")):
                # an item already listed, that nobody has touched, whose heir keys name an older item a person worked that is
                # no longer listed (two bills of one visit both listed before the 837s named the patient, then merged): the
                # person's work comes to the item that is listed, as it would to a new one (fx-837-r1-3). A person's own
                # status or note on this item stands.
                via_bill = next((w for w in (existing.get(_fid(*h)) for h in heirs_of[fid])
                                 if w is not None and w["finding_id"] not in self.findings and worked(w) and w["status"] != "resolved"),
                                None)
            if prev is None and heir is None and via_bill is None and ema_heirs and f.get("line_key") and f.get("patient_control_number"):
                from .ema_reports import bill_of_claim
                b_f = bill_of_claim(f["patient_control_number"])
                for cd in (f.get("procedure_code"), f.get("original_procedure_code"), f.get("billed_code")):
                    w = ema_heirs.get((b_f, f.get("service_date"), cd)) if cd else None
                    if w is not None and w["finding_id"] not in ema_taken:
                        via_bill = w
                        ema_taken.add(w["finding_id"])
                        break
            if via_bill is not None:
                # a new item is a new matter: it starts To do (Started if someone was working the claim), with the notes; never
                # Sent, Paid or Let go from the earlier item, whose own record stays in its events
                st0 = "in_progress" if via_bill["status"] in ("in_progress", "appealed") else "open"
                prev = {"status": st0, "notes": via_bill.get("notes"), "first_seen": (prev or {}).get("first_seen") or self.now,
                        "finding_id": fid}
                events.append((fid, self.now, "signal_changed", via_bill["status"], st0, f.get("shortfall_cents"), "system",
                               f"was {via_bill['signal']} on claim {via_bill.get('patient_control_number') or 'the same EMA claim'} "
                               f"({via_bill['status']}), now {f['signal']}",
                               self.run_id))
            if heir is not None:
                if heir["finding_id"] not in inherited:
                    prev = heir
                    inherited.add(heir["finding_id"])
                    moved.append((heir["finding_id"], fid))
                    events.append((fid, self.now, "signal_changed", heir["status"], heir["status"], f.get("shortfall_cents"), "system",
                                   f"was {heir['signal']}, now {f['signal']}", self.run_id))
                    for mark, kind in ((overridden, "override_carried"), (confirmed, "confirmed_carried")):
                        if heir["finding_id"] in mark:      # the person's decision follows the line to its new id
                            events.append((fid, self.now, kind, heir["status"], heir["status"], None, "system", f"from {heir['finding_id']}", self.run_id))
                            mark.add(fid)
            st, notes, first = (prev["status"], prev["notes"], prev["first_seen"]) if prev else ("open", None, self.now)
            auto_note, prev_status = (prev.get("auto_note"), prev.get("prev_status")) if prev else (None, None)
            before = st
            if prev:    # what people recorded about this finding survives every re-run
                f["appealed_at"], f["status_changed_at"] = prev.get("appealed_at"), prev.get("status_changed_at")
            if f.get("auto_status"):
                if st in ("open", "in_progress", "appealed") and not (prev and prev["finding_id"] in overridden):
                    prev_status, st, auto_note = st, "resolved", f.get("auto_notes")     # remembered so it can be undone exactly
                elif st == "resolved" and auto_note:
                    auto_note = f.get("auto_notes")         # closed by an earlier analysis and still paid
                else:
                    # A person's decision stands: dismissed, lost, recovered with their own amount, or set again after an
                    # automatic closure. The payment is shown, not applied.
                    if st == "resolved":
                        f["recovered_cents"], f["recovered_date"] = prev.get("recovered_cents"), prev.get("recovered_date")
                    else:
                        f["hint"] = f"A later payment was found; kept as {label.get(st, st)} because a person set it"
                    auto_note, prev_status = None, None
            elif st == "resolved" and auto_note:
                # the later payment no longer holds (for example its remittance was superseded): back to where it was
                st, auto_note, prev_status = (prev_status or "open"), None, None
            elif st == "resolved" and prev and prev["finding_id"] in confirmed and not still_paid(f):
                # a person recorded this recovery on a payment the analysis had found, and that payment no longer stands
                st, auto_note, prev_status = "open", None, None
                f["hint"] = "The payment this was recovered on was taken back; check it"
            elif st == "resolved" and prev:
                f["recovered_cents"], f["recovered_date"] = prev.get("recovered_cents"), prev.get("recovered_date")
            else:
                auto_note, prev_status = None, None        # an automatic note left from before a person changed the status
            if st != "resolved":
                f["recovered_cents"], f["recovered_date"] = None, None
            if (prev and before != st) or (not prev and st == "resolved"):
                f["status_changed_at"] = self.now
                events.append((fid, self.now, "paid_on_later_remit" if st == "resolved" else "reopened", before, st,
                               f.get("recovered_cents") if st == "resolved" else f.get("shortfall_cents"), "system", auto_note, self.run_id))
            f["status"], f["notes"], f["first_seen"], f["last_seen"], f["stale"] = st, notes, first, self.now, 0
            f["auto_note"], f["prev_status"] = auto_note, prev_status
            out_rows.append(f)
        # findings a person worked, dismissed or lost that the current rules no longer produce are kept, marked stale, so that
        # no work is lost and a dismissed finding cannot come back as new. Stale rows are left out of every total.
        # Any item a person wrote on is kept, even while open, so no note is lost; an unanswered claim's item stays after its
        # notes moved on, so it comes back with its own status if its claim id is listed again. One a person marked Paid stays
        # counted (a recovery on the record), not stale.
        for fid, prev in existing.items():
            if fid in self.findings or fid in inherited or not worked(prev):
                continue
            prev["stale"] = 0 if (prev.get("signal") in EMA_CLAIM_SIGNALS and prev["status"] == "resolved") else 1
            out_rows.append(prev)
        # every item's state, decided in one place (remitwatch.states) on the status just settled; an item a person set
        # back to work after an automatic closure is judged without the payment that closure rested on (they rejected it)
        self._overridden = overridden
        self._set_states(out_rows)
        rows = [tuple(r.get(k) for k in cols) for r in out_rows]
        # (the EMA line summary keeps every line, whatever this run lists: no refresh waits on the items saved here)
        c.execute("DELETE FROM findings")
        c.executemany(f"INSERT INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
        if events:
            c.executemany("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note, run_id) VALUES (?,?,?,?,?,?,?,?,?)", events)
        carry_to_heirs(c, moved, self.now, self.run_id)
        c.execute("DELETE FROM progress_snapshots WHERE run_id=?", (self.run_id,))
        c.execute("""INSERT INTO progress_snapshots (run_id, at, category, status, n, cents)
                     SELECT ?, ?, category, status, COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings
                     WHERE COALESCE(stale,0)=0 GROUP BY category, status""", (self.run_id, self.now))
        self._save_exposure()
        summary = Counter(f["signal"] for f in self.findings.values())
        c.execute("INSERT OR REPLACE INTO detection_runs (run_id, started_at, finished_at, version, lines, findings, summary) VALUES (?,?,?,?,?,?,?)",
                  (self.run_id, self.now, datetime.now(timezone.utc).isoformat(timespec="seconds"), DETECT_VERSION, len(self.lines), len(rows), json.dumps(summary)))
        if getattr(self, "events_seen", None) is not None:
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (EVENTS_SEEN_META, json.dumps(self.events_seen)))
        c.commit()
        return dict(summary)

    def _save_exposure(self) -> None:
        """Denominators for rates: primary-claim lines by payment month, payer, plan, provider, billed code and place of service."""
        agg: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
        for l in self.lines:
            if l.get("claim_status_code") not in ("1", "19") or not l.get("procedure_code") or not l.get("month"):
                continue
            if (l.get("charge_cents") or 0) <= 0:
                continue
            billed = l.get("original_procedure_code") or l["procedure_code"]
            key = (l["month"], l["payer_group"], l["payer_kind"], l.get("rendering_provider_npi") or "", l.get("provider_tier") or "unknown",
                   billed, l["pos_class"], l.get("plan") or "")
            a = agg[key]
            a[0] += 1
            a[1] += 1 if (l.get("paid_cents") or 0) == 0 and (l.get("allowed_cents") or 0) == 0 else 0
            a[2] += l.get("charge_cents") or 0
            a[3] += l.get("allowed_cents") or 0
            a[4] += l.get("paid_cents") or 0
        self.conn.execute("DELETE FROM exposure")
        self.conn.executemany("INSERT OR REPLACE INTO exposure VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [k + tuple(v) for k, v in agg.items()])
        # the last 500 days at day resolution for the alert engine: line counts, and the allowed amounts on cleanly paid lines
        days = [l.get("payment_date") or l.get("txn_payment_date") or "" for l in self.lines]
        last = max(days) if days else ""
        cutoff = _add_days(last, -500) or ""
        daily: Counter = Counter()
        points: Counter = Counter()
        for l in self.lines:
            day = l.get("payment_date") or l.get("txn_payment_date") or ""
            if day < cutoff or l.get("claim_status_code") not in ("1", "19") or not l.get("procedure_code") or (l.get("charge_cents") or 0) <= 0:
                continue
            billed = l.get("original_procedure_code") or l["procedure_code"]
            scope = "other" if billed not in EM_CODES else ("em4" if EM_LEVEL[billed] >= 4 else "em")
            daily[(day, l["payer_group"], l["payer_kind"], l.get("rendering_provider_npi") or "", scope)] += 1
            if l.get("clean"):
                points[(day, l["payer_group"], l["payer_kind"], l["procedure_code"], l.get("provider_tier") or "unknown", l["pos_class"], l["allowed_cents"])] += 1
        self.conn.execute("DELETE FROM exposure_daily")
        self.conn.executemany("INSERT OR REPLACE INTO exposure_daily VALUES (?,?,?,?,?,?)", [k + (v,) for k, v in daily.items()])
        self.conn.execute("DELETE FROM rate_points")
        self.conn.executemany("INSERT OR REPLACE INTO rate_points VALUES (?,?,?,?,?,?,?,?)", [k + (v,) for k, v in points.items()])

    def run(self) -> dict:
        # the newest person's event this run can see (finding_events ids only grow): one written after this moment, while
        # the run goes on or later, asks for another run (pipeline.people_changed_since_detection); a timestamp to the
        # second cannot tell a change made in the run's first second from one it saw
        try:
            self.events_seen = int(self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM finding_events").fetchone()[0])
        except sqlite3.Error:
            self.events_seen = 0
        from . import runlog, selfcheck
        # (H) the items as they are before this run, for the decision log ("Why did this change?"), and the rule of each state
        self.state_rules: dict[str, str] = {}
        self._decisions_before = runlog.snapshot_items(self.conn)
        # (G) the worklist as people see it now, for safe mode, the vanished check and the track record
        self._selfcheck_ctx = selfcheck.begin_detection(self.conn, run_id=self.run_id, today=self.today)
        try:
            if self.conn.execute("SELECT 1 FROM ema_rows LIMIT 1").fetchone():
                from .link import build_links
                build_links(self.store, log=lambda *_a: None)
        except Exception as exc:
            runlog.caught(exc, "links")
            # the links could not be built: the weeks the exports miss are still measured (they need no links), and if
            # even that fails the old list is dropped rather than served as today's
            from .link import EMA_COVERAGE_KEY, save_ema_coverage
            try:
                self.conn.rollback()
                save_ema_coverage(self.conn)
                self.conn.commit()
            except Exception as exc2:
                runlog.caught(exc2, "links_fallback", level="warning")
                try:
                    self.conn.rollback()
                    self.conn.execute("DELETE FROM meta WHERE key=?", (EMA_COVERAGE_KEY,))
                    self.conn.commit()
                except Exception as exc3:
                    runlog.caught(exc3, "links_coverage", level="warning")
        # the order of the tech lead's amendment 4.5 (EMA's steps do nothing on a store without EMA's reports)
        self._ema_prepare()             # 2-3: the map when it is dirty; C1 causes, answer times, posting lag
        self.load()                     # 4
        self.build_baselines()
        self.run_line_rules()
        self.run_claim_rules()
        self.hook_expected()
        self.fold_payer_policy()        #    a payer's policy (a code it never pays as primary) is not an appeal per line
        self.run_ema_open_claims()      # 5: the weekly exports' unanswered claims, checked against EMA's reports
        self.run_837_open_claims()      #    claims the 837s show sent that the exports do not list (before the reports' own)
        self.__dict__.pop("_s837", None)
        self.run_ema_report_claims()    #    never sent, rejected, unanswered (from the reports)
        self.run_ema_denials()          # 6: denials only EMA saw (C5)
        self.run_aggregate_rules()      # 7
        self.run_ema_contractual()      #    denials posted as contractual (C7)
        self.emit_second_rates()        # 8
        self.hook_archive_rules()       # 9: the archive-only branches first (needs_B2: before resolve_elsewhere)
        self.resolve_elsewhere()        #    then the archive's own closures
        self.run_ema_outcomes()         #    the line outcomes, once; paid in EMA (C6)
        self.run_ema_unposted()         # 10: 835 payments EMA never posted (C4)
        self.run_ema_writeoffs()        # 11: written off in EMA (C2) and the overlays (W2)
        self.run_more_rules()           #    secondary never billed, credits, EMA-only short payments
        self._rarely_paid_step()        #    why a payer does not pay a code it almost never pays (the owner's decision 4)
        self.hook_fold()
        from . import archive           # unit E: a locked year's aggregate items stay as measured on its whole year
        archive.carry_locked_aggregates(self)
        out = self.save()               # 12: heirs, states
        self.run_ema_summaries()        # 13: W4, W3, the reconcile summary, the policy table
        self._save_ema_wiring()
        try:        # the feed date this analysis used, for the dashboard's copy (which does not hold every line) and its fax hold,
                    # with the hole it stops at, if any (feed_gap), which the copy's fax hold and feed check name
            hole: dict = {}
            day = feed_date(self.conn, hole=hole)
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                              (FEED_META, json.dumps({"date": day, "gap": hole or None, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})))
            # and the payers whose remittances stop before claims sent to them were due (CH-8), for the Data tab's checks
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (PAYERS_BEHIND_META, json.dumps(self.payers_behind)))
            # the payers whose 837 claims never get an ERA, and the weeks one feed shows sends the other does not (T-N13)
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (NO_ERA_META, json.dumps(self.no_era)))
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                              (WAITING_837_META, json.dumps(getattr(self, "waiting_837", None) or {}, sort_keys=True)))
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (SEND_WEEKS_META, json.dumps(self._send_weeks())))
            # and the reversals of a payment that is not on file (T-I5): a remittance that is missing
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                              (REVERSALS_NOT_ON_FILE_META, json.dumps(getattr(self, "not_on_file", None) or {})))
            self.conn.commit()
        except sqlite3.Error as exc:
            runlog.caught(exc, "feed_meta", level="warning")
            self.conn.rollback()        # the analysis is saved; the API works the date out from the store, or the newest payment
        try:        # reasons hidden for a payer (actions.apply_hide_rule) hide the new items they cover
            from .actions import apply_hide_rules
            if apply_hide_rules(self.conn):
                self._restate_closed()  # their state follows at once (states.py), as the next analysis would set it
                self.conn.commit()
        except Exception as exc:
            runlog.caught(exc, "hide_rules")
            self.conn.rollback()        # the analysis is saved; the rules run again after the next one
        self.hook_hide()
        self._after_save_checks()       # (G) ranking, invariants, safe mode, items kept; before the swing alarm reads the list
        if getattr(self, "_open_before", None) is not None:
            try:    # what this run put on the open list and took off it, item by item, for the swing alarm
                record_moves(self.conn, self.run_id, self._open_before)
                self.conn.commit()
            except sqlite3.Error as exc:
                runlog.caught(exc, "swing_moves", level="warning")
                self.conn.rollback()    # the analysis is saved; the alarm then reads the totals alone for this run
        self._record_decisions()        # (H) every automatic change to an item, with its rule and evidence
        return out

    def _after_save_checks(self) -> None:
        """(G, remitwatch.selfcheck) After the save: each item's dollars x its pattern's recovery rate (the ranking), then
        the self-check on what was saved (invariants, items that vanished kept, safe mode, the track record). A held run
        puts the last good worklist back before the swing alarm and the decision log read it."""
        from . import runlog, runtime, selfcheck
        try:
            self.value_summary = selfcheck.apply_expected_values(self.conn, selfcheck.recovery_rates(self.conn))
        except Exception as exc:       # noqa: BLE001  (the ranking waits for the next run; the items stand)
            runlog.caught(exc, "selfcheck:values")
        # the lines passed by as copies of an answer delivered again the same day: their one item is the other delivery's
        ctx = dict(getattr(self, "_selfcheck_ctx", None) or {}, delivered_again=sorted(getattr(self, "answer_copies", ()) or ()))
        try:
            self.selfcheck = selfcheck.after_detection(self.conn, ctx,
                                                       mode="serverless" if runtime.serverless() else "server",
                                                       today=self.today, run_id=self.run_id)
        except Exception as exc:       # noqa: BLE001  (a self-check that crashes is a guard that is off: said every run, never silent)
            runlog.caught(exc, "selfcheck")
            self.selfcheck = selfcheck.crashed(self.conn, exc, run_id=self.run_id, today=self.today)
        rl = runlog.current()
        if rl is not None:
            rl.set("invariants", (self.selfcheck or {}).get("checks"))

    def _restate_closed(self) -> int:
        """Items a step after the save closed (the hide rules move new items to Let go): their state is decided again
        (states.py: Let go by a rule is accepted), so no item reads Let go and Work now together until the next analysis
        (the self-check's open_and_closed check would hold the run for it)."""
        from . import states
        cur = self.conn.execute("SELECT * FROM findings WHERE COALESCE(stale,0)=0 AND status IN ('resolved','lost','dismissed') "
                                "AND work_state IN ('work_now','waiting')")
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        if not rows:
            return 0
        got = self._ema_step("restate", states.states_of, self.conn, rows,
                             outcomes=dict(self.ema_outcomes) if self.ema_on else {}, today=self.today) or {}
        upd = [(st.get("work_state"), st.get("follow_up_on"), (st.get("state_basis") or "")[:300] or None, st.get("appeal_block"),
                st.get("rule"), fid) for fid, st in got.items() if st]
        self.conn.executemany("UPDATE findings SET work_state=?, follow_up_on=?, state_basis=?, appeal_block=?, state_rule=? "
                              "WHERE finding_id=?", upd)
        return len(upd)

    def _record_decisions(self) -> None:
        """(H, remitwatch.runlog) The decision log of this run: one row per item an automatic step changed."""
        from . import runlog
        try:
            self.decisions = runlog.record_decisions(self.conn, self.run_id, self._decisions_before,
                                                     rules=getattr(self, "state_rules", None), rule_version=DETECT_VERSION)
            rl = runlog.current()
            if rl is not None:
                rl.set("decisions", self.decisions)
        except Exception as exc:        # noqa: BLE001  (the items are saved; only this run's decision rows are missing, and the log says so)
            runlog.caught(exc, "decisions")


HEIRS_DDL = "CREATE TABLE IF NOT EXISTS finding_heirs (old_id TEXT PRIMARY KEY, new_id TEXT, at TEXT, run_id TEXT)"


def carry_to_heirs(c: sqlite3.Connection, moved: list[tuple[str, str]], at: str, run_id: str | None) -> None:
    """When new information changes a line's signal, its money gets a new id and inherits the old item's status (save). What
    was recorded on the old id goes with it, in the same save: its faxes (so the follow-ups chase the heir and a second
    appeal is refused as for the same id), its packets, its history, and the items a payer's fax was tied to. Each pair is
    kept in finding_heirs, so that a record written for the old id later (a page opened before the run, a journal entry
    folded after it) is moved at every save and as it is applied (actions.heir_of). Chains (an heir that changes again)
    point at the newest heir; an id that is an item again is nobody's heir. Rows are moved, not looked up by claim: every
    reader (the follow-ups, the panel, the Faxes page, the send's guard) keeps reading by item id."""
    c.execute(HEIRS_DDL)
    if moved:
        c.executemany("INSERT OR REPLACE INTO finding_heirs (old_id, new_id, at, run_id) VALUES (?,?,?,?)", [(o, n, at, run_id) for o, n in moved])
        c.executemany("UPDATE finding_heirs SET new_id=? WHERE new_id=?", [(n, o) for o, n in moved])
    c.execute("DELETE FROM finding_heirs WHERE old_id = new_id OR old_id IN (SELECT finding_id FROM findings)")
    pairs = {o: n for o, n in c.execute("SELECT old_id, new_id FROM finding_heirs")}
    if not pairs:
        return
    have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("faxes", "appeal_packets", "finding_events"):
        if table in have:
            c.executemany(f"UPDATE {table} SET finding_id=? WHERE finding_id=?", [(n, o) for o, n in pairs.items()])
    if "faxes" in have:         # a payer's letter tied to several items (or offered to some) names them in its record
        for key, doc in c.execute("SELECT fax_key, doc FROM faxes WHERE direction='in' AND doc IS NOT NULL").fetchall():
            try:
                d = json.loads(doc)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            changed = False
            for field in ("tied", "suggested"):
                ids = d.get(field)
                if isinstance(ids, list) and any(i in pairs for i in ids):
                    d[field] = list(dict.fromkeys(pairs.get(i, i) for i in ids))
                    changed = True
            if changed:
                c.execute("UPDATE faxes SET doc=? WHERE fax_key=?", (json.dumps(d, default=str), key))


def run_detection(db_path: str, run_id: str | None = None, *, skip_activity: bool = False) -> dict:
    """One analysis of the store at db_path, then the provider tables, unless the self-regulation left them for a calm
    cycle (skip_activity: remitwatch.selfcheck's plan sheds them; activity.stale keeps the note that they are behind)."""
    import gc

    from .activity import mark_pending, refresh
    store = Store(db_path)
    try:
        det = Detector(store, run_id)
        summary = det.run()
        del det                 # the provider tables are built after the detector has given its memory back
        gc.collect()
        if skip_activity:
            mark_pending(store.conn)
            store.conn.commit()
        else:
            refresh(store, log=lambda *_a: None)
        return summary
    finally:
        store.close()
