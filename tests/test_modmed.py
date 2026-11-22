"""
Tests for the ModMed EMA connector (remitwatch.modmed) and the routes built on it, against a stand-in for ModMed's
sign-in server and FHIR R4 API (tests/fake_modmed.py) running on 127.0.0.1: the signed sign-in and its fallbacks, the
token's reuse and renewal, finding a claim's visit note (and never another patient's), the forms a note comes in, paging,
an export-only app, the routes, the setup check and the CLI. Every patient, note, clinician and amount is made up.
"""
from __future__ import annotations

import base64
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from remitwatch import modmed as M  # noqa: E402
from fake_modmed import FakeModMed  # noqa: E402

PEM = M.generate_key()
DOS = "2026-09-14"
NPI_A, NPI_B = "1111111111", "2222222222"


@pytest.fixture
def fake():
    f = FakeModMed(jwk=M.public_jwk(M.load_private_key(PEM))).start()
    yield f
    f.stop()


def settings(f: FakeModMed, **kw) -> M.Settings:
    return M.Settings(**{"client_id": f.client_id, "private_key": PEM, "fhir_base": f.fhir_base, **kw})


def chart(f: FakeModMed) -> None:
    """Joan Rivers (EMA patient p1) seen on DOS by clinician A: her signed visit note tied to the visit, a procedure note
    the same day by clinician B, an unsigned draft and a note entered in error; Jon Rivers (p2), another patient."""
    f.add_patient("p1", "Rivers", "Joan", ("MRN100",))
    f.add_patient("p2", "Rivers", "Jon")
    f.add_practitioner("pr1", NPI_A, "Clinician A")
    f.add_practitioner("pr2", NPI_B, "Clinician B")
    f.add_encounter("e1", "p1", DOS)
    f.add_note("d1", "p1", "2026-09-15", b"%PDF-1.4 visit note", encounter="e1", author="pr1")
    f.add_note("d2", "p1", DOS, b"<html><body><p>Mohs procedure note</p></body></html>", "text/html", period=DOS, loinc="28570-0", author="pr2")
    f.add_note("d3", "p1", DOS, b"draft", "text/plain", period=DOS, doc_status="preliminary")
    f.add_note("d4", "p1", DOS, b"wrong chart", "text/plain", encounter="e1", status="entered-in-error")


def key(**kw) -> M.ClaimKey:
    return M.ClaimKey(**{"family": "RIVERS", "given": "JOAN", "dos": DOS, **kw})


# --------------------------------------------------------------------------- keys and sign-in
def test_keys_and_the_published_key_set():
    k = M.load_private_key(PEM)
    jwk = M.public_jwk(k)
    assert jwk["kty"] == "EC" and jwk["crv"] == "P-384" and jwk["alg"] == "ES384" and "d" not in jwk
    assert jwk["kid"] == M.thumbprint(jwk) and len(base64.urlsafe_b64decode(jwk["x"] + "==")) == 48
    rsa = M.load_private_key(M.generate_key("RSA"))
    rj = M.public_jwk(rsa)
    assert rj["kty"] == "RSA" and rj["alg"] == "RS384" and "d" not in rj and "p" not in rj
    # the key as people paste it into a host's settings: with \n written out, or the whole PEM base64-encoded
    for raw in (PEM.replace("\n", "\\n"), base64.b64encode(PEM.encode()).decode()):
        s = M.Settings.from_env({"MODMED_CLIENT_ID": "x", "MODMED_PRIVATE_KEY": raw})
        assert s.configured() and M.public_jwk(M.load_private_key(s.private_key))["kid"] == jwk["kid"]
    doc = M.jwks(M.Settings(client_id="x", private_key=PEM, key_id="my-kid"))
    assert doc == {"keys": [dict(jwk, kid="my-kid")]}
    assert M.jwks(M.Settings(client_id="x", client_secret="s")) is None
    with pytest.raises(M.ModMedError, match="not a readable private key"):
        M.load_private_key("-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----")
    assert not M.Settings.from_env({"MODMED_CLIENT_ID": "x"}).configured()


def test_sign_in_check_and_one_token_for_many_calls(fake):
    chart(fake)
    out = M.check(settings(fake))
    assert out["ok"] and out["mode"] == "search", out
    steps = {s["step"]: s for s in out["steps"]}
    assert steps["Discovery"]["ok"] and fake.token_url in steps["Discovery"]["detail"]
    assert steps["Sign-in"]["ok"] and "system/*.read" in steps["Sign-in"]["detail"] and steps["Server"]["ok"]
    # the check asks for a count, never a patient's record
    pq = [r for r in fake.requests if r["path"] == "/fhir/r4/Patient"]
    assert pq and pq[0]["query"].get("_summary") == ["count"]
    mm = M.ModMed(settings(fake))
    for _ in range(3):
        mm.metadata()
    assert sum(1 for r in fake.requests if r["path"] == "/token") == 2      # one for the check, one for the three calls
    assert all(r["auth"] and r["auth"].startswith("Bearer ") for r in fake.requests if r["path"].startswith("/fhir/r4/") and "well-known" not in r["path"])


def test_the_assertion_audience_falls_back_to_the_fhir_base(fake):
    fake.aud = "base"                 # a server that wants the FHIR base as the assertion's audience
    mm = M.ModMed(settings(fake))
    mm.token()
    assert mm._aud == fake.fhir_base and sum(1 for r in fake.requests if r["path"] == "/token") == 2
    mm.token(force=True)              # the one that worked is tried first from then on
    assert sum(1 for r in fake.requests if r["path"] == "/token") == 3
    with pytest.raises(M.ModMedError, match="refused the app's key"):
        M.ModMed(settings(fake, client_id="someone-else")).token()


def test_client_secret_and_api_key():
    f = FakeModMed(secret="s3cret", api_key="k-123").start()
    try:
        chart(f)
        s = M.Settings(client_id=f.client_id, client_secret="s3cret", api_key="k-123", fhir_base=f.fhir_base, token_url=f.token_url)
        assert M.check(s)["ok"]
        assert all(r["api_key"] == "k-123" for r in f.requests if "well-known" not in r["path"])
        with pytest.raises(M.ModMedError, match="MODMED_CLIENT_SECRET"):
            M.ModMed(M.Settings(client_id=f.client_id, client_secret="wrong", api_key="k-123", fhir_base=f.fhir_base, token_url=f.token_url)).token()
    finally:
        f.stop()


def test_a_refused_token_is_renewed_once(fake):
    chart(fake)
    mm = M.ModMed(settings(fake))
    mm.metadata()
    fake.revoked = True               # the server drops every token it gave
    assert mm.metadata()["fhirVersion"] == "4.0.1"
    assert sum(1 for r in fake.requests if r["path"] == "/token") == 2


def test_no_discovery_document_uses_the_token_url_given(fake):
    fake.discovery = False
    out = M.check(settings(fake, token_url=fake.token_url))
    assert out["ok"] and {s["step"]: s for s in out["steps"]}["Discovery"]["ok"] is None


# --------------------------------------------------------------------------- the note for a claim
def test_the_visit_note_by_ema_patient_id(fake):
    chart(fake)
    r = M.note_for_claim(M.ModMed(settings(fake)), key(npi=NPI_A, ema_patient_ids=["p1"]))
    assert r["status"] == "found" and r["found_by"] == "EMA patient ID"
    n = r["note"]
    # the note tied to the day's visit; the draft and the note entered in error are never used
    assert n["id"] == "d1" and n["tie"] == "encounter" and n["content_type"] == "application/pdf" and n["data"] == b"%PDF-1.4 visit note"
    assert n["filename"] == f"ema-note-{DOS}-d1.pdf" and n["type"] == "Progress note" and n["author"] == "Clinician A"
    assert [o["id"] for o in r["others"]] == ["d2"]          # the procedure note that day, one click away


def test_a_note_picked_by_hand_and_only_one_of_the_claims(fake):
    chart(fake)
    mm = M.ModMed(settings(fake))
    r = M.note_for_claim(mm, key(ema_patient_ids=["p1"]), pick="d2")
    assert r["status"] == "found" and r["note"]["id"] == "d2" and r["note"]["content_type"] == "text/html" and r["note"]["filename"].endswith(".html")
    other = M.note_for_claim(mm, key(ema_patient_ids=["p1"]), pick="d3")      # the unsigned draft is not one of them
    assert other["status"] == "ambiguous" and "not one of this claim's" in other["reason"]


def test_never_another_patients_chart(fake):
    chart(fake)
    fake.add_patient("p9", "Ortega", "Joan")
    fake.add_encounter("e9", "p9", DOS)
    fake.add_note("d9", "p9", DOS, b"another patient", "text/plain", encounter="e9")
    mm = M.ModMed(settings(fake))
    # an EMA patient ID that points at someone with another family name is not taken. The name finds Joan Rivers, but a
    # name alone never sends a note: a person confirms it
    r = M.note_for_claim(mm, key(ema_patient_ids=["p9"]))
    assert r["status"] == "ambiguous" and r["found_by"] == "name" and {c["id"] for c in r["candidates"]} == {"d1", "d2"}
    assert "d9" not in json.dumps(r) and "Only the name agrees" in r["reason"] and "did not lead to" in r["reason"]
    assert M.note_for_claim(mm, key(ema_patient_ids=["p9"]), pick="d1")["note"]["id"] == "d1"        # the person's pick
    assert M.note_for_claim(mm, M.ClaimKey(family="NOBODY", given="X", dos=DOS))["status"] == "not_found"
    # the medical record number the payer echoed, and the full name: two identifiers agree
    r = M.note_for_claim(mm, key(mrn="MRN100"))
    assert r["status"] == "found" and r["found_by"] == "medical record number" and r["identified_by"] == ["medical record number", "name"]


def test_two_patients_with_one_name(fake):
    fake.add_patient("a1", "Smith", "Ann", birth_date="1950-01-01")
    fake.add_patient("a2", "Smith", "Ann", birth_date="1990-02-02")
    fake.add_encounter("ea", "a1", DOS)
    fake.add_note("na", "a1", DOS, b"note a", "text/plain", encounter="ea")
    mm = M.ModMed(settings(fake))
    k = M.ClaimKey(family="SMITH", given="ANN", dos=DOS)
    # only one of them was seen that day, but the name cannot say she is the claim's patient: two Ann Smiths fit. With
    # names off nothing on the page could tell them apart, so the note is attached by hand; with names on, the person
    # sees who she is
    off = M.note_for_claim(mm, k)
    assert off["status"] == "ambiguous" and off["candidates"] == [] and "attach it to the item" in off["reason"]
    on = M.note_for_claim(mm, k, reveal=True)
    assert on["status"] == "ambiguous" and [c["id"] for c in on["candidates"]] == ["na"]
    assert on["candidates"][0]["patient"] == {"name": "Ann Smith", "birth_date": "1950-01-01", "ids": []}
    fake.add_encounter("eb", "a2", DOS)
    fake.add_note("nb", "a2", DOS, b"note b", "text/plain", encounter="eb")
    r = M.note_for_claim(mm, k, reveal=True)
    assert r["status"] == "ambiguous" and {c["id"] for c in r["candidates"]} == {"na", "nb"}
    assert {c["patient"]["birth_date"] for c in r["candidates"]} == {"1950-01-01", "1990-02-02"}       # told apart
    assert {c["patient_n"] for c in r["candidates"]} == {1, 2}
    assert M.note_for_claim(mm, k, pick="nb", reveal=True)["note"]["data"] == b"note b"
    # with names off, a pick between the two is refused: the rows would look the same
    refused = M.note_for_claim(mm, k, pick="nb")
    assert refused["status"] == "ambiguous" and "note" not in refused and refused["candidates"] == []
    assert "patient" not in json.dumps(M.note_for_claim(mm, k)["candidates"])


def test_several_notes_that_day_by_npi_then_kind(fake):
    fake.add_patient("p1", "Rivers", "Joan")
    fake.add_practitioner("pr1", NPI_A, "Clinician A")
    fake.add_practitioner("pr2", NPI_B, "Clinician B")
    fake.add_encounter("e1", "p1", DOS)
    fake.add_note("v1", "p1", DOS, b"A's note", "text/plain", encounter="e1", author="pr1", loinc="28570-0")
    fake.add_note("v2", "p1", DOS, b"B's note", "text/plain", encounter="e1", author="pr2")
    mm = M.ModMed(settings(fake))
    ids = ["p1"]                    # EMA's patient ID on the claim's EMA rows, and the name: the patient is identified
    assert M.note_for_claim(mm, key(npi=NPI_A, ema_patient_ids=ids))["note"]["id"] == "v1"        # the rendering clinician's
    assert M.note_for_claim(mm, key(ema_patient_ids=ids))["note"]["id"] == "v2"         # no NPI: the progress note first
    fake.add_note("v3", "p1", DOS, b"another progress note", "text/plain", encounter="e1", author="pr2")
    r = M.note_for_claim(mm, key(ema_patient_ids=ids))
    assert r["status"] == "ambiguous" and {c["id"] for c in r["candidates"]} == {"v1", "v2", "v3"}      # one patient: a pick is fine


def test_the_forms_a_note_comes_in(fake):
    other = FakeModMed(jwk=fake.jwk, open_binaries=True).start()
    try:
        fake.add_patient("p1", "Rivers", "Joan")
        fake.add_encounter("e1", "p1", DOS)
        other.binaries["far"] = ("text/plain", b"stored elsewhere")
        fake.add_note("in", "p1", DOS, b"inline text", "text/plain", encounter="e1", inline=True)
        mm = M.ModMed(settings(fake))
        k = key(ema_patient_ids=["p1"])
        assert M.note_for_claim(mm, k)["note"]["data"] == b"inline text"
        fake.docs.clear()
        fake.add_note("far", "p1", DOS, b"", "text/plain", encounter="e1", url=f"{other.fhir_base}/Binary/far")
        got = M.note_for_claim(mm, k)
        assert got["status"] == "found"
        # the token is never handed to another host, even one that answers for the note
        assert [r["auth"] for r in other.requests] == [None]
        fake.docs.clear()
        fake.binary_as_json = True
        fake.add_note("js", "p1", DOS, b"%PDF-1.4 wrapped", "application/pdf", encounter="e1")
        n = M.note_for_claim(mm, k)["note"]
        assert n["data"] == b"%PDF-1.4 wrapped" and n["content_type"] == "application/pdf"
    finally:
        other.stop()


def test_a_server_that_refuses_a_parameter_and_pages(fake):
    fake.reject_category = True
    fake.page_size = 1
    chart(fake)
    for i in range(3):
        fake.add_note(f"old{i}", "p1", "2026-01-0" + str(i + 1), b"old", "text/plain", period="2026-01-01")
    r = M.note_for_claim(M.ModMed(settings(fake)), key(npi=NPI_A, ema_patient_ids=["p1"]))
    assert r["status"] == "found" and r["note"]["id"] == "d1"
    dq = [r for r in fake.requests if r["path"] == "/fhir/r4/DocumentReference"]
    assert "category" in dq[0]["query"] and "category" not in dq[1]["query"]          # refused, then the simpler search
    assert any("_page" in r["query"] for r in dq)                                     # the next links were followed


def test_an_export_only_app(fake):
    fake.search_forbidden = True
    chart(fake)
    s = settings(fake, group_id="g1")
    out = M.check(s)
    assert out["ok"] and out["mode"] == "export"
    M._EXPORTS.clear()
    mm = M.ModMed(s, sleep=lambda _: None)
    r = M.note_for_claim(mm, key(npi=NPI_A, ema_patient_ids=["p1"]), since="2026-09-01")
    assert r["status"] == "found" and r["source"] == "export" and r["note"]["id"] == "d1"
    kicks = [r for r in fake.requests if r["path"].endswith("$export")]
    assert len(kicks) == 2 and all(k["prefer"] == "respond-async" and k["path"] == "/fhir/r4/Group/g1/$export" for k in kicks)
    assert kicks[1]["query"]["_since"] == ["2026-08-25T00:00:00Z"]          # a week before the batch's first date of service
    # a second claim in the same batch reuses the export
    M.note_for_claim(mm, key(ema_patient_ids=["p1"]), since="2026-09-01", pick="d2")
    assert len([r for r in fake.requests if r["path"].endswith("$export")]) == 2


def test_nothing_to_look_up():
    assert M.find_note(None, M.ClaimKey(family="", given="", dos=DOS))["status"] == "not_found"
    with pytest.raises(M.NotConfigured):
        M.ModMed(M.Settings())


# --------------------------------------------------------------------------- whose note: what a name alone can never do
def _twins(f: FakeModMed, fam_a: str, giv_a: str, fam_b: str, giv_b: str) -> None:
    """Patient A (the claim's) seen on DOS, her note not signed yet; patient B seen the same day, his or hers signed."""
    f.add_patient("pa", fam_a, giv_a, birth_date="1950-01-01")
    f.add_patient("pb", fam_b, giv_b, birth_date="1990-02-02")
    f.add_encounter("ea", "pa", DOS)
    f.add_encounter("eb", "pb", DOS)
    f.add_note("na", "pa", DOS, b"A's draft", "text/plain", encounter="ea", doc_status="preliminary")
    f.add_note("nb", "pb", DOS, b"B's signed note", "text/plain", encounter="eb")


@pytest.mark.parametrize("case, b, claim, want", [
    ("a first name that starts the other's", ("Smith", "Ann", "Smith", "Annette"), ("SMITH", "ANN"), "by hand"),
    ("two patients of one name", ("Smith", "Ann", "Smith", "Ann"), ("SMITH", "ANN"), "by hand"),
    ("a family name that starts the other's", ("Hall", "Ann", "Halloran", "Ann"), ("HALL", "ANN"), "not_found"),
    ("a family name inside the other", ("Hall", "Ann", "Marshall", "Ann"), ("HALL", "ANN"), "not_found"),
    ("an initial for a first name", ("Smith", "J", "Smith", "John"), ("SMITH", "J"), "not_found"),
    ("no first name on the claim", ("Smith", "Ann", "Smith", "Zoe"), ("SMITH", ""), "not_found"),
])
@pytest.mark.parametrize("export_only", [False, True])
def test_a_name_alone_never_takes_another_patients_note(case, b, claim, want, export_only):
    """The claim's patient has no signed note that day and another patient with a like name has one: the other note is
    never taken (it was, by name alone, before), and with names off it is not even offered."""
    f = FakeModMed(jwk=M.public_jwk(M.load_private_key(PEM)), search_forbidden=export_only).start()
    try:
        _twins(f, *b)
        M._EXPORTS.clear()
        M._PENDING.clear()
        mm = M.ModMed(settings(f, group_id="g1" if export_only else ""), sleep=lambda _: None)
        k = M.ClaimKey(claim[0], claim[1], DOS)
        r = M.note_for_claim(mm, k, since=DOS)
        assert r["status"] != "found" and "note" not in r, (case, r)
        if want == "by hand":
            assert r["status"] == "ambiguous" and r["candidates"] == [] and "attach it to the item" in r["reason"], case
            # names on: the other patient is shown as who he or she is, so a person can see it is not the claim's
            on = M.note_for_claim(mm, k, since=DOS, reveal=True)
            assert on["status"] == "ambiguous" and [c["patient"]["birth_date"] for c in on["candidates"]] == ["1990-02-02"], case
        else:
            assert r["status"] == "not_found", (case, r)
    finally:
        f.stop()


def test_an_ema_patient_id_that_finds_nobody_leaves_the_name_to_a_person(fake):
    """The claim's EMA patient ID is not one EMA's API knows: the name finds two Ann Smiths, and nothing is sent."""
    _twins(fake, "Smith", "Ann", "Smith", "Ann")
    mm = M.ModMed(settings(fake))
    r = M.note_for_claim(mm, M.ClaimKey("SMITH", "ANN", DOS, ema_patient_ids=["EMA-77"]))
    assert r["status"] == "ambiguous" and r["found_by"] == "name" and r["candidates"] == []
    # the same ID as an identifier of patient A: A is identified (EMA's patient ID and the name), and A has no signed note
    fake.patients["pa"]["identifier"] = [{"system": "urn:ema:mrn", "value": "EMA-77"}]
    r = M.note_for_claim(mm, M.ClaimKey("SMITH", "ANN", DOS, ema_patient_ids=["EMA-77"]))
    assert r["status"] == "not_found" and r["found_by"] == "EMA patient ID"


def test_more_than_one_patient_is_counted_before_anything_is_narrowed(fake):
    """Two Ann Smiths, one note tied to the visit and one by its clinical date: the better tie no longer hides the other
    patient. Six Maria Garcias: every one is looked at, not the first five. A search cut short is never taken as whole."""
    fake.add_patient("pa", "Smith", "Ann")
    fake.add_patient("pb", "Smith", "Ann")
    fake.add_encounter("ea", "pa", DOS)
    fake.add_note("na", "pa", DOS, b"A's note", "text/plain", encounter="ea")
    fake.add_note("nb", "pb", DOS, b"B's note", "text/plain", period=DOS)
    mm = M.ModMed(settings(fake))
    k = M.ClaimKey("SMITH", "ANN", DOS)
    assert M.note_for_claim(mm, k)["candidates"] == []
    assert {c["id"] for c in M.note_for_claim(mm, k, reveal=True)["candidates"]} == {"na", "nb"}
    # identified by EMA's patient ID as well as the name: patient A's note, whatever the other Ann Smith has
    r = M.note_for_claim(mm, M.ClaimKey("SMITH", "ANN", DOS, ema_patient_ids=["pa"]))
    assert r["status"] == "found" and r["note"]["id"] == "na" and r["identified_by"] == ["EMA patient ID", "name"]
    for i in range(1, 7):
        fake.add_patient(f"g{i}", "Garcia", "Maria")
    for i in (1, 6):
        fake.add_encounter(f"e{i}", f"g{i}", DOS)
        fake.add_note(f"n{i}", f"g{i}", DOS, f"note {i}".encode(), "text/plain", encounter=f"e{i}")
    g = M.ClaimKey("GARCIA", "MARIA", DOS)
    assert {c["id"] for c in M.note_for_claim(mm, g, reveal=True)["candidates"]} == {"n1", "n6"}
    fake.page_size = 1                  # the name search stops at three pages: six patients are not all seen
    cut = M.note_for_claim(mm, g, reveal=True)
    assert cut["status"] == "ambiguous" and cut["candidates"] == [] and "cannot all be looked through" in cut["reason"]


def test_a_search_says_when_it_stopped_early(fake):
    fake.page_size = 1
    for i in range(4):
        fake.add_patient(f"s{i}", "Smith", "Ann")
    mm = M.ModMed(settings(fake))
    got = mm.search("Patient", {"family": "Smith"}, max_pages=2)
    assert len(got) == 2 and got.truncated
    assert sum(1 for r in fake.requests if r["path"] == "/fhir/r4/Patient") == 2         # the page after the last is not fetched
    whole = mm.search("Patient", {"family": "Smith"}, max_pages=5)
    assert len(whole) == 4 and not whole.truncated


def test_a_later_visits_note_is_never_taken_for_an_earlier_one(fake):
    """The visit's own note is a draft; a note written two weeks later names no visit. It may be the follow-up visit's,
    so only a person can choose it. A later note tied to its own visit is not offered at all."""
    fake.add_patient("p1", "Rivers", "Joan")
    fake.add_encounter("e1", "p1", DOS)
    fake.add_note("draft", "p1", DOS, b"draft", "text/plain", encounter="e1", doc_status="preliminary")
    fake.add_note("later", "p1", "2026-09-28", b"follow-up note", "text/plain")
    mm = M.ModMed(settings(fake))
    k = key(ema_patient_ids=["p1"])
    r = M.note_for_claim(mm, k)
    assert r["status"] == "ambiguous" and [(c["id"], c["tie"], c["pick_only"]) for c in r["candidates"]] == [("later", "after", True)]
    assert M.note_for_claim(mm, k, pick="later")["note"]["id"] == "later"            # a person's choice
    fake.docs.pop("draft")
    assert M.note_for_claim(mm, k)["status"] == "ambiguous"                         # no note of the day at all: the same
    fake.add_encounter("e2", "p1", "2026-09-28")
    fake.docs["later"]["context"] = {"encounter": [{"reference": "Encounter/e2"}]}
    assert M.note_for_claim(mm, k)["status"] == "not_found"                          # the later visit's own note
    fake.add_note("sameday", "p1", DOS, b"written that day", "text/plain")           # no link, but written on the day
    r = M.note_for_claim(mm, k)
    assert r["status"] == "found" and r["note"]["id"] == "sameday" and r["note"]["tie"] == "date"


def test_a_document_that_is_not_a_visit_note_is_left_to_a_person(fake):
    """A photo, a consent form or a file that does not say what it is, tied to the day: never taken by itself, and a
    picture is never labelled as text."""
    fake.add_patient("p1", "Rivers", "Joan")
    fake.add_encounter("e1", "p1", DOS)
    fake.add_note("photo", "p1", DOS, b"\xff\xd8\xff\xe0 a picture", "image/jpeg", encounter="e1", loinc="72170-4", category=None)
    mm = M.ModMed(settings(fake))
    k = key(ema_patient_ids=["p1"])
    r = M.note_for_claim(mm, k)            # no clinical note in the window: the search without a category finds the photo
    assert r["status"] == "ambiguous" and [(c["id"], c["pick_only"]) for c in r["candidates"]] == [("photo", True)]
    picked = M.note_for_claim(mm, k, pick="photo")["note"]
    assert picked["content_type"] == "image/jpeg" and picked["filename"].endswith(".jpg")
    fake.add_note("consent", "p1", DOS, b"%PDF-1.4 consent", "application/pdf", encounter="e1", loinc="59284-0")
    fake.add_note("blob", "p1", DOS, b"%PDF-1.4 unknown", "application/octet-stream", encounter="e1")
    r = M.note_for_claim(mm, k)            # filed as clinical notes now, so the search by category finds these two
    assert r["status"] == "ambiguous" and {(c["id"], c["pick_only"]) for c in r["candidates"]} == {("consent", True), ("blob", True)}
    fake.add_note("visit", "p1", DOS, b"%PDF-1.4 the visit note", "application/pdf", encounter="e1")
    r = M.note_for_claim(mm, k)
    assert r["status"] == "found" and r["note"]["id"] == "visit" and {o["id"] for o in r["others"]} == {"consent", "blob"}
    # the same over an export (no category filter there)
    M._EXPORTS.clear()
    M._PENDING.clear()
    fake.search_forbidden = True
    fake.docs.pop("visit")
    r = M.note_for_claim(M.ModMed(settings(fake, group_id="g1"), sleep=lambda _: None), k, since=DOS)
    assert r["source"] == "export" and r["status"] == "ambiguous" and all(c["pick_only"] for c in r["candidates"])


def test_the_api_key_goes_only_to_modmeds_own_hosts():
    f = FakeModMed(secret="s3cret", api_key="k-123").start()
    other = FakeModMed(open_binaries=True).start()
    try:
        f.add_patient("p1", "Rivers", "Joan")
        f.add_encounter("e1", "p1", DOS)
        other.binaries["far"] = ("text/plain", b"stored elsewhere")
        f.add_note("far", "p1", DOS, b"", "text/plain", encounter="e1", url=f"{other.fhir_base}/Binary/far")
        s = M.Settings(client_id=f.client_id, client_secret="s3cret", api_key="k-123", fhir_base=f.fhir_base, token_url=f.token_url)
        r = M.note_for_claim(M.ModMed(s), key(ema_patient_ids=["p1"]))
        assert r["status"] == "found" and r["note"]["data"] == b"stored elsewhere"
        assert [(q["auth"], q["api_key"]) for q in other.requests] == [(None, None)]         # neither the token nor the key
        assert all(q["api_key"] == "k-123" for q in f.requests if "well-known" not in q["path"])
    finally:
        other.stop()
        f.stop()


# --------------------------------------------------------------------------- an export-only app: one budget, resumed
class _Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Resp:
    def __init__(self, status, body=b"", headers=None):
        self.status_code, self.content, self.headers = status, body if isinstance(body, bytes) else json.dumps(body).encode(), headers or {}
        self.text = self.content.decode()

    def json(self):
        return json.loads(self.content)


class _BulkOnly:
    """A stand-in for an export-only ModMed on a clock of its own: searches are refused, and each export is ready
    `ready_after` seconds after it starts (asked about every `retry` seconds)."""

    def __init__(self, clock, ready_after, retry=30, real_delay=0.0):
        self.clock, self.ready_after, self.retry, self.real_delay = clock, ready_after, retry, real_delay
        self.jobs: dict[str, tuple[float, str]] = {}
        self.lock = __import__("threading").Lock()

    def post(self, url, data=None, headers=None, timeout=None):
        return _Resp(200, {"access_token": "t", "expires_in": 3600})

    def get(self, url, params=None, headers=None, timeout=None):
        import time as _t
        if url.endswith("smart-configuration"):
            return _Resp(404, {})
        if url.endswith("$export"):
            with self.lock:
                jid = str(len(self.jobs) + 1)
                self.jobs[jid] = (self.clock(), (params or {}).get("_type"))
            return _Resp(202, b"", {"Content-Location": f"https://fhir.example/r4/bulkstatus/{jid}"})
        if "/bulkstatus/" in url:
            if self.real_delay:
                _t.sleep(self.real_delay)
            started, _ty = self.jobs[url.rsplit("/", 1)[1]]
            if self.clock() - started < self.ready_after:
                return _Resp(202, b"", {"Retry-After": str(self.retry)})
            return _Resp(200, {"output": [], "requiresAccessToken": True})
        return _Resp(403, {"resourceType": "OperationOutcome", "issue": [{"diagnostics": "search not allowed"}]})


def _bulk_mm(clock, session):
    s = M.Settings(client_id="c", client_secret="x", fhir_base="https://fhir.example/r4", token_url="https://fhir.example/token")
    return M.ModMed(s, session=session, clock=clock, sleep=clock.sleep)


def test_an_export_only_lookup_keeps_to_one_budget_and_picks_up_where_it_stopped(monkeypatch):
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    M._EXPORTS.clear()
    M._PENDING.clear()
    clock = _Clock()
    bulk = _BulkOnly(clock, ready_after=170)
    mm = _bulk_mm(clock, bulk)
    k = M.ClaimKey("SAMPLE", "PAT", "2026-10-01")
    t0 = clock()
    with pytest.raises(M.ModMedError, match="still running"):
        M.note_for_claim(mm, k, budget=200)
    assert clock() - t0 <= 200 + 1 and len(bulk.jobs) == 2        # both exports within one budget (was 340 s and more)
    # the next lookup asks about the export already started instead of starting another
    M.note_for_claim(mm, k, budget=200)
    assert len(bulk.jobs) == 2
    # an export ready at 190 s, asked about every 30 s, with 200 s to wait: asked once more as the wait ends
    M._EXPORTS.clear()
    M._PENDING.clear()
    late = _BulkOnly(clock, ready_after=190, retry=30)
    out = _bulk_mm(clock, late).export(["Patient"], None, budget=200)
    assert out == {} and len(late.jobs) == 1


def test_on_a_serverless_host_the_lookup_leaves_time_for_the_review(monkeypatch):
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_MAX_SECONDS", "240")
    M._EXPORTS.clear()
    M._PENDING.clear()
    clock = _Clock()
    mm = _bulk_mm(clock, _BulkOnly(clock, ready_after=10_000))
    t0 = clock()
    with pytest.raises(M.ModMedError):
        M.note_for_claim(mm, M.ClaimKey("SAMPLE", "PAT", "2026-10-01"), budget=200)
    assert clock() - t0 <= 240 - M.SERVERLESS_RESERVE + 1


def test_lookups_made_together_share_one_export(monkeypatch):
    import threading
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    M._EXPORTS.clear()
    M._PENDING.clear()
    clock = _Clock()
    bulk = _BulkOnly(clock, ready_after=0, real_delay=0.05)        # each status answer takes a moment, so the three overlap
    mm = _bulk_mm(clock, bulk)
    go = threading.Barrier(3)
    errors = []

    def one(n):
        go.wait()
        try:
            M.note_for_claim(mm, M.ClaimKey("SAMPLE", "PAT", "2026-10-0" + str(n)), budget=200, since="2026-10-01")
        except Exception as exc:          # pragma: no cover - shown if it happens
            errors.append(exc)
    ts = [threading.Thread(target=one, args=(n,)) for n in (1, 2, 3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert not errors and len(bulk.jobs) == 2                       # one export of each part, not three of each


# --------------------------------------------------------------------------- keys and secrets
def test_a_key_of_another_kind_is_refused_plainly(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519

    from remitwatch import cloud
    pkcs8 = lambda k: k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()  # noqa: E731
    for pem in (pkcs8(ed25519.Ed25519PrivateKey.generate()), pkcs8(ec.generate_private_key(ec.SECP256K1()))):
        with pytest.raises(M.ModMedError, match="must be an EC key"):
            M.load_private_key(pem)
        with pytest.raises(M.ModMedError, match="must be an EC key"):
            M.jwks(M.Settings(client_id="x", private_key=pem))
        steps = {s["step"]: s for s in M.check(M.Settings(client_id="x", private_key=pem))["steps"]}
        assert steps["Key"]["ok"] is False and "modmed-key" in steps["Key"]["detail"]
        monkeypatch.setenv("MODMED_PRIVATE_KEY", pem)
        monkeypatch.setenv("MODMED_CLIENT_ID", "x")
        chk = next(c for c in cloud.setup_status(probe=False)["checks"] if c["key"] == "modmed")      # the page still loads
        assert chk["ok"] is False


def test_settings_never_print_their_secrets():
    s = M.Settings(client_id="c", private_key="-----BEGIN PRIVATE KEY-----SECRETMATERIAL-----END PRIVATE KEY-----",
                   client_secret="topsecret", api_key="k-123")
    assert "SECRETMATERIAL" not in repr(s) and "topsecret" not in repr(s) and "k-123" not in repr(s) and "client_id='c'" in repr(s)


def test_the_published_key_is_worked_out_once_per_key(monkeypatch):
    calls = []
    real = M.load_private_key
    monkeypatch.setattr(M, "load_private_key", lambda pem: (calls.append(1), real(pem))[1])
    M._JWKS.clear()
    s = M.Settings(client_id="x", private_key=PEM)
    first = M.jwks(s)
    assert M.jwks(s) == first and M.jwks(s) == first and len(calls) == 1
    other = M.generate_key()
    assert M.jwks(M.Settings(client_id="x", private_key=other)) != first and len(calls) == 2


def test_the_setup_check_reads_no_patient_record(fake):
    """A server that refuses a count search is asked for one id and nothing else."""
    chart(fake)
    fake.reject_summary = True
    out = M.check(settings(fake))
    assert out["ok"] and out["mode"] == "search"
    pq = [r["query"] for r in fake.requests if r["path"] == "/fhir/r4/Patient"]
    assert pq[0].get("_summary") == ["count"] and pq[1].get("_elements") == ["id"] and "_summary" not in pq[1]
    assert "Rivers" not in json.dumps(out) and "Joan" not in json.dumps(out)


# --------------------------------------------------------------------------- the routes, the setup check and the CLI
def _store_with_a_note_appeal(tmp_path):
    """A real store from invented remittances with one denial that needs the visit note (medical necessity, CARC 50),
    its claim for patient SAMPLE PAT on 2026-11-01, still inside its window, and EMA's patient ID on its EMA row."""
    from remitwatch.detect import Detector
    from tests.test_features import NPI, build_store, clean_visits, svc
    denied = ("CLP*CB0000300C017*1*223.65*0.00*0.00*12*PC300*11~NM1*QC*1*SAMPLE*PAT*Q***MI*M00000300~"
              f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~REF*1W*MEMB999888~DTM*232*20261101~"
              + svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20261101"))
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [denied], pay_date="20261110")
    Detector(store, run_id="t").run()
    store.conn.execute("""INSERT INTO ema_rows (row_fingerprint, family, claim_id, patient_id, service_date, cpt)
                          VALUES ('fp1', 'charges', 'CB0000300C017', 'EMA77', '2026-11-01', '99214')""")
    store.conn.commit()
    store.close()
    return str(tmp_path / "s.sqlite"), NPI


def test_the_routes_build_a_packet_from_ema(tmp_path, fake, monkeypatch):
    from fastapi.testclient import TestClient

    import remitwatch.api as api_mod
    db, npi = _store_with_a_note_appeal(tmp_path)
    fake.add_patient("EMA77", "Sample", "Pat")
    fake.add_practitioner("prx", npi, "Test Provider")
    fake.add_encounter("ex", "EMA77", "2026-11-01")
    fake.add_note("nx", "EMA77", "2026-11-01", b"<html><body><p>Problem-focused visit for a changing lesion. Exam, assessment, plan.</p></body></html>",
                  "text/html", encounter="ex", author="prx")
    monkeypatch.setattr(api_mod, "DB_PATH", db)
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = TestClient(api_mod.app)
    xhr = {"X-Requested-With": "remitwatch"}
    fid = c.get("/api/findings?signal=DENIED_MEDICAL_NECESSITY").json()["items"][0]["finding_id"]
    # not connected yet: said plainly, and the page hides the buttons
    for k in ("MODMED_CLIENT_ID", "MODMED_PRIVATE_KEY", "MODMED_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert c.get("/api/meta").json()["modmed"] is False
    nc = c.post("/api/appeals/from_ema", json={"finding_id": fid}, headers=xhr)
    # a server can take the note by hand meanwhile (the Upload page, remitwatch.uploads): it says so
    assert nc.status_code == 200 and nc.json()["status"] == "not_found" and "Upload page" in nc.json()["reason"]
    assert c.get("/.well-known/jwks.json").status_code == 404
    monkeypatch.setenv("MODMED_CLIENT_ID", fake.client_id)
    monkeypatch.setenv("MODMED_PRIVATE_KEY", PEM.replace("\n", "\\n"))
    monkeypatch.setenv("MODMED_FHIR_BASE", fake.fhir_base)
    assert c.get("/api/meta").json()["modmed"] is True
    # the public key, open to ModMed, without the private half
    jw = c.get("/.well-known/jwks.json")
    assert jw.status_code == 200 and "d" not in jw.json()["keys"][0] and jw.json()["keys"][0]["kid"] == fake.jwk["kid"]
    assert c.post("/api/modmed/test", headers=xhr).json()["ok"] is True
    assert c.post("/api/modmed/test").status_code == 400                     # a cross-site form cannot start it
    q = c.get("/api/appeals/ema_queue").json()
    assert [i["finding_id"] for i in q["items"]] == [fid] and q["since"] == "2026-11-01"
    r = c.post("/api/appeals/from_ema", json={"finding_id": fid, "use_ai": False}, headers=xhr).json()
    assert r["status"] == "built" and r["found_by"] == "EMA patient ID" and r["note"]["id"] == "nx" and "data" not in r["note"]
    pid = r["packet"]["packet_id"]
    assert [p["packet_id"] for p in c.get(f"/api/packets?finding_id={fid}").json()] == [pid]
    pdf = c.get(f"/api/packets/{pid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    # built once: the queue moves on, and the batch leaves it
    assert c.get("/api/appeals/ema_queue").json()["items"] == []
    assert c.post("/api/appeals/from_ema", json={"finding_id": fid, "skip_if_packet": True}, headers=xhr).json()["status"] == "skipped"
    # a second note that day by someone else: the rendering clinician's is still the one; with no claim, a plain refusal
    fake.add_note("ny", "EMA77", "2026-11-01", b"other", "text/plain", encounter="ex")
    again = c.post("/api/appeals/from_ema", json={"finding_id": fid, "use_ai": False}, headers=xhr).json()
    assert again["note"]["id"] == "nx" and [o["id"] for o in again["others"]] == ["ny"]
    bad = c.post("/api/appeals/from_ema", json={"finding_id": "../x"}, headers=xhr)
    assert bad.status_code == 400


def test_setup_lists_the_connection(monkeypatch):
    from remitwatch import cloud
    for k in ("MODMED_CLIENT_ID", "MODMED_PRIVATE_KEY", "MODMED_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    off = next(c for c in cloud.setup_status(probe=False)["checks"] if c["key"] == "modmed")
    assert off["ok"] is None and "attached by hand" in off["detail"]
    # the key alone (ModMed has not given a client ID yet): its id is shown, and what to do next
    monkeypatch.setenv("MODMED_PRIVATE_KEY", PEM)
    kid = M.public_jwk(M.load_private_key(PEM))["kid"]
    half = next(c for c in cloud.setup_status(probe=False)["checks"] if c["key"] == "modmed")
    assert half["ok"] is None and kid in half["detail"] and "MODMED_CLIENT_ID" in half["detail"]
    monkeypatch.setenv("MODMED_CLIENT_ID", "abc")
    out = cloud.setup_status(probe=False)
    on = next(c for c in out["checks"] if c["key"] == "modmed")
    assert on["ok"] is True and out["modmed"] == {"configured": True, "key": True, "kid": kid} and kid in on["detail"]
    # a key that does not load is said plainly
    monkeypatch.setenv("MODMED_PRIVATE_KEY", "-----BEGIN PRIVATE KEY----- nope -----END PRIVATE KEY-----")
    bad = next(c for c in cloud.setup_status(probe=False)["checks"] if c["key"] == "modmed")
    assert bad["ok"] is False and "BEGIN and END" in bad["detail"]


def test_cli_makes_a_key_pair(capsys):
    from remitwatch import cli
    assert cli.main(["modmed-key"]) == 0
    printed = capsys.readouterr().out
    pem = printed[printed.index("-----BEGIN PRIVATE KEY-----"):printed.index("-----END PRIVATE KEY-----") + 25]
    jwk = json.loads(printed[printed.index('{\n  "keys"'):printed.index("\nKey id")])["keys"][0]
    assert M.public_jwk(M.load_private_key(pem))["kid"] == jwk["kid"] and "d" not in jwk


def test_the_key_is_read_however_the_settings_box_kept_it():
    """Vercel keeps a pasted key with its line breaks; other boxes write them as \\n, or run the key onto one line."""
    import base64
    ref = M.public_jwk(M.load_private_key(PEM))
    for kept in (PEM, PEM.replace("\n", "\\n"), " ".join(PEM.split()), PEM.replace("\n", "\r\n"), '"' + PEM.replace("\n", "\\n") + '"',
                 base64.b64encode(PEM.encode()).decode()):
        assert M.jwks(M.Settings.from_env({"MODMED_PRIVATE_KEY": kept}))["keys"][0] == ref
    with pytest.raises(M.ModMedError, match="BEGIN and END"):
        M.jwks(M.Settings.from_env({"MODMED_PRIVATE_KEY": "-----BEGIN PRIVATE KEY----- not a key -----END PRIVATE KEY-----"}))
