from pathlib import Path

from physical_context.config import Settings


def test_default_paths_expand_under_home() -> None:
    settings = Settings(_env_file=None)

    assert settings.port == 8787
    assert settings.resolved_data_root == Path.home() / ".pcl"
    assert settings.captures_dir == Path.home() / ".pcl" / "captures"
    assert settings.database_path == Path.home() / ".pcl" / "physical_context.db"


def test_environment_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PCL_PORT", "9000")
    monkeypatch.setenv("PCL_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("PCL_ANTHROPIC_MODEL", "configured-model")
    monkeypatch.setenv("PCL_VOYAGE_MODEL", "configured-embedding-model")
    monkeypatch.setenv("PCL_LOCAL_CAPTION", "true")
    monkeypatch.setenv("PCL_LOCAL_EMBED", "true")
    monkeypatch.setenv("PCL_SHARPNESS_THRESHOLD", "125.5")
    monkeypatch.setenv("PCL_BRIGHTNESS_THRESHOLD", "30")

    settings = Settings(_env_file=None)

    assert settings.port == 9000
    assert settings.resolved_data_root == tmp_path
    assert settings.anthropic_model == "configured-model"
    assert settings.voyage_model == "configured-embedding-model"
    assert settings.local_caption is True
    assert settings.local_embed is True
    assert settings.sharpness_threshold == 125.5
    assert settings.brightness_threshold == 30.0
