from __future__ import annotations

import sys
from argparse import ArgumentTypeError, Namespace
from contextlib import nullcontext
from inspect import isgenerator
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import yaml
from jinja2.environment import Environment
from jinja2.exceptions import TemplateNotFound
from jinja2.loaders import FileSystemLoader
from jinja2.runtime import DebugUndefined, StrictUndefined, Undefined
from jinja2.utils import missing
from jsonschema.exceptions import ValidationError
from rich.console import Console
from rich.measure import Measurement
from rich.text import Text

from template_files import (
    ActionError,
    AuditContext,
    AuditEnvironment,
    AuditStubs,
    LocalRepository,
    TemplateState,
    dump_summary,
    get_output_text,
    get_summary_text,
    parse_args,
    parse_config,
    perror,
    print,
    read_config,
    remove_file,
    template_file,
    validate_dir,
    validate_file,
)

if TYPE_CHECKING:
    from typing import Any, Final

    from pytest import CaptureFixture, MonkeyPatch
    from pytest_mocker import MockerFixture


ON_LINUX: Final = sys.platform == "linux"
DATA: Final = Path(__file__).parent / "data"
CONFIGS: Final = DATA / "configs"
UPSTREAM: Final = DATA / "upstream"


@pytest.fixture
def console() -> Console:
    return Console(color_system="standard", width=100_000_000, record=True)


def ids(value: Any) -> Any:
    if isinstance(value, TemplateState):
        return value.value
    elif isinstance(value, Path):
        return str(Path(*value.parts[-2:]))
    return value


def test_print(capsys: CaptureFixture, console: Console) -> None:
    print("foo", console=console)
    stdout, stderr = capsys.readouterr()
    assert stdout == "foo\n"
    assert not stderr

    print("foo", style="bold", console=console)
    stdout, stderr = capsys.readouterr()
    assert stdout == "\x1b[1mfoo\x1b[0m\n"
    assert not stderr

    print("foo", style="bold yellow", indent=4, console=console)
    stdout, stderr = capsys.readouterr()
    assert stdout == "\x1b[1;33m    \x1b[0m\x1b[1;33mfoo\x1b[0m\n"
    assert not stderr


def test_perror(capsys: CaptureFixture, console: Console) -> None:
    perror("foo", console=console)
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert stderr == "\x1b[1;31mfoo\x1b[0m\n"

    perror("foo", style="bold", console=console)
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert stderr == "\x1b[1mfoo\x1b[0m\n"

    perror("foo", style="bold yellow", indent=4, console=console)
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


@pytest.mark.parametrize(
    "count,expected",
    [
        (-2, TemplateState.MISSING),
        (-1, TemplateState.MISSING),
        (0, TemplateState.UNUSED),
        (1, TemplateState.USED),
        (2, TemplateState.USED),
    ],
    ids=ids,
)
def test_TemplateState_from_count(count: int, expected: TemplateState) -> None:
    assert TemplateState.from_count(count) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (Undefined(), TemplateState.MISSING),
        (StrictUndefined(), TemplateState.MISSING),
        (DebugUndefined(), TemplateState.MISSING),
        (missing, TemplateState.OPTIONAL),
        ("value", TemplateState.CONTEXT),
    ],
)
def test_TemplateState_from_value(value: Any, expected: TemplateState) -> None:
    assert TemplateState.from_value(value) == expected


@pytest.mark.parametrize(
    "state,expected_emoji,expected_style",
    [
        (TemplateState.UNUSED, "warning", "yellow"),
        (TemplateState.MISSING, "cross_mark", "red"),
        (TemplateState.USED, "white_check_mark", "green"),
        (TemplateState.CONTEXT, "books", "blue"),
        (TemplateState.OPTIONAL, "heavy_plus_sign", "yellow"),
    ],
    ids=ids,
)
def test_TemplateState_get_emoji_style(
    state: TemplateState, expected_emoji: str, expected_style: str
) -> None:
    emoji, style = state._get_emoji_style()
    assert emoji.name == expected_emoji
    assert style == expected_style


@pytest.mark.parametrize(
    "state,emoji,style",
    [
        (TemplateState.UNUSED, "\u26a0\ufe0f", "yellow"),  # ⚠️
        (TemplateState.MISSING, "\u274c\ufe0f", "red"),  # ❌
        (TemplateState.USED, "\u2705\ufe0f", "green"),  # ✅
        (TemplateState.CONTEXT, "\U0001f4da\ufe0f", "blue"),  # 📚
        (TemplateState.OPTIONAL, "\u2795\ufe0f", "yellow"),  # ➕
    ],
    ids=ids,
)
def test_TemplateState_rich_console(
    console: Console, state: TemplateState, emoji: str, style: str
) -> None:
    result = state.__rich_console__(console, console.options)
    assert isgenerator(result)
    expanded = list(result)
    assert len(expanded) == 1
    text = expanded[0]
    assert isinstance(text, Text)
    assert text.plain == f"{emoji} ({state.value})"
    assert text.spans[-1].style == style


@pytest.mark.parametrize(
    "state,size",
    [
        (TemplateState.UNUSED, 10),
        (TemplateState.MISSING, 12),
        (TemplateState.USED, 9),
        (TemplateState.CONTEXT, 12),
        (TemplateState.OPTIONAL, 13),
    ],
    ids=ids,
)
def test_TemplateState_rich_measure(
    console: Console, state: TemplateState, size: int
) -> None:
    assert state.__rich_measure__(console, console.options) == Measurement(size, size)


def test_AuditStubs(mocker: MockerFixture) -> None:
    environment = Environment()
    cache = {(None, "stub"): "placeholder"}
    audit = AuditStubs(environment, cache)

    count = mocker.spy(audit, "count")

    assert audit.get((None, "stub")) == "placeholder"
    assert count.call_count == 1
    assert audit.get((None, "missing")) is None
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
    environment = AuditEnvironment(loader=FileSystemLoader(UPSTREAM))
    context = AuditContext(environment, {}, None, {})
    context.vars["variable"] = (value := uuid4().hex)

    with environment.audit(*(current := ("file", "src", "dst"))) as (stubs, variables):
        assert isinstance(stubs, dict) and not stubs
        assert isinstance(variables, dict) and not variables

        assert environment.current == current
        assert environment.stubs[current] is stubs
        assert environment.variables[current] is variables

        for _ in range(5):
            assert environment.get_template("stub")
        for _ in range(3):
            with pytest.raises(TemplateNotFound):
                environment.get_template("missing")
        assert context.resolve_or_missing("variable") == value
        assert context.resolve_or_missing("missing") == missing

        assert stubs["stub"] == +5
        assert stubs["missing"] == -3
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
        *[(path, False) for path in (CONFIGS / "valid").iterdir()],
        *[(path, True) for path in (CONFIGS / "invalid").iterdir()],
    ],
    ids=ids,
)
def test_read_config(path: Path, raises: bool) -> None:
    with pytest.raises(ValidationError) if raises else nullcontext():
        assert read_config(path)


@pytest.mark.parametrize(
    "path,config,raises",
    [
        *[(path, None, False) for path in (CONFIGS / "valid").iterdir()],
        *[(path, None, True) for path in (CONFIGS / "inconsistent").iterdir()],
        pytest.param(None, {"org/repo": [1]}, True, id="cannot parse number"),
        pytest.param(None, {"org/repo": [True]}, True, id="cannot parse bool"),
    ],
    ids=ids,
)
def test_parse_config(path: Path | None, config: dict | None, raises: bool) -> None:
    # read config from path
    if path:
        config = yaml.load(path.read_text(), Loader=yaml.SafeLoader)
    # either path or config must be provided
    assert config is not None

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


def test_template_file(tmp_path: Path, capsys: CaptureFixture) -> None:
    environment = AuditEnvironment(loader=FileSystemLoader(UPSTREAM))
    current = LocalRepository(tmp_path)
    upstream = LocalRepository(UPSTREAM)

    # template without errors
    assert (
        template_file(
            environment,
            current,
            upstream,
            "success",
            tmp_path / "out",
            {"variable": "value"},
        )
        == 0
    )
    stdout, stderr = capsys.readouterr()
    assert stdout
    assert not stderr

    # template with missing context
    assert (
        template_file(environment, current, upstream, "success", tmp_path / "out", {})
        == 1
    )
    stdout, stderr = capsys.readouterr()
    assert stdout
    assert stderr

    # template missing file
    assert (
        template_file(environment, current, upstream, "missing", tmp_path / "out", {})
        == 1
    )
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert stderr

    # template python error
    assert (
        template_file(
            environment, current, upstream, "python_error", tmp_path / "out", {}
        )
        == 1
    )
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert stderr

    # template error
    assert (
        template_file(
            environment, current, upstream, "template_error", tmp_path / "out", {}
        )
        == 1
    )
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert stderr


def test_iterate_config() -> None:
    pass


def test_get_summary_text() -> None:
    summary = get_summary_text(text := uuid4().hex)
    assert text in summary


def test_get_output_text() -> None:
    output = get_output_text(0, text := uuid4().hex)
    assert "<details>" in output
    assert text in output
    output = get_output_text(1, text := uuid4().hex)
    assert "<details open>" in output
    assert text in output


def test_dump_summary(
    capsys: CaptureFixture, monkeypatch: MonkeyPatch, tmp_path: Path, console: Console
) -> None:
    (step_summary := tmp_path / "step_summary").write_text("text to overwrite\n")
    (output := tmp_path / "output").write_text(old := "text to append\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    print(text := uuid4().hex, console=console)
    perror(error := uuid4().hex, console=console)
    dump_summary(0, console=console)
    stdout, stderr = capsys.readouterr()
    assert text in stdout
    assert error in stderr
    assert step_summary.read_text() == get_summary_text(f"{text}\n{error}\n")
    assert output.read_text() == old + get_output_text(0, f"{text}\n{error}\n")

    output.write_text(old)

    print(text := uuid4().hex, console=console)
    perror(error := uuid4().hex, console=console)
    dump_summary(1, console=console)
    stdout, stderr = capsys.readouterr()
    assert text in stdout
    assert error in stderr
    assert step_summary.read_text() == get_summary_text(f"{text}\n{error}\n")
    assert output.read_text() == old + get_output_text(1, f"{text}\n{error}\n")
