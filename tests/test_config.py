from pathlib import Path

import pytest

from cboard.config import load_config


def test_accounts_can_be_added_by_config_only(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('schema_version = 1\n' + "\n".join(f'[[accounts]]\nid = "account{i}"\nlabel = "계정 {i}"\ncodex_home = "{tmp_path}/home{i}"\n' for i in range(6)))
    config = load_config(path)
    assert len(config.accounts) == 6
    assert config.accounts[-1].label == "계정 5"


def test_duplicate_home_and_invalid_interval_are_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(f'[[accounts]]\nid="a"\ncodex_home="{tmp_path}/home"\n[[accounts]]\nid="b"\ncodex_home="{tmp_path}/home"')
    with pytest.raises(ValueError, match="Duplicate"):
        load_config(path)
    path.write_text('loaded_interval=nan')
    with pytest.raises(ValueError, match="Intervals"):
        load_config(path)


def test_account_terminal_appearance_is_optional_and_configurable(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(f'[[accounts]]\nid="a"\ncodex_home="{tmp_path}/a"\niterm_profile="Alpha"\niterm_background="A0B1C2"\n[[accounts]]\nid="b"\ncodex_home="{tmp_path}/b"')
    a, b = load_config(path).accounts
    assert a.iterm_profile == "Alpha"
    assert a.iterm_background == "a0b1c2"
    assert b.iterm_profile is b.iterm_background is None


@pytest.mark.parametrize("setting", [
    'iterm_profile=""', 'iterm_profile=123', r'iterm_profile="Alpha\n"',
    'iterm_background="blue"', 'iterm_background="#001e3a"',
    'iterm_background=123', r'iterm_background="001e3a\u001b"',
])
def test_invalid_terminal_appearance_rejected(tmp_path, setting):
    path = tmp_path / "config.toml"
    path.write_text(f'[[accounts]]\nid="a"\ncodex_home="{tmp_path}/a"\n{setting}\n')
    with pytest.raises(ValueError, match="iterm_"):
        load_config(path)
