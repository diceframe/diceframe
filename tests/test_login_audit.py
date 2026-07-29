import json

from src.webui.login_audit import LoginAuditStore


def test_login_audit_persists_and_keeps_only_newest_entries(tmp_path):
    store = LoginAuditStore(tmp_path, max_entries=3)

    store.record("192.0.2.1", False)
    store.record("192.0.2.2", True)
    store.record("192.0.2.3", False)
    store.record("192.0.2.4", True)

    restored = LoginAuditStore(tmp_path, max_entries=3)
    entries = restored.recent()

    assert [entry["ip"] for entry in entries] == [
        "192.0.2.4",
        "192.0.2.3",
        "192.0.2.2",
    ]
    assert entries[0]["success"] is True
    assert all(entry["at"].endswith("Z") for entry in entries)


def test_login_audit_recovers_from_malformed_file(tmp_path):
    path = tmp_path / "login_audit.json"
    path.write_text("{not json", encoding="utf-8")

    store = LoginAuditStore(tmp_path)
    store.record("127.0.0.1", True)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entries"][0]["ip"] == "127.0.0.1"
