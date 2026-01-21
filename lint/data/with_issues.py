# Test file with intentional lint issues (unused imports)
# NOTE: This file is excluded from the repo's pre-commit via pyproject.toml
# It's used by the test workflow to verify the lint action detects issues

import os
import sys


def hello():
    print("hello")
