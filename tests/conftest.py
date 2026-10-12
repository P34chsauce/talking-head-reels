import pytest

from reels import config as C


@pytest.fixture(autouse=True)
def shipped_config(tmp_path, monkeypatch):
    """Tests run against config/, never against a private/ override on this machine."""
    monkeypatch.setattr(C, "PRIVATE", tmp_path / "no-private")
