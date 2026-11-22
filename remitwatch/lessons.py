"""
Suggested templates for next time.

Each time Claude reviews a visit note for an appeal and finds it short (it says not to appeal, or the note supports the
code only in part), the review is kept as a lesson (remitwatch.notes.lesson): the payer, the code, the clinician, what
the note was missing and what to document next time. Nothing names the patient.

Here the lessons are added up by code, and each code gets the elements its documentation has to carry (from the CPT
and CMS rules the reviews apply) next to what the notes actually lacked. On request Claude writes the template itself:
the fields the EMA template should make required, the prompt each field gives the clinician, and the line the clinician
signs. The template asks the clinician to record what was done; it never writes clinical content for them.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .codes import EM_CODES

# What each kind of service's note must carry to be paid as billed (AMA CPT guidelines, the CMS NCCI policy manual).
ELEMENTS: dict[str, dict] = {
    "em": {"label": "Office visits (99202 to 99215)", "fields": [
        ("Problems addressed", "Each problem evaluated or treated today and its status: new with an uncertain prognosis, chronic and stable "
                               "(at the treatment goal), chronic and worsening or not at goal, or acute with systemic symptoms."),
        ("Data reviewed and analyzed", "Outside notes reviewed (whose), each test ordered or reviewed by name, an independent historian and why, "
                                       "a test read independently, a discussion of management with another physician."),
        ("Risk of management", "Prescription drugs started, changed or continued with monitoring; a decision for or against minor or major "
                               "surgery and the patient's risk factors; social factors that limit treatment."),
        ("Total time (when time sets the level)", "Minutes spent on the date of the visit and on what, leaving out separately billed procedures."),
    ]},
    "mod25": {"label": "A visit on the same day as a procedure (modifier 25)", "fields": [
        ("A separate problem", "A problem other than the one the procedure treats, or work well past the procedure's usual pre-service work, "
                               "with its own assessment and plan."),
        ("The procedure decision kept apart", "The decision to do a minor procedure belongs to the procedure: the note says what else was "
                                              "evaluated and managed at the visit."),
    ]},
    "mohs": {"label": "Mohs surgery (17311 to 17315)", "fields": [
        ("Stages and blocks", "Each stage, and the number of tissue blocks in it."),
        ("Map and slides", "The map, and the surgeon's own reading of each stage's slides."),
        ("Indication", "Site, size and histology, and the appropriate-use criteria the tumor meets."),
    ]},
    "repair": {"label": "Repairs (12001 to 13160)", "fields": [
        ("Length", "Each repair's length in centimeters (summed by anatomic group and by complexity)."),
        ("Complexity", "What makes it intermediate or complex: layered closure, undermining and how far, debridement, retention sutures."),
    ]},
    "flap": {"label": "Flaps and grafts (14000 to 15770)", "fields": [
        ("Defect size", "The defect's size in square centimeters, primary and secondary defects."),
        ("Design", "The flap or graft type and why a simpler repair would not do."),
    ]},
    "biopsy": {"label": "Biopsies (11102 to 11107)", "fields": [
        ("Method", "Tangential, punch or incisional, for each lesion."),
        ("Each lesion", "Each lesion's site, listed separately."),
    ]},
    "destruction": {"label": "Destruction (17000 to 17286)", "fields": [
        ("Count", "The number of lesions destroyed, by type (premalignant, benign, malignant)."),
        ("Method and sites", "The method (cryotherapy, curettage, chemical) and each site."),
    ]},
    "excision": {"label": "Excisions (11400 to 11646)", "fields": [
        ("Excised diameter", "The lesion's diameter plus the narrowest margins, in centimeters."),
        ("Final pathology", "Benign or malignant, from the pathology report."),
    ]},
    "necessity": {"label": "Medical necessity", "fields": [
        ("Indication", "The diagnosis, and the symptoms or findings that make the service necessary."),
        ("The payer's criteria", "The coverage criteria the service meets, in the payer's own words (its policy or the LCD)."),
    ]},
    "other": {"label": "Other services", "fields": [
        ("What was done", "What was done, where, how much, and why, so the code billed is the only one that fits."),
    ]},
}


def family(code: str | None, signal: str | None) -> str:
    c = (code or "").strip()
    if signal in ("EM_BUNDLED_MOD25", "EM_BUNDLED_MOD57_MAJOR", "EM_BUNDLED_MOD24"):
        return "mod25"
    if signal == "DENIED_MEDICAL_NECESSITY":
        return "necessity"
    if c in EM_CODES:
        return "em"
    if c.startswith("1731"):
        return "mohs"
    if re.fullmatch(r"120\d\d|1210\d|1300\d|131\d\d", c):
        return "repair"
    if re.fullmatch(r"14\d{3}|15\d{3}", c):
        return "flap"
    if c.startswith("1110"):
        return "biopsy"
    if re.fullmatch(r"170\d\d|171\d\d|172\d\d", c):
        return "destruction"
    if re.fullmatch(r"114\d\d|116\d\d", c):
        return "excision"
    return "other"


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()[:90]


def _top(texts: list[str], k: int = 8) -> list[str]:
    """The most frequent of a list of short sentences (the same point said the same way counts once more), newest first
    among equals."""
    seen: dict[str, list] = {}
    for i, t in enumerate(texts):
        if t and t.strip():
            seen.setdefault(_norm(t), [t.strip(), 0, i])[1] += 1
    return [v[0] for v in sorted(seen.values(), key=lambda v: (-v[1], v[2]))[:k]]


def summarize(conn: sqlite3.Connection, with_providers: bool = True) -> dict:
    """The lessons by code, the largest dollars first: how many notes fell short and for how much, the payers and (for
    the owner) the clinicians, what was missing, what to document, and the elements the code's notes must carry."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "note_lessons" not in have:
        return {"codes": [], "n": 0, "cents": 0}
    conn.row_factory = sqlite3.Row
    groups: dict[str, dict] = {}
    seen: set[str] = set()
    for r in conn.execute("SELECT * FROM note_lessons ORDER BY created_at DESC, lesson_key DESC"):
        # an item's packet built again (or from another note of that day) is the same note falling short once: its newest
        # review counts, not one per packet
        if r["finding_id"]:
            if r["finding_id"] in seen:
                continue
            seen.add(r["finding_id"])
        fam = family(r["code"], r["signal"])
        key = f"{fam}:{r['code'] or ''}" if fam != "mod25" else "mod25"
        g = groups.setdefault(key, {"key": key, "family": fam, "label": ELEMENTS[fam]["label"], "code": r["code"] if fam != "mod25" else None,
                                    "n": 0, "lost_n": 0, "cents": 0, "lost_cents": 0, "payers": {}, "providers": {}, "gaps": [], "tips": [],
                                    "last": r["created_at"]})
        lost = r["recommendation"] == "do_not_appeal" or r["supports"] == "no"
        g["n"] += 1
        g["cents"] += r["amount_cents"] or 0
        g["lost_n"] += lost
        g["lost_cents"] += (r["amount_cents"] or 0) if lost else 0
        if r["payer_group"]:
            g["payers"][r["payer_group"]] = g["payers"].get(r["payer_group"], 0) + 1
        if with_providers and (r["provider"] or r["npi"]):
            who = r["provider"] or r["npi"]
            g["providers"][who] = g["providers"].get(who, 0) + 1
        g["gaps"] += json.loads(r["gaps"] or "[]")
        g["tips"] += json.loads(r["tips"] or "[]")
    out = []
    for g in groups.values():
        g["gaps"], g["tips"] = _top(g["gaps"]), _top(g["tips"])
        g["payers"] = [{"payer_group": k, "n": v} for k, v in sorted(g["payers"].items(), key=lambda x: -x[1])]
        g["providers"] = [{"name": k, "n": v} for k, v in sorted(g["providers"].items(), key=lambda x: -x[1])] if with_providers else []
        g["elements"] = [{"field": a, "what": b} for a, b in ELEMENTS[g["family"]]["fields"]]
        out.append(g)
    out.sort(key=lambda g: (-g["lost_cents"], -g["cents"]))
    return {"codes": out, "n": sum(g["n"] for g in out), "cents": sum(g["cents"] for g in out),
            "lost_n": sum(g["lost_n"] for g in out), "lost_cents": sum(g["lost_cents"] for g in out)}


TEMPLATE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "fields", "attestation", "notes"],
    "properties": {
        "name": {"type": "string"},
        "fields": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["label", "required", "prompt", "why"],
                                              "properties": {"label": {"type": "string"}, "required": {"type": "boolean"},
                                                             "prompt": {"type": "string"}, "why": {"type": "string"}}}},
        "attestation": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}
TEMPLATE_RUBRIC = """You design note templates for a dermatology practice's electronic health record (ModMed EMA). A template is a
set of fields the clinician fills in before signing; required fields block signing until they are filled.

You are given a kind of service, the elements its documentation must carry under the AMA CPT guidelines and CMS policy, and
what reviews of real notes found missing when payers denied or cut the claim. Write the template that makes those gaps
impossible to leave: each field, whether it is required, the prompt the clinician sees (a question about what they did,
never a sentence for them to accept), and why the payer needs it.

Rules:
1. Never write clinical content for the clinician. The fields ask what was done; the clinician records it.
2. A field must ask for something the clinician did or decided at the visit. Nothing that would invite documenting work
   that was not done.
3. Keep it short: the fewest fields that close every gap listed. Plain words a medical assistant can build in EMA.
4. The attestation is one sentence the clinician signs: that the note records the work they personally performed.
5. In notes, list anything the practice should set up outside the template (a payer policy to keep on file, a front-desk step)."""


def write_template(group: dict, client: Any = None) -> dict:
    """Claude's suggested template for one code's lessons (summarize's group). Only the code, the kind of service, the
    payers' names and the review's gaps and tips go to the model: nothing about a patient."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    from .notes import MODEL
    ask = {"service": group.get("label"), "code": group.get("code"), "elements": group.get("elements"),
           "payers": [p["payer_group"] for p in group.get("payers") or []][:8],
           "missing_in_notes": (group.get("gaps") or [])[:12], "reviewers_advice": (group.get("tips") or [])[:12]}
    with client.messages.stream(model=MODEL, max_tokens=6000, system=TEMPLATE_RUBRIC,
                                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": TEMPLATE_SCHEMA}},
                                messages=[{"role": "user", "content": "Write the template.\n\n" + json.dumps(ask, indent=1)}]) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("The model declined to write this template.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("The template was cut off before it was finished.")
    from .notes import schema_problems
    out = json.loads(next(b.text for b in message.content if b.type == "text"))
    bad = schema_problems(out, TEMPLATE_SCHEMA)
    if bad:
        raise RuntimeError(f"The template was not in the expected form ({bad[0]}).")
    return out
