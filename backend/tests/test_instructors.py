"""Instructor create (similar-name check + "different person" override), search
ranking and the subject-code duplicate check. No network, no DB."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import instructors, subjects
from app.security import require_staff
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
