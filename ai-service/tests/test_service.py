from fastapi.testclient import TestClient


def test_internal_session_interrupt_and_resume(monkeypatch, tmp_path):
    monkeypatch.setenv("DITTO_LLM_MODE", "mock")
    monkeypatch.setenv("DITTO_INTERNAL_API_KEY", "test-key")
    monkeypatch.setenv("DITTO_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))

    from ditto_service.main import app

    headers = {"X-Internal-Api-Key": "test-key"}
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200

        started = client.post(
            "/internal/v1/sessions",
            headers=headers,
            json={
                "draft": "내일까지 조금 더 고민해 보면 좋을 것 같아요.",
                "sender": {
                    "user_id": "sender-id",
                    "name": "Sender",
                    "time_zone": "Asia/Seoul",
                    "language": "ko",
                },
                "receiver": {
                    "user_id": "receiver-id",
                    "name": "Alex",
                    "time_zone": "America/Los_Angeles",
                    "language": None,
                },
                "receiver_work": {
                    "start": "10:00",
                    "end": "19:00",
                    "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"],
                },
            },
        )
        assert started.status_code == 200
        result = started.json()
        assert result["status"] == "interrupt"

        thread_id = result["thread_id"]
        while result["status"] == "interrupt":
            answer = result["interrupt"]["item"]["candidates"][0]
            resumed = client.post(
                f"/internal/v1/sessions/{thread_id}/answers",
                headers=headers,
                json={"answer": answer},
            )
            assert resumed.status_code == 200
            result = resumed.json()

        assert result["card"]["assignee"] == "Alex"
        assert result["card"]["expected_outcome"]


def test_internal_key_is_required(monkeypatch, tmp_path):
    monkeypatch.setenv("DITTO_LLM_MODE", "mock")
    monkeypatch.setenv("DITTO_INTERNAL_API_KEY", "test-key")
    monkeypatch.setenv("DITTO_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))

    from ditto_service.main import app

    with TestClient(app) as client:
        response = client.get("/internal/v1/sessions/not-found")
        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_INTERNAL_API_KEY"


def test_internal_message_translation(monkeypatch, tmp_path):
    monkeypatch.setenv("DITTO_LLM_MODE", "mock")
    monkeypatch.setenv("DITTO_INTERNAL_API_KEY", "test-key")
    monkeypatch.setenv("DITTO_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))

    from ditto_service.main import app

    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/translations",
            headers={"X-Internal-Api-Key": "test-key"},
            json={
                "content": "내일까지 검토 부탁드려요.",
                "source_language": "ko",
                "target_language": "en",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "translated_content": "[en] 내일까지 검토 부탁드려요.",
            "source_language": "ko",
            "target_language": "en",
        }
