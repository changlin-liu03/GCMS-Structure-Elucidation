"""Configuration-directory behavior, independent of model/API access."""
import shutil

import pytest
import yaml

from gcms_agent.config import PROJECT_ROOT, load_config


@pytest.fixture
def custom_configs(tmp_path):
    destination = tmp_path / "custom configs"
    shutil.copytree(PROJECT_ROOT / "configs", destination)
    return destination


def update_yaml(path, section, key, value):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if section is None:
        data[key] = value
    else:
        data.setdefault(section, {})[key] = value
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_custom_economy_profile_overrides_custom_base(custom_configs):
    update_yaml(custom_configs / "model.yaml", None, "model", "custom-model")
    update_yaml(custom_configs / "runtime.yaml", None, "max_rounds", 7)
    update_yaml(custom_configs / "profiles/economy.yaml", "runtime", "max_rounds", 2)
    config = load_config(custom_configs, profile="economy")
    assert config["model"]["model"] == "custom-model"
    assert config["runtime"]["max_rounds"] == 2


def test_missing_custom_economy_profile_has_no_default_fallback(custom_configs):
    profile = custom_configs / "profiles/economy.yaml"
    profile.unlink()
    with pytest.raises(FileNotFoundError, match="Missing economy profile") as error:
        load_config(custom_configs, profile="economy")
    assert str(profile) in str(error.value)


@pytest.mark.parametrize("profile", [None, "standard"])
def test_standard_configuration_does_not_require_economy_profile(custom_configs, profile):
    (custom_configs / "profiles/economy.yaml").unlink()
    update_yaml(custom_configs / "runtime.yaml", None, "max_rounds", 7)
    assert load_config(custom_configs, profile=profile)["runtime"]["max_rounds"] == 7


def test_relative_custom_directory_works_outside_project(custom_configs, monkeypatch):
    update_yaml(custom_configs / "profiles/economy.yaml", "runtime", "max_rounds", 2)
    monkeypatch.chdir(custom_configs.parent)
    assert load_config(custom_configs.name, profile="economy")["runtime"]["max_rounds"] == 2
