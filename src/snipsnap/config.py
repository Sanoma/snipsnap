"""Project configuration from the ``[tool.snipsnap]`` table of ``pyproject.toml``."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigError(Exception):
    pass


class Config(BaseModel):
    """Keys use the same kebab-case names as the matching command line flags."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        alias_generator=lambda name: name.replace("_", "-"),
    )

    include: tuple[str, ...] = Field(
        default=(), description="only scan files matching one of these globs (default: all)"
    )
    exclude: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    min_lines: int | None = Field(default=None, ge=1)
    similarity: float | None = Field(default=None, gt=0, le=1)
    include_overrides: bool = False
    strict: bool = False


def _read_table(pyproject: Path) -> dict[str, object] | None:
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError:
        return None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{pyproject}: {exc}") from exc
    table = data.get("tool", {}).get("snipsnap")
    if table is None:
        return None
    if not isinstance(table, dict):
        raise ConfigError(f"{pyproject}: [tool.snipsnap] must be a table")
    return table


def _validate(table: dict[str, object], source: Path) -> Config:
    try:
        return Config.model_validate(table)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, err['loc'])) or '[tool.snipsnap]'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{source}: invalid [tool.snipsnap]: {problems}") from exc


def load_config(start: Path, explicit: Path | None = None) -> Config:
    """Config of the nearest ``pyproject.toml`` at or above ``start`` that has a
    ``[tool.snipsnap]`` table, or of ``explicit``; defaults if there is none."""
    if explicit is not None:
        table = _read_table(explicit)
        if table is None:
            raise ConfigError(f"{explicit}: no [tool.snipsnap] table")
        return _validate(table, explicit)
    for directory in (start, *start.parents):
        pyproject = directory / "pyproject.toml"
        if (table := _read_table(pyproject)) is not None:
            return _validate(table, pyproject)
    return Config()
