"""Read YAML settings files and run a command function.

The settings files are project.yml, deployment.yml, and the configs for
synth and sweep.
"""

from typing import TYPE_CHECKING

from . import _messages as msg
from . import _wording
from ._io import (
    YamlParseError,
    display_name,
    load_yaml,
    resolve_in_workspace,
    validated_name,
)
from ._messages import Label
from ._shared import RESERVED_KEYS, UserFacingError
from ._ui import ValidationContext

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path


class ConfigError(UserFacingError):
    """Raised when a config file cannot be used."""


def read_config(config_path: Path | str) -> dict:
    """Read a YAML configuration file and return its contents as a dict.

    Raise ConfigError if the file is missing, empty, not YAML, or not a
    mapping. The error message never quotes the file, because the file may
    hold the secret.
    """
    name = display_name(config_path)
    try:
        result = load_yaml(config_path)
    except FileNotFoundError:
        raise ConfigError(msg.CONFIG_NOT_FOUND(name=name)) from None
    except UnicodeDecodeError:
        raise ConfigError(msg.FILE_NOT_UTF8(name=name)) from None
    except YamlParseError as e:
        raise ConfigError(
            msg.CONFIG_NOT_YAML(name=name, line=e.line, column=e.column)
        ) from None
    if result is None:
        raise ConfigError(msg.CONFIG_EMPTY(name=name))
    if not isinstance(result, dict):
        raise ConfigError(msg.CONFIG_NOT_MAPPING(name=name))
    return result


def _key_problem(
    configuration: dict, allowed: Iterable[str], config_name: str
) -> str | None:
    """Return a message about reserved or unknown keys in a config, or None."""
    disallowed = sorted(RESERVED_KEYS & configuration.keys())
    if disallowed:
        return msg.CONFIG_RESERVED_KEYS(
            name=config_name, keys=_wording.listed(disallowed)
        )
    allowed = [key for key in allowed if key not in RESERVED_KEYS]
    unknown = sorted(map(str, configuration.keys() - set(allowed)))
    if unknown:
        return msg.CONFIG_UNKNOWN_KEYS(
            name=config_name, keys=unknown, allowed=_wording.listed(allowed)
        )
    return None


def load_config(config_path: Path | str, allowed_keys: Iterable[str]) -> dict | int:
    """Load a config file from the workspace and check its keys.

    Return the config if it is valid. Otherwise report the problem and return
    the exit code.
    """
    try:
        validated_name(str(config_path), what="The config file name")
        resolved = resolve_in_workspace(config_path)
        config = read_config(resolved)
    except (ConfigError, ValueError) as e:
        return ValidationContext().fail_once(Label.CONFIG_FILE, str(e))
    problem = _key_problem(config, allowed_keys, display_name(resolved))
    if problem:
        return ValidationContext().fail_once(Label.CONFIG_FILE, str(problem))
    return config


def run_command(func: Callable[..., int], configuration: dict) -> int:
    """Run a pprl command function with the given configuration.

    Every command checks all of its inputs before it writes anything. So the
    only error expected here is an output file that appeared in the
    meantime. Any other error is a bug, and is left to surface as one.
    """
    try:
        return func(**configuration)
    except FileExistsError as e:
        return ValidationContext().fail_once(Label.OUTPUT_FILE, str(e))
