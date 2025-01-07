"""Copy files from external locations as defined in `sync.yml`."""

from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from collections import defaultdict
from contextlib import contextmanager
from enum import Enum
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from github import Auth, Github, UnknownObjectException
from jinja2.environment import Environment
from jinja2.exceptions import TemplateError, TemplateNotFound
from jinja2.loaders import FileSystemLoader
from jinja2.runtime import Context, Undefined
from jinja2.utils import missing
from jsonschema import validate
from rich import box
from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.padding import Padding
from rich.table import Table

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import Any, Callable

    from github.Repository import Repository
    from jinja2.style import Style

    AuditCurrent = tuple[str, str, str]
    AuditCounter = dict[str, int]
    AuditRegister = dict[str, Any]

INDENT = 4
CONSOLE = Console(color_system="standard", width=100_000_000, record=True)


def print(renderable, *, indent: int = 0, console: Console = CONSOLE, **kwargs) -> None:
    if indent:
        renderable = Padding.indent(renderable, indent)
    console.print(renderable, **kwargs)


def perror(
    renderable,
    *,
    console: Console = CONSOLE,
    style: Style | str | None = "bold red",
    **kwargs,
) -> None:
    try:
        console.stderr = True
        print(renderable, console=console, style=style, **kwargs)
    finally:
        console.stderr = False


class ActionError(Exception):
    pass


class TemplateState(Enum):
    UNUSED = "unused"
    MISSING = "missing"
    USED = "used"
    CONTEXT = "context"
    OPTIONAL = "optional"

    @classmethod
    def from_count(cls, count: int) -> TemplateState:
        if count < 0:
            return cls.MISSING
        if count == 0:
            return cls.UNUSED
        return cls.USED

    @classmethod
    def from_value(cls, value: Any) -> TemplateState:
        if isinstance(value, Undefined):
            return cls.MISSING
        if value is missing:
            return cls.OPTIONAL
        return cls.CONTEXT

    @cache
    def _get_emoji_style(self) -> tuple[str, str]:
        match self:
            case self.UNUSED:
                return ":warning-emoji:", "yellow"
            case self.MISSING:
                return ":cross_mark:", "red"
            case self.USED:
                return ":white_check_mark:", "green"
            case self.CONTEXT:
                return ":books:", "blue"
            case self.OPTIONAL:
                return ":heavy_plus_sign:", "yellow"
            case _:  # pragma: no cover
                raise ValueError("Invalid TemplateState")

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        emoji, style = self._get_emoji_style()
        # trying to return this in any other way will result in rich splitting the emoji
        # and the text into separate lines
        yield console.render_str(f"{emoji} [{style}]({self.value})[/]")

    def __rich_measure__(
        self, console: Console, options: ConsoleOptions
    ) -> Measurement:
        # explicitly calculate the size of the emoji and the text, otherwise, rich will
        # decide the emoji has no width and use the default/terminal width as the max
        emoji, style = self._get_emoji_style()
        # len(emoji) + space + parenthesis + len(value)
        size = console.measure(emoji).maximum + 1 + 2 + len(self.value)
        return Measurement(size, size)


class AuditFileSystemLoader(FileSystemLoader):
    def count(self, environment: Environment, key: str, increment: int) -> None:
        # count template usage
        if None not in (
            current := getattr(environment, "current", None),
            stubs := getattr(environment, "stubs", None),
        ):
            stubs[current][key] += increment

    def get_source(
        self,
        environment: Environment,
        stub: str,
    ) -> tuple[str, str, Callable[[], bool]]:
        try:
            # delegate to FileSystemLoader
            value = super().get_source(environment, stub)
        except TemplateNotFound:
            # TemplateNotFound: template does not exist, mark it as missing
            self.count(environment, stub, -1)
            raise
        else:
            # template found, mark it as used
            self.count(environment, stub, +1)
            return value


class AuditContext(Context):
    def register(self, environment: Environment, key: str, value: Any) -> None:
        # register variable usage, no point to count usage since it will always be 1
        if None not in (
            current := getattr(environment, "current", None),
            variables := getattr(environment, "variables", None),
        ):
            variables[current][key] = value

    def resolve_or_missing(self, key: str) -> Any:
        # delegate to Context
        value = super().resolve_or_missing(key)
        # register variable usage
        self.register(self.environment, key, value)
        return value


class AuditEnvironment(Environment):
    current: tuple[str, str, str] | None = None
    stubs: dict[AuditCurrent, AuditCounter]
    variables: dict[AuditCurrent, AuditRegister]

    context_class: type[Context] = AuditContext
    undefined: type[Undefined]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.stubs = defaultdict(lambda: defaultdict(int))
        self.variables = defaultdict(dict)

    @contextmanager
    def audit(
        self, file: str, src: str, dst: str
    ) -> Iterator[tuple[AuditCounter, AuditRegister]]:
        class AuditUndefined(Undefined):
            def __str__(slf) -> str:
                # only store undefined variables, ignore missing attributes/elements
                if slf._undefined_obj is missing and self.current:
                    self.variables[self.current][slf._undefined_name] = slf
                return super().__str__()

        stored_undefined = self.undefined
        try:
            # set current file & custom undefined
            self.current = (file, src, dst)
            self.undefined = AuditUndefined

            yield self.stubs[self.current], self.variables[self.current]
        finally:
            # clear current file & reset undefined
            self.current = None
            self.undefined = stored_undefined


def validate_file(value: str) -> Path | None:
    try:
        path = Path(value).expanduser().resolve()
        path.read_text()
        return path
    except (IsADirectoryError, PermissionError) as err:
        # IsADirectoryError: value is a directory, not a file
        # PermissionError: value is not readable
        raise ArgumentTypeError(f"{value} is not a valid file: {err}")
    except FileNotFoundError:
        # FileNotFoundError: value does not exist
        return None


def validate_dir(value: str) -> Path:
    try:
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        ignore = path / ".ignore"
        ignore.touch()
        ignore.unlink()
        return path
    except (FileExistsError, PermissionError) as err:
        # FileExistsError: value is a file, not a directory
        # PermissionError: value is not writable
        raise ArgumentTypeError(f"{value} is not a valid directory: {err}")


def parse_args(args: Sequence[str] | None = None) -> Namespace:
    # parse CLI for inputs
    parser = ArgumentParser()
    parser.add_argument("--config", type=validate_file, required=True)
    parser.add_argument("--stubs", type=validate_dir, required=True)
    return parser.parse_args(args)


def read_config(config: Path) -> dict:
    # read and validate configuration file
    config = yaml.load(
        config.read_text(),
        Loader=yaml.SafeLoader,
    )
    validate(
        config,
        schema={
            "type": "object",
            "patternProperties": {
                # GitHub repository name or local directory
                r"\w+/\w+|\..+": {
                    "type": "array",
                    "items": {
                        "type": ["string", "object"],
                        "minLength": 1,
                        "properties": {
                            "src": {"type": "string"},
                            "dst": {"type": "string"},
                            "remove": {"type": "boolean"},
                            "with": {
                                "type": "object",
                                "patternProperties": {
                                    r"\w+": {
                                        "type": [
                                            "string",
                                            "number",
                                            "boolean",
                                            "object",
                                            "array",
                                            "null",
                                        ],
                                    },
                                },
                            },
                        },
                    },
                }
            },
        },
    )
    return config


def parse_config(file: str | dict) -> tuple[str | None, Path, bool, dict[str, Any]]:
    src: str | None
    dst: Path
    remove: bool
    context: dict[str, Any]

    if isinstance(file, str):
        src = file
        dst = Path(file)
        remove = False
        context = {}
    elif isinstance(file, dict):
        src = file.get("src", None)
        if (tmp := file.get("dst", src)) is None:
            perror(f"* :cross_mark: Invalid file definition (`{file}`), expected `dst`")
            raise ActionError
        dst = Path(tmp)
        remove = file.get("remove", False)
        context = file.get("with", {})
    else:
        perror(
            f"* :cross_mark: Invalid file definition (`{file}`), "
            f"expected `str` or `dict`"
        )
        raise ActionError

    # to template a file we need a source file
    if not remove and src is None:
        perror(f"* :cross_mark: Invalid file definition (`{file}`), expected `src`")
        raise ActionError

    return src, dst, remove, context


def remove_file(dst: Path) -> int:
    try:
        dst.unlink()
    except FileNotFoundError:
        # FileNotFoundError: dst does not exist
        print(f"* :warning-emoji: `{dst}` already removed", indent=INDENT)
    except PermissionError as err:
        # PermissionError: not possible to remove dst
        perror(f"* :cross_mark: Failed to remove `{dst}`: {err}", indent=INDENT)
        return 1
    else:
        print(f"* :cross_mark_button: `{dst}` removed")
    return 0  # no errors


def template_file(
    env: Environment,
    current_repo: Repository,
    upstream_name: str,
    upstream_repo: Repository,
    src: str | None,
    dst: Path,
    context: dict[str, Any],
) -> int:
    # fetch src file
    try:
        content = upstream_repo.get_contents(src).decoded_content.decode()
    except UnknownObjectException as err:
        perror(f"* :cross_mark: Failed to fetch `{src}`: {err}", indent=INDENT)
        return 1

    # standard context with source and destination details
    standard_context = {
        # the current repository from which this GHA is being run,
        # where the new files will be written
        "repo": current_repo,
        "dst": current_repo,
        "destination": current_repo,
        "current": current_repo,
        # source (should be rarely, if ever, used in templating)
        "src": upstream_repo,
        "source": upstream_repo,
    }

    with env.audit(upstream_name, src, dst) as (stubs, variables):
        try:
            template = env.from_string(content)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(template.render(**{**context, **standard_context}))
        except TemplateError as err:
            perror(f"* :cross_mark: Failed to template `{src}`: {err}", indent=INDENT)
            return 1

        # display stubs & context for this file
        table = None
        error = False
        warning = False
        if stubs or variables:
            table = Table.grid(padding=(0, 1))
            # stubs
            for stub, count in stubs.items():
                state = TemplateState.from_count(count)
                table.add_row("*", state, f"`{stub}`")
            # variables
            for variable, value in variables.items():
                if variable in standard_context:
                    continue
                state = TemplateState.from_value(value)
                if isinstance(value, Undefined):
                    error = True
                    value = ""
                elif value is missing:
                    warning = True
                    value = ""
                table.add_row("*", state, f"`{variable}={value}`")
            for variable in set(context) - set(variables):
                value = context[variable]
                table.add_row("*", TemplateState.UNUSED, f"`{variable}={value}`")

        if error:
            perror(f"* :cross_mark: Context missing `{src}` → `{dst}`", indent=INDENT)
        elif warning:
            print(f"* :warning-emoji: `{src}` → `{dst}`", indent=INDENT)
        else:
            print(f"* :white_check_mark: `{src}` → `{dst}`", indent=INDENT)
        if table:
            print(table, indent=INDENT * 2)

        return int(error)


class LocalContents:
    # mirror GitHub contents object
    def __init__(self, path: Path):
        self.decoded_content = path.read_text().encode()


class LocalRepository:
    # mirror GitHub repository object
    def __init__(self, *paths: str | os.PathLike[str] | Path) -> None:
        self.path = Path(*paths).resolve()
        if not paths or not self.path.is_dir():
            raise FileNotFoundError(f"{self.path} is not a directory")

        # constants
        self.html_url = f"file://{self.path}"
        self.user = "<local>"
        self.name = "<local>"

    def get_contents(self, path: str) -> LocalContents:
        return LocalContents(self.path / path)


def iterate_config(
    config_path: Path,
    config: dict,
    gh: Github,
    env: Environment,
    current_repo: Repository,
) -> int:
    # iterate over configuration and template files
    errors = 0
    for upstream_name, files in config.items():
        try:
            if upstream_name.startswith("."):
                upstream_repo = LocalRepository(config_path.parent / upstream_name)
            else:
                upstream_repo = gh.get_repo(upstream_name)
        except (UnknownObjectException, FileNotFoundError) as err:
            # UnknownObjectException: repository does not exist
            # FileNotFoundError: path does not exist
            perror(f"* :cross_mark: Failed to fetch `{upstream_name}`: {err}")
            errors += 1
            continue
        else:
            print(f"* :arrows_counterclockwise: Fetching files from `{upstream_name}`")

        for file in files:
            try:
                # parse/standardize configuration
                src, dst, remove, context = parse_config(file)
            except ActionError:
                errors += 1
                continue

            if remove:
                errors += remove_file(dst)
            else:
                errors += template_file(
                    env, current_repo, upstream_name, upstream_repo, src, dst, context
                )

    return errors


def dump_summary(errors: int):
    # dump summary to GitHub Actions summary
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    output = os.getenv("GITHUB_OUTPUT")
    if summary or output:
        html = CONSOLE.export_text()
    if summary:
        Path(summary).write_text(f"### Templating Audit\n{html}")
    if output:
        with Path(output).open("a") as fh:
            fh.write(
                # https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/workflow-commands-for-github-actions#setting-an-output-parameter
                # https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/workflow-commands-for-github-actions#multiline-strings
                f"summary<<GITHUB_OUTPUT_summary\n"
                f"<details {'open' if errors else ''}>\n"
                f"<summary>Templating Audit</summary>\n"
                f"\n"
                f"{html}\n"
                f"\n"
                f"</details>\n"
                f"GITHUB_OUTPUT_summary\n"
            )


def main():
    args = parse_args()
    if not args.config:
        print(":warning-emoji: No configuration file found, nothing to update")
        dump_summary()
        sys.exit(0)
    errors = 0

    config = read_config(args.config)

    # initialize stub loader
    loader = AuditFileSystemLoader(args.stubs)

    # initialize Jinja environment
    env = AuditEnvironment(
        loader=loader,
        # {{ }} is used in MermaidJS
        # ${{ }} is used in GitHub Actions
        # { } is used in Python
        # %( )s is used in Python
        block_start_string="[%",
        block_end_string="%]",
        variable_start_string="[[",
        variable_end_string="]]",
        comment_start_string="[#",
        comment_end_string="#]",
        keep_trailing_newline=True,
    )

    # initialize GitHub client
    gh = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))

    # get current repository
    current_name = os.environ["GITHUB_REPOSITORY"]
    try:
        current_repo = gh.get_repo(current_name)
    except UnknownObjectException as err:
        perror(f":cross_mark: Failed to fetch `{current_name}`: {err}")
        errors += 1

    if not errors:
        errors += iterate_config(args.config, config, gh, env, current_repo)

    # provide audit of stub usage
    stubs = defaultdict(int)
    for counts in env.stubs.values():
        for key, count in counts.items():
            stubs[key] += count

    if stubs:
        table = Table("Stub", "State", "Count", box=box.MARKDOWN)
        for stub, count in stubs.items():
            state = TemplateState.from_count(count)
            table.add_row(f"`{stub}`", state, str(abs(count)))
        print(table)

    if errors:
        perror(f"Got {errors} error(s)")

    dump_summary(errors)
    sys.exit(errors)


if __name__ == "__main__":
    main()
