"""Copy files from external locations as defined in `sync.yml`."""

from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from collections import defaultdict
from contextlib import contextmanager
from enum import Enum, nonmember
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from github import Auth, Github, UnknownObjectException
from jinja2 import Environment, FileSystemLoader
from jinja2.exceptions import TemplateError, TemplateNotFound
from jsonschema import validate
from rich import box
from rich.console import Console
from rich.padding import Padding
from rich.table import Table

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any, Callable

    from github.Repository import Repository

INDENT = 4
console = Console(color_system="standard", width=100_000_000, record=True)


def print(renderable, *, indent: int = 0, **kwargs) -> None:
    if indent:
        renderable = Padding.indent(renderable, indent)
    console.print(renderable, **kwargs)


def perror(renderable, **kwargs) -> None:
    kwargs.setdefault("style", "bold red")
    try:
        console.stderr = True
        print(renderable, **kwargs)
    finally:
        console.stderr = False


class ActionError(Exception):
    pass


class TemplateState(Enum):
    UNUSED = "unused"
    MISSING = "missing"
    USED = "used"
    CONTEXT = "context"
    WIDTH = nonmember(12)

    def __rich__(self) -> str:
        if self == self.UNUSED:
            return f"[yellow]⚠️ ({self.value})[/yellow]"
        elif self == self.MISSING:
            return f"[red]❌ ({self.value})[/red]"
        elif self == self.USED:
            return f"[green]✅ ({self.value})[/green]"
        elif self == self.CONTEXT:
            return f"[blue]📑 ({self.value})[/blue]"
        else:
            raise ValueError("Invalid TemplateState")


class StubLoader(FileSystemLoader):
    current: tuple[str, str, str] | None
    stubs: dict[str, TemplateState]
    templates: dict[tuple[str, str, str], dict[str, TemplateState]]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.templates = defaultdict(dict)
        self.stubs = dict.fromkeys(self.list_templates(), TemplateState.UNUSED)

    def get_source(
        self,
        environment: Environment,
        stub: str,
    ) -> tuple[str, str, Callable[[], bool]]:
        # assume template is used, track for current file and globally
        if self.current:
            self.templates[self.current][stub] = TemplateState.USED
        self.stubs[stub] = TemplateState.USED

        try:
            # delegate to FileSystemLoader
            return super().get_source(environment, stub)
        except TemplateNotFound:
            # TemplateNotFound: template does not exist, mark it as missing
            if self.current:
                self.templates[self.current][stub] = TemplateState.MISSING
            self.stubs[stub] = TemplateState.MISSING
            raise

    @contextmanager
    def get_stubs(
        self,
        file: str,
        src: str,
        dst: str,
    ) -> Iterator[dict[str, TemplateState]]:
        try:
            # set current file
            self.current = (file, src, dst)

            yield self.templates[self.current]
        finally:
            # clear current file
            self.current = None


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


def parse_args() -> Namespace:
    # parse CLI for inputs
    parser = ArgumentParser()
    parser.add_argument("--config", type=validate_file, required=True)
    parser.add_argument("--stubs", type=validate_dir, required=True)
    return parser.parse_args()


def read_config(args: Namespace) -> dict:
    # read and validate configuration file
    config = yaml.load(
        args.config.read_text(),
        Loader=yaml.SafeLoader,
    )
    validate(
        config,
        schema={
            "type": "object",
            "patternProperties": {
                r"\w+/\w+": {
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
            perror(f"* ❌ Invalid file definition (`{file}`), expected `dst`")
            raise ActionError
        dst = Path(tmp)
        remove = file.get("remove", False)
        context = file.get("with", {})
    else:
        perror(f"* ❌ Invalid file definition (`{file}`), expected `str` or `dict`")
        raise ActionError

    # to template a file we need a source file
    if not remove and src is None:
        perror(f"* ❌ Invalid file definition (`{file}`), expected `src`")
        raise ActionError

    return src, dst, remove, context


def remove_file(dst: Path) -> int:
    try:
        dst.unlink()
    except FileNotFoundError:
        # FileNotFoundError: dst does not exist
        print(f"* ⚠️ `{dst}` already removed", indent=INDENT)
    except PermissionError as err:
        # PermissionError: not possible to remove dst
        perror(f"* ❌ Failed to remove `{dst}`: {err}", indent=INDENT)
        return 1
    else:
        print(f"* ❎ `{dst}` removed")
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
        perror(f"* ❌ Failed to fetch `{src}`: {err}", indent=INDENT)
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

    with env.loader.get_stubs(upstream_name, src, dst) as stubs:
        try:
            template = env.from_string(content)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(template.render(**{**context, **standard_context}))
        except TemplateError as err:
            perror(f"* ❌ Failed to template `{src}`: {err}", indent=INDENT)
            return 1

        print(f"* ✅ `{src}` → `{dst}`", indent=INDENT)

        # display stubs & context for this file
        if stubs or context:
            table = Table.grid(padding=1)
            table.add_column()
            table.add_column(max_width=TemplateState.WIDTH)
            table.add_column()
            for stub, state in stubs.items():
                table.add_row("*", state, f"`{stub}`")
            for key, value in context.items():
                table.add_row("*", TemplateState.CONTEXT, f"`{key}={value}`")
            print(table, indent=INDENT * 2)
    return 0  # no errors


def iterate_config(
    config: dict,
    gh: Github,
    env: Environment,
    current_repo: Repository,
) -> int:
    # iterate over configuration and template files
    errors = 0
    for upstream_name, files in config.items():
        try:
            upstream_repo = gh.get_repo(upstream_name)
        except UnknownObjectException as err:
            perror(f"* ❌ Failed to fetch `{upstream_name}`: {err}")
            errors += 1
            continue
        else:
            print(f"* 🔄 Fetching files from `{upstream_name}`")

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


def dump_summary():
    # dump summary to GitHub Actions summary
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    output = os.getenv("GITHUB_OUTPUT")
    if summary or output:
        html = console.export_text()
    if summary:
        Path(summary).write_text(f"### Templating Audit\n{html}")
    if output:
        with Path(output).open("a") as fh:
            fh.write(
                # https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/workflow-commands-for-github-actions#setting-an-output-parameter
                # https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/workflow-commands-for-github-actions#multiline-strings
                f"summary<<GITHUB_OUTPUT_summary\n"
                f"<details>\n"
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
        print("⚠️ No configuration file found, nothing to update")
        dump_summary()
        sys.exit(0)
    errors = 0

    config = read_config(args)

    # initialize stub loader
    loader = StubLoader(args.stubs)

    # initialize Jinja environment
    env = Environment(
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
        perror(f"❌ Failed to fetch `{current_name}`: {err}")
        errors += 1

    if not errors:
        errors += iterate_config(config, gh, env, current_repo)

    # provide audit of stub usage
    table = Table(box=box.MARKDOWN)
    table.add_column("Stub")
    table.add_column("State", max_width=TemplateState.WIDTH)
    for stub, state in loader.stubs.items():
        table.add_row(f"`{stub}`", state)
    print(table)

    if errors:
        perror(f"Got {errors} error(s)")

    dump_summary()
    sys.exit(errors)


if __name__ == "__main__":
    main()
