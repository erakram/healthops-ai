import os
import tempfile
from pathlib import Path

# Point the app at a throwaway database before anything imports app.config.
_tmp = Path(tempfile.mkdtemp(prefix="healthops-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DIR"] = str(_tmp / "documents")
os.environ["SEED_DEMO_DATA"] = "true"
os.environ["GROQ_API_KEY"] = ""
os.environ["JOB_MAX_ATTEMPTS"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from tests.fake_llm import FakeLLM  # noqa: E402


@pytest.fixture(scope="session")
def fake_llm():
    return FakeLLM(review_issues=[{
        "title": "Chest X-ray billed but deferred", "severity": "medium",
        "detail": "The note says the chest X-ray was deferred, but CPT 71046 is billed.",
        "quote": "Chest X-ray was deferred as symptoms are improving.",
    }])


@pytest.fixture(scope="session")
def client(fake_llm):
    app.state.llm = fake_llm
    with TestClient(app) as c:
        yield c


def login(client: TestClient, role: str) -> None:
    client.cookies.clear()
    r = client.post("/api/auth/demo-login", json={"email": f"{role}@healthops.demo"})
    assert r.status_code == 200, r.text
