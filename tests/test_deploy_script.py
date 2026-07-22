from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deploy_uses_clean_lockfile_install() -> None:
    source = (ROOT / "deploy.sh").read_text(encoding="utf-8")

    assert "npm ci" in source
    assert "npm install" not in source
    assert 'PATH="$(pwd)/../.venv/bin:$PATH" npm run build' in source


def test_deploy_backup_uses_vacuum_into_with_verification() -> None:
    # 活跃写入下 sqlite3.Connection.backup() 会概率性产损坏快照（2026-07-22 实证，
    # 见 docs/superpowers/specs/2026-07-22-deploy-backup-vacuum-into-design.md）。
    source = (ROOT / "deploy.sh").read_text(encoding="utf-8")

    assert "VACUUM INTO" in source
    assert "integrity_check" in source
    code_lines = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
    assert not any(".backup(" in line for line in code_lines)


def test_generated_calibration_reports_are_ignored() -> None:
    ignore_patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "docs/reports/" in ignore_patterns
