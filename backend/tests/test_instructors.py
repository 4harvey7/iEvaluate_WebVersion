"""Instructor create (similar-name check + "different person" override), search
ranking, login activation (one Brevo activation email, never Supabase's own
reset mail), the Brevo mailer itself and the subject-code duplicate check.
No network, no DB."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, mailer
from app.main import app
from app.routers import instructors, subjects
from app.security import CurrentUser, require_staff
from tests.test_watcher_contract import USER, MemSB

NEW_ID = "99999999-9999-9999-9999-999999999999"
RODZ = "aaaaaaaa-0000-0000-0000-000000000001"
RONALD = "aaaaaaaa-0000-0000-0000-000000000002"
BODY = {"first_name": "rodz", "last_name": "Licayan", "department_id": 3, "employment_type": "FULL-TIME"}


class RpcSB(MemSB):
    def __init__(self, tables=None, rpc_data=None):
        super().__init__(tables)
        self.rpc_data = rpc_data or {}
        self.auth = SimpleNamespace(admin=SimpleNamespace(
            create_user=lambda p: SimpleNamespace(user=SimpleNamespace(id=NEW_ID)),
            delete_user=lambda i: None,
        ))

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=list(self.rpc_data.get(name, []))))


def _person(pid, first, last, **extra):
    return {"id": pid, "first_name": first, "last_name": last, "disabled": False, "is_placeholder": False, **extra}


@pytest.fixture
def setup(monkeypatch):
    audits = []

    def _setup(sb, eligible=None):
        ids = set(eligible if eligible is not None else [p["id"] for p in sb.tables.get("user_info", [])])
        monkeypatch.setattr(instructors, "get_supabase", lambda: sb)
        monkeypatch.setattr(instructors, "department_lookup", lambda: {3: {"d_name": "CCS", "d_code": "CCS"}})
        monkeypatch.setattr(instructors, "role_maps", lambda: ({"FULL-TIME": 1, "PART-TIME": 2}, {}))
        monkeypatch.setattr(instructors, "get_instructor_if_eligible", lambda i: {"id": i} if i in ids else None)
        monkeypatch.setattr(instructors, "audit", lambda *a, **k: audits.append(a))
        app.dependency_overrides[require_staff] = lambda: USER
        return TestClient(app, raise_server_exceptions=False)

    _setup.audits = audits
    yield _setup
    app.dependency_overrides.clear()


def _two_licayans():
    return RpcSB({"user_info": [_person(RONALD, "Ronald Kinggg", "licayan"), _person(RODZ, "Rodz Harvey d", "Licayan")],
                  "instructor_departments": [{"instructor_id": RODZ, "department_id": 3, "is_primary": True}]})


def test_similar_name_blocks_and_ignores_same_surname_only(setup):
    client = setup(_two_licayans())
    r = client.post("/instructors", json=BODY)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "duplicate_instructor"
    assert [m["full_name"] for m in d["existing_instructors"]] == ["Rodz Harvey d Licayan"]
    assert d["existing_instructor"]["id"] == RODZ
    assert d["existing_instructor"]["department_name"] == "CCS"
    assert d["message"].startswith("An instructor with a very similar name already exists: Rodz Harvey d Licayan")


def test_different_first_name_is_created(setup):
    client = setup(_two_licayans())
    r = client.post("/instructors", json={**BODY, "first_name": "Ramon"})
    assert r.status_code == 200 and r.json()["id"] == NEW_ID


def test_several_matches_come_back_eligible_and_exact_first(setup):
    e_exact, e_near, n_exact = "b1", "b2", "b3"
    sb = RpcSB({"user_info": [
        _person(n_exact, "Maria", "Santos"),
        _person(e_near, "Maria L.", "Santos"),
        _person(e_exact, "Maria", "Santos"),
    ]})
    client = setup(sb, eligible=[e_exact, e_near])
    r = client.post("/instructors", json={**BODY, "first_name": "Maria", "last_name": "Santos"})
    assert r.status_code == 409
    ms = r.json()["detail"]["existing_instructors"]
    assert [m["id"] for m in ms] == [e_exact, e_near, n_exact]
    assert [m["eligible"] for m in ms] == [True, True, False]
    assert r.json()["detail"]["message"].startswith("Instructors with very similar names already exist:")


def test_confirm_new_person_creates_and_audits(setup):
    client = setup(_two_licayans())
    r = client.post("/instructors", json={**BODY, "confirm_new_person": True})
    assert r.status_code == 200
    meta = setup.audits[-1][2]
    assert meta["similar_to"] == [RODZ]


def test_exact_name_index_rejection_is_clear_409(setup):
    from postgrest.exceptions import APIError

    class NameTakenSB(RpcSB):
        def table(self, name):
            q = super().table(name)
            if name == "user_info":
                def upsert(*a, **k):
                    raise APIError({"code": "23505", "hint": None, "details": None, "message":
                                    'duplicate key value violates unique constraint "user_info_full_name_unique_idx"'})
                q.upsert = upsert
            return q

    client = setup(NameTakenSB({"user_info": [_person(RODZ, "rodz", "Licayan")]}))
    r = client.post("/instructors", json={**BODY, "confirm_new_person": True})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "instructor_name_taken" and "already exists" in d["message"]


def test_old_fuzzy_rpc_hit_still_blocks(setup):
    sb = RpcSB({"user_info": []}, rpc_data={"find_duplicate_instructor": [_person(RODZ, "Rods", "Likayan")]})
    client = setup(sb, eligible=[RODZ])
    r = client.post("/instructors", json=BODY)
    assert r.status_code == 409
    assert r.json()["detail"]["existing_instructor"]["id"] == RODZ


def test_search_puts_all_words_match_first(setup):
    rows = [
        {"id": RONALD, "first_name": "Ronald Kinggg", "last_name": "licayan", "department_id": 3, "is_placeholder": False, "disabled": False},
        {"id": RODZ, "first_name": "Rodz Harvey d", "last_name": "Licayan", "department_id": 3, "is_placeholder": False, "disabled": False},
    ]
    client = setup(RpcSB({}, rpc_data={"search_instructors": rows}))
    r = client.get("/instructors/search", params={"q": "rodz licayan"})
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [RODZ, RONALD]


# ---------------------------------------------------------------- activate an instructor login
ADMIN = CurrentUser(id="admin-1", email=None, first_name="A", last_name="D", role="SAO_ADMIN", token="t")
PLACEHOLDER = "cccccccc-0000-0000-0000-000000000001"
OTHER = "cccccccc-0000-0000-0000-000000000002"


class ActivateSB(MemSB):
    """MemSB plus ilike/neq filters and an auth admin that records calls. Any
    Supabase email-sending call is recorded too, so tests can prove none was
    made (the only email is the Brevo one, via mailer.send_email)."""

    def __init__(self, tables=None):
        super().__init__(tables)
        self.auth_updates, self.emails = [], []
        self.auth = SimpleNamespace(
            admin=SimpleNamespace(update_user_by_id=lambda uid, attrs: self.auth_updates.append((uid, dict(attrs)))),
            reset_password_for_email=lambda *a, **k: self.emails.append(a),
        )

    def table(self, name):
        q = super().table(name)

        def ilike(col, pattern):
            want = pattern.replace("\\%", "%").replace("\\_", "_").replace("\\\\", "\\").lower()
            q.filters.append(lambda r: str(r.get(col) or "").lower() == want)
            return q

        def neq(col, val):
            q.filters.append(lambda r: r.get(col) != val)
            return q

        q.ilike, q.neq = ilike, neq
        return q


def _placeholder_row(**extra):
    return {"id": PLACEHOLDER, "first_name": "Ana", "last_name": "Cruz", "email": "placeholder-x@ievaluate.internal",
            "is_placeholder": True, "disabled": True, "account_status": "pending",
            "university_id": None, "address": None, **extra}


@pytest.fixture
def activate(monkeypatch):
    audits, email_clients, sent = [], [], []
    result = {"value": (True, None)}

    def fake_send(to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})
        return result["value"]

    def _setup(sb, user=ADMIN, send_result=(True, None)):
        result["value"] = send_result
        monkeypatch.setattr(instructors, "get_supabase", lambda: sb)
        monkeypatch.setattr(instructors, "audit", lambda *a, **k: audits.append(a))
        monkeypatch.setattr(mailer, "send_email", fake_send)
        # Any attempt to build a Supabase client for sending email is recorded.
        monkeypatch.setattr(db, "new_auth_client", lambda: email_clients.append(1) or SimpleNamespace(
            auth=SimpleNamespace(reset_password_for_email=lambda *a, **k: sb.emails.append(a))))
        app.dependency_overrides[require_staff] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    _setup.audits, _setup.email_clients, _setup.sent = audits, email_clients, sent
    yield _setup
    app.dependency_overrides.clear()


def test_activate_success_sends_activation_email(activate):
    sb = ActivateSB({"user_info": [_placeholder_row()]})
    client = activate(sb)
    r = client.post(f"/instructors/{PLACEHOLDER}/activate", json={"real_email": "  Ana.Cruz@School.edu "})
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "activated": True, "email": "ana.cruz@school.edu", "email_sent": True}
    # Same auth user: new login email, confirmed, unbanned.
    assert sb.auth_updates == [(PLACEHOLDER, {"email": "ana.cruz@school.edu", "email_confirm": True, "ban_duration": "none"})]
    row = sb.tables["user_info"][0]
    assert row["email"] == "ana.cruz@school.edu"
    assert row["disabled"] is False and row["is_placeholder"] is False and row["account_status"] == "approved"
    # Exactly one Brevo email, to the new address, with the sign-in steps.
    assert len(activate.sent) == 1
    mail = activate.sent[0]
    assert mail["to"] == "ana.cruz@school.edu"
    assert mail["subject"] == "Account Activated — Set Your Password"
    assert "Hello, Ana!" in mail["html"]
    assert "<b>activated by the SAO admin</b>" in mail["html"]
    assert "<b>Your login email:</b> ana.cruz@school.edu" in mail["html"]
    assert "Tap <b>Forgot Password?</b> on the login screen." in mail["html"]
    assert "Enter <b>ana.cruz@school.edu</b> and tap <b>Send Code</b>." in mail["html"]
    assert "complete your ID and address" in mail["html"]  # both missing on the placeholder
    # Supabase's own reset mail is never sent.
    assert sb.emails == [] and activate.email_clients == []
    assert not hasattr(instructors, "new_auth_client")
    who, action, meta = activate.audits[-1]
    assert who == ADMIN.id and action == "instructor_account_activated"
    assert meta == {"instructor_id": PLACEHOLDER, "new_email": "ana.cruz@school.edu", "email_sent": True}


def test_activate_email_mentions_only_missing_details(activate):
    sb = ActivateSB({"user_info": [_placeholder_row(university_id="2024-001", address="  ")]})
    client = activate(sb)
    r = client.post(f"/instructors/{PLACEHOLDER}/activate", json={"real_email": "ana.cruz@school.edu"})
    assert r.status_code == 200
    assert "the app will ask you to complete your address.</p>" in activate.sent[0]["html"]

    sb = ActivateSB({"user_info": [_placeholder_row(university_id="2024-001", address="Cebu")]})
    activate.sent.clear()
    r = activate(sb).post(f"/instructors/{PLACEHOLDER}/activate", json={"real_email": "ana.cruz@school.edu"})
    assert r.status_code == 200
    assert "complete your" not in activate.sent[0]["html"]


def test_activate_brevo_failure_still_activates(activate):
    sb = ActivateSB({"user_info": [_placeholder_row()]})
    client = activate(sb, send_result=(False, "send_failed"))
    r = client.post(f"/instructors/{PLACEHOLDER}/activate", json={"real_email": "ana.cruz@school.edu"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "activated": True, "email": "ana.cruz@school.edu", "email_sent": False}
    row = sb.tables["user_info"][0]
    assert row["account_status"] == "approved" and row["is_placeholder"] is False and row["disabled"] is False
    # No revert of the auth user: only the one activating update.
    assert sb.auth_updates == [(PLACEHOLDER, {"email": "ana.cruz@school.edu", "email_confirm": True, "ban_duration": "none"})]
    assert len(activate.sent) == 1 and sb.emails == []
    meta = activate.audits[-1][2]
    assert meta["email_sent"] is False and meta["email_error"] == "send_failed"


def test_activate_without_brevo_config_still_activates(activate, monkeypatch):
    sb = ActivateSB({"user_info": [_placeholder_row()]})
    client = activate(sb)
    # The real mailer, with no Brevo settings: no HTTP call at all.
    monkeypatch.setattr(mailer, "send_email", REAL_SEND_EMAIL)
    monkeypatch.setattr(mailer, "get_settings", lambda: _mail_settings(key="", sender=""))
    monkeypatch.setattr(mailer, "_client", lambda: pytest.fail("no HTTP call without config"))
    r = client.post(f"/instructors/{PLACEHOLDER}/activate", json={"real_email": "ana.cruz@school.edu"})
    assert r.status_code == 200
    assert r.json()["email_sent"] is False
    assert sb.tables["user_info"][0]["account_status"] == "approved"
    meta = activate.audits[-1][2]
    assert meta["email_sent"] is False and meta["email_error"] == "not_configured"


def test_activate_duplicate_email_is_409(activate):
    taken = {"id": OTHER, "first_name": "B", "last_name": "C", "email": "ana.cruz@school.edu", "is_placeholder": False}
    sb = ActivateSB({"user_info": [_placeholder_row(), taken]})
    client = activate(sb)
    r = client.post(f"/instructors/{PLACEHOLDER}/activate", json={"real_email": "ANA.CRUZ@school.edu"})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "email_in_use" and d["message"] == "That email already belongs to another account."
    assert sb.auth_updates == [] and sb.updates == [] and activate.audits == []
    assert activate.sent == []


def test_activate_non_admin_is_403(activate):
    sb = ActivateSB({"user_info": [_placeholder_row()]})
    client = activate(sb, user=USER)  # SAO_STAFF
    r = client.post(f"/instructors/{PLACEHOLDER}/activate", json={"real_email": "ana.cruz@school.edu"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "admin_only"
    assert sb.auth_updates == [] and sb.updates == []
    assert activate.sent == []


def test_activate_not_a_placeholder_is_refused(activate):
    real = {**_placeholder_row(), "is_placeholder": False, "disabled": False, "account_status": "approved"}
    sb = ActivateSB({"user_info": [real]})
    client = activate(sb)
    r = client.post(f"/instructors/{PLACEHOLDER}/activate", json={"real_email": "ana.cruz@school.edu"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "not_placeholder"
    assert sb.auth_updates == [] and sb.updates == [] and sb.emails == []
    assert activate.sent == []


# ---------------------------------------------------------------- Brevo mailer
REAL_SEND_EMAIL = mailer.send_email
KEY = "test-brevo-key-not-real"


def _mail_settings(key=KEY, sender="sao@school.edu", name="SAO Management"):
    return SimpleNamespace(brevo_api_key=key, brevo_sender_email=sender, brevo_sender_name=name,
                           email_configured=bool(key and sender))


def _mock_brevo(monkeypatch, handler):
    monkeypatch.setattr(mailer, "get_settings", lambda: _mail_settings())
    monkeypatch.setattr(mailer, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def test_send_email_posts_to_brevo(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json={"messageId": "<x@brevo>"})

    _mock_brevo(monkeypatch, handler)
    assert mailer.send_email("ana@school.edu", "Hi", "<p>x</p>") == (True, None)
    assert len(seen) == 1
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://api.brevo.com/v3/smtp/email"
    assert req.headers["api-key"] == KEY
    assert req.headers["content-type"] == "application/json" and req.headers["accept"] == "application/json"
    assert json.loads(req.content) == {
        "sender": {"name": "SAO Management", "email": "sao@school.edu"},
        "to": [{"email": "ana@school.edu"}],
        "subject": "Hi",
        "htmlContent": "<p>x</p>",
    }


def test_send_email_brevo_error_is_send_failed(monkeypatch, caplog):
    _mock_brevo(monkeypatch, lambda request: httpx.Response(400, json={"code": "invalid_parameter"}))
    assert mailer.send_email("ana@school.edu", "Hi", "<p>x</p>") == (False, "send_failed")
    assert "400" in caplog.text and KEY not in caplog.text


def test_send_email_timeout_is_send_failed_not_raised(monkeypatch, caplog):
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    _mock_brevo(monkeypatch, handler)
    assert mailer.send_email("ana@school.edu", "Hi", "<p>x</p>") == (False, "send_failed")
    assert KEY not in caplog.text


def test_send_email_not_configured(monkeypatch):
    monkeypatch.setattr(mailer, "get_settings", lambda: _mail_settings(sender=""))
    monkeypatch.setattr(mailer, "_client", lambda: pytest.fail("no HTTP call without config"))
    assert mailer.send_email("ana@school.edu", "Hi", "<p>x</p>") == (False, "not_configured")


def test_activation_email_escapes_values():
    subject, html = mailer.activation_email('<script>alert("x")</script>', "a&b<i>@school.edu", False, False)
    assert subject == "Account Activated — Set Your Password"
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "a&amp;b&lt;i&gt;@school.edu" in html and "<i>" not in html
    assert "complete your" not in html
    _, html = mailer.activation_email("  ", "a@b.co", True, False)
    assert "Hello, there!" in html
    assert "<p>After you sign in, the app will ask you to complete your ID.</p>" in html
    assert "If you did not expect this email, please contact the SAO office immediately." in html
    assert "Best regards,<br>SAO Management Team" in html


def test_subject_duplicate_found_past_the_first_fifty_rows(monkeypatch):
    rows = [{"id": str(i), "subject_code": f"OTHER {i}", "subject_name": "x", "department_id": 3} for i in range(60)]
    rows.append({"id": "m", "subject_code": "MATH 101", "subject_name": "Math", "department_id": 3})
    sb = MemSB({"subjects": rows})
    monkeypatch.setattr(subjects, "get_supabase", lambda: sb)
    monkeypatch.setattr(subjects, "department_lookup", lambda: {3: {"d_name": "CCS", "d_code": "CCS"}})
    monkeypatch.setattr(subjects, "audit", lambda *a, **k: None)
    app.dependency_overrides[require_staff] = lambda: USER
    try:
        r = TestClient(app, raise_server_exceptions=False).post(
            "/subjects", json={"subject_code": "math-101", "subject_name": "Math", "department_id": 3})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 409
    assert r.json()["detail"]["existing_subject"]["subject_code"] == "MATH 101"
