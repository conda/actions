"""Copy files from external locations as defined in `sync.yml`."""
from __future__ import annotations

import os
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

import yaml
from github import Auth, Github
from jinja2 import Environment, FileSystemLoader
from jsonschema import validate


def validate(value: str, is_dir: bool) -> Path:
    if value:
        pass
    elif is_dir and (path := Path(value).expanduser().resolve()).is_dir():
        return path
    elif not is_dir and (path := Path(value).expanduser().resolve()).is_file():
        return path
    raise ValueError(f"{value} is not a valid file")


# parse CLI for inputs
parser = ArgumentParser()
parser.add_argument(
    "--config",
    type=lambda value: validate(value, is_dir=False),
    required=True,
)
parser.add_argument(
    "--stubs",
    type=lambda value: validate(value, is_dir=True),
    required=True,
)
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
for repository, files in config.items():
    repo = gh.get_repo(repository)

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

        content = repo.get_contents(src).decoded_content.decode()
        template = env.from_string(content)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(template.render(repo=repo, **context))

        print(f"Templated {repository}/{src} as {dst}")
