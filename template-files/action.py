"""Copy files from external locations as defined in `sync.yml`."""
from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from pathlib import Path
from typing import Any

import yaml
from github import Auth, Github, UnknownObjectException
from github.Repository import Repository
from jinja2 import Environment, FileSystemLoader
from jsonschema import validate
from rich.console import Console

print = Console(color_system="standard", soft_wrap=True).print
perror = Console(
    color_system="standard",
    soft_wrap=True,
    stderr=True,
    style="bold red",
).print


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
        ignore = (path / ".ignore")
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
                            "with": {
                                "type": "object",
                                "patternProperties": {
                                    r"\w+": {"type": "string"},
                                },
                            },
                        },
                        "required": ["src"],
                    },
                }
            },
        },
    )
    return config


def iterate_config(config: dict, gh: Github, env: Environment, source: Repository) -> int:
    # iterate over configuration and template files
    errors = 0
    for repository, files in config.items():
        try:
            destination = gh.get_repo(repository)
        except UnknownObjectException as err:
            perror(f"❌ Failed to fetch {repository}: {err}")
            errors += 1
            continue

        for file in files:
            src: str
            dst: Path
            context: dict[str, Any]

            if isinstance(file, str):
                src = file
                dst = Path(file)
                context = {}
            elif isinstance(file, dict):
                src = file["src"]
                dst = Path(file.get("dst", src))
                context = file.get("with", {})

            try:
                content = destination.get_contents(src).decoded_content.decode()
            except UnknownObjectException as err:
                perror(f"❌ Failed to fetch {src} from {repository}: {err}")
                errors += 1
                continue

            # inject stuff about the source and destination
            context["repo"] = context["dst"] = context["destination"] = source
            context["src"] = context["source"] = destination

            template = env.from_string(content)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(template.render(**context))

            print(f"✅ Templated {repository}/{src} as {dst}")

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
    repository = os.environ["GITHUB_REPOSITORY"]
    try:
        source = gh.get_repo(repository)
    except UnknownObjectException as err:
        perror(f"❌ Failed to fetch {repository}: {err}")
        errors += 1

    if not errors:
        errors += iterate_config(config, gh, env, source)

    if errors:
        perror(f"Got {errors} error(s)")
    sys.exit(errors)


if __name__ == '__main__':
    main()
