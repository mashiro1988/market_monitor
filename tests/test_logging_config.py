from loguru import logger

import config
from services import logging_config


def test_configure_logging_creates_rotating_file_sink(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_FILE_ENABLED", True)
    monkeypatch.setattr(config, "LOG_DIR", "app-logs")
    monkeypatch.setattr(config, "LOG_FILE_NAME", "test.log")
    monkeypatch.setattr(config, "LOG_LEVEL", "INFO")
    monkeypatch.setattr(config, "LOG_ROTATION", "1 MB")
    monkeypatch.setattr(config, "LOG_RETENTION", "1 day")
    monkeypatch.setattr(config, "LOG_COMPRESSION", "")
    monkeypatch.setattr(logging_config, "ROOT_DIR", tmp_path)
    logging_config._CONFIGURED = False

    log_path = logging_config.configure_logging(force=True)
    logger.info("persistent log smoke test")
    logger.complete()

    assert log_path == tmp_path / "app-logs" / "test.log"
    assert log_path.exists()
    assert "persistent log smoke test" in log_path.read_text(encoding="utf-8")
