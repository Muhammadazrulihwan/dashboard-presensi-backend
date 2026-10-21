"""Master karyawan (PRD F15): daftar, pencarian, filter, detail, dan batas data per departemen."""
from tests.conftest import as_dept
from tests.test_four_sources import activity_xlsx, approval_xlsx, confirm_package, report_xlsx, timeoff_xlsx, upload_package


def _seed(client):
    """Muat paket empat sumber: master karyawan berisi LS019, LS021, LS417 (semua organisasi IT)."""
    body = upload_package(client, {
        "activity": activity_xlsx(), "report": report_xlsx(),
        "timeoff": timeoff_xlsx(), "approval": approval_xlsx(),
    })
    confirm_package(client, body["upload_id"])


def test_employee_list_search_and_detail(client):
    _seed(client)

    page = client.get("/employees").json()
    assert page["total"] == 3
    ids = {e["employee_id"] for e in page["items"]}
    assert ids == {"LS019", "LS021", "LS417"}
    # Urut nama
    assert [e["nama"] for e in page["items"]] == ["Berry Alpian Hidayat", "Kamaludin", "Wita Aeni"]

    # Cari berdasarkan NIK dan nama
    assert client.get("/employees", params={"q": "LS019"}).json()["total"] == 1
    assert client.get("/employees", params={"q": "wita"}).json()["items"][0]["employee_id"] == "LS021"

    # Detail
    d = client.get("/employees/LS019").json()
    assert d["nama"] == "Kamaludin" and d["organisasi"] == "IT"
    assert client.get("/employees/TIDAK-ADA").status_code == 404


def test_employee_meta_and_filters(client):
    _seed(client)
    meta = client.get("/employees/meta").json()
    assert meta["total"] == 3 and meta["organisasi"] == ["IT"]
    assert meta["resign_count"] == 0

    assert client.get("/employees", params={"organisasi": "IT"}).json()["total"] == 3
    assert client.get("/employees", params={"organisasi": "HR"}).json()["total"] == 0
    assert client.get("/employees", params={"resign": "true"}).json()["total"] == 0
    assert client.get("/employees", params={"resign": "false"}).json()["total"] == 3


def test_employee_pagination(client):
    _seed(client)
    p1 = client.get("/employees", params={"page": 1, "page_size": 2}).json()
    p2 = client.get("/employees", params={"page": 2, "page_size": 2}).json()
    assert p1["total"] == 3 and len(p1["items"]) == 2
    assert len(p2["items"]) == 1
    assert {e["employee_id"] for e in p1["items"]} | {e["employee_id"] for e in p2["items"]} == {"LS019", "LS021", "LS417"}


def test_employee_scope_and_access(client):
    """Sales/Marketing hanya melihat karyawan Sales/Marketing; data di sini IT sehingga kosong/404."""
    _seed(client)

    as_dept("Sales/Marketing")
    assert client.get("/employees").json()["total"] == 0
    assert client.get("/employees/LS019").status_code == 404
    assert client.get("/employees/meta").json()["total"] == 0

    as_dept("IT")   # IT boleh melihat ringkasan master
    assert client.get("/employees").json()["total"] == 3

    as_dept(None)   # tanpa departemen: bukan anggota 4 departemen dashboard
    assert client.get("/employees").status_code == 403
