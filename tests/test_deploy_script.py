from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deploy_uses_clean_lockfile_install() -> None:
    source = (ROOT / "deploy.sh").read_text(encoding="utf-8")

    assert "npm ci" in source
    assert "npm install" not in source
    assert 'PATH="$(pwd)/../.venv/bin:$PATH" npm run build' in source


def test_generated_calibration_reports_are_ignored() -> None:
    ignore_patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "docs/reports/" in ignore_patterns
