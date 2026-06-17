import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def find_bundled_python():
    candidates = [
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / "venv" / "Scripts" / "python.exe",
        PROJECT_ROOT.parent / "Application" / "carriage_venv" / "Scripts" / "python.exe",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def relaunch_with_bundled_python():
    candidate = find_bundled_python()
    if candidate is None:
        return

    current = Path(sys.executable).resolve()
    if current == candidate.resolve():
        return

    if os.environ.get("AL_REHMAN_GOODS_TRANSPORT_RELAUNCHED") == "1":
        return

    env = os.environ.copy()
    env["AL_REHMAN_GOODS_TRANSPORT_RELAUNCHED"] = "1"
    completed = subprocess.run(
        [str(candidate), str(PROJECT_ROOT / "run.py"), *sys.argv[1:]],
        env=env,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    relaunch_with_bundled_python()


import uvicorn

from al_rehman_goods_transport import create_app
from al_rehman_goods_transport.core.config import settings


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, reload=False)
