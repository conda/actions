import json
import os
import sys
import sysconfig
from pathlib import Path

if sys.platform == "win32":
    assert os.environ["SUBDIR"] == "win-arm64", os.environ["SUBDIR"]
    assert sysconfig.get_platform() == "win-arm64", sysconfig.get_platform()
    for path in Path(sys.prefix, "conda-meta").glob("*.json"):
        record = json.loads(path.read_text())
        assert record["subdir"] in {"win-arm64", "noarch"}, record
