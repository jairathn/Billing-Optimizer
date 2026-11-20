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
    # an EMA patient ID that points at someone with another family name is not taken: the name search finds Joan Rivers
    r = M.note_for_claim(mm, key(ema_patient_ids=["p9"]))
    assert r["status"] == "found" and r["found_by"] == "name" and r["note"]["id"] == "d1"
    assert M.note_for_claim(mm, M.ClaimKey(family="NOBODY", given="X", dos=DOS))["status"] == "not_found"
    # the medical record number the payer echoed
    assert M.note_for_claim(mm, key(mrn="MRN100"))["found_by"] == "medical record number"


def test_two_patients_with_one_name(fake):
    fake.add_patient("a1", "Smith", "Ann")
    fake.add_patient("a2", "Smith", "Ann")
    fake.add_encounter("ea", "a1", DOS)
    fake.add_note("na", "a1", DOS, b"note a", "text/plain", encounter="ea")
    mm = M.ModMed(settings(fake))
    k = M.ClaimKey(family="SMITH", given="ANN", dos=DOS)
    assert M.note_for_claim(mm, k)["note"]["id"] == "na"          # only one of them was seen that day
    fake.add_encounter("eb", "a2", DOS)
    fake.add_note("nb", "a2", DOS, b"note b", "text/plain", encounter="eb")
    r = M.note_for_claim(mm, k)
    assert r["status"] == "ambiguous" and {c["id"] for c in r["candidates"]} == {"na", "nb"}
    assert M.note_for_claim(mm, k, pick="nb")["note"]["data"] == b"note b"


def test_several_notes_that_day_by_npi_then_kind(fake):
    fake.add_patient("p1", "Rivers", "Joan")
    fake.add_practitioner("pr1", NPI_A, "Clinician A")
    fake.add_practitioner("pr2", NPI_B, "Clinician B")
    fake.add_encounter("e1", "p1", DOS)
    fake.add_note("v1", "p1", DOS, b"A's note", "text/plain", encounter="e1", author="pr1", loinc="28570-0")
    fake.add_note("v2", "p1", DOS, b"B's note", "text/plain", encounter="e1", author="pr2")
    mm = M.ModMed(settings(fake))
    assert M.note_for_claim(mm, key(npi=NPI_A))["note"]["id"] == "v1"        # the rendering clinician's
    assert M.note_for_claim(mm, key())["note"]["id"] == "v2"                 # no NPI: the progress note before a procedure note
    fake.add_note("v3", "p1", DOS, b"another progress note", "text/plain", encounter="e1", author="pr2")
    assert M.note_for_claim(mm, key())["status"] == "ambiguous"


def test_the_forms_a_note_comes_in(fake):
    other = FakeModMed(jwk=fake.jwk, open_binaries=True).start()
    try:
        fake.add_patient("p1", "Rivers", "Joan")
        fake.add_encounter("e1", "p1", DOS)
        other.binaries["far"] = ("text/plain", b"stored elsewhere")
        fake.add_note("in", "p1", DOS, b"inline text", "text/plain", encounter="e1", inline=True)
        mm = M.ModMed(settings(fake))
        assert M.note_for_claim(mm, key())["note"]["data"] == b"inline text"
        fake.docs.clear()
        fake.add_note("far", "p1", DOS, b"", "text/plain", encounter="e1", url=f"{other.fhir_base}/Binary/far")
        got = M.note_for_claim(mm, key())
        assert got["status"] == "found"
        # the token is never handed to another host, even one that answers for the note
        assert [r["auth"] for r in other.requests] == [None]
        fake.docs.clear()
        fake.binary_as_json = True
        fake.add_note("js", "p1", DOS, b"%PDF-1.4 wrapped", "application/pdf", encounter="e1")
        n = M.note_for_claim(mm, key())["note"]
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
    assert nc.status_code == 503 and "MODMED_CLIENT_ID" in nc.json()["detail"]
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
    monkeypatch.setenv("MODMED_CLIENT_ID", "abc")
    monkeypatch.setenv("MODMED_PRIVATE_KEY", PEM)
    out = cloud.setup_status(probe=False)
    on = next(c for c in out["checks"] if c["key"] == "modmed")
    assert on["ok"] is True and out["modmed"] == {"configured": True, "key": True}


def test_cli_makes_a_key_pair(capsys):
    from remitwatch import cli
    assert cli.main(["modmed-key"]) == 0
    printed = capsys.readouterr().out
    pem = printed[printed.index("-----BEGIN PRIVATE KEY-----"):printed.index("-----END PRIVATE KEY-----") + 25]
    jwk = json.loads(printed[printed.index('{\n  "keys"'):printed.index("\nKey id")])["keys"][0]
    assert M.public_jwk(M.load_private_key(pem))["kid"] == jwk["kid"] and "d" not in jwk
