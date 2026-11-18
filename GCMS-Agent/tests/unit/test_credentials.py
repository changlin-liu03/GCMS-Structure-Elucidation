import pytest
from gcms_agent.credentials import load_project_credentials


def test_loads_only_project_key_without_shell_or_variable_expansion(tmp_path):
    (tmp_path / ".env").write_text('DEEPSEEK_API_KEY="test-local-value"\nANTHROPIC_BASE_URL=https://untrusted.invalid\nOTHER=$(whoami)\n', encoding="utf-8")
    env = {}
    assert load_project_credentials(tmp_path, env)
    assert env == {"DEEPSEEK_API_KEY": "test-local-value"}


def test_process_environment_wins_without_reading_file(tmp_path):
    (tmp_path / ".env").write_text('DEEPSEEK_API_KEY="malformed', encoding="utf-8")
    env = {"DEEPSEEK_API_KEY": "test-process-value"}
    assert load_project_credentials(tmp_path, env)
    assert env["DEEPSEEK_API_KEY"] == "test-process-value"


def test_never_searches_parent_directories(tmp_path):
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=parent-test-value", encoding="utf-8")
    env = {}
    assert not load_project_credentials(tmp_path / "child", env)
    assert env == {}


@pytest.mark.parametrize("contents", [
    "DEEPSEEK_API_KEY=", "DEEPSEEK_API_KEY=replace-with-your-key",
    'DEEPSEEK_API_KEY="sensitive-test-value',
    "DEEPSEEK_API_KEY=sensitive-test-value\nDEEPSEEK_API_KEY=another-test-value",
])
def test_errors_never_echo_values(tmp_path, contents):
    (tmp_path / ".env").write_text(contents, encoding="utf-8")
    env = {}
    with pytest.raises(ValueError) as error:
        load_project_credentials(tmp_path, env)
    assert "sensitive-test-value" not in str(error.value)
    assert env == {}
