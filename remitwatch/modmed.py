"""
ModMed EMA, read through ModMed's ONC-certified FHIR R4 API: the visit note behind each claim RemitWatch flags, found
and downloaded so an appeal can be built from it without anyone looking the note up and uploading it.

Connecting it (docs/OPERATIONS.md, "Connecting ModMed EMA"):
  1. Make the app's key: `python -m remitwatch.cli modmed-key`. It prints the private key, to keep in the host's settings
     as MODMED_PRIVATE_KEY, and the public key as a JWK, for ModMed. Once the key is set the site also publishes the
     public key at /.well-known/jwks.json, for a registration that asks for a JWKS URL instead.
  2. Register the app with ModMed (portal.api.modmed.com) as a Bulk FHIR (backend) application with that public key,
     and note its client ID.
  3. In EMA: Admin, Manage Bulk FHIR, add the client ID. That is the practice's consent.
  4. Set MODMED_CLIENT_ID and press "Test the ModMed connection" on /setup.

Settings (environment):
  MODMED_CLIENT_ID      the app's client ID
  MODMED_PRIVATE_KEY    PEM, EC P-384 (ES384, ModMed's documented choice) or RSA (RS384): the signed client assertion of
                        SMART Backend Services (RFC 7523). Line breaks may be written as \\n, or the PEM base64-encoded
  MODMED_KEY_ID         the key's id as registered (kid); default its RFC 7638 thumbprint, as /.well-known/jwks.json shows
  MODMED_CLIENT_SECRET  instead of a key, where ModMed issued a client secret
  MODMED_API_KEY        sent as the x-api-key header on every call, where ModMed issued one
  MODMED_FHIR_BASE      default https://fhirmp.mmi.prod.fhir.ema-api.com/fhir/r4
  MODMED_TOKEN_URL      default the server's own (its .well-known/smart-configuration), else ModMed's sso.ema.md endpoint
  MODMED_SCOPE          default system/*.read
  MODMED_AUDIENCE       the assertion's audience; default the token URL (the SMART standard), then the FHIR base if refused
  MODMED_GROUP_ID       the Group to export from, for an app ModMed allows only Bulk FHIR exports (no searches)

Finding the note for a claim: the patient first by EMA's own patient ID on the claim's EMA rows (as a FHIR id, then as
an identifier), then by the medical record number the payer echoed, then by name; a patient is only ever taken when
the family name agrees with the claim's, so another patient's note can never be attached. Then that patient's visits
on the date of service and the notes tied to them (or, failing a link, dated that day or after), current and signed
only. More than one note that day: the one written by the clinician who rendered the service (NPI), else the person
picks. Where ModMed allows the app no searches, the same lookups run over a Bulk FHIR export.

Read only: nothing is written to EMA. What is read stays in memory or in the appeal packet, as with an uploaded note.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

DEFAULT_BASE = "https://fhirmp.mmi.prod.fhir.ema-api.com/fhir/r4"
DEFAULT_TOKEN_URL = "https://sso.ema.md/auth/realms/fhir/protocol/openid-connect/token"
DEFAULT_SCOPE = "system/*.read"
NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
# LOINC types of the notes an appeal can use, best first
NOTE_TYPES = {"11506-3": "Progress note", "28570-0": "Procedure note", "34133-9": "Summary of episode note", "11488-4": "Consult note",
              "34117-2": "History and physical note", "18842-5": "Discharge summary"}
NOTE_AFTER_DAYS = 45            # a note is signed on or after its visit; one dated later than this belongs to another visit
CONTENT_PREF = ("application/pdf", "text/html", "application/xhtml+xml", "text/plain", "text/rtf", "application/rtf")
EXTENSIONS = {"application/pdf": ".pdf", "text/html": ".html", "application/xhtml+xml": ".html", "text/plain": ".txt",
              "text/rtf": ".rtf", "application/rtf": ".rtf"}
TIMEOUT = (10, 60)
MAX_PAGES = 20
MAX_NOTE_BYTES = 15 * 1024 * 1024
EXPORT_TTL = 30 * 60            # a Bulk FHIR export is reused this long, so a batch of notes takes one export


class ModMedError(Exception):
    """A call to ModMed failed; the message says what to do and holds no patient data."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotConfigured(ModMedError):
    pass


class Forbidden(ModMedError):
    """The token is good but ModMed does not let this app make the call (a Bulk-only app searching, a scope missing)."""


# --------------------------------------------------------------------------- settings and keys
def _pem(text: str) -> str:
    """The private key as a PEM, however the host's settings kept it: with its line breaks, with them written as \\n,
    base64 of the whole PEM, or run onto one line with spaces (some settings boxes do that to a pasted key)."""
    t = (text or "").strip().strip('"').strip("'")
    if not t:
        return ""
    if "-----BEGIN" not in t:
        try:
            t = base64.b64decode(t).decode("ascii")
        except Exception:
            return t
    t = t.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").strip()
    m = re.fullmatch(r"(-----BEGIN [A-Z ]+-----)\s*(.+?)\s*(-----END [A-Z ]+-----)", t, flags=re.S)
    if m:
        body = re.sub(r"\s+", "", m.group(2))
        t = "\n".join([m.group(1), *[body[i:i + 64] for i in range(0, len(body), 64)], m.group(3)]) + "\n"
    return t


@dataclass
class Settings:
    client_id: str = ""
    private_key: str = ""
    key_id: str = ""
    client_secret: str = ""
    api_key: str = ""
    fhir_base: str = DEFAULT_BASE
    token_url: str = ""
    scope: str = DEFAULT_SCOPE
    audience: str = ""
    group_id: str = ""

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        e = os.environ if env is None else env
        g = lambda k, d="": (e.get(k) or d).strip()     # noqa: E731
        return cls(client_id=g("MODMED_CLIENT_ID"), private_key=_pem(e.get("MODMED_PRIVATE_KEY") or ""), key_id=g("MODMED_KEY_ID"),
                   client_secret=g("MODMED_CLIENT_SECRET"), api_key=g("MODMED_API_KEY"),
                   fhir_base=(g("MODMED_FHIR_BASE") or DEFAULT_BASE).rstrip("/"), token_url=g("MODMED_TOKEN_URL"),
                   scope=g("MODMED_SCOPE") or DEFAULT_SCOPE, audience=g("MODMED_AUDIENCE"), group_id=g("MODMED_GROUP_ID"))

    def configured(self) -> bool:
        return bool(self.client_id and (self.private_key or self.client_secret))

    def method(self) -> str:
        return "private_key_jwt" if self.private_key else "client_secret" if self.client_secret else "none"

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True).encode()).hexdigest()


def load_private_key(pem: str):
    from cryptography.hazmat.primitives import serialization
    try:
        return serialization.load_pem_private_key(_pem(pem).encode(), password=None)
    except Exception as exc:
        raise ModMedError(f"MODMED_PRIVATE_KEY is not a readable private key ({type(exc).__name__}). Paste the whole PEM, "
                          "including the BEGIN and END lines; line breaks may be written as \\n.") from exc


def key_alg(key) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    if isinstance(key, ec.EllipticCurvePrivateKey):
        return {"secp384r1": "ES384", "secp256r1": "ES256", "secp521r1": "ES512"}.get(key.curve.name) or "ES384"
    if isinstance(key, rsa.RSAPrivateKey):
        return "RS384"
    raise ModMedError("MODMED_PRIVATE_KEY must be an EC (P-384) or RSA key.")


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _int_b64u(n: int, length: int | None = None) -> str:
    return _b64u(n.to_bytes(length or max(1, (n.bit_length() + 7) // 8), "big"))


def public_jwk(key, kid: str | None = None) -> dict:
    """The key's public half as a JWK (no private member), with its algorithm and id."""
    from cryptography.hazmat.primitives.asymmetric import ec
    pub = key.public_key()
    nums = pub.public_numbers()
    if isinstance(key, ec.EllipticCurvePrivateKey):
        size = (key.curve.key_size + 7) // 8
        crv = {"secp384r1": "P-384", "secp256r1": "P-256", "secp521r1": "P-521"}[key.curve.name]
        jwk = {"kty": "EC", "crv": crv, "x": _int_b64u(nums.x, size), "y": _int_b64u(nums.y, size)}
    else:
        jwk = {"kty": "RSA", "n": _int_b64u(nums.n), "e": _int_b64u(nums.e)}
    jwk.update({"kid": kid or thumbprint(jwk), "alg": key_alg(key), "use": "sig", "key_ops": ["verify"]})
    return jwk


def thumbprint(jwk: dict) -> str:
    """RFC 7638: the SHA-256 of the key's required members, in order, without spaces."""
    req = ("crv", "kty", "x", "y") if jwk["kty"] == "EC" else ("e", "kty", "n")
    return _b64u(hashlib.sha256(json.dumps({k: jwk[k] for k in req}, separators=(",", ":"), sort_keys=True).encode()).digest())


def jwks(settings: Settings | None = None) -> dict | None:
    """The public key set the site publishes for ModMed, or None without a key."""
    s = settings or Settings.from_env()
    if not s.private_key:
        return None
    return {"keys": [public_jwk(load_private_key(s.private_key), s.key_id or None)]}


def generate_key(kind: str = "EC") -> str:
    """A new private key as PKCS#8 PEM: EC P-384 (ES384) by default, or RSA 3072 (RS384)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072) if kind.upper() == "RSA" else ec.generate_private_key(ec.SECP384R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def client_assertion(settings: Settings, aud: str, now: float | None = None) -> str:
    """SMART Backend Services' signed client assertion: issued by and about the client, for the token endpoint, five
    minutes at most, never reused."""
    import jwt
    key = load_private_key(settings.private_key)
    jwk = public_jwk(key, settings.key_id or None)
    t = int(now if now is not None else time.time())
    claims = {"iss": settings.client_id, "sub": settings.client_id, "aud": aud, "exp": t + 240, "iat": t, "jti": uuid.uuid4().hex}
    return jwt.encode(claims, key, algorithm=jwk["alg"], headers={"kid": jwk["kid"], "typ": "JWT"})


# --------------------------------------------------------------------------- the client
def _outcome(resp) -> str:
    """What an error response says, without patient data: an OperationOutcome's diagnostics or an OAuth error."""
    try:
        j = resp.json()
    except Exception:
        return (resp.text or "")[:200].replace("\n", " ")
    if isinstance(j, dict):
        if j.get("resourceType") == "OperationOutcome":
            return "; ".join(filter(None, ((i.get("diagnostics") or (i.get("details") or {}).get("text") or i.get("code") or "")
                                            for i in j.get("issue") or [])))[:300]
        if j.get("error"):
            return f"{j.get('error')}: {j.get('error_description') or ''}".strip(": ")[:300]
    return str(j)[:200]


class ModMed:
    """One connection to ModMed's FHIR API: a token kept until shortly before it expires, calls retried when ModMed asks
    for patience (429, 503), a token renewed once when refused (401). The bearer token goes only to the FHIR server
    (and to export files that say they need it), never to another host."""

    def __init__(self, settings: Settings | None = None, session: Any = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.s = settings or Settings.from_env()
        if not self.s.configured():
            raise NotConfigured("ModMed is not connected: set MODMED_CLIENT_ID and MODMED_PRIVATE_KEY (or MODMED_CLIENT_SECRET).")
        if session is None:
            import requests
            session = requests.Session()
        self.http = session
        self.clock, self.sleep = clock, sleep
        self.base = self.s.fhir_base.rstrip("/")
        self._host = urlparse(self.base).netloc
        self._token: str | None = None
        self._token_exp = 0.0
        self._aud: str | None = None
        self._token_url: str | None = None
        self._smart: dict | None = None
        self._lock = threading.Lock()
        self.granted_scope = ""

    # ---- discovery and token
    def smart_configuration(self) -> dict:
        if self._smart is None:
            try:
                r = self.http.get(f"{self.base}/.well-known/smart-configuration", headers=self._plain({"Accept": "application/json"}), timeout=TIMEOUT)
                self._smart = r.json() if r.status_code == 200 else {}
            except Exception:
                self._smart = {}
        return self._smart

    def token_url(self) -> str:
        if self._token_url is None:
            self._token_url = self.s.token_url or self.smart_configuration().get("token_endpoint") or DEFAULT_TOKEN_URL
        return self._token_url

    def _plain(self, headers: dict) -> dict:
        return dict(headers, **({"x-api-key": self.s.api_key} if self.s.api_key else {}))

    def token(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and self.clock() < self._token_exp - 60:
                return self._token
            j = self._request_token()
            self._token = j["access_token"]
            self._token_exp = self.clock() + float(j.get("expires_in") or 300)
            self.granted_scope = j.get("scope") or self.granted_scope
            return self._token

    def _request_token(self) -> dict:
        url = self.token_url()
        form = {"grant_type": "client_credentials", "scope": self.s.scope}
        headers = self._plain({"Accept": "application/json"})
        if self.s.private_key:
            # the assertion's audience: the token endpoint (the standard), then the FHIR base, which some of ModMed's
            # documents name; the one that works is kept
            auds = [self.s.audience] if self.s.audience else ([self._aud] if self._aud else []) + [a for a in (url, self.base) if a != self._aud]
            last = ""
            for aud in auds:
                data = dict(form, client_assertion_type=ASSERTION_TYPE, client_assertion=client_assertion(self.s, aud, self.clock()))
                r = self._post(url, data, headers)
                if r.status_code == 200:
                    self._aud = aud
                    return self._token_json(r)
                last = _outcome(r)
                if r.status_code not in (400, 401) or "client" not in last.lower() and "audience" not in last.lower() and "assertion" not in last.lower():
                    break
            raise ModMedError(f"ModMed refused the app's key: {last or 'no reason given'}. Check MODMED_CLIENT_ID, that the public key "
                              "registered with ModMed is this one (/.well-known/jwks.json), and MODMED_KEY_ID.", 401)
        r = self._post(url, dict(form, client_id=self.s.client_id, client_secret=self.s.client_secret), headers)
        if r.status_code == 200:
            return self._token_json(r)
        raise ModMedError(f"ModMed refused the client secret: {_outcome(r) or r.status_code}. Check MODMED_CLIENT_ID, MODMED_CLIENT_SECRET "
                          "and MODMED_API_KEY.", r.status_code)

    def _post(self, url: str, data: dict, headers: dict):
        try:
            return self.http.post(url, data=data, headers=headers, timeout=TIMEOUT)
        except Exception as exc:
            raise ModMedError(f"ModMed's sign-in server did not answer ({type(exc).__name__}).") from exc

    @staticmethod
    def _token_json(r) -> dict:
        try:
            j = r.json()
        except Exception as exc:
            raise ModMedError("ModMed's sign-in server answered without a token.") from exc
        if not j.get("access_token"):
            raise ModMedError("ModMed's sign-in server answered without a token.")
        return j

    # ---- calls
    def _url(self, path_or_url: str) -> str:
        if re.match(r"^https?://", path_or_url):
            return path_or_url
        return urljoin(self.base + "/", path_or_url.lstrip("/"))

    def get(self, path_or_url: str, params: dict | None = None, accept: str = "application/fhir+json", auth: bool | None = None,
            headers: dict | None = None):
        """One GET, retried as the server asks; raises ModMedError (Forbidden for 403) with a plain reason."""
        url = self._url(path_or_url)
        send_token = (urlparse(url).netloc == self._host) if auth is None else auth
        renewed = False
        for attempt in range(4):
            h = self._plain({"Accept": accept, **(headers or {})})
            if send_token:
                h["Authorization"] = f"Bearer {self.token()}"
            try:
                r = self.http.get(url, params=params, headers=h, timeout=TIMEOUT)
            except Exception as exc:
                if attempt < 2:
                    self.sleep(2 * (attempt + 1))
                    continue
                raise ModMedError(f"ModMed did not answer ({type(exc).__name__}).") from exc
            if r.status_code == 401 and send_token and not renewed:
                renewed = True
                self.token(force=True)
                continue
            if r.status_code in (429, 502, 503, 504) and attempt < 3:
                try:
                    wait = float(r.headers.get("Retry-After") or 2 * (attempt + 1))
                except ValueError:
                    wait = 2 * (attempt + 1)
                self.sleep(min(max(wait, 1.0), 15.0))
                continue
            if r.status_code == 403:
                raise Forbidden(f"ModMed does not let this app do that ({_outcome(r) or '403'}).", 403)
            if r.status_code >= 400:
                raise ModMedError(f"ModMed answered {r.status_code}: {_outcome(r) or 'no reason given'}.", r.status_code)
            return r
        raise ModMedError("ModMed kept asking to wait; try again in a few minutes.", 429)

    def get_json(self, path_or_url: str, params: dict | None = None) -> dict:
        r = self.get(path_or_url, params)
        try:
            return r.json()
        except Exception as exc:
            raise ModMedError("ModMed answered with something that is not FHIR JSON.") from exc

    def search(self, resource: str, params: dict, max_pages: int = MAX_PAGES) -> list[dict]:
        """Every resource of the type a search returns, following the server's next links."""
        out: list[dict] = []
        bundle = self.get_json(resource, params)
        for _ in range(max_pages):
            out += [e["resource"] for e in bundle.get("entry") or [] if isinstance(e.get("resource"), dict)
                    and e["resource"].get("resourceType") == resource]
            nxt = next((link.get("url") for link in bundle.get("link") or [] if link.get("relation") == "next"), None)
            if not nxt:
                break
            bundle = self.get_json(nxt)
        return out

    def read(self, resource: str, rid: str) -> dict | None:
        try:
            j = self.get_json(f"{resource}/{rid}")
        except ModMedError as exc:
            if exc.status in (404, 410):
                return None
            raise
        return j if j.get("resourceType") == resource else None

    def metadata(self) -> dict:
        return self.get_json("metadata")

    def attachment(self, att: dict) -> tuple[bytes, str]:
        """An attachment's bytes and type: inline data, or its URL (a FHIR Binary, served raw or as JSON)."""
        ctype = (att.get("contentType") or "").split(";")[0].strip().lower()
        if att.get("data"):
            data = base64.b64decode(att["data"])
        elif att.get("url"):
            r = self.get(att["url"], accept=ctype or "*/*")
            got = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = r.content
            if got in ("application/fhir+json", "application/json") and ctype not in ("application/json",):
                try:
                    j = r.json()
                except Exception:
                    j = {}
                if j.get("resourceType") == "Binary":
                    data = base64.b64decode(j.get("data") or "")
                    got = (j.get("contentType") or ctype).lower()
            ctype = ctype or got
        else:
            raise ModMedError("The note has no content to download.")
        if len(data) > MAX_NOTE_BYTES:
            raise ModMedError("The note is larger than 15 MB.")
        return data, ctype or "application/octet-stream"

    # ---- Bulk FHIR
    def export(self, types: Iterable[str], since: str | None = None, budget: float = 240.0) -> dict[str, list[dict]]:
        """A Bulk FHIR export of these resource types (the Group's when MODMED_GROUP_ID is set, else every patient's),
        waited for up to `budget` seconds; the resources by type."""
        path = f"Group/{self.s.group_id}/$export" if self.s.group_id else "Patient/$export"
        params = {"_type": ",".join(types)}
        if since:
            params["_since"] = since
        r = self.get(path, params, headers={"Prefer": "respond-async"})
        status_url = r.headers.get("Content-Location")
        if r.status_code != 202 or not status_url:
            raise ModMedError(f"ModMed did not start the export ({r.status_code}).", r.status_code)
        end = self.clock() + budget
        manifest = None
        while self.clock() < end:
            s = self.get(status_url, accept="application/json")
            if s.status_code == 200:
                manifest = s.json()
                break
            try:
                wait = float(s.headers.get("Retry-After") or 5)
            except ValueError:
                wait = 5.0
            self.sleep(max(1.0, min(wait, 30.0, end - self.clock())))
        if manifest is None:
            raise ModMedError("ModMed's export is still running; try again in a few minutes.", 202)
        out: dict[str, list[dict]] = {}
        need = bool(manifest.get("requiresAccessToken", True))
        for f in manifest.get("output") or []:
            body = self.get(f["url"], accept="application/fhir+ndjson", auth=need).text
            for line in body.splitlines():
                if line.strip():
                    res = json.loads(line)
                    out.setdefault(res.get("resourceType") or f.get("type"), []).append(res)
        return out


_CLIENTS: dict[str, ModMed] = {}
_CLIENTS_LOCK = threading.Lock()


def client(settings: Settings | None = None) -> ModMed:
    """The process's connection for these settings (its token is reused across requests)."""
    s = settings or Settings.from_env()
    if not s.configured():
        raise NotConfigured("ModMed is not connected: set MODMED_CLIENT_ID and MODMED_PRIVATE_KEY (or MODMED_CLIENT_SECRET).")
    with _CLIENTS_LOCK:
        key = s.fingerprint()
        if key not in _CLIENTS:
            _CLIENTS.clear()
            _CLIENTS[key] = ModMed(s)
        return _CLIENTS[key]


# --------------------------------------------------------------------------- where the notes come from
def _day(v: str | None) -> str:
    return (v or "")[:10]


class LiveSource:
    """Lookups as FHIR searches, US Core's required ones first, simpler forms where the server refuses a parameter."""

    def __init__(self, mm: ModMed):
        self.mm = mm
        self._pract: dict[str, dict | None] = {}

    def patient(self, pid: str) -> dict | None:
        return self.mm.read("Patient", pid) if re.fullmatch(r"[A-Za-z0-9\-.]{1,64}", pid or "") else None

    def patients_by_identifier(self, value: str) -> list[dict]:
        return self.mm.search("Patient", {"identifier": value}, max_pages=2)

    def patients_by_name(self, family: str, given: str) -> list[dict]:
        params = {"family": family}
        if given.split():
            params["given"] = given.split()[0]
        return self.mm.search("Patient", params, max_pages=3)

    def encounters(self, pid: str, dos: str) -> list[dict]:
        try:
            return self.mm.search("Encounter", {"patient": pid, "date": dos}, max_pages=2)
        except Forbidden:
            raise
        except ModMedError:
            return [e for e in self.mm.search("Encounter", {"patient": pid}, max_pages=5) if _encounter_on(e, dos)]

    def documents(self, pid: str, dos: str) -> list[dict]:
        last = (date.fromisoformat(dos) + timedelta(days=NOTE_AFTER_DAYS)).isoformat()
        tries = [{"patient": pid, "category": "clinical-note", "date": [f"ge{dos}", f"le{last}"]},
                 {"patient": pid, "date": [f"ge{dos}", f"le{last}"]},
                 {"patient": pid}]
        err: ModMedError | None = None
        answered = False
        for params in tries:
            try:
                found = self.mm.search("DocumentReference", params, max_pages=5)
            except Forbidden:
                raise
            except ModMedError as exc:     # a parameter this server does not take: the next, simpler search
                err = exc
                continue
            answered = True
            if found:
                return found
        if err and not answered:
            raise err
        return []

    def practitioner(self, ref: str) -> dict | None:
        rid = ref.split("/")[-1] if ref.startswith("Practitioner/") else None
        if not rid:
            return None
        if rid not in self._pract:
            try:
                self._pract[rid] = self.mm.read("Practitioner", rid)
            except ModMedError:
                self._pract[rid] = None
        return self._pract[rid]

    def content(self, att: dict) -> tuple[bytes, str]:
        return self.mm.attachment(att)


_EXPORTS: dict[tuple[str, str], tuple[float, dict]] = {}


class ExportSource(LiveSource):
    """The same lookups over a Bulk FHIR export, for an app ModMed lets only export: patients (all of them, as a
    patient's record may not have changed lately), and the visits and notes changed since the window began."""

    def __init__(self, mm: ModMed, since_day: str, budget: float = 240.0):
        super().__init__(mm)
        fp = mm.s.fingerprint()
        hit = next((v for k, v in _EXPORTS.items() if k[0] == fp and k[1] <= since_day and mm.clock() - v[0] < EXPORT_TTL), None)
        if hit:
            data = hit[1]
        else:
            data = dict(mm.export(["Patient", "Practitioner"], None, budget))
            data.update(mm.export(["Encounter", "DocumentReference"], f"{since_day}T00:00:00Z", budget))
            _EXPORTS.clear()
            _EXPORTS[(fp, since_day)] = (mm.clock(), data)
        self.d = data
        self.by_id = {(r.get("resourceType"), r.get("id")): r for rs in data.values() for r in rs}

    def patient(self, pid: str) -> dict | None:
        return self.by_id.get(("Patient", pid))

    def patients_by_identifier(self, value: str) -> list[dict]:
        return [p for p in self.d.get("Patient", []) if any((i.get("value") or "") == value for i in p.get("identifier") or [])]

    def patients_by_name(self, family: str, given: str) -> list[dict]:
        return [p for p in self.d.get("Patient", []) if _family_ok(p, family) and (not given or _given_ok(p, given))]

    def encounters(self, pid: str, dos: str) -> list[dict]:
        return [e for e in self.d.get("Encounter", []) if _ref_id((e.get("subject") or {}).get("reference")) == pid and _encounter_on(e, dos)]

    def documents(self, pid: str, dos: str) -> list[dict]:
        return [d for d in self.d.get("DocumentReference", []) if _ref_id((d.get("subject") or {}).get("reference")) == pid]

    def practitioner(self, ref: str) -> dict | None:
        return self.by_id.get(("Practitioner", ref.split("/")[-1]))


# --------------------------------------------------------------------------- matching a claim to its note
def _norm(s: str | None) -> str:
    return re.sub(r"[^A-Z]", "", (s or "").upper())


def _ref_id(ref: str | None) -> str:
    return (ref or "").rstrip("/").split("/")[-1]


def _names(p: dict) -> list[dict]:
    return [n for n in p.get("name") or [] if isinstance(n, dict)]


def _family_ok(p: dict, family: str) -> bool:
    """The claim's family name agrees with one of the patient's (letters only; either may hold the other, for a
    hyphenated or a married name)."""
    want = _norm(family)
    if not want:
        return False
    for n in _names(p):
        got = _norm(n.get("family"))
        if got and (got == want or (min(len(got), len(want)) >= 4 and (got in want or want in got))):
            return True
    return False


def _given_ok(p: dict, given: str) -> bool:
    """First names agree on their first word (a middle initial typed into the first-name field is ignored); either may
    be the start of the other."""
    first = lambda v: _norm((v or "").split()[0]) if (v or "").split() else ""      # noqa: E731
    want = first(given)
    if not want:
        return True
    for n in _names(p):
        for g in n.get("given") or []:
            got = first(g)
            if got and (got == want or got.startswith(want) or want.startswith(got)):
                return True
    return False


def _encounter_on(e: dict, dos: str) -> bool:
    per = e.get("period") or {}
    start, end = _day(per.get("start")), _day(per.get("end")) or _day(per.get("start"))
    return bool(start) and start <= dos <= (end or start)


@dataclass
class ClaimKey:
    """What a claim says about whose note it needs: the patient's name, the date of service, the rendering clinician
    and any identifier that names the patient in EMA. Held in memory only."""
    family: str
    given: str
    dos: str
    npi: str = ""
    ema_patient_ids: list[str] = field(default_factory=list)
    mrn: str = ""


def claim_key(detail: dict, raw_claim: dict | None, ema_patient_ids: Iterable[str] = ()) -> ClaimKey:
    f = detail.get("finding") or {}
    c = raw_claim or {}
    dos = _day(f.get("service_date") or c.get("service_date_start") or c.get("statement_from") or "")
    return ClaimKey(family=c.get("patient_last") or c.get("corrected_patient_last") or "", given=c.get("patient_first") or c.get("corrected_patient_first") or "",
                    dos=dos, npi=f.get("rendering_provider_npi") or c.get("rendering_provider_npi") or "",
                    ema_patient_ids=[str(x) for x in dict.fromkeys(i for i in ema_patient_ids if i)], mrn=c.get("medical_record_number") or "")


def _note_type(d: dict) -> tuple[str, str]:
    for cd in (d.get("type") or {}).get("coding") or []:
        if cd.get("code") in NOTE_TYPES:
            return cd["code"], NOTE_TYPES[cd["code"]]
    t = d.get("type") or {}
    return "", t.get("text") or next((cd.get("display") for cd in t.get("coding") or [] if cd.get("display")), "") or "Note"


def _doc_date(d: dict) -> str:
    ctx = d.get("context") or {}
    return _day((ctx.get("period") or {}).get("start")) or _day(d.get("date"))


def _usable(d: dict) -> bool:
    """Current (not superseded or entered in error), signed (not preliminary), and with something to download."""
    if (d.get("status") or "current") != "current":
        return False
    if (d.get("docStatus") or "final") not in ("final", "amended"):
        return False
    return any((c.get("attachment") or {}).get("url") or (c.get("attachment") or {}).get("data") for c in d.get("content") or [])


def _ties(d: dict, dos: str, encounter_ids: set[str]) -> str | None:
    """How the note belongs to the visit: 'encounter' (it names one of the day's visits), 'period' (its clinical date is
    the day), 'date' (no link: written that day or within NOTE_AFTER_DAYS after), or None."""
    ctx = d.get("context") or {}
    refs = {_ref_id(e.get("reference")) for e in ctx.get("encounter") or [] if isinstance(e, dict)}
    if refs & encounter_ids:
        return "encounter"
    if refs:
        return None                 # tied to another visit
    start = _day((ctx.get("period") or {}).get("start"))
    if start:
        return "period" if start == dos else None
    written = _day(d.get("date"))
    if written and dos <= written <= (date.fromisoformat(dos) + timedelta(days=NOTE_AFTER_DAYS)).isoformat():
        return "date"
    return None


def _author_npis(src: LiveSource, d: dict) -> set[str]:
    out = set()
    for a in d.get("author") or []:
        ref = (a or {}).get("reference") or ""
        p = src.practitioner(ref) if ref.startswith("Practitioner/") else None
        for i in (p or {}).get("identifier") or []:
            if i.get("system") == NPI_SYSTEM and i.get("value"):
                out.add(i["value"])
    return out


def _author_label(d: dict) -> str:
    return ", ".join(filter(None, ((a or {}).get("display") for a in d.get("author") or []))) or ""


def _candidate(d: dict, tie: str) -> dict:
    code, label = _note_type(d)
    return {"id": d.get("id"), "date": _doc_date(d), "written": _day(d.get("date")), "type": label, "loinc": code,
            "author": _author_label(d), "tie": tie, "status": d.get("docStatus") or "final"}


def _patients(src: LiveSource, key: ClaimKey) -> tuple[list[dict], str]:
    """The EMA patients the claim can belong to, and how they were found. Only a patient whose family name agrees with
    the claim's is ever taken."""
    for pid in key.ema_patient_ids:
        p = src.patient(pid)
        if p and _family_ok(p, key.family):
            return [p], "EMA patient ID"
        hits = [x for x in src.patients_by_identifier(pid) if _family_ok(x, key.family)]
        if hits:
            return hits, "EMA patient ID"
    if key.mrn:
        hits = [x for x in src.patients_by_identifier(key.mrn) if _family_ok(x, key.family)]
        if hits:
            return hits, "medical record number"
    if key.family:
        hits = [x for x in src.patients_by_name(key.family, key.given) if _family_ok(x, key.family) and _given_ok(x, key.given)]
        return hits, "name"
    return [], "name"


def find_note(src: LiveSource, key: ClaimKey, pick: str | None = None) -> dict:
    """The note for a claim: {'status': 'found' | 'ambiguous' | 'not_found', 'reason', 'found_by', 'candidates',
    and with 'found': 'note' {id, type, date, author, filename, content_type, data}}. `pick` chooses one of the
    candidates by its id (and only one of them)."""
    if not key.dos or not key.family:
        return {"status": "not_found", "reason": "The claim has no patient name or date of service to look up.", "candidates": []}
    patients, how = _patients(src, key)
    if not patients:
        return {"status": "not_found", "reason": f"No patient in EMA matches this claim's name ({how}).", "candidates": [], "found_by": how}
    rows: list[tuple[int, dict, dict, str]] = []          # (rank, document, patient, tie)
    for p in patients[:5]:
        pid = p.get("id") or ""
        enc_ids = {e.get("id") for e in src.encounters(pid, key.dos) if e.get("id")}
        for d in src.documents(pid, key.dos):
            if not _usable(d):
                continue
            tie = _ties(d, key.dos, enc_ids)
            if tie:
                rows.append(({"encounter": 0, "period": 1, "date": 2}[tie], d, p, tie))
    if not rows:
        return {"status": "not_found", "found_by": how, "candidates": [],
                "reason": f"EMA has no signed note for {key.dos} for this patient." + (" (More than one patient has this name; none had a note that day.)" if len(patients) > 1 else "")}
    every = list(rows)
    if pick:
        chosen = [r for r in rows if r[1].get("id") == pick]
        if not chosen:
            return {"status": "ambiguous", "found_by": how, "reason": "That note is not one of this claim's.", "candidates": [_candidate(r[1], r[3]) for r in rows]}
        rows = chosen
    best = min(r[0] for r in rows)
    rows = [r for r in rows if r[0] == best]
    if len({r[2].get("id") for r in rows}) > 1:
        return {"status": "ambiguous", "found_by": how, "reason": "More than one patient with this name has a note that day; pick the note.",
                "candidates": [_candidate(r[1], r[3]) for r in rows]}
    if len(rows) > 1 and key.npi:
        mine = [r for r in rows if key.npi in _author_npis(src, r[1])]
        rows = mine or rows
    if len(rows) > 1:
        # a progress or procedure note before other kinds; still more than one: the person picks
        order = list(NOTE_TYPES)
        rank = lambda r: order.index(_note_type(r[1])[0]) if _note_type(r[1])[0] in order else len(order)      # noqa: E731
        top = min(rank(r) for r in rows)
        rows = [r for r in rows if rank(r) == top]
    if len(rows) > 1:
        return {"status": "ambiguous", "found_by": how, "reason": f"EMA has {len(rows)} notes for {key.dos}; pick the one for this claim.",
                "candidates": [_candidate(r[1], r[3]) for r in rows]}
    d = rows[0][1]
    atts = [c.get("attachment") or {} for c in d.get("content") or []]
    atts = [a for a in atts if a.get("url") or a.get("data")]
    pref = lambda a: CONTENT_PREF.index((a.get("contentType") or "").split(";")[0].strip().lower()) \
        if (a.get("contentType") or "").split(";")[0].strip().lower() in CONTENT_PREF else len(CONTENT_PREF)     # noqa: E731
    att = sorted(atts, key=pref)[0]
    data, ctype = src.content(att)
    ext = EXTENSIONS.get(ctype) or (".pdf" if data[:5] == b"%PDF-" else ".txt")
    cand = _candidate(d, rows[0][3])
    # the same patient's other notes that day, so the person can switch (a procedure note instead of the visit note)
    others = [_candidate(r[1], r[3]) for r in every if r[2].get("id") == rows[0][2].get("id") and r[1].get("id") != d.get("id")]
    return {"status": "found", "found_by": how, "reason": "", "candidates": [cand], "others": others,
            "note": dict(cand, filename=f"ema-note-{key.dos}-{re.sub(r'[^A-Za-z0-9]', '', d.get('id') or 'x')[:24]}{ext}",
                         content_type=ctype, data=data)}


def note_for_claim(mm: ModMed, key: ClaimKey, pick: str | None = None, budget: float = 240.0, since: str | None = None) -> dict:
    """find_note over live searches, or over an export where ModMed lets this app only export (`since`: the first day
    the export must cover, for a batch the earliest date of service in it; default a week before this claim's)."""
    try:
        return find_note(LiveSource(mm), key, pick)
    except Forbidden:
        if not key.dos:
            raise
        since = min(since or key.dos, key.dos)
        since = (date.fromisoformat(since) - timedelta(days=7)).isoformat()
        out = find_note(ExportSource(mm, since, budget), key, pick)
        out["source"] = "export"
        return out


# --------------------------------------------------------------------------- the connection check
def check(settings: Settings | None = None, session: Any = None) -> dict:
    """What works, step by step, for /setup: the settings, the key, discovery, the token, the server, a search. Nothing
    about a patient is read or returned (the search asks for a count only)."""
    s = settings or Settings.from_env()
    steps: list[dict] = []

    def step(name: str, ok: bool | None, detail: str) -> None:
        steps.append({"step": name, "ok": ok, "detail": detail})

    if not s.configured():
        step("Settings", False, "MODMED_CLIENT_ID and MODMED_PRIVATE_KEY (or MODMED_CLIENT_SECRET) are not set.")
        return {"ok": False, "steps": steps, "mode": None}
    step("Settings", True, f"client ID set; {'signed key' if s.private_key else 'client secret'}; FHIR server {s.fhir_base}")
    if s.private_key:
        try:
            jwk = public_jwk(load_private_key(s.private_key), s.key_id or None)
            step("Key", True, f"{jwk['alg']} key, id {jwk['kid']}")
        except ModMedError as exc:
            step("Key", False, str(exc))
            return {"ok": False, "steps": steps, "mode": None}
    try:
        mm = ModMed(s, session=session)
    except ModMedError as exc:
        step("Connection", False, str(exc))
        return {"ok": False, "steps": steps, "mode": None}
    smart = mm.smart_configuration()
    step("Discovery", True if smart.get("token_endpoint") else None,
         f"token endpoint {smart['token_endpoint']}" if smart.get("token_endpoint") else f"no discovery document; using {mm.token_url()}")
    try:
        mm.token()
        step("Sign-in", True, f"token issued{('; scope ' + mm.granted_scope) if mm.granted_scope else ''}{('; audience ' + mm._aud) if mm._aud else ''}")
    except ModMedError as exc:
        step("Sign-in", False, str(exc))
        return {"ok": False, "steps": steps, "mode": None}
    try:
        cap = mm.metadata()
        kinds = {r.get("type") for rest in cap.get("rest") or [] for r in rest.get("resource") or []}
        need = ["Patient", "Encounter", "DocumentReference", "Binary"]
        miss = [k for k in need if kinds and k not in kinds]
        step("Server", not miss, f"FHIR {cap.get('fhirVersion') or '?'}{('; ' + (cap.get('software') or {}).get('name', '')) if cap.get('software') else ''}"
             + (f"; does not list {', '.join(miss)}" if miss else ""))
    except ModMedError as exc:
        step("Server", None, f"capability statement not read: {exc}")
    mode = None
    try:
        try:
            mm.get_json("Patient", {"_count": 1, "_summary": "count"})
        except Forbidden:
            raise
        except ModMedError as exc:
            if exc.status != 400:
                raise
            mm.get_json("Patient", {"_count": 1})          # read and dropped: only whether the search works is kept
        mode = "search"
        step("Search", True, "patients can be searched: notes are looked up claim by claim")
    except Forbidden as exc:
        mode = "export"
        step("Search", None, f"searches are not allowed for this app ({exc}); notes come from Bulk FHIR exports"
             + ("" if s.group_id else ". Set MODMED_GROUP_ID if ModMed gave a group to export from"))
    except ModMedError as exc:
        step("Search", False, str(exc))
    return {"ok": mode is not None, "steps": steps, "mode": mode}

