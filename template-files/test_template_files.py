from __future__ import annotations

import sys
from argparse import ArgumentTypeError, Namespace
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import yaml
from action import (
    ActionError,
    AuditContext,
    AuditEnvironment,
    AuditFileSystemLoader,
    TemplateState,
    parse_args,
    parse_config,
    perror,
    print,
    read_config,
    remove_file,
    validate_dir,
    validate_file,
)
from jinja2.environment import Environment
from jinja2.exceptions import TemplateNotFound
from jinja2.runtime import DebugUndefined, StrictUndefined, Undefined
from jinja2.utils import missing
from jsonschema.exceptions import ValidationError

if TYPE_CHECKING:
    from pytest import CaptureFixture, MonkeyPatch
    from pytest_mocker import MockerFixture


ON_LINUX = sys.platform == "linux"
DATA = Path(__file__).parent / "data"
CONFIGS = DATA / "configs"


def test_print(capsys: CaptureFixture) -> None:
    print("foo")
    stdout, stderr = capsys.readouterr()
    assert stdout == "foo\n"
    assert not stderr

    print("foo", style="bold")
    stdout, stderr = capsys.readouterr()
    assert stdout == "\x1b[1mfoo\x1b[0m\n"
    assert not stderr

    print("foo", style="bold yellow", indent=4)
    stdout, stderr = capsys.readouterr()
    assert stdout == "\x1b[1;33m    \x1b[0m\x1b[1;33mfoo\x1b[0m\n"
    assert not stderr


def test_perror(capsys: CaptureFixture) -> None:
    perror("foo")
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert stderr == "\x1b[1;31mfoo\x1b[0m\n"

    perror("foo", style="bold")
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert stderr == "\x1b[1mfoo\x1b[0m\n"

    perror("foo", style="bold yellow", indent=4)
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert stderr == "\x1b[1;33m    \x1b[0m\x1b[1;33mfoo\x1b[0m\n"


def test_TemplateState() -> None:
    assert set(TemplateState.__members__.values()) == {
        TemplateState.UNUSED,
        TemplateState.MISSING,
        TemplateState.USED,
        TemplateState.CONTEXT,
        TemplateState.OPTIONAL,
    }


def test_TemplateState_from_count() -> None:
    assert TemplateState.from_count(-2) == TemplateState.MISSING
    assert TemplateState.from_count(-1) == TemplateState.MISSING
    assert TemplateState.from_count(0) == TemplateState.UNUSED
    assert TemplateState.from_count(1) == TemplateState.USED
    assert TemplateState.from_count(2) == TemplateState.USED


def test_TemplateState_from_value() -> None:
    assert TemplateState.from_value(Undefined()) == TemplateState.MISSING
    assert TemplateState.from_value(StrictUndefined()) == TemplateState.MISSING
    assert TemplateState.from_value(DebugUndefined()) == TemplateState.MISSING
    assert TemplateState.from_value(missing) == TemplateState.OPTIONAL
    assert TemplateState.from_value("value") == TemplateState.CONTEXT


def test_TemplateState_get_emoji_style() -> None:
    assert TemplateState.UNUSED._get_emoji_style() == (":warning-emoji:", "yellow")
    assert TemplateState.MISSING._get_emoji_style() == (":cross_mark:", "red")
    assert TemplateState.USED._get_emoji_style() == (":white_check_mark:", "green")
    assert TemplateState.CONTEXT._get_emoji_style() == (":books:", "blue")
    assert TemplateState.OPTIONAL._get_emoji_style() == (":heavy_plus_sign:", "yellow")


def test_AuditFileSystemLoader(mocker: MockerFixture) -> None:
    environment = Environment()
    loader = AuditFileSystemLoader(DATA)

    count = mocker.spy(loader, "count")

    assert loader.get_source(environment, "stub.txt")
    assert count.call_count == 1
    with pytest.raises(TemplateNotFound):
        loader.get_source(environment, "missing.txt")
    assert count.call_count == 2


def test_AuditContext(mocker: MockerFixture) -> None:
    environment = Environment()
    context = AuditContext(environment, {}, None, {})
    context.vars["variable"] = (value := uuid4().hex)

    register = mocker.spy(context, "register")

    assert context.resolve_or_missing("variable") == value
    assert register.call_count == 1
    assert context.resolve_or_missing("missing") == missing
    assert register.call_count == 2


def test_AuditEnvironment() -> None:
    environment = AuditEnvironment()
    loader = AuditFileSystemLoader(DATA)
    context = AuditContext(environment, {}, None, {})
    context.vars["variable"] = (value := uuid4().hex)

    with environment.audit(
        file := uuid4().hex,
        src := uuid4().hex,
        dst := uuid4().hex,
    ) as (stubs, variables):
        assert isinstance(stubs, dict) and not stubs
        assert isinstance(variables, dict) and not variables

        current = (file, src, dst)
        assert environment.current == current
        assert environment.stubs[current] is stubs
        assert environment.variables[current] is variables

        for _ in range(5):
            assert loader.get_source(environment, "stub.txt")
        for _ in range(3):
            with pytest.raises(TemplateNotFound):
                loader.get_source(environment, "missing.txt")
        assert context.resolve_or_missing("variable") == value
        assert context.resolve_or_missing("missing") == missing

        assert stubs["stub.txt"] == +5
        assert stubs["missing.txt"] == -3
        assert variables["variable"] == value
        assert variables["missing"] == missing


def test_validate_file(tmp_path: Path) -> None:
    # directory
    with pytest.raises(ArgumentTypeError, match=r"not a valid file"):
        assert validate_file(tmp_path)

    # missing
    assert validate_file(tmp_path / "missing") is None

    # file
    (file := tmp_path / "file").write_text(uuid4().hex)
    assert validate_file(file) == file

    # permissions
    stat = file.stat()
    try:
        # make file unreadable
        file.chmod(0o000)
        with pytest.raises(ArgumentTypeError, match=r"not a valid file"):
            assert validate_file(tmp_path)
    finally:
        # cleanup so tmp_path can be removed
        file.chmod(stat.st_mode)


def test_validate_dir(tmp_path: Path) -> None:
    # directory
    assert validate_dir(tmp_path)

    # missing
    assert validate_dir(tmp_path / "missing")

    # file
    (path := tmp_path / "file").write_text(uuid4().hex)
    with pytest.raises(ArgumentTypeError, match=r"not a valid directory"):
        assert validate_dir(path) == path

    # permissions
    # TODO: not easy to test using either chmod or chown


def test_parse_args(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        assert parse_args([])

    (config := tmp_path / "config.yml").write_text("")
    with pytest.raises(SystemExit):
        assert parse_args([f"--config={config}"])

    (stubs := tmp_path / "stubs").mkdir()
    with pytest.raises(SystemExit):
        assert parse_args([f"--stubs={stubs}"])

    assert parse_args([f"--config={config}", f"--stubs={stubs}"]) == Namespace(
        config=config, stubs=stubs
    )


@pytest.mark.parametrize(
    "path,raises",
    [
        *[
            pytest.param(path, False, id=path.name)
            for path in (CONFIGS / "valid").iterdir()
        ],
        *[
            pytest.param(path, True, id=path.name)
            for path in (CONFIGS / "invalid").iterdir()
        ],
    ],
)
def test_read_config(path: Path, raises: bool) -> None:
    with pytest.raises(ValidationError) if raises else nullcontext():
        assert read_config(path)


@pytest.mark.parametrize(
    "config,raises",
    [
        *[
            pytest.param(
                yaml.load(path.read_text(), Loader=yaml.SafeLoader), False, id=path.name
            )
            for path in (CONFIGS / "valid").iterdir()
        ],
        *[
            pytest.param(
                yaml.load(path.read_text(), Loader=yaml.SafeLoader), True, id=path.name
            )
            for path in (CONFIGS / "inconsistent").iterdir()
        ],
        pytest.param({"org/repo": [1]}, True, id="cannot parse number"),
        pytest.param({"org/repo": [True]}, True, id="cannot parse bool"),
    ],
)
def test_parse_config(config: dict, raises: bool) -> None:
    for repo, files in config.items():
        for file in files:
            with pytest.raises(ActionError) if raises else nullcontext():
                assert parse_config(file)


def test_remove_file(tmp_path: Path, capsys: CaptureFixture) -> None:
    # directory
    assert remove_file(tmp_path) == 1
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert "Failed to remove" in stderr

    # missing
    assert remove_file(tmp_path / "missing") == 0
    stdout, stderr = capsys.readouterr()
    assert "already removed" in stdout
    assert not stderr

    # file
    (file := tmp_path / "file").touch()
    assert remove_file(file) == 0
    stdout, stderr = capsys.readouterr()
    assert "removed" in stdout

    # permissions
    (file := tmp_path / "file").touch()
    stat = tmp_path.stat()
    try:
        # make directory unreadable
        tmp_path.chmod(0o000)
        assert remove_file(file) == 1
        stdout, stderr = capsys.readouterr()
        assert not stdout
        assert "Failed to remove" in stderr
    finally:
        # cleanup so tmp_path can be removed
        tmp_path.chmod(stat.st_mode)
