import os
from pathlib import Path

from run_workbench import load_local_deepseek_key


def test_load_local_deepseek_key_reads_zshrc_export(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    zshrc = home / ".zshrc"
    zshrc.write_text(
        'export OTHER_KEY="ignored"\n'
        'export DEEPSEEK_API_KEY="local-deepseek-key"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    loaded = load_local_deepseek_key()

    assert loaded is True
    assert os.environ["DEEPSEEK_API_KEY"] == "local-deepseek-key"


def test_load_local_deepseek_key_keeps_existing_environment(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text('export DEEPSEEK_API_KEY="from-zshrc"\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "already-present")

    loaded = load_local_deepseek_key()

    assert loaded is False
    assert os.environ["DEEPSEEK_API_KEY"] == "already-present"
