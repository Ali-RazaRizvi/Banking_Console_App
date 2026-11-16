"""Locates the config/ and logs/ folders, both in development and when packaged."""
import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    return base_dir() / "config"


def logs_dir() -> Path:
    path = base_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
