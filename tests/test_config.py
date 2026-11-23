import socket

import pytest

from drive_backup.config import Config, add_folder, load_config, save_config


def test_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path / "c.json")
    assert cfg.folders == []
    assert cfg.paused is False
    assert cfg.computer_name == socket.gethostname()


def test_roundtrip(tmp_path):
    p = tmp_path / "c.json"
    (tmp_path / "Docs").mkdir()
    cfg = add_folder(load_config(p), str(tmp_path / "Docs"))
    cfg.ignore = ["node_modules"]
    cfg.paused = True
    save_config(cfg, p)
    assert load_config(p) == cfg


def test_add_folder_disambiguates(tmp_path):
    (tmp_path / "a" / "Docs").mkdir(parents=True)
    (tmp_path / "b" / "Docs").mkdir(parents=True)
    cfg = load_config(tmp_path / "c.json")
    cfg = add_folder(cfg, str(tmp_path / "a" / "Docs"))
    cfg = add_folder(cfg, str(tmp_path / "b" / "Docs"))
    assert [f.drive_name for f in cfg.folders] == ["Docs", "Docs (2)"]


def test_add_nested_rejected(tmp_path):
    (tmp_path / "x" / "y").mkdir(parents=True)
    cfg = add_folder(load_config(tmp_path / "c.json"), str(tmp_path / "x"))
    with pytest.raises(ValueError):
        add_folder(cfg, str(tmp_path / "x" / "y"))
    cfg2 = add_folder(load_config(tmp_path / "d.json"), str(tmp_path / "x" / "y"))
    with pytest.raises(ValueError):
        add_folder(cfg2, str(tmp_path / "x"))
