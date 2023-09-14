"""Copy files from external locations as defined in `sync.yml`."""
from __future__ import annotations

import sys
import os
from argparse import ArgumentParser, ArgumentTypeError
from pathlib import Path
from typing import Any

from rich.console import Console

import yaml
from github import Auth, Github, UnknownObjectException
from jinja2 import Environment, FileSystemLoader
from jsonschema import validate


print = Console(color_system="standard", soft_wrap=True).print
perror = Console(color_system="standard", soft_wrap=True, stderr=True, style="bold red").print


def validate_file(value: str) -> Path:
    try:
        path = Path(value).expanduser().resolve()
        path.read_text()
        return path
    except (IsADirectoryError, FileNotFoundError, PermissionError) as err:
        # IsADirectoryError: value is a directory, not a file
        # FileNotFoundError: value does not exist
        # PermissionError: value is not readable
        raise ArgumentTypeError(f"{value} is not a valid file: {err}")


def validate_dir(value: str) -> Path:
    try:
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    except FileExistsError as err:
        # FileExistsError: value is a file, not a directory
        raise ArgumentTypeError(f"{value} is not a valid directory: {err}")


# parse CLI for inputs
parser = ArgumentParser()
parser.add_argument("--config", type=validate_file, required=True)
parser.add_argument("--stubs", type=validate_dir, required=True)
args = parser.parse_args()

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
    }
)

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
)
gh = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))

# iterate over configuration and template files
errors = 0
for repository, files in config.items():
    try:
        repo = gh.get_repo(repository)
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
            content = repo.get_contents(src).decoded_content.decode()
        except UnknownObjectException as err:
            perror(f"❌ Failed to fetch {src} from {repository}: {err}")
            errors += 1
            continue

        template = env.from_string(content)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(template.render(repo=repo, **context))

        print(f"✅ Templated {repository}/{src} as {dst}")

if errors:
    perror(f"Got {errors} error(s)")
sys.exit(errors)
