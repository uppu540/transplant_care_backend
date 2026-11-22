from pathlib import Path

from app.config import PROJECT_ENV_FILE, Settings


def test_settings_use_the_project_env_file_from_any_working_directory(
    monkeypatch,
    tmp_path,
):
    project_root = Path(__file__).resolve().parents[1]
    assert PROJECT_ENV_FILE == project_root / ".env"
    assert Settings.model_config["env_file"] == PROJECT_ENV_FILE

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MONGODB_DATABASE", "transplantcare_test")
    assert Settings().mongodb_database == "transplantcare_test"
