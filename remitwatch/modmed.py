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
  MODMED_API_KEY        sent as the x-api-key header on calls to the FHIR server and the sign-in server, where ModMed
                        issued one (never to another host)
  MODMED_FHIR_BASE      default https://fhirmp.mmi.prod.fhir.ema-api.com/fhir/r4
  MODMED_TOKEN_URL      default the server's own (its .well-known/smart-configuration), else ModMed's sso.ema.md endpoint
  MODMED_SCOPE          default system/*.read
  MODMED_AUDIENCE       the assertion's audience; default the token URL (the SMART standard), then the FHIR base if refused
  MODMED_GROUP_ID       the Group to export from, for an app ModMed allows only Bulk FHIR exports (no searches)

Finding the note for a claim (find_note). What is checked, and nothing more is promised:

  The patient. First the patient EMA's own patient ID on the claim's EMA rows names (as a FHIR id, then as an
  identifier) and the patient the medical record number the payer echoed names; only when those find nobody, the
  patients with the claim's name. A patient whose family name disagrees with the claim's is never a candidate. A note
  is attached by itself only when exactly one patient is identified by two of the claim's identifiers that agree: EMA's
  patient ID, the medical record number, the full name (the same family name and first name). A name alone, or a name
  and a date, is never enough: two patients can share a name and a visit day. The member ID is not used, because a
  payer can give one ID to a whole family and the 835 carries no birth date. Anything weaker comes back for a person to
  pick, and the choices show who each patient is (name, birth date, identifiers) where names are switched on. Where
  names are off and more than one patient fits, the choices cannot be told apart, so the note is attached by hand.

  The note. Current and signed only. It must be a clinical note in a form that can be read (PDF, HTML, text, RTF,
  Word), and it must be for the date of service: tied to a visit of that day, or carrying that day as its clinical
  date, or, with neither, written on that day. A note written later that names no visit may belong to a later visit.
  That one, and a document that is not a clinical note, are never attached by themselves: a person can pick them.

  More than one note that day for the identified patient: the one written by the clinician who rendered the service
  (NPI), then a progress note before other kinds, else the person picks.

Where ModMed allows the app no searches, the same lookups run over a Bulk FHIR export. The two exports share one time
budget (on a serverless host, short enough to leave time for the review). An export still running when the budget
ends is asked about again by the next lookup in the same process, not started again, and lookups made together wait
for one export.

Read only: nothing is written to EMA. What is read stays in memory or in the appeal packet, as with an uploaded note.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
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
NOTE_AFTER_DAYS = 45            # how far past the visit EMA is searched: a note is often signed days after its visit
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# the forms a note can be read in, best first; a document in no such form (a photo, a scan) is never used
CONTENT_PREF = ("application/pdf", "text/html", "application/xhtml+xml", "text/plain", "text/rtf", "application/rtf", DOCX)
EXTENSIONS = {"application/pdf": ".pdf", "text/html": ".html", "application/xhtml+xml": ".html", "text/plain": ".txt",
              "text/rtf": ".rtf", "application/rtf": ".rtf", DOCX: ".docx"}
UNTYPED = ("", "application/octet-stream", "binary/octet-stream")      # a file that does not say what it is: a person's pick only
TIE_RANK = {"encounter": 0, "period": 1, "date": 2, "after": 3}
TIMEOUT = (10, 60)
MAX_PAGES = 20
MAX_NOTE_BYTES = 15 * 1024 * 1024
MAX_CANDIDATES = 8              # patients looked through when none is tied to the claim by two identifiers
EXPORT_TTL = 30 * 60            # a finished Bulk FHIR export is reused this long, so a batch of notes takes one export
EXPORT_KEEP = 6 * 60 * 60       # an export still running is asked about again for this long before a new one is started
SERVERLESS_RESERVE = 120        # seconds a serverless function keeps back after the lookup, for the review and the packet
STILL_RUNNING = "ModMed's export is still running; try again in a few minutes."


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
    private_key: str = field(default="", repr=False)        # the three secrets never appear when the settings are printed
    key_id: str = ""
    client_secret: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
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


CURVES = {"secp384r1": ("ES384", "P-384"), "secp256r1": ("ES256", "P-256"), "secp521r1": ("ES512", "P-521")}
WRONG_KEY_KIND = ("MODMED_PRIVATE_KEY must be an EC key (P-384, P-256 or P-521) or an RSA key. "
                  "Make one with: python -m remitwatch.cli modmed-key")


def load_private_key(pem: str):
    """The app's private key. A key of a kind the sign-in cannot use (Ed25519, another curve) is refused here, in plain
    words, so every caller hears it the same way."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    try:
        key = serialization.load_pem_private_key(_pem(pem).encode(), password=None)
    except Exception as exc:
        raise ModMedError(f"MODMED_PRIVATE_KEY is not a readable private key ({type(exc).__name__}). Paste the whole PEM, "
                          "including the BEGIN and END lines; line breaks may be written as \\n.") from exc
    if isinstance(key, rsa.RSAPrivateKey) or (isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name in CURVES):
        return key
    raise ModMedError(WRONG_KEY_KIND)


def key_alg(key) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    if isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name in CURVES:
        return CURVES[key.curve.name][0]
    if isinstance(key, rsa.RSAPrivateKey):
        return "RS384"
    raise ModMedError(WRONG_KEY_KIND)


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _int_b64u(n: int, length: int | None = None) -> str:
    return _b64u(n.to_bytes(length or max(1, (n.bit_length() + 7) // 8), "big"))


def public_jwk(key, kid: str | None = None) -> dict:
    """The key's public half as a JWK (no private member), with its algorithm and id."""
    from cryptography.hazmat.primitives.asymmetric import ec
    alg = key_alg(key)                  # a key of another kind is refused here, in plain words
    nums = key.public_key().public_numbers()
    if isinstance(key, ec.EllipticCurvePrivateKey):
        size = (key.curve.key_size + 7) // 8
        jwk = {"kty": "EC", "crv": CURVES[key.curve.name][1], "x": _int_b64u(nums.x, size), "y": _int_b64u(nums.y, size)}
    else:
        jwk = {"kty": "RSA", "n": _int_b64u(nums.n), "e": _int_b64u(nums.e)}
    jwk.update({"kid": kid or thumbprint(jwk), "alg": alg, "use": "sig", "key_ops": ["verify"]})
    return jwk


def thumbprint(jwk: dict) -> str:
    """RFC 7638: the SHA-256 of the key's required members, in order, without spaces."""
    req = ("crv", "kty", "x", "y") if jwk["kty"] == "EC" else ("e", "kty", "n")
    return _b64u(hashlib.sha256(json.dumps({k: jwk[k] for k in req}, separators=(",", ":"), sort_keys=True).encode()).digest())


_JWKS: dict[str, dict] = {}
_JWKS_LOCK = threading.Lock()


def jwks(settings: Settings | None = None) -> dict | None:
    """The public key set the site publishes for ModMed, or None without a key. Worked out once per key: the route is
    open to anyone, and reading an RSA key takes a tenth of a second."""
    s = settings or Settings.from_env()
    if not s.private_key:
        return None
    k = hashlib.sha256(f"{s.key_id}\n{s.private_key}".encode()).hexdigest()
    with _JWKS_LOCK:
        hit = _JWKS.get(k)
    if hit is None:
        hit = {"keys": [public_jwk(load_private_key(s.private_key), s.key_id or None)]}
        with _JWKS_LOCK:
            _JWKS.clear()           # one key at a time: a changed key replaces the old one
            _JWKS[k] = hit
    return json.loads(json.dumps(hit))


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


class Found(list):
    """What a search returned; `truncated` when it stopped at its page limit with more pages left."""
    truncated = False


class ModMed:
    """One connection to ModMed's FHIR API: a token kept until shortly before it expires, calls retried when ModMed asks
    for patience (429, 503), a token renewed once when refused (401). The bearer token and the API key go only to the
    FHIR server (and to export files that say they need the token); the API key also goes to the sign-in server. Never
    to another host, such as one an attachment's link names."""

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
            h = {"Accept": accept, **(headers or {})}
            if send_token:              # the API key and the token go together, and only where the token may go
                h = self._plain(h)
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

    def search(self, resource: str, params: dict, max_pages: int = MAX_PAGES) -> Found:
        """Every resource of the type a search returns, following the server's next links up to `max_pages` pages. A
        search that stops there with more pages left says so (`truncated`); the page after the last is never fetched."""
        out = Found()
        bundle = self.get_json(resource, params)
        pages = 1
        while True:
            out += [e["resource"] for e in bundle.get("entry") or [] if isinstance(e.get("resource"), dict)
                    and e["resource"].get("resourceType") == resource]
            nxt = next((link.get("url") for link in bundle.get("link") or [] if link.get("relation") == "next"), None)
            if not nxt:
                break
            if pages >= max_pages:
                out.truncated = True
                break
            bundle = self.get_json(nxt)
            pages += 1
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
    def _kick_off(self, types: list[str], since: str | None) -> str:
        path = f"Group/{self.s.group_id}/$export" if self.s.group_id else "Patient/$export"
        params = {"_type": ",".join(types)}
        if since:
            params["_since"] = since
        r = self.get(path, params, headers={"Prefer": "respond-async"})
        status_url = r.headers.get("Content-Location")
        if r.status_code != 202 or not status_url:
            raise ModMedError(f"ModMed did not start the export ({r.status_code}).", r.status_code)
        return status_url

    def export(self, types: Iterable[str], since: str | None = None, budget: float = 240.0,
               deadline: float | None = None) -> dict[str, list[dict]]:
        """A Bulk FHIR export of these resource types (the Group's when MODMED_GROUP_ID is set, else every patient's),
        waited for until `deadline` (on this connection's clock; else `budget` seconds from now), asking once more when
        the wait ends; the resources by type. An export still running then is kept (_PENDING): the next call for the
        same types asks about it again instead of starting another."""
        types = list(types)
        end = deadline if deadline is not None else self.clock() + budget
        pkey = (self.s.fingerprint(), tuple(types))
        with _PENDING_LOCK:
            pend = _PENDING.get(pkey)
            if pend and (self.clock() - pend[0] > EXPORT_KEEP or (pend[1] or "") > (since or "")):
                pend = None             # too old, or it starts later than this call needs
        status_url = pend[2] if pend else None
        if not status_url:
            status_url = self._kick_off(types, since)
            with _PENDING_LOCK:
                _PENDING[pkey] = (self.clock(), since, status_url)
        manifest = None
        while True:
            try:
                s = self.get(status_url, accept="application/json")
            except ModMedError as exc:
                with _PENDING_LOCK:
                    _PENDING.pop(pkey, None)
                if pend and exc.status in (404, 410):       # the export kept from before is gone: start one now
                    pend = None
                    status_url = self._kick_off(types, since)
                    with _PENDING_LOCK:
                        _PENDING[pkey] = (self.clock(), since, status_url)
                    continue
                raise
            if s.status_code == 200:
                manifest = s.json()
                break
            left = end - self.clock()
            if left <= 0:
                break
            try:
                wait = float(s.headers.get("Retry-After") or 5)
            except ValueError:
                wait = 5.0
            self.sleep(min(max(wait, 1.0), 30.0, left))       # then asked once more, however little time is left
        if manifest is None:
            raise ModMedError(STILL_RUNNING, 202)
        with _PENDING_LOCK:
            _PENDING.pop(pkey, None)
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
# exports still running when their caller's time ran out: (settings, types) -> (started, _since, status link)
_PENDING: dict[tuple, tuple[float, str | None, str]] = {}
_PENDING_LOCK = threading.Lock()


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

    def for_claim(self, key: "ClaimKey") -> "LiveSource":
        """The records a claim may be matched against (all of them here; an upload source narrows them)."""
        return self

    def identify(self, p: dict, key: "ClaimKey") -> list[str]:
        """Which of the claim's identifiers this patient's record agrees with (find_note needs two, or a person)."""
        return _agree(p, key)

    def patient(self, pid: str) -> dict | None:
        return self.mm.read("Patient", pid) if re.fullmatch(r"[A-Za-z0-9\-.]{1,64}", pid or "") else None

    def patients_by_identifier(self, value: str) -> list[dict]:
        return self.mm.search("Patient", {"identifier": value}, max_pages=2)

    def patients_by_name(self, family: str, given: str) -> list[dict]:
        """Everyone the server finds for the name (FHIR name searches match the start of a name); `truncated` when there
        were more pages than are read."""
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


# finished exports, reused for EXPORT_TTL: (settings, "people" or "notes") -> (fetched, first day covered, resources by type)
_EXPORTS: dict[tuple[str, str], tuple[float, str, dict]] = {}
_EXPORTS_LOCK = threading.Lock()        # one export at a time: lookups made together wait for it instead of starting more


def _exported(mm: ModMed, part: str, types: list[str], since_day: str, end: float) -> dict:
    """One part of the export, from the cache when it covers `since_day` and is fresh, else fetched by `end`."""
    fp = mm.s.fingerprint()

    def cached():
        hit = _EXPORTS.get((fp, part))
        return hit[2] if hit and hit[1] <= since_day and mm.clock() - hit[0] < EXPORT_TTL else None
    got = cached()
    if got is not None:
        return got
    if not _EXPORTS_LOCK.acquire(timeout=max(0.0, end - mm.clock())):
        raise ModMedError(STILL_RUNNING, 202)
    try:
        got = cached()                  # another lookup may have fetched it meanwhile
        if got is None:
            got = mm.export(types, f"{since_day}T00:00:00Z" if since_day else None, deadline=end)
            for k in [k for k in _EXPORTS if k[0] != fp]:
                _EXPORTS.pop(k, None)
            _EXPORTS[(fp, part)] = (mm.clock(), since_day, got)
        return got
    finally:
        _EXPORTS_LOCK.release()


class ExportSource(LiveSource):
    """The same lookups over a Bulk FHIR export, for an app ModMed lets only export: patients (all of them, as a
    patient's record may not have changed lately), and the visits and notes changed since the window began. Both
    exports share one time budget."""

    def __init__(self, mm: ModMed, since_day: str, budget: float = 240.0):
        super().__init__(mm)
        end = mm.clock() + budget
        data = dict(_exported(mm, "people", ["Patient", "Practitioner"], "", end))
        data.update(_exported(mm, "notes", ["Encounter", "DocumentReference"], since_day, end))
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


def _parts(name: str | None) -> set[str]:
    """The words of a family name, letters only ("Smith-Jones" -> SMITH, JONES)."""
    return {w for w in (_norm(x) for x in re.split(r"[\s\-]+", name or "")) if w}


def _family_ok(p: dict, family: str) -> bool:
    """The claim's family name agrees with one of the patient's: the same letters, or one whole word of three letters or
    more in common (a hyphenated or a married name: SMITH and SMITH-JONES). A name that merely contains the other (HALL,
    MARSHALL) or starts like it (HALL, HALLORAN) does not agree."""
    want, words = _norm(family), {w for w in _parts(family) if len(w) >= 3}
    if not want:
        return False
    for n in _names(p):
        got = _norm(n.get("family"))
        if got and (got == want or words & {w for w in _parts(n.get("family")) if len(w) >= 3}):
            return True
    return False


def _first(v: str | None) -> str:
    """The first word of a first name, letters only (a middle initial typed into the first-name field is ignored)."""
    return _norm((v or "").split()[0]) if (v or "").split() else ""


def _given_ok(p: dict, given: str) -> bool:
    """Whether the patient can be the claim's by first name, for a person to decide: the same first word, or, when both
    have three letters or more, one the start of the other (ANN and ANNETTE, or a name the payer cut short). A claim
    with no first name matches nobody."""
    want = _first(given)
    if not want:
        return False
    for n in _names(p):
        for g in n.get("given") or []:
            got = _first(g)
            if got == want or (min(len(got), len(want)) >= 3 and (got.startswith(want) or want.startswith(got))):
                return True
    return False


def _name_agrees(p: dict, family: str, given: str) -> bool:
    """The full name agrees: the same family name (letters only) and the same first word of the first name, in one of
    the patient's names. This is what "the name" counts for when the patient is identified."""
    want_f, want_g = _norm(family), _first(given)
    if not want_f or not want_g:
        return False
    return any(_norm(n.get("family")) == want_f and want_g == _first((n.get("given") or [""])[0]) for n in _names(p))


def _encounter_on(e: dict, dos: str) -> bool:
    per = e.get("period") or {}
    start, end = _day(per.get("start")), _day(per.get("end")) or _day(per.get("start"))
    return bool(start) and start <= dos <= (end or start)


@dataclass
class ClaimKey:
    """What a claim says about whose note it needs: the patient's name, the date of service, the rendering clinician,
    any identifier that names the patient in EMA, and the claim's own ID. Held in memory only."""
    family: str
    given: str
    dos: str
    npi: str = ""
    ema_patient_ids: list[str] = field(default_factory=list)
    mrn: str = ""
    claim_id: str = ""              # EMA's Claim ID, which the 835 echoes as the patient control number


def claim_key(detail: dict, raw_claim: dict | None, ema_patient_ids: Iterable[str] = ()) -> ClaimKey:
    f = detail.get("finding") or {}
    c = raw_claim or {}
    dos = _day(f.get("service_date") or c.get("service_date_start") or c.get("statement_from") or "")
    return ClaimKey(family=c.get("patient_last") or c.get("corrected_patient_last") or "", given=c.get("patient_first") or c.get("corrected_patient_first") or "",
                    dos=dos, npi=f.get("rendering_provider_npi") or c.get("rendering_provider_npi") or "",
                    ema_patient_ids=[str(x) for x in dict.fromkeys(i for i in ema_patient_ids if i)], mrn=c.get("medical_record_number") or "",
                    claim_id=f.get("patient_control_number") or c.get("patient_control_number") or "")


PERSON = "a person"         # what identifies the patient when a person chose the note (a pick, a note attached to the item)


def _nid(v: Any) -> str:
    """An identifier as compared: letters and digits, upper case."""
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def _agree(p: dict, key: ClaimKey) -> list[str]:
    """The claim's identifiers this patient's record carries, each value counted once (EMA's patient ID and the
    record number can be the same number), and "name" when the full name agrees. The member ID is not one of them:
    a payer can give one ID to a whole family, and the 835 carries no birth date to tell a parent from a child of the
    same name."""
    have = {_nid(p.get("id"))} | {_nid(i.get("value")) for i in p.get("identifier") or [] if isinstance(i, dict)}
    have.discard("")
    out: list[str] = []
    used: set[str] = set()
    for label, vals in (("EMA patient ID", key.ema_patient_ids), ("medical record number", [key.mrn])):
        hit = ({_nid(v) for v in vals} - {""}) & have - used
        if hit:
            used |= hit
            out.append(label)
    if _name_agrees(p, key.family, key.given):
        out.append("name")
    return out


def _enough(ids: list[str]) -> bool:
    return PERSON in ids or len(ids) >= 2


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
    the day), 'date' (no link: written that day), 'after' (no link: written within NOTE_AFTER_DAYS after the day, so it
    may be a later visit's note: only a person can choose it), or None."""
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
    if written == dos:
        return "date"
    if written and dos < written <= (date.fromisoformat(dos) + timedelta(days=NOTE_AFTER_DAYS)).isoformat():
        return "after"
    return None


def _ctype(att: dict) -> str:
    return (att.get("contentType") or "").split(";")[0].strip().lower()


def _attachments(d: dict) -> list[dict]:
    atts = [c.get("attachment") or {} for c in d.get("content") or []]
    atts = [a for a in atts if a.get("url") or a.get("data")]
    return sorted(atts, key=lambda a: CONTENT_PREF.index(_ctype(a)) if _ctype(a) in CONTENT_PREF else len(CONTENT_PREF))


def _is_note(d: dict) -> bool:
    """A clinical note: one of the note types appeals use (NOTE_TYPES), or, with no LOINC type at all, filed as a
    clinical note. A photo, a consent form, a pathology or imaging report, an outside record: not one."""
    if _note_type(d)[0]:
        return True
    if any(cd.get("code") and (cd.get("system") or "http://loinc.org") == "http://loinc.org" for cd in (d.get("type") or {}).get("coding") or []):
        return False                # a LOINC type that is not a visit note
    return any(cd.get("code") == "clinical-note" for c in d.get("category") or [] for cd in (c or {}).get("coding") or [])


def _readable(d: dict) -> bool:
    """The note comes in a form the review and the packet read (CONTENT_PREF). A file that does not say what it is, or a
    picture, is left to a person."""
    atts = _attachments(d)
    return bool(atts) and _ctype(atts[0]) in CONTENT_PREF


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


def _who(p: dict) -> dict:
    """What tells one patient from another, for a person picking between them (only where names are shown)."""
    n = next(iter(_names(p)), {})
    name = " ".join(filter(None, [" ".join(x for x in n.get("given") or [] if x), n.get("family") or ""])).strip() or n.get("text") or ""
    ids = [str(i.get("value")) for i in p.get("identifier") or [] if isinstance(i, dict) and i.get("value")]
    return {"name": name.title() if name.isupper() or name.islower() else name, "birth_date": p.get("birthDate") or None, "ids": ids[:4]}


class _Row:
    __slots__ = ("n", "p", "d", "tie")

    def __init__(self, n: int, p: dict, d: dict, tie: str):
        self.n, self.p, self.d, self.tie = n, p, d, tie

    @property
    def auto(self) -> bool:
        """The note may be taken without a person: tied to the day itself, a clinical note, in a form that reads."""
        return self.tie in ("encounter", "period", "date") and _is_note(self.d) and _readable(self.d)


def _candidate(r: _Row, ids: list[str], reveal: bool) -> dict:
    code, label = _note_type(r.d)
    c = {"id": r.d.get("id"), "date": _doc_date(r.d), "written": _day(r.d.get("date")), "type": label, "loinc": code,
         "author": _author_label(r.d), "tie": r.tie, "status": r.d.get("docStatus") or "final",
         "patient_n": r.n + 1, "identified_by": ids, "pick_only": not r.auto}
    if reveal:
        c["patient"] = _who(r.p)
    return c


def _patients(src: LiveSource, key: ClaimKey) -> tuple[list[dict], str, bool, bool]:
    """The EMA patients the claim can belong to: (patients, how they were found, whether the list is whole, whether an
    identifier on the claim found nobody or someone of another family name). EMA's patient ID on the claim's EMA rows
    and the medical record number the payer echoed come first; only when they find nobody is the name searched. A
    patient whose family name disagrees with the claim's is never one of them."""
    found: dict[str, dict] = {}
    how = ""
    missed = False
    for label, vals in (("EMA patient ID", key.ema_patient_ids), ("medical record number", [key.mrn] if key.mrn else [])):
        for v in vals:
            p = src.patient(v) if label == "EMA patient ID" else None
            hits = [p] if p else list(src.patients_by_identifier(v))
            if not hits:
                missed = True
            for x in hits:
                if _family_ok(x, key.family):
                    found.setdefault(x.get("id") or v, x)
                    how = how or label
                else:
                    missed = True
    if found:
        return list(found.values()), how, True, missed
    if not _first(key.given):
        return [], "name", True, missed
    hits = src.patients_by_name(key.family, key.given)
    by_name = {x.get("id") or str(i): x for i, x in enumerate(hits) if _family_ok(x, key.family) and _given_ok(x, key.given)}
    return list(by_name.values()), "name", not getattr(hits, "truncated", False), missed


def _found(src: LiveSource, key: ClaimKey, how: str, ids: list[str], row: _Row, others: list[_Row], reveal: bool) -> dict:
    d = row.d
    att = _attachments(d)[0]
    data, ctype = src.content(att)
    ext = EXTENSIONS.get(ctype) or (".pdf" if data[:5] == b"%PDF-" else ".txt" if ctype.startswith("text/")
                                    else mimetypes.guess_extension(ctype or "") or ".bin")
    cand = _candidate(row, ids, reveal)
    return {"status": "found", "found_by": how, "identified_by": ids, "reason": "", "candidates": [cand],
            "others": [_candidate(o, ids, reveal) for o in others],
            "note": dict(cand, filename=f"ema-note-{key.dos}-{re.sub(r'[^A-Za-z0-9]', '', d.get('id') or 'x')[:24]}{ext}",
                         content_type=ctype, data=data)}


ATTACH_BY_HAND = "Save the visit note from EMA and attach it to the item."


def find_note(src: LiveSource, key: ClaimKey, pick: str | None = None, reveal: bool = False) -> dict:
    """The note for a claim: {'status': 'found' | 'ambiguous' | 'not_found', 'reason', 'found_by', 'identified_by',
    'candidates', and with 'found': 'note' {id, type, date, author, filename, content_type, data} and 'others' (the same
    patient's other documents that day)}.

    A note is taken without a person only when exactly one patient is identified by two of the claim's identifiers that
    agree (EMA's patient ID, the medical record number, the full name), or by a person (a note attached to the item),
    and the note is a signed clinical note of the date of service. Everything weaker comes back 'ambiguous' for a person
    to pick, and whether more than one patient is involved is decided over every candidate before anything is narrowed.
    `pick` chooses one of the candidates by its id. Between two patients a person can pick only where the choices show
    who each patient is (`reveal`, the site's names switch); otherwise the note is attached by hand."""
    if not key.dos or not key.family:
        return {"status": "not_found", "reason": "The claim has no patient name or date of service to look up.", "candidates": []}
    src = src.for_claim(key)
    patients, how, whole, missed = _patients(src, key)
    if not patients:
        why = "The claim has no first name to look the patient up by." if how == "name" and not _first(key.given) \
            else f"No patient in EMA matches this claim ({how})."
        return {"status": "not_found", "reason": why, "candidates": [], "found_by": how}
    if not whole or len(patients) > MAX_CANDIDATES:
        return {"status": "ambiguous", "found_by": how, "candidates": [],
                "reason": "So many patients in EMA have a name like this claim's that they cannot all be looked through here. " + ATTACH_BY_HAND}
    rows: list[_Row] = []
    for n, p in enumerate(patients):
        pid = p.get("id") or ""
        enc_ids = {e.get("id") for e in src.encounters(pid, key.dos) if e.get("id")}
        for d in src.documents(pid, key.dos):
            if _usable(d):
                tie = _ties(d, key.dos, enc_ids)
                if tie:
                    rows.append(_Row(n, p, d, tie))
    if not rows:
        return {"status": "not_found", "found_by": how, "candidates": [],
                "reason": f"EMA has no signed note for {key.dos} for this patient." + (" (More than one patient has this name; none had a note that day.)" if len(patients) > 1 else "")}
    # every candidate patient, with what identifies each, before anything is narrowed
    with_rows = sorted({r.n for r in rows})
    ids = {n: src.identify(patients[n], key) for n in with_rows}
    strong = [n for n in with_rows if _enough(ids[n])]
    several = len(patients) > 1         # more than one patient fits the claim, whether or not each has a note that day
    cands = lambda rs: [_candidate(r, ids[r.n], reveal) for r in rs]      # noqa: E731

    def by_hand(reason: str) -> dict:
        return {"status": "ambiguous", "found_by": how, "candidates": [], "reason": reason + " " + ATTACH_BY_HAND}
    if pick:
        chosen = [r for r in rows if r.d.get("id") == pick]
        if not chosen:
            if several and not reveal:
                return by_hand("That note is not one of this claim's, and more than one patient who fits the claim has a note that day.")
            return {"status": "ambiguous", "found_by": how, "reason": "That note is not one of this claim's.", "candidates": cands(rows)}
        r = chosen[0]
        if not reveal and strong != [r.n] and (several or not ids[r.n]):
            return by_hand("More than one patient in EMA fits this claim, or the name agrees only in part, and names are switched "
                           "off here, so the person cannot be checked.")
        return _found(src, key, how, [x for x in ids[r.n] if x != PERSON] + [PERSON], r, [o for o in rows if o.n == r.n and o is not r], reveal)
    if len(strong) == 1:
        n = strong[0]
        mine = [r for r in rows if r.n == n]
        auto = [r for r in mine if r.auto]
        if not auto:
            return {"status": "ambiguous", "found_by": how, "identified_by": ids[n], "candidates": cands(mine),
                    "reason": f"EMA has no signed visit note tied to {key.dos} for this patient. These documents may be it: "
                              "a later note with no visit named, or a document that is not a visit note. Pick one only if it is this visit's note."}
        best = min(TIE_RANK[r.tie] for r in auto)
        top = [r for r in auto if TIE_RANK[r.tie] == best]
        if len(top) > 1 and key.npi:
            top = [r for r in top if key.npi in _author_npis(src, r.d)] or top
        if len(top) > 1:
            # a progress or procedure note before other kinds; still more than one: the person picks
            order = list(NOTE_TYPES)
            rank = lambda r: order.index(_note_type(r.d)[0]) if _note_type(r.d)[0] in order else len(order)      # noqa: E731
            low = min(rank(r) for r in top)
            top = [r for r in top if rank(r) == low]
        if len(top) > 1:
            return {"status": "ambiguous", "found_by": how, "identified_by": ids[n], "candidates": cands(mine),
                    "reason": f"EMA has {len(top)} notes for {key.dos} for this patient; pick the one for this claim."}
        return _found(src, key, how, ids[n], top[0], [r for r in mine if r is not top[0]], reveal)
    if len(strong) > 1 or several:
        if not reveal:
            return by_hand("More than one patient in EMA fits this claim, and names are switched off here, so they cannot be told apart.")
        return {"status": "ambiguous", "found_by": how, "candidates": cands(rows),
                "reason": ("More than one patient who fits the claim has a note that day. Check who each is, then pick the note."
                           if len(with_rows) > 1 else "More than one patient in EMA fits this claim, and one of them has a note that "
                           "day. Check that it is this claim's patient, then pick the note.")}
    n = with_rows[0]
    if not reveal and not ids[n]:
        return by_hand("The name in EMA agrees with the claim's only in part, and names are switched off here, so it cannot be checked.")
    return {"status": "ambiguous", "found_by": how, "identified_by": ids[n], "candidates": cands(rows),
            "reason": ("The claim's EMA patient ID or record number did not lead to a patient of this name in EMA, so the patient "
                       "was found by name. "
                       if missed and how == "name" else "") + _said(ids[n])
            + " A note goes out by itself only when two identifiers agree. Check that the note is this patient's, then pick it."}


def _said(ids: list[str]) -> str:
    if ids == ["name"]:
        return "Only the name agrees with the claim."
    if ids:
        return f"Only the {ids[0]} agrees with the claim; the first name differs or is missing."
    return "The name agrees only in part (the first name differs, or one is the start of the other)."


def note_for_claim(mm: ModMed, key: ClaimKey, pick: str | None = None, budget: float = 240.0, since: str | None = None,
                   reveal: bool = False) -> dict:
    """find_note over live searches, or over an export where ModMed lets this app only export (`since`: the first day
    the export must cover, for a batch the earliest date of service in it; default a week before this claim's).
    `budget` is the time the export may take in all; on a serverless host it is kept short enough to leave the function
    time for the review and the packet (runtime.max_seconds less SERVERLESS_RESERVE)."""
    try:
        return find_note(LiveSource(mm), key, pick, reveal)
    except Forbidden:
        if not key.dos:
            raise
        from . import runtime
        if runtime.serverless():
            budget = min(budget, max(30.0, runtime.max_seconds() - SERVERLESS_RESERVE))
        since = min(since or key.dos, key.dos)
        since = (date.fromisoformat(since) - timedelta(days=7)).isoformat()
        out = find_note(ExportSource(mm, since, budget), key, pick, reveal)
        out["source"] = "export"
        return out


# --------------------------------------------------------------------------- the connection check
def check(settings: Settings | None = None, session: Any = None) -> dict:
    """What works, step by step, for /setup: the settings, the key, discovery, the token, the server, a search. Nothing
    about a patient is returned. The search asks for a count only; a server that refuses a count search is asked for
    one patient's id alone (no name, no birth date), which is dropped."""
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
            mm.get_json("Patient", {"_count": 1, "_elements": "id"})      # an id at most, dropped: only whether the search works is kept
        mode = "search"
        step("Search", True, "patients can be searched: notes are looked up claim by claim")
    except Forbidden as exc:
        mode = "export"
        step("Search", None, f"searches are not allowed for this app ({exc}); notes come from Bulk FHIR exports"
             + ("" if s.group_id else ". Set MODMED_GROUP_ID if ModMed gave a group to export from"))
    except ModMedError as exc:
        step("Search", False, str(exc))
    return {"ok": mode is not None, "steps": steps, "mode": mode}

