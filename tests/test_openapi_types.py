import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_api_types_are_generated_from_openapi():
    types_path = ROOT / "frontend" / "src" / "api" / "types.ts"
    before = types_path.read_text(encoding="utf-8")

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_openapi_types.py")],
        cwd=str(ROOT),
        check=True,
    )

    assert types_path.read_text(encoding="utf-8") == before
