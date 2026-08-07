from api.logging_config import build_logging


def test_development_does_not_write_json_file_by_default(tmp_path):
    config = build_logging("development", "INFO", tmp_path)

    assert "arquivo" not in config["handlers"]
    assert config["root"]["handlers"] == ["console"]


def test_development_can_write_json_file_when_enabled(tmp_path):
    config = build_logging("development", "INFO", tmp_path, write_json_file=True)

    assert config["handlers"]["arquivo"]["filename"] == str(tmp_path / "api.jsonl")
    assert config["handlers"]["arquivo"]["formatter"] == "json"
    assert config["root"]["handlers"] == ["console", "arquivo"]


def test_production_keeps_json_file_without_explicit_opt_in(tmp_path):
    config = build_logging("production", "INFO", tmp_path)

    assert "arquivo" in config["handlers"]
