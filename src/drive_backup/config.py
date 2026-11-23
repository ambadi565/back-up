"""User configuration: which folders to back up, ignore patterns, pause flag."""
from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class WatchedFolder:
    path: str
    drive_name: str


@dataclass
class Config:
    folders: list[WatchedFolder] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    paused: bool = False
    computer_name: str = field(default_factory=socket.gethostname)


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config()
    return Config(
        folders=[WatchedFolder(**f) for f in raw.get("folders", [])],
        ignore=list(raw.get("ignore", [])),
        paused=bool(raw.get("paused", False)),
        computer_name=raw.get("computer_name") or socket.gethostname(),
    )


def save_config(cfg: Config, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def add_folder(cfg: Config, path: str) -> Config:
    new = Path(path).expanduser().resolve()
    for f in cfg.folders:
        if _overlaps(new, Path(f.path)):
            raise ValueError(f"{new} overlaps with already-watched folder {f.path}")
    taken = {f.drive_name for f in cfg.folders}
    base = new.name or str(new).strip("\\/:") or "folder"
    name, n = base, 2
    while name in taken:
        name, n = f"{base} ({n})", n + 1
    return Config(
        folders=[*cfg.folders, WatchedFolder(str(new), name)],
        ignore=list(cfg.ignore),
        paused=cfg.paused,
        computer_name=cfg.computer_name,
    )


def remove_folder(cfg: Config, path: str) -> Config:
    target = str(Path(path).expanduser().resolve())
    return Config(
        folders=[f for f in cfg.folders if f.path != target and f.path != path],
        ignore=list(cfg.ignore),
        paused=cfg.paused,
        computer_name=cfg.computer_name,
    )
