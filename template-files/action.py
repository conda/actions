"""Copy files from external locations as defined in `sync.yml`."""
from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from github import Auth, Github, UnknownObjectException
from github.Repository import Repository
from jinja2 import Environment, FileSystemLoader
from jsonschema import validate
from rich.console import Console

if TYPE_CHECKING:
    from typing import Any, Literal

print = Console(color_system="standard", soft_wrap=True).print
perror = Console(
    color_system="standard",
    soft_wrap=True,
    stderr=True,
    style="bold red",
).print


class ActionError(Exception):
    pass

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
                                    r"\w+": {"type": "string"},
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
            perror(f"❌ Invalid file definition ({file}), expected dst")
            raise ActionError
        dst = Path(tmp)
        remove = file.get("remove", False)
        context = file.get("with", {})
    else:
        perror(f"❌ Invalid file definition ({file}), expected str or dict")
        raise ActionError

    # to template a file we need a source file
    if not remove and src is None:
        perror(f"❌ Invalid file definition ({file}), expected src")
        raise ActionError

    return src, dst, remove, context


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
            perror(f"❌ Failed to fetch {upstream_name}: {err}")
            errors += 1
            continue

        for file in files:
            try:
                # parse/standardize configuration
                src, dst, remove, context = parse_config(file)
            except ActionError:
                errors += 1
                continue

            # remove dst file
            if remove:
                try:
                    dst.unlink()
                except FileNotFoundError:
                    # FileNotFoundError: dst does not exist
                    print(f"⚠️ {dst} has already been removed")
                except PermissionError as err:
                    # PermissionError: not possible to remove dst
                    perror(f"❌ Failed to remove {dst}: {err}")
                    errors += 1
                    continue
                else:
                    print(f"✅ Removed {dst}")
            else:
                # fetch src file
                try:
                    content = upstream_repo.get_contents(src).decoded_content.decode()
                except UnknownObjectException as err:
                    perror(f"❌ Failed to fetch {src} from {upstream_name}: {err}")
                    errors += 1
                    continue
                else:
                    # inject stuff about the source and destination
                    context.update({
                        # the current repository from which this GHA is being run,
                        # where the new files will be written
                        "repo": current_repo,
                        "dst": current_repo,
                        "destination": current_repo,
                        "current": current_repo,
                        # source (should be rarely, if ever, used in templating)
                        "src": upstream_repo,
                        "source": upstream_repo,
                    })

                    template = env.from_string(content)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(template.render(**context))

                    print(f"✅ Templated {upstream_name}/{src} as {dst}")

    return errors


def main():
    errors = 0

    args = parse_args()
    if not args.config:
        print("⚠️ No configuration file found, nothing to update")
        sys.exit(0)

    config = read_config(args)

    # initialize Jinja environment and GitHub client
    env = Environment(
        loader=FileSystemLoader(args.stubs),
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
    gh = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))

    # get current repository
    current_name = os.environ["GITHUB_REPOSITORY"]
    try:
        current_repo = gh.get_repo(current_name)
    except UnknownObjectException as err:
        perror(f"❌ Failed to fetch {current_name}: {err}")
        errors += 1

    if not errors:
        errors += iterate_config(config, gh, env, current_repo)

    if errors:
        perror(f"Got {errors} error(s)")
    sys.exit(errors)


if __name__ == "__main__":
    main()
