from __future__ import annotations

import sys
from argparse import ArgumentTypeError, Namespace
from contextlib import nullcontext
from inspect import isgenerator
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import yamlrocks
from github import GithubException
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
    iterate_config,
    parse_args,
    parse_config,
    perror,
    print,
    read_config,
    remove_file,
    template_file,
    update_config,
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

WARNING_EMOJI: Final = "\N{WARNING SIGN}\N{VARIATION SELECTOR-16}"
CROSS_MARK_EMOJI: Final = "\N{CROSS MARK}\N{VARIATION SELECTOR-16}"
CHECK_MARK_EMOJI: Final = "\N{WHITE HEAVY CHECK MARK}\N{VARIATION SELECTOR-16}"
BOOKS_EMOJI: Final = "\N{BOOKS}\N{VARIATION SELECTOR-16}"
PLUS_SIGN_EMOJI: Final = "\N{HEAVY PLUS SIGN}\N{VARIATION SELECTOR-16}"


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
    "state,emoji,style",
    [
        (TemplateState.UNUSED, ":warning-emoji:", "yellow"),
        (TemplateState.MISSING, ":cross_mark-emoji:", "red"),
        (TemplateState.USED, ":white_check_mark-emoji:", "green"),
        (TemplateState.CONTEXT, ":books-emoji:", "blue"),
        (TemplateState.OPTIONAL, ":heavy_plus_sign-emoji:", "yellow"),
    ],
    ids=ids,
)
def test_TemplateState_get_emoji_style(
    state: TemplateState, emoji: str, style: str
) -> None:
    assert state.emoji == emoji
    assert state.style == style


@pytest.mark.parametrize(
    "state,emoji,style",
    [
        (TemplateState.UNUSED, WARNING_EMOJI, "yellow"),
        (TemplateState.MISSING, CROSS_MARK_EMOJI, "red"),
        (TemplateState.USED, CHECK_MARK_EMOJI, "green"),
        (TemplateState.CONTEXT, BOOKS_EMOJI, "blue"),
        (TemplateState.OPTIONAL, PLUS_SIGN_EMOJI, "yellow"),
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
    "state",
    [
        TemplateState.UNUSED,
        TemplateState.MISSING,
        TemplateState.USED,
        TemplateState.CONTEXT,
        TemplateState.OPTIONAL,
    ],
    ids=ids,
)
def test_TemplateState_rich_measure(console: Console, state: TemplateState) -> None:
    size = console.measure(state.emoji).maximum + 1 + 2 + len(state.value)
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
        config = yamlrocks.loads(path.read_bytes())
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
    assert template_file(
        environment,
        current,
        upstream,
        "success",
        tmp_path / "out",
        {"variable": "value"},
    ) == (0, False)
    stdout, stderr = capsys.readouterr()
    assert stdout
    assert not stderr

    # template with missing context
    assert template_file(
        environment, current, upstream, "success", tmp_path / "out", {}
    ) == (1, False)
    stdout, stderr = capsys.readouterr()
    assert stdout
    assert stderr

    # missing source: remove dst and mark the config entry obsolete
    assert template_file(
        environment, current, upstream, "missing", tmp_path / "out", {}
    ) == (0, True)
    assert not (tmp_path / "out").exists()
    stdout, stderr = capsys.readouterr()
    assert not stderr
    assert "removed" in next(
        line for line in stdout.splitlines() if "not found" in line
    )

    # missing source and missing destination
    assert template_file(
        environment, current, upstream, "missing", tmp_path / "out", {}
    ) == (0, True)
    stdout, stderr = capsys.readouterr()
    assert not stderr
    assert "already removed" in next(
        line for line in stdout.splitlines() if "not found" in line
    )

    # template python error
    assert template_file(
        environment, current, upstream, "python_error", tmp_path / "out", {}
    ) == (1, False)
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert stderr

    # template error
    assert template_file(
        environment, current, upstream, "template_error", tmp_path / "out", {}
    ) == (1, False)
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert stderr


def test_template_file_fetch_error(
    tmp_path: Path, capsys: CaptureFixture, monkeypatch: MonkeyPatch
) -> None:
    environment = AuditEnvironment(loader=FileSystemLoader(UPSTREAM))
    current = LocalRepository(tmp_path)
    upstream = LocalRepository(UPSTREAM)
    dst = tmp_path / "out"
    dst.write_text("existing")

    def fail(src: str) -> None:
        raise GithubException(500, "boom")

    # failed fetches other than a missing source must not delete dst
    monkeypatch.setattr(upstream, "get_contents", fail)
    assert template_file(environment, current, upstream, "success", dst, {}) == (
        1,
        False,
    )
    assert dst.read_text() == "existing"
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert "Failed to fetch" in stderr


def test_update_config_anchor(tmp_path: Path, capsys: CaptureFixture) -> None:
    config_path = tmp_path / "config.yml"
    original = (
        ".upstream:\n"
        "  - src: missing\n"
        "    dst: a\n"
        "    with: &vars\n"
        "      variable: value\n"
        "  - src: keep\n"
        "    dst: b\n"
        "    with: *vars\n"
    )
    config_path.write_text(original)

    # dropping an anchor that is still referenced must not corrupt the config
    assert update_config(config_path, [(".upstream", 0)]) == 1
    assert config_path.read_text() == original
    _, stderr = capsys.readouterr()
    assert "anchor" in stderr


def test_update_config_invalid_yaml(tmp_path: Path, capsys: CaptureFixture) -> None:
    config_path = tmp_path / "config.yml"
    original = "a: [unclosed\n"
    config_path.write_text(original)

    assert update_config(config_path, [("a", 0)]) == 1
    assert config_path.read_text() == original
    _, stderr = capsys.readouterr()
    assert "Failed to parse" in stderr


def test_iterate_config(
    tmp_path: Path, capsys: CaptureFixture, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    (upstream := tmp_path / ".upstream").mkdir()
    (upstream / "keep").write_text("keep")
    (tmp_path / ".other").mkdir()
    (tmp_path / "gone").write_text("gone")

    (config_path := tmp_path / "config.yml").write_text(
        "# leading comment\n"
        ".upstream:\n"
        "  # keep this entry\n"
        "  - src: keep\n"
        "    dst: keep.txt\n"
        "  # source was retired upstream\n"
        "  - src: missing\n"
        "    dst: gone\n"
        "  # destination is already gone\n"
        "  - dst: absent.txt\n"
        "    remove: true\n"
        "\n"
        "# empty after cleanup\n"
        ".other:\n"
        "  - dst: absent2.txt\n"
        "    remove: true\n"
    )

    environment = AuditEnvironment(loader=FileSystemLoader(tmp_path))
    current = LocalRepository(tmp_path)
    errors, removals = iterate_config(
        config_path, read_config(config_path), None, environment, current
    )
    assert errors == 0

    # destination handling
    assert (tmp_path / "keep.txt").read_text() == "keep"
    assert not (tmp_path / "gone").exists()
    assert removals == [
        (Path("gone"), "missing"),
        (Path("absent.txt"), None),
        (Path("absent2.txt"), None),
    ]

    # obsolete entries are gone, everything else (including comments) is kept
    assert read_config(config_path) == {
        ".upstream": [{"src": "keep", "dst": "keep.txt"}]
    }
    config_text = config_path.read_text()
    assert config_text.startswith("# leading comment\n")
    assert "keep this entry" in config_text
    assert "retired upstream" not in config_text

    stdout, stderr = capsys.readouterr()
    assert "not found" in stdout
    assert "already removed" in stdout
    assert "Removed obsolete entries" in stdout
    assert not stderr


def test_iterate_config_fetch_error(
    tmp_path: Path, capsys: CaptureFixture, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    (tmp_path / ".upstream").mkdir()
    (tmp_path / "kept").write_text("kept")
    config_path = tmp_path / "config.yml"
    config_path.write_text(".upstream:\n  - src: keep\n    dst: kept\n")

    def fail(self: LocalRepository, src: str) -> None:
        raise GithubException(500, "boom")

    # other fetch failures are errors and leave dst and the config untouched
    monkeypatch.setattr(LocalRepository, "get_contents", fail)
    environment = AuditEnvironment(loader=FileSystemLoader(tmp_path))
    current = LocalRepository(tmp_path)
    errors, removals = iterate_config(
        config_path, read_config(config_path), None, environment, current
    )
    assert errors == 1
    assert removals == []
    assert (tmp_path / "kept").read_text() == "kept"
    assert config_path.read_text() == ".upstream:\n  - src: keep\n    dst: kept\n"
    _, stderr = capsys.readouterr()
    assert "Failed to fetch" in stderr


def test_get_summary_text() -> None:
    summary = get_summary_text(text := uuid4().hex, [])
    assert text in summary
    assert "### :mag: Templating Audit" in summary
    assert "Removed files" not in summary

    summary = get_summary_text(text, [(Path("a.txt"), "missing.txt")])
    assert "> :wastebasket: **Removed files**" in summary
    assert "> - `a.txt` (`missing.txt` not found)" in summary
    assert summary.index("> [!NOTE]") < summary.index(text)


def test_get_output_text() -> None:
    output = get_output_text(0, text := uuid4().hex, [])
    assert "<details>" in output
    assert "<summary>:mag: Templating Audit</summary>" in output
    assert text in output
    assert "Removed files" not in output
    output = get_output_text(1, text := uuid4().hex, [])
    assert "<details open>" in output
    assert text in output

    # removals are rendered before, and therefore outside, the details block
    output = get_output_text(0, text, [(Path("a.txt"), None)])
    assert "> - `a.txt`\n" in output
    assert output.index("> [!NOTE]") < output.index("<details>")


def test_dump_summary(
    capsys: CaptureFixture, monkeypatch: MonkeyPatch, tmp_path: Path, console: Console
) -> None:
    (step_summary := tmp_path / "step_summary").write_text(
        old_summary := "text to append\n"
    )
    (output := tmp_path / "output").write_text(old := "text to append\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    print(text := uuid4().hex, console=console)
    perror(error := uuid4().hex, console=console)
    dump_summary(0, [], console=console)
    stdout, stderr = capsys.readouterr()
    assert text in stdout
    assert error in stderr
    first = get_summary_text(f"{text}\n{error}\n", [])
    assert step_summary.read_text() == old_summary + first
    assert output.read_text() == old + get_output_text(0, f"{text}\n{error}\n", [])

    output.write_text(old)

    print(text := uuid4().hex, console=console)
    perror(error := uuid4().hex, console=console)
    removals = [(Path("a.txt"), "missing.txt")]
    dump_summary(1, removals, console=console)
    stdout, stderr = capsys.readouterr()
    assert text in stdout
    assert error in stderr
    assert step_summary.read_text() == old_summary + first + get_summary_text(
        f"{text}\n{error}\n", removals
    )
    assert output.read_text() == old + get_output_text(
        1, f"{text}\n{error}\n", removals
    )
