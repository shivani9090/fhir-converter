import socket, subprocess, sys, time, urllib.request
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

@pytest.fixture(scope="session")
def base_url():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.app:app", "--port", str(port)], cwd=ROOT
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(url + "/health")
            break
        except OSError:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("API did not start")
    yield url
    proc.terminate()
    proc.wait(10)

@pytest.fixture(scope="session")
def samples():
    """Synthetic sample inputs shipped with the repo (data/sample/{ccda,stu3})."""
    return ROOT / "data" / "sample"

@pytest.fixture
def api(playwright, base_url):
    ctx = playwright.request.new_context(base_url=base_url)
    yield ctx
    ctx.dispose()
