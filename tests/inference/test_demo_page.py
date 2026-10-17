from fastapi.testclient import TestClient

from fluffy.demo.app import app


def test_clip_page_loads():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Watch a clip" in response.text
    assert 'value="speech_cognitive"' in response.text
    assert "Fluffy would do this" in response.text
    assert "Fluffy would ask" in response.text
    assert "Nothing to do" in response.text


def test_analyze_requires_a_video():
    client = TestClient(app)
    response = client.post("/api/analyze", data={"profile": "blind", "utterance": ""})
    assert response.status_code == 400
    assert "video" in response.json()["detail"].lower()
