#!/usr/bin/env python3
"""
proxyclick.py - browser-free client for the Proxyclick / Eptura Visitor web-app API.

Reverse-engineered from app.proxyclick.com on 2026-10-26 (the same private API the
Angular web app calls). Python 3.9+, standard library only.

Credentials (never printed):
  PROXYCLICK_EMAIL / PROXYCLICK_PASSWORD environment variables, or a file
  ~/.config/proxyclick/credentials.env with those two KEY=VALUE lines.
Token cache:
  ~/.config/proxyclick/token.json (mode 0600). The JWT lives 24 h; it is reused
  until 5 minutes before expiry and refreshed automatically on a 401.
  The login response also sets an HttpOnly cookie (__Secure-Fgp) whose hash is
  embedded in the JWT ("fingerprint" claim); the API rejects the token without
  it, so the cookie is cached and sent along with every call.

Run `proxyclick.py --help` or `proxyclick.py create-visit --help`.
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

BASE = "https://app.proxyclick.com"
CONF_DIR = os.path.expanduser(os.environ.get("PROXYCLICK_CONFIG_DIR", "~/.config/proxyclick"))
CRED_FILE = os.path.join(CONF_DIR, "credentials.env")
TOKEN_FILE = os.path.join(CONF_DIR, "token.json")
CREATED_FILE = os.path.join(CONF_DIR, "visitors-created.json")  # email -> visitor id, created by this script
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------- helpers
class ApiError(Exception):
    def __init__(self, status, method, url, body):
        self.status, self.method, self.url, self.body = status, method, url, body
        super().__init__(f"HTTP {status} {method} {url}: {body[:400]}")


def die(msg, code=2):
    print("error: " + msg, file=sys.stderr)
    sys.exit(code)


def out(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def load_credentials():
    """Env vars win over the credentials file. Returns (email, password)."""
    vals = {}
    if os.path.exists(CRED_FILE):
        with open(CRED_FILE) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    email = os.environ.get("PROXYCLICK_EMAIL") or vals.get("PROXYCLICK_EMAIL")
    password = os.environ.get("PROXYCLICK_PASSWORD") or vals.get("PROXYCLICK_PASSWORD")
    return email, password


def jwt_claims(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def http(method, path, token=None, body=None, params=None, verbose=False, cookie=None, resp_headers=None):
    url = path if path.startswith("http") else BASE + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json, text/plain, */*", "User-Agent": USER_AGENT}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json;charset=utf-8"
    if token:
        headers["Authorization"] = "Bearer " + token
    if cookie:
        headers["Cookie"] = cookie
    if verbose:
        print(f"> {method} {url}" + (f" {json.dumps(body)[:600]}" if body is not None else ""), file=sys.stderr)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            text = resp.read().decode("utf-8", "replace")
            status = resp.status
            if resp_headers is not None:
                resp_headers["set-cookie"] = resp.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, method, url, e.read().decode("utf-8", "replace"))
    if verbose:
        print(f"< {status} ({len(text)} bytes)", file=sys.stderr)
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


# --------------------------------------------------------------------------- client
class Client:
    def __init__(self, company=None, verbose=False):
        self.company = company or os.environ.get("PROXYCLICK_COMPANY")
        self.verbose = verbose
        self.token = None
        self.cookie = None
        self._me = None
        self._companies = None
        self._fields = None
        self._params = None

    # -- auth
    def login(self, force=False):
        if not force:
            cached = self._cached_token()
            if cached:
                self.token, self.cookie = cached
                return "cached"
        email, password = load_credentials()
        if not email or not password:
            die("no credentials. Set PROXYCLICK_EMAIL and PROXYCLICK_PASSWORD in the environment "
                f"or in {CRED_FILE} (one KEY=VALUE per line, chmod 600).")
        payload = {"username": email, "password": password, "directory": "proxyclick",
                   "rememberMe": False, "destination": "/", "submit": "Log+in"}
        hdrs = {}
        try:
            res = http("POST", "/authenticate", body=payload, verbose=False, resp_headers=hdrs)
        except ApiError as e:
            if e.status in (400, 401, 403):
                die(f"login rejected (HTTP {e.status}). Check the email/password for {email}.")
            raise
        token = res.get("access_token") if isinstance(res, dict) else None
        if not token:
            die("login did not return an access_token; the login API may have changed. Response keys: "
                + (",".join(res.keys()) if isinstance(res, dict) else str(res)[:200]))
        cookies = [c.split(";", 1)[0].strip() for c in hdrs.get("set-cookie", [])]
        fgp = [c for c in cookies if c.startswith("__Secure-Fgp=")]
        self.cookie = "; ".join(fgp or cookies) or None
        if not fgp:
            print("warning: login did not set the __Secure-Fgp cookie; API calls may be rejected", file=sys.stderr)
        self.token = token
        self._save_token(token, self.cookie)
        return "fresh"

    def _cached_token(self):
        try:
            with open(TOKEN_FILE) as fh:
                d = json.load(fh)
            if d.get("exp", 0) - 300 > time.time():
                return d["token"], d.get("cookie")
        except Exception:
            pass
        return None

    def _save_token(self, token, cookie):
        os.makedirs(CONF_DIR, mode=0o700, exist_ok=True)
        exp = jwt_claims(token).get("exp") or int(time.time()) + 23 * 3600
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"token": token, "cookie": cookie, "exp": exp, "saved_at": int(time.time())}, fh)

    def call(self, method, path, body=None, params=None, _retry=True):
        if not self.token:
            self.login()
        try:
            return http(method, path, self.token, body, params, self.verbose, cookie=self.cookie)
        except ApiError as e:
            if e.status == 401 and _retry:
                self.login(force=True)
                return self.call(method, path, body, params, _retry=False)
            raise

    # -- discovery
    def me(self):
        if self._me is None:
            self._me = self.call("GET", "/api/me")
        return self._me

    def companies(self):
        if self._companies is None:
            self._companies = self.call("GET", "/api/me/companies")
        return self._companies

    def co(self):
        if not self.company:
            self.company = self.me().get("originalCompanyId")
            if not self.company:
                cos = self.companies()
                if len(cos) == 1:
                    self.company = cos[0]["id"]
                else:
                    die("account has several companies; pass --company CO-XXXX. Options: "
                        + ", ".join(f"{c['id']} ({c['name']})" for c in cos))
        return self.company

    def company_info(self):
        for c in self.companies():
            if c.get("id") == self.co():
                return c
        return self.call("GET", f"/api/app/{self.co()}/profile")

    def tz(self):
        return self.company_info().get("timezone") or "UTC"

    def params(self):
        if self._params is None:
            try:
                self._params = self.call("GET", f"/api/app/{self.co()}/vm/params") or {}
            except ApiError:
                self._params = {}
        return self._params

    def custom_fields(self):
        if self._fields is None:
            self._fields = [f for f in self.call("GET", f"/api/app/{self.co()}/vm/custom-fields") or []
                            if f.get("published", True)]
        return self._fields

    def visitor_types(self):
        return self.call("GET", f"/api/private/companies/{self.co()}/vm/visitorTypes") or []

    # -- people
    def all_visitors(self):
        return self.call("GET", f"/api/app/{self.co()}/vm/visitors") or []

    def visitors_by_prefix(self, q):
        return self.call("GET", f"/api/app/{self.co()}/vm/visitors", params={"name": q}) or []

    def address_book(self, q, kind="visitor", page_size=10):
        res = self.call("GET", f"/api/me/{self.co()}/address-book",
                        params={"name": q, "type": kind, "page": 1, "pageSize": page_size})
        return (res or {}).get("contacts", [])

    def visitor(self, vid):
        return self.call("GET", f"/api/app/{self.co()}/vm/visitors/{vid}")

    # -- visits
    def create_meeting(self, payload):
        return self.call("POST", f"/api/app/{self.co()}/vm/meetings", body=payload)

    def set_visit_fields(self, day, mid, vid, fields):
        return self.call("PUT", f"/api/app/{self.co()}/vm/visits/{day}/{mid}/{vid}/custom-fields",
                         body={"customFields": fields, "auditChanges": False})

    def invite(self, mid):
        return self.call("POST", f"/api/app/{self.co()}/vm/meetings/{mid}/invite", body={})

    def add_to_address_book(self, vid):
        return self.call("PUT", f"/api/me/{self.co()}/address-book/{vid}", body={})

    def meeting(self, mid):
        return self.call("GET", f"/api/app/{self.co()}/vm/meetings/{mid}")

    def visits(self, day):
        return self.call("GET", f"/api/app/{self.co()}/vm/visits/{day}") or {}


# --------------------------------------------------------------------------- domain helpers
def slim_visitor(v):
    return {"id": v.get("id"), "firstname": v.get("firstname"), "lastname": v.get("lastname"),
            "email": v.get("email"), "companyName": v.get("companyName"),
            "phone": v.get("phone") or v.get("mobile") or "",
            "lastVisitAt": v.get("lastVisitAt")}


def _created_cache():
    try:
        with open(CREATED_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def remember_created(email, vid):
    if not email:
        return
    d = _created_cache()
    d[email.strip().lower()] = {"id": vid, "at": int(time.time())}
    os.makedirs(CONF_DIR, mode=0o700, exist_ok=True)
    with open(CREATED_FILE, "w") as fh:
        json.dump(d, fh)


def find_visitors(c, query):
    """Email -> exact match over all visitors (plus the local created-by-this-script cache, because
    a visitor only appears in the company list once it is in an address book). Otherwise API prefix
    match on 'First Last', then the address book, then a local substring match over name/company/email."""
    q = query.strip()
    if "@" in q:
        ql = q.lower()
        hits = [v for v in c.all_visitors() if (v.get("email") or "").lower() == ql]
        if not hits and ql in _created_cache():
            try:
                v = c.visitor(_created_cache()[ql]["id"])
                if (v.get("email") or "").lower() == ql:
                    hits = [v]
            except ApiError:
                pass
        return hits
    hits = c.visitors_by_prefix(q)
    if hits:
        return hits
    hits = c.address_book(q, kind="visitor", page_size=25)
    if hits:
        return hits
    ql = q.lower()
    return [v for v in c.all_visitors()
            if ql in f"{v.get('firstname') or ''} {v.get('lastname') or ''} {v.get('companyName') or ''} {v.get('email') or ''}".lower()]


def resolve_host(c, host):
    """host: None/'me' -> logged-in user; 'US-...' id; an email; or a name (unique match)."""
    if not host or host.lower() == "me":
        return c.me()["id"], f"{c.me().get('firstname','')} {c.me().get('lastname','')}".strip()
    if re.fullmatch(r"US-[A-Z0-9]+", host):
        return host, host
    users = c.address_book(host if "@" not in host else host.split("@")[0], kind="user", page_size=25)
    if "@" in host:
        users = [u for u in users if (u.get("email") or "").lower() == host.lower()]
    if len(users) == 1:
        u = users[0]
        return u["id"], f"{u.get('firstname','')} {u.get('lastname','')}".strip()
    if not users:
        die(f"no host (employee) matches '{host}'. Try `hosts <name>` to search.")
    die(f"host '{host}' is ambiguous: " + "; ".join(f"{u['id']} {u.get('firstname')} {u.get('lastname')} <{u.get('email')}>" for u in users))


def match_field(fields, key):
    key_l = key.strip().lower()
    if key_l.isdigit():
        for f in fields:
            if str(f["id"]) == key_l:
                return f
        die(f"no custom field with id {key}")
    exact = [f for f in fields if f["name"].strip().lower() == key_l]
    if len(exact) == 1:
        return exact[0]
    partial = [f for f in fields if key_l in f["name"].lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        die(f"no custom field matches '{key}'. Known fields: " + "; ".join(f"{f['id']}={f['name']}" for f in fields))
    die(f"'{key}' matches several custom fields: " + "; ".join(f"{f['id']}={f['name']}" for f in partial))


def match_value(field, value):
    """LIST fields must use one of the configured values; accept exact (case-insensitive) or unique prefix."""
    values = field.get("values") or []
    if not values:
        return value
    vl = value.strip().lower()
    for v in values:
        if v.lower() == vl:
            return v
    pref = [v for v in values if v.lower().startswith(vl)]
    if len(pref) == 1:
        return pref[0]
    sub = [v for v in values if vl in v.lower()]
    if len(sub) == 1:
        return sub[0]
    die(f"'{value}' is not a valid option for '{field['name']}'. Options: " + " | ".join(values))


def resolve_custom_fields(c, specs, ignore_mandatory=False):
    """specs: ['Name=Value', '12345=Building 7', ...] -> list of {id, value}.
    Mandatory fields fall back to their configured default; without one they must be given."""
    fields = c.custom_fields()
    chosen = {}
    for spec in specs or []:
        if "=" not in spec:
            die(f"--field expects NAME=VALUE, got '{spec}'")
        k, v = spec.split("=", 1)
        f = match_field(fields, k)
        chosen[f["id"]] = {"id": f["id"], "value": match_value(f, v), "name": f["name"]}
    missing = []
    for f in fields:
        if f["id"] in chosen:
            continue
        if f.get("defaultValue") not in (None, ""):
            chosen[f["id"]] = {"id": f["id"], "value": f["defaultValue"], "name": f["name"]}
        elif f.get("mandatory"):
            missing.append(f)
    if missing and not ignore_mandatory:
        die("these mandatory custom fields have no default, pass them with --field: "
            + "; ".join(f"--field \"{f['name']}={(f.get('values') or ['<text>'])[0]}\"" for f in missing))
    return list(chosen.values())


def parse_day(c, s):
    tz = ZoneInfo(c.tz()) if ZoneInfo else None
    now = datetime.now(tz)
    s = (s or "today").lower()
    if s == "today":
        return now.date()
    if s == "tomorrow":
        return (now + timedelta(days=1)).date()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        die("--date must be YYYY-MM-DD, today or tomorrow")


def build_times(c, day, start, end, duration):
    tz = ZoneInfo(c.tz()) if ZoneInfo else None
    now = datetime.now(tz)
    if start:
        hh, mm = map(int, start.split(":"))
        start_dt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
    elif day == now.date():
        q = (now.minute // 15 + 1) * 15
        start_dt = now.replace(second=0, microsecond=0, minute=0) + timedelta(minutes=q)
    else:
        start_dt = datetime(day.year, day.month, day.day, 9, 0, tzinfo=tz)
    if end:
        hh, mm = map(int, end.split(":"))
        end_dt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
    else:
        if duration is None:
            duration = c.params().get("defaultVisitDuration") or 120
            if duration < 10:  # some tenants store hours
                duration = int(duration * 60)
        end_dt = start_dt + timedelta(minutes=int(duration))
    if end_dt <= start_dt:
        die("end time must be after start time")
    return start_dt, end_dt


def iso(dt):
    s = dt.isoformat(timespec="seconds")
    return s if dt.tzinfo else s + "Z"


# --------------------------------------------------------------------------- commands
def cmd_login(c, a):
    how = c.login(force=a.force)
    me = c.me()
    out({"login": how, "user": {"id": me.get("id"), "name": f"{me.get('firstname','')} {me.get('lastname','')}".strip(),
                                 "email": me.get("email")},
         "companies": [{"id": x["id"], "name": x["name"], "timezone": x.get("timezone")} for x in c.companies()],
         "default_company": c.co(), "token_expires": datetime.fromtimestamp(jwt_claims(c.token).get("exp", 0)).isoformat(),
         "token_file": TOKEN_FILE})


def cmd_whoami(c, a):
    cmd_login(c, argparse.Namespace(force=False))


def cmd_config(c, a):
    info = c.company_info()
    out({"company": {"id": info.get("id"), "name": info.get("name"), "timezone": info.get("timezone"), "locale": info.get("locale")},
         "defaultVisitDuration": c.params().get("defaultVisitDuration"),
         "emailMandatory": c.params().get("emailMandatory"),
         "custom_fields": [{"id": f["id"], "name": f["name"], "type": f["type"]["label"], "mandatory": f.get("mandatory"),
                            "default": f.get("defaultValue"), "system": f.get("system"), "values": f.get("values") or None}
                           for f in c.custom_fields()],
         "visitor_types": [{"id": t["id"], "name": t["name"], "default": t.get("isDefault"), "active": t.get("isActive")}
                           for t in c.visitor_types()]})


def cmd_search(c, a):
    hits = find_visitors(c, a.query)
    out({"query": a.query, "count": len(hits), "visitors": [slim_visitor(v) for v in hits[:a.limit]]})


def cmd_hosts(c, a):
    users = c.address_book(a.query, kind="user", page_size=a.limit)
    out({"query": a.query, "count": len(users),
         "hosts": [{"id": u["id"], "name": f"{u.get('firstname','')} {u.get('lastname','')}".strip(), "email": u.get("email")} for u in users]})


def cmd_visits(c, a):
    day = parse_day(c, a.date)
    res = c.visits(day.isoformat())
    rows = []
    for v in res.get("visits", []):
        vis, meet = v.get("visitor") or {}, v.get("meeting") or {}
        rows.append({"visit_id": v.get("id"), "meeting_id": meet.get("id") or ((v.get("id") or "").split("/") + ["", ""])[1],
                     "visitor": f"{vis.get('firstname','')} {vis.get('lastname','')}".strip(), "visitor_id": vis.get("id"),
                     "company": vis.get("companyName"), "status": (v["status"].get("value") or v["status"].get("name")) if isinstance(v.get("status"), dict) else v.get("status"),
                     "expectedAt": v.get("expectedAt"), "leavingAt": v.get("leavingAt"), "arrivedAt": v.get("arrivedAt"),
                     "host": f"{(v.get('host') or {}).get('firstname','')} {(v.get('host') or {}).get('lastname','')}".strip(),
                     "lastInvitationSentAt": v.get("lastInvitationSentAt"),
                     "customFields": {f.get("name"): f.get("value") for f in v.get("customFields") or []},
                     "visitorFields": {f.get("name"): f.get("value") for f in vis.get("customFields") or []}})
    out({"date": day.isoformat(), "total": res.get("total", len(rows)), "visits": rows})


def cmd_meeting(c, a):
    out(c.meeting(a.meeting_id))


def cmd_visitor(c, a):
    out(c.visitor(a.visitor_id))


def cmd_invite(c, a):
    c.invite(a.meeting_id)
    out({"meeting_id": a.meeting_id, "invite_sent": True})


def cmd_create_visit(c, a):
    # 1. who is coming
    existing, created_inline = None, False
    if a.visitor_id:
        existing = c.visitor(a.visitor_id)
    elif not a.always_new:
        key = a.email or (f"{a.first} {a.last}".strip() if a.first and a.last else None)
        if key:
            hits = find_visitors(c, key)
            if a.email:
                existing = hits[0] if hits else None
            else:
                same = [v for v in hits if (v.get("firstname") or "").strip().lower() == a.first.strip().lower()
                        and (v.get("lastname") or "").strip().lower() == a.last.strip().lower()]
                if len(same) == 1:
                    existing = same[0]
                elif len(same) > 1:
                    die(f"{len(same)} visitors are named {a.first} {a.last}; pass --email or --visitor-id. "
                        + "; ".join(f"{v['id']} <{v.get('email')}> {v.get('companyName') or ''}" for v in same))
    if not existing and not (a.first and a.last):
        die("new visitor needs --first and --last (plus --email if the company requires it)")

    # 2. host, times, custom fields
    host_id, host_name = resolve_host(c, a.host)
    day = parse_day(c, a.date)
    start_dt, end_dt = build_times(c, day, a.start, a.end, a.duration)
    fields = resolve_custom_fields(c, a.field, a.ignore_mandatory)
    field_payload = [{"id": f["id"], "value": f["value"]} for f in fields]
    vtype_id = None
    if a.visitor_type:
        types = c.visitor_types()
        m = [t for t in types if str(t["id"]) == a.visitor_type or t["name"].lower() == a.visitor_type.lower()]
        if len(m) != 1:
            die("unknown --visitor-type; options: " + ", ".join(f"{t['id']}={t['name']}" for t in types))
        vtype_id = m[0]["id"]

    if existing:
        visitor = {"id": existing["id"],
                   "firstName": a.first or existing.get("firstname"), "lastName": a.last or existing.get("lastname"),
                   "email": a.email or existing.get("email") or "", "mobile": existing.get("mobile") or "",
                   "companyName": a.company_name or existing.get("companyName") or "",
                   "language": a.language or (existing.get("language") or {}).get("code") or "en",
                   "licensePlate": existing.get("licensePlate"), "about": existing.get("about"), "function": existing.get("function"),
                   "customFields": field_payload, "addToAddressBook": False, "recognizeOnKiosk": False,
                   "frequent": bool(existing.get("frequent")), "phone": a.phone or existing.get("phone") or "", "smsReminder": False}
        if vtype_id or (existing.get("visitorType") or {}).get("id"):
            visitor["visitorTypeId"] = vtype_id or existing["visitorType"]["id"]
    else:
        created_inline = True
        visitor = {"firstName": a.first.strip(), "lastName": a.last.strip(), "email": (a.email or "").strip(),
                   "companyName": (a.company_name or "").strip(), "language": a.language or "en",
                   "customFields": field_payload, "addToAddressBook": False, "recognizeOnKiosk": False,
                   "frequent": False, "phone": a.phone or "", "smsReminder": False}
        if vtype_id:
            visitor["visitorTypeId"] = vtype_id

    title = a.title or f"{visitor['firstName']} {visitor['lastName']}".strip()
    payload = {"title": title, "hostId": host_id, "visitors": [visitor],
               "startAt": iso(start_dt), "endAt": iso(end_dt), "recurrence": None,
               "checkinNotificationUsers": [], "checkoutNotificationUsers": [], "checkoutReminderNotificationUsers": [],
               "checkinNotificationType": "FIRST_LAST"}
    if a.description:
        payload["description"] = a.description

    plan = {"company": c.co(), "visitor_mode": "existing" if existing else "new",
            "visitor_id": existing["id"] if existing else None, "host": {"id": host_id, "name": host_name},
            "startAt": payload["startAt"], "endAt": payload["endAt"], "timezone": c.tz(),
            "custom_fields": {f["name"]: f["value"] for f in fields},
            "invite": a.invite, "add_to_address_book": a.address_book}
    if a.dry_run:
        out({"dry_run": True, "plan": plan, "payload": payload})
        return

    # 3. create (one call creates the visitor too when no id is given)
    meeting = c.create_meeting(payload)
    mid = meeting["id"]
    vid = meeting["visitors"][0]["id"]
    warnings = []
    # 4. visit-level custom fields (the web app stores these with a second call)
    if field_payload:
        try:
            c.set_visit_fields(day.isoformat(), mid, vid, field_payload)
        except ApiError as e:
            warnings.append(f"custom-fields PUT failed: HTTP {e.status} {e.body[:200]}")
    # 5. side effects (address book is on by default: without it the visitor is not searchable)
    invite_sent = False
    if a.invite:
        if not visitor.get("email"):
            warnings.append("invite skipped: visitor has no email")
        else:
            c.invite(mid)
            invite_sent = True
    if created_inline:
        remember_created(visitor.get("email"), vid)
    if a.address_book:
        try:
            c.add_to_address_book(vid)
        except ApiError as e:
            warnings.append(f"address-book PUT failed: HTTP {e.status}; the visitor will not show up in searches")

    out({"ok": True, "meeting_id": mid, "visitor_id": vid, "visitor_created": created_inline,
         "visitor": f"{visitor['firstName']} {visitor['lastName']}".strip(), "email": visitor.get("email"),
         "host": host_name, "startAt": meeting.get("startAt"), "endAt": meeting.get("endAt"),
         "status": meeting.get("status"), "invite_sent": invite_sent,
         "custom_fields": plan["custom_fields"], "warnings": warnings,
         "logbook_url": f"{BASE}/{c.co()}/dashboard"})


def cmd_move_visit(c, a):
    """Re-point an existing visit (meeting) to another visitor card, re-apply the visit custom
    fields (the move drops them) and optionally re-send the invitation (the QR pass embeds the
    visitor id, so the old email stops working)."""
    m = c.meeting(a.meeting_id)
    old = m["visitors"][0]
    target = c.visitor(a.to)
    day = m["startAt"][:10]
    old_fields = []
    for v in c.visits(day).get("visits", []):
        if v["id"].endswith(f"/{a.meeting_id}/{old['id']}"):
            old_fields = [{"id": f["id"], "value": f["value"]} for f in v.get("customFields") or [] if f.get("value") not in (None, "")]
    body = {"title": m["title"], "hostId": m["host"]["id"],
            "visitors": [{"id": target["id"], "firstName": target.get("firstname"), "lastName": target.get("lastname"),
                          "email": target.get("email") or "", "companyName": target.get("companyName") or "",
                          "language": (target.get("language") or {}).get("code", "en"), "phone": target.get("phone") or "",
                          "mobile": target.get("mobile") or "",
                          "customFields": [{"id": f["id"], "value": f["value"]} for f in old.get("customFields") or []],
                          "addToAddressBook": False, "recognizeOnKiosk": False, "frequent": False, "smsReminder": False}],
            "startAt": m["startAt"], "endAt": m["endAt"], "recurrence": None,
            "checkinNotificationUsers": [], "checkoutNotificationUsers": [], "checkoutReminderNotificationUsers": [],
            "checkinNotificationType": m.get("checkinNotificationType") or "FIRST_LAST"}
    if a.dry_run:
        out({"dry_run": True, "from": old["id"], "to": target["id"], "visit_fields_to_reapply": old_fields, "payload": body})
        return
    c.call("PUT", f"/api/app/{c.co()}/vm/meetings/{a.meeting_id}", body=body)
    fields = old_fields or [{"id": f["id"], "value": f["defaultValue"]} for f in c.custom_fields() if f.get("defaultValue")]
    if fields:
        c.set_visit_fields(day, a.meeting_id, target["id"], fields)
    invite_sent = False
    if a.invite and target.get("email"):
        c.invite(a.meeting_id)
        invite_sent = True
    out({"ok": True, "meeting_id": a.meeting_id, "moved_from": old["id"], "moved_to": target["id"], "date": day,
         "visit_fields": {str(f["id"]): f["value"] for f in fields}, "invite_sent": invite_sent,
         "note": "the previously emailed pass is no longer valid" if not invite_sent else None})


# --------------------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(description="Proxyclick / Eptura Visitor API client (no browser needed).")
    p.add_argument("--company", help="company id like CO-XXXXXXX (default: the account's home company)")
    p.add_argument("-v", "--verbose", action="store_true", help="log every HTTP call to stderr")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("login", help="authenticate (or reuse the cached token) and show who you are")
    s.add_argument("--force", action="store_true", help="ignore the cached token")
    s.set_defaults(fn=cmd_login)
    sub.add_parser("whoami", help="same as login").set_defaults(fn=cmd_whoami)
    sub.add_parser("config", help="company timezone, custom fields (ids, options, mandatory) and visitor types").set_defaults(fn=cmd_config)

    s = sub.add_parser("search", help="find visitors by name, company or exact email")
    s.add_argument("query"); s.add_argument("--limit", type=int, default=20); s.set_defaults(fn=cmd_search)
    s = sub.add_parser("hosts", help="find employees (hosts) by name or email")
    s.add_argument("query"); s.add_argument("--limit", type=int, default=10); s.set_defaults(fn=cmd_hosts)
    s = sub.add_parser("visits", help="list the logbook for a day")
    s.add_argument("date", nargs="?", default="today", help="YYYY-MM-DD | today | tomorrow"); s.set_defaults(fn=cmd_visits)
    s = sub.add_parser("meeting", help="raw meeting (visit) object"); s.add_argument("meeting_id"); s.set_defaults(fn=cmd_meeting)
    s = sub.add_parser("visitor", help="raw visitor object"); s.add_argument("visitor_id"); s.set_defaults(fn=cmd_visitor)
    s = sub.add_parser("invite", help="(re)send the invitation email for a meeting"); s.add_argument("meeting_id"); s.set_defaults(fn=cmd_invite)

    s = sub.add_parser("create-visit", help="register a visit; reuses an existing visitor or creates one")
    s.add_argument("--first"); s.add_argument("--last"); s.add_argument("--email")
    s.add_argument("--company-name", help="the visitor's organisation"); s.add_argument("--phone")
    s.add_argument("--language", help="2-letter code, default en")
    s.add_argument("--visitor-id", help="skip matching and use this V-... id")
    s.add_argument("--always-new", action="store_true", help="never reuse an existing visitor record")
    s.add_argument("--host", help="'me' (default), a US-... id, an employee email or a unique name")
    s.add_argument("--date", default="today", help="YYYY-MM-DD | today | tomorrow")
    s.add_argument("--start", help="HH:MM in the company timezone (default: next quarter hour / 09:00)")
    s.add_argument("--end", help="HH:MM"); s.add_argument("--duration", type=int, help="minutes (default: company setting, usually 120)")
    s.add_argument("--field", action="append", metavar="NAME=VALUE", help="custom field; repeatable; LIST fields accept a unique prefix")
    s.add_argument("--ignore-mandatory", action="store_true", help="do not fail on missing mandatory custom fields")
    s.add_argument("--visitor-type", help="visitor type name or id (optional)")
    s.add_argument("--title", help="meeting title (default: visitor name)"); s.add_argument("--description")
    s.add_argument("--invite", action="store_true", help="email the visitor their invitation / QR pass")
    s.add_argument("--no-address-book", dest="address_book", action="store_false",
                   help="do not add the visitor to your address book (then searches will not find them later)")
    s.add_argument("--dry-run", action="store_true", help="show the plan and payload, send nothing")
    s.set_defaults(fn=cmd_create_visit)

    s = sub.add_parser("move-visit", help="re-point a visit to another visitor card (fixes duplicates); re-applies fields")
    s.add_argument("meeting_id"); s.add_argument("--to", required=True, help="target V-... id")
    s.add_argument("--invite", action="store_true", help="re-send the invitation afterwards (the old pass stops working)")
    s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_move_visit)

    a = p.parse_args(argv)
    c = Client(company=a.company, verbose=a.verbose)
    try:
        a.fn(c, a)
    except ApiError as e:
        die(str(e), code=3)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
