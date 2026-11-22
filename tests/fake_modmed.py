"""
A stand-in for ModMed's sign-in server and FHIR R4 API, for the tests of remitwatch.modmed: a real HTTP server on
127.0.0.1 that checks the app's signed client assertion against its registered public key (or a client secret and API
key), and serves patients, visits, notes, binaries, clinicians, paging and a Bulk FHIR export. Switches turn on what the
connector must cope with: searches not allowed (an export-only app), an assertion audience other than the token URL,
a server that refuses the category parameter, no discovery document. Every patient, note and clinician is made up.
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class FakeModMed:
    def __init__(self, *, jwk: dict | None = None, client_id: str = "rw-test-client", secret: str | None = None, api_key: str | None = None,
                 aud: str = "token", search_forbidden: bool = False, reject_category: bool = False, discovery: bool = True, page_size: int = 2,
                 binary_as_json: bool = False, open_binaries: bool = False):
        self.jwk, self.client_id, self.secret, self.api_key = jwk, client_id, secret, api_key
        self.aud, self.search_forbidden, self.reject_category, self.discovery, self.page_size = aud, search_forbidden, reject_category, discovery, page_size
        self.binary_as_json = binary_as_json
        self.open_binaries = open_binaries          # files served without a token, as a pre-signed storage link is
        self.patients: dict[str, dict] = {}
        self.encounters: dict[str, dict] = {}
        self.docs: dict[str, dict] = {}
        self.binaries: dict[str, tuple[str, bytes]] = {}
        self.practitioners: dict[str, dict] = {}
        self.requests: list[dict] = []
        self.tokens: set[str] = set()
        self.jtis: set[str] = set()
        self.revoked = False
        self.exports: dict[str, dict] = {}
        self._n = 0
        self.server: ThreadingHTTPServer | None = None

    # ---- data
    def add_patient(self, pid: str, family: str, given: str, identifiers: tuple = (), birth_date: str | None = None) -> dict:
        p = {"resourceType": "Patient", "id": pid, "name": [{"family": family, "given": [given]}],
             "identifier": [{"system": "urn:ema:mrn", "value": v} for v in identifiers], "meta": {"lastUpdated": "2025-01-01T00:00:00Z"}}
        if birth_date:
            p["birthDate"] = birth_date
        self.patients[pid] = p
        return p

    def add_encounter(self, eid: str, pid: str, day: str) -> dict:
        e = {"resourceType": "Encounter", "id": eid, "status": "finished", "subject": {"reference": f"Patient/{pid}"},
             "period": {"start": f"{day}T09:00:00-05:00", "end": f"{day}T09:30:00-05:00"}, "meta": {"lastUpdated": f"{day}T12:00:00Z"}}
        self.encounters[eid] = e
        return e

    def add_practitioner(self, rid: str, npi: str, name: str) -> dict:
        p = {"resourceType": "Practitioner", "id": rid, "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": npi}],
             "name": [{"text": name}]}
        self.practitioners[rid] = p
        return p

    def add_note(self, did: str, pid: str, written: str, body: bytes, ctype: str = "application/pdf", *, encounter: str | None = None,
                 period: str | None = None, loinc: str = "11506-3", status: str = "current", doc_status: str = "final",
                 author: str | None = None, inline: bool = False, url: str | None = None, category: str | None = "clinical-note") -> dict:
        att: dict = {"contentType": ctype}
        if inline:
            att["data"] = base64.b64encode(body).decode()
        elif url:
            att["url"] = url
        else:
            self.binaries[f"b-{did}"] = (ctype, body)
            att["url"] = f"Binary/b-{did}"
        d = {"resourceType": "DocumentReference", "id": did, "status": status, "docStatus": doc_status,
             "type": {"coding": [{"system": "http://loinc.org", "code": loinc, "display": "note"}]},
             "category": [{"coding": [{"system": "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category", "code": category}]}]
             if category else [],
             "subject": {"reference": f"Patient/{pid}"}, "date": f"{written}T17:00:00Z", "content": [{"attachment": att}],
             "meta": {"lastUpdated": f"{written}T17:00:00Z"}}
        ctx: dict = {}
        if encounter:
            ctx["encounter"] = [{"reference": f"Encounter/{encounter}"}]
        if period:
            ctx["period"] = {"start": f"{period}T09:00:00-05:00"}
        if ctx:
            d["context"] = ctx
        if author:
            d["author"] = [{"reference": f"Practitioner/{author}", "display": self.practitioners.get(author, {}).get("name", [{}])[0].get("text", "")}]
        self.docs[did] = d
        return d

    # ---- server
    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def fhir_base(self) -> str:
        return f"{self.origin}/fhir/r4"

    @property
    def token_url(self) -> str:
        return f"{self.origin}/token"

    def start(self) -> "FakeModMed":
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):         # quiet
                pass

            def do_GET(self):
                fake._get(self)

            def do_POST(self):
                fake._post(self)

            def do_DELETE(self):
                self.send_response(202)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    # ---- replies
    @staticmethod
    def _send(h, status: int, body: bytes | dict | None = None, ctype: str = "application/fhir+json", headers: dict | None = None) -> None:
        raw = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b"")
        h.send_response(status)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(raw)

    @staticmethod
    def _oo(text: str) -> dict:
        return {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "processing", "diagnostics": text}]}

    def _record(self, h, method: str) -> tuple[str, dict]:
        u = urlparse(h.path)
        q = parse_qs(u.query, keep_blank_values=True)
        self.requests.append({"method": method, "path": u.path, "query": q, "auth": h.headers.get("Authorization"),
                              "api_key": h.headers.get("x-api-key"), "accept": h.headers.get("Accept"), "prefer": h.headers.get("Prefer")})
        return u.path, q

    def _post(self, h) -> None:
        path, _ = self._record(h, "POST")
        n = int(h.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(h.rfile.read(n).decode()).items()}
        if path != "/token":
            return self._send(h, 404, self._oo("not found"))
        if self.api_key and h.headers.get("x-api-key") != self.api_key:
            return self._send(h, 401, {"error": "invalid_client", "error_description": "missing or wrong x-api-key"}, "application/json")
        if form.get("grant_type") != "client_credentials":
            return self._send(h, 400, {"error": "unsupported_grant_type"}, "application/json")
        if form.get("client_assertion"):
            import jwt
            if form.get("client_assertion_type") != "urn:ietf:params:oauth:client-assertion-type:jwt-bearer":
                return self._send(h, 400, {"error": "invalid_request"}, "application/json")
            want = {"token": [self.token_url], "base": [self.fhir_base], "any": [self.token_url, self.fhir_base]}[self.aud]
            try:
                head = jwt.get_unverified_header(form["client_assertion"])
                if not self.jwk or head.get("kid") != self.jwk.get("kid"):
                    raise ValueError("unknown key id")
                key = jwt.PyJWK(self.jwk).key
                claims = jwt.decode(form["client_assertion"], key=key, algorithms=[self.jwk["alg"]], audience=want,
                                    options={"require": ["exp", "iat", "jti", "iss", "sub", "aud"]})
                if claims["iss"] != self.client_id or claims["sub"] != self.client_id:
                    raise ValueError("wrong client")
                if claims["jti"] in self.jtis:
                    raise ValueError("assertion reused")
                if claims["exp"] - claims["iat"] > 300:
                    raise ValueError("assertion lives too long")
                self.jtis.add(claims["jti"])
            except Exception as exc:
                return self._send(h, 401, {"error": "invalid_client", "error_description": f"Client authentication with signed JWT failed: {exc}"},
                                  "application/json")
        elif self.secret is not None:
            if form.get("client_id") != self.client_id or form.get("client_secret") != self.secret:
                return self._send(h, 401, {"error": "invalid_client", "error_description": "Invalid client credentials"}, "application/json")
        else:
            return self._send(h, 401, {"error": "invalid_client"}, "application/json")
        self._n += 1
        tok = f"tok-{self._n}"
        self.tokens.add(tok)
        return self._send(h, 200, {"access_token": tok, "token_type": "Bearer", "expires_in": 300, "scope": form.get("scope", "")}, "application/json")

    def _get(self, h) -> None:
        path, q = self._record(h, "GET")
        one = lambda k: (q.get(k) or [None])[0]      # noqa: E731
        if path == "/fhir/r4/.well-known/smart-configuration":
            return self._send(h, 200, {"token_endpoint": self.token_url, "grant_types_supported": ["client_credentials"]}, "application/json") \
                if self.discovery else self._send(h, 404, self._oo("no"))
        auth = (h.headers.get("Authorization") or "").removeprefix("Bearer ")
        if self.open_binaries and path.startswith("/fhir/r4/Binary/"):
            auth = next(iter(self.tokens), auth) if self.tokens else "open"
            self.tokens.add(auth)
        if not path.startswith("/fhir/r4/") or auth not in self.tokens or self.revoked:
            if self.revoked:
                self.revoked = False
                self.tokens.clear()
            return self._send(h, 401, self._oo("unauthorized"))
        if self.api_key and h.headers.get("x-api-key") != self.api_key:
            return self._send(h, 401, self._oo("missing x-api-key"))
        rest = path[len("/fhir/r4/"):]
        if rest == "metadata":
            return self._send(h, 200, {"resourceType": "CapabilityStatement", "fhirVersion": "4.0.1", "software": {"name": "Fake EMA"},
                                       "rest": [{"mode": "server", "resource": [{"type": t} for t in ("Patient", "Encounter", "DocumentReference", "Binary", "Practitioner")]}]})
        if rest.startswith("bulkstatus/"):
            job = self.exports[rest.split("/")[1]]
            job["polls"] += 1
            if job["polls"] < 2:
                return self._send(h, 202, b"", "application/json", {"Retry-After": "1", "X-Progress": "working"})
            return self._send(h, 200, {"transactionTime": "2026-11-20T00:00:00Z", "request": job["request"], "requiresAccessToken": True,
                                       "output": [{"type": t, "url": f"{self.fhir_base}/bulkfiles/{job['id']}/{t}.ndjson"} for t in job["types"]], "error": []},
                              "application/json")
        if rest.startswith("bulkfiles/"):
            _, jid, fname = rest.split("/")
            job, t = self.exports[jid], fname.split(".")[0]
            store = {"Patient": self.patients, "Encounter": self.encounters, "DocumentReference": self.docs, "Practitioner": self.practitioners}[t]
            rows = [r for r in store.values() if not job["since"] or (r.get("meta") or {}).get("lastUpdated", "9999") >= job["since"]]
            return self._send(h, 200, "\n".join(json.dumps(r) for r in rows).encode(), "application/fhir+ndjson")
        if rest.endswith("$export"):
            if h.headers.get("Prefer") != "respond-async":
                return self._send(h, 400, self._oo("Prefer: respond-async is required"))
            jid = f"job{len(self.exports) + 1}"
            self.exports[jid] = {"id": jid, "types": (one("_type") or "Patient").split(","), "since": one("_since"), "polls": 0, "request": h.path}
            return self._send(h, 202, b"", "application/json", {"Content-Location": f"{self.fhir_base}/bulkstatus/{jid}"})
        parts = rest.split("/")
        if len(parts) == 2:
            store = {"Patient": self.patients, "Encounter": self.encounters, "DocumentReference": self.docs, "Practitioner": self.practitioners}.get(parts[0])
            if parts[0] == "Binary":
                b = self.binaries.get(parts[1])
                if not b:
                    return self._send(h, 404, self._oo("no such binary"))
                if self.binary_as_json or "fhir+json" in (h.headers.get("Accept") or ""):
                    return self._send(h, 200, {"resourceType": "Binary", "id": parts[1], "contentType": b[0], "data": base64.b64encode(b[1]).decode()})
                return self._send(h, 200, b[1], b[0])
            if store is None or parts[1] not in store:
                return self._send(h, 404, self._oo("not found"))
            return self._send(h, 200, store[parts[1]])
        if self.search_forbidden and parts[0] in ("Patient", "Encounter", "DocumentReference"):
            return self._send(h, 403, self._oo("This application may only use Bulk FHIR"))
        if parts[0] == "Patient":
            rows = list(self.patients.values())
            if one("identifier"):
                rows = [p for p in rows if any(i["value"] == one("identifier") for i in p["identifier"])]
            if one("family"):
                rows = [p for p in rows if p["name"][0]["family"].upper().startswith(one("family").upper())]
            if one("given"):
                rows = [p for p in rows if p["name"][0]["given"][0].upper().startswith(one("given").upper())]
            if one("_summary") == "count":
                return self._send(h, 200, {"resourceType": "Bundle", "type": "searchset", "total": len(rows)})
        elif parts[0] == "Encounter":
            rows = [e for e in self.encounters.values() if e["subject"]["reference"] == f"Patient/{one('patient')}"
                    and (not one("date") or e["period"]["start"][:10] == one("date"))]
        elif parts[0] == "DocumentReference":
            if self.reject_category and "category" in q:
                return self._send(h, 400, self._oo("Unknown search parameter 'category'"))
            rows = [d for d in self.docs.values() if d["subject"]["reference"] == f"Patient/{one('patient')}"]
            if one("category"):             # as a FHIR server does: only the documents filed under that category
                rows = [d for d in rows if any(cd.get("code") == one("category") for c in d.get("category") or [] for cd in c.get("coding") or [])]
            for cond in q.get("date") or []:
                op, day = cond[:2], cond[2:]
                rows = [d for d in rows if (d["date"][:10] >= day if op == "ge" else d["date"][:10] <= day)]
        else:
            return self._send(h, 404, self._oo("unknown resource"))
        page = int(one("_page") or 1)
        size = self.page_size
        chunk = rows[(page - 1) * size: page * size]
        bundle: dict = {"resourceType": "Bundle", "type": "searchset", "total": len(rows),
                        "entry": [{"fullUrl": f"{self.fhir_base}/{r['resourceType']}/{r['id']}", "resource": r} for r in chunk], "link": []}
        if page * size < len(rows):
            nq = {k: v for k, v in q.items() if k != "_page"}
            qs = "&".join(f"{k}={x}" for k, vs in nq.items() for x in vs)
            bundle["link"].append({"relation": "next", "url": f"{self.fhir_base}/{parts[0]}?{qs}&_page={page + 1}"})
        return self._send(h, 200, bundle)
