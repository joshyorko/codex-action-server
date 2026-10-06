"""Thread-section mutations expose only bounded typed operations."""

from unittest.mock import patch

from test_actions import FakeClient, load_actions


class SectionClient(FakeClient):
    def request(self, method, params):
        self.calls.append((method, params))
        if method == "threadSection/create":
            return {"section": {"id": "section-1", "name": params["name"]}}
        if method == "threadSection/update":
            return {
                "section": {
                    "id": params["sectionId"],
                    "name": params["name"],
                }
            }
        if method == "threadSection/delete":
            return {}
        raise AssertionError(method)


def test_sections_have_receipt_protected_typed_create_update_delete(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = SectionClient(None)
    with patch.object(module, "Client", return_value=client):
        created = module.create_thread_section(
            module.ThreadSectionCreateRequest(
                target="local",
                request_id="section-create",
                name="Review",
            )
        ).result.result
        updated = module.update_thread_section(
            module.ThreadSectionUpdateRequest(
                target="local",
                request_id="section-update",
                section_id="section-1",
                name="Reviewed",
            )
        ).result.result
        deleted = module.delete_thread_section(
            module.ThreadSectionDeleteRequest(
                target="local",
                request_id="section-delete",
                section_id="section-1",
            )
        ).result.result

    assert created["section"]["id"] == "section-1"
    assert all(
        value["dispatch"]["state"] == "accepted"
        for value in (created, updated, deleted)
    )
    assert client.calls == [
        ("threadSection/create", {"name": "Review"}),
        (
            "threadSection/update",
            {"sectionId": "section-1", "name": "Reviewed"},
        ),
        ("threadSection/delete", {"sectionId": "section-1"}),
    ]
