#!/usr/bin/env python3
"""Exercise real Git branch merges and run a locked Python script."""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent
CASES_DIRECTORY = ROOT / "cases"
GIT = "git"
PYTHON = sys.executable
BASE_UV = ""
PATCHED_UV = ""
PACKAGES = json.loads((ROOT / "package-manifest.json").read_text())
BASE_VERSIONS = {"httpx": "0.27.0", "rich": "13.7.1", "tomli": "2.0.1"}
LEFT_VERSIONS = {**BASE_VERSIONS, "httpx": "0.27.2"}
RIGHT_VERSIONS = {**BASE_VERSIONS, "rich": "13.8.0"}
MERGED_VERSIONS = {**LEFT_VERSIONS, "rich": "13.8.0"}
REPORT = """# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx>=0.27,<0.28",
#     "tomli>=2,<3",
#     "rich>=13.7,<14",
# ]
# ///

from importlib.metadata import version
import json

import httpx
import tomli
from rich.console import Console
from rich.table import Table

configuration = tomli.loads('[report]\\ntitle = "Sample report"\\n')
transport = httpx.MockTransport(
    lambda request: httpx.Response(
        200, json={"rows": [{"name": "Example", "quantity": 17}]}
    )
)
with httpx.Client(transport=transport) as client:
    response = client.get("https://example.test/report")
    response.raise_for_status()
    rows = response.json()["rows"]

table = Table(title=configuration["report"]["title"])
table.add_column("Name")
table.add_column("Quantity")
for row in rows:
    table.add_row(row["name"], str(row["quantity"]))
Console(width=60, color_system=None).print(table)
print("VERSIONS=" + json.dumps(
    {name: version(name) for name in ("httpx", "rich", "tomli")}, sort_keys=True
))
"""


class Journey:
    def __init__(self, name, base_binary, branch_binary, sorted_source=False):
        self.directory = CASES_DIRECTORY / name
        self.directory.mkdir(parents=True)
        self.repo = self.directory / "repo"
        self.repo.mkdir()
        self.wheels = self.directory / "wheelhouse"
        self.wheels.mkdir()
        self.binary = base_binary
        self.branch_binary = branch_binary
        self.commands = []
        self.report = REPORT
        if sorted_source:
            self.report = self.report.replace(
                '#     "tomli>=2,<3",\n#     "rich>=13.7,<14",',
                '#     "rich>=13.7,<14",\n#     "tomli>=2,<3",',
            )
        self.combined = self.report.replace(
            "httpx>=0.27,<0.28", "httpx>=0.27.2,<0.28"
        ).replace("rich>=13.7,<14", "rich>=13.8,<14")
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("UV_", "GIT_"))
        }
        self.environment.update(
            {
                "UV_NO_CONFIG": "1",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_COUNT": "0",
                "UV_PREVIEW_FEATURES": "lockfile-v2,lock-without-metadata",
                "UV_NO_INDEX": "1",
                "UV_FIND_LINKS": "../wheelhouse",
                "UV_OFFLINE": "1",
                "UV_PYTHON": PYTHON,
                "UV_PYTHON_DOWNLOADS": "never",
                "UV_CACHE_DIR": str(self.directory / "cache"),
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_AUTHOR_NAME": "Repro User",
                "GIT_AUTHOR_EMAIL": "repro@example.invalid",
                "GIT_COMMITTER_NAME": "Repro User",
                "GIT_COMMITTER_EMAIL": "repro@example.invalid",
                "NO_COLOR": "1",
            }
        )
        for package in PACKAGES:
            if package["stage"] != "new":
                shutil.copyfile(
                    ROOT / "catalog" / package["filename"],
                    self.wheels / package["filename"],
                )

    def command(self, arguments, expected=(0,), directory=None):
        location = directory or self.repo
        completed = subprocess.run(
            arguments,
            cwd=location,
            env=self.environment,
            capture_output=True,
            text=True,
        )
        record = {
            "argv": arguments,
            "cwd": str(location),
            "exit": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        self.commands.append(record)
        self.save()
        if expected is not None:
            assert completed.returncode in expected, record
        return completed

    def git(self, *arguments, expected=(0,), directory=None):
        return self.command(
            [GIT, "-c", "commit.gpgsign=false", *arguments], expected, directory
        )

    def uv(self, *arguments, expected=(0,), directory=None):
        return self.command([self.binary, *arguments], expected, directory)

    def save(self):
        (self.directory / "commands.json").write_text(
            json.dumps(self.commands, indent=2) + "\n"
        )
        transcript = []
        for record in self.commands:
            arguments = record["argv"][1:]
            if record["argv"][0] == GIT:
                executable = "git"
                arguments = arguments[2:]
            elif record["argv"][0] in (BASE_UV, PATCHED_UV):
                executable = "uv"
            else:
                executable = record["argv"][0]
            transcript.append("$ " + shlex.join([executable, *arguments]))
            transcript.append(f"exit: {record['exit']}")
            transcript.append(record["stdout"].rstrip())
            transcript.append(record["stderr"].rstrip())
            transcript.append("")
        (self.directory / "TRANSCRIPT.txt").write_text("\n".join(transcript))
        for label, filename in (("source", "report.py"), ("lock", "report.py.lock")):
            path = self.repo / filename
            if path.exists():
                (self.directory / f"latest-{label}.txt").write_text(path.read_text())

    def versions(self, directory=None):
        location = directory or self.repo
        data = tomllib.loads((location / "report.py.lock").read_text())
        assert data["version"] == 2
        assert not any(package.get("metadata") for package in data["package"])
        return {
            package["name"]: package["version"]
            for package in data["package"]
            if package["name"] in BASE_VERSIONS
        }

    def all_versions(self):
        data = tomllib.loads((self.repo / "report.py.lock").read_text())
        return {package["name"]: package["version"] for package in data["package"]}

    def run_script(self, expected_versions, directory=None):
        completed = self.uv(
            "run", "--locked", "--script", "report.py", directory=directory
        )
        match = re.search(r"^VERSIONS=(.+)$", completed.stdout, flags=re.MULTILINE)
        assert match, completed.stdout
        assert json.loads(match.group(1)) == expected_versions
        return completed.stdout

    def initialize(self):
        self.git("init", "-b", "main")
        (self.repo / "report.py").write_text(self.report)
        self.uv("lock", "--script", "report.py")
        assert self.versions() == BASE_VERSIONS
        self.initial_versions = self.all_versions()
        shutil.copyfile(self.repo / "report.py", self.directory / "base.py")
        self.run_script(BASE_VERSIONS)
        self.git("add", "report.py", "report.py.lock")
        self.git("commit", "-m", "Add locked report script")
        self.base_commit = self.git("rev-parse", "HEAD").stdout.strip()
        shutil.copyfile(self.repo / "report.py.lock", self.directory / "base.lock")
        self.binary = self.branch_binary
        for package in PACKAGES:
            if package["stage"] == "new":
                shutil.copyfile(
                    ROOT / "catalog" / package["filename"],
                    self.wheels / package["filename"],
                )

    def branches(self):
        self.git("switch", "-c", "upgrade-httpx")
        self.uv("add", "--script", "report.py", "httpx>=0.27.2,<0.28")
        assert self.versions() == LEFT_VERSIONS
        assert self.all_versions() == {**self.initial_versions, "httpx": "0.27.2"}
        self.run_script(LEFT_VERSIONS)
        self.git("diff")
        shutil.copyfile(self.repo / "report.py", self.directory / "left.py")
        shutil.copyfile(self.repo / "report.py.lock", self.directory / "left.lock")
        self.git("add", "report.py", "report.py.lock")
        self.git("commit", "-m", "Require the HTTPX update")
        self.git("switch", "main")
        self.git("switch", "-c", "upgrade-rich")
        self.uv("add", "--script", "report.py", "rich>=13.8,<14")
        assert self.versions() == RIGHT_VERSIONS
        assert self.all_versions() == {**self.initial_versions, "rich": "13.8.0"}
        self.run_script(RIGHT_VERSIONS)
        self.git("diff")
        shutil.copyfile(self.repo / "report.py", self.directory / "right.py")
        shutil.copyfile(self.repo / "report.py.lock", self.directory / "right.lock")
        self.git("add", "report.py", "report.py.lock")
        self.git("commit", "-m", "Require the Rich update")
        self.git("switch", "upgrade-httpx")

    def merge(self):
        merged = self.git(
            "merge", "--no-ff", "--no-edit", "upgrade-rich", expected=(0, 1)
        )
        conflicts = self.git(
            "diff", "--name-only", "--diff-filter=U"
        ).stdout.splitlines()
        self.git("status", "--short")
        self.git("log", "--graph", "--all", "--oneline")
        shutil.copyfile(self.repo / "report.py", self.directory / "merge.py")
        shutil.copyfile(self.repo / "report.py.lock", self.directory / "merge.lock")
        if "report.py" in conflicts:
            (self.repo / "report.py").write_text(self.combined)
            self.git("add", "report.py")
        else:
            source = (self.repo / "report.py").read_text()
            assert "httpx>=0.27.2,<0.28" in source
            assert "rich>=13.8,<14" in source
            assert "tomli>=2,<3" in source
        result = {
            "merge_exit": merged.returncode,
            "conflicting_files": conflicts,
            "base_commit": self.base_commit,
            "metadata_free": True,
        }
        if "report.py.lock" in conflicts:
            failed = self.uv("run", "--locked", "--script", "report.py", expected=(2,))
            assert (
                "Failed to parse" in failed.stderr
                and "TOML parse error" in failed.stderr
            )
            result["run_after_source_resolution_exit"] = failed.returncode
            result["run_error"] = failed.stderr
        else:
            before = (self.repo / "report.py.lock").read_bytes()
            self.uv("lock", "--script", "report.py", "--check")
            assert (self.repo / "report.py.lock").read_bytes() == before
            result["versions"] = self.versions()
            assert result["versions"] == MERGED_VERSIONS
            result["run_output"] = self.run_script(MERGED_VERSIONS)
            if merged.returncode:
                self.git("add", "report.py.lock")
                self.git("commit", "-m", "Merge both dependency updates")
            assert not self.git("status", "--porcelain").stdout
        (self.directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        self.save()
        return result

    def recoveries(self):
        results = {}
        for label in ("delete-lock", "keep-ours-lock"):
            location = self.directory / label
            shutil.copytree(self.repo, location)
            if label == "delete-lock":
                self.command(["rm", "report.py.lock"], directory=location)
                expected = {**MERGED_VERSIONS, "tomli": "2.0.2"}
            else:
                self.git(
                    "checkout", "--ours", "--", "report.py.lock", directory=location
                )
                expected = MERGED_VERSIONS
            self.uv("lock", "--script", "report.py", directory=location)
            assert self.versions(location) == expected
            output = self.run_script(expected, directory=location)
            self.git("add", "report.py", "report.py.lock", directory=location)
            self.git(
                "commit", "-m", f"Resolve the merge with {label}", directory=location
            )
            assert not self.git("status", "--porcelain", directory=location).stdout
            results[label] = {"versions": expected, "run_output": output}
        (self.directory / "recovery-results.json").write_text(
            json.dumps(results, indent=2) + "\n"
        )
        self.save()
        return results


def run_case(name, base_binary, branch_binary, sorted_source=False, recover=False):
    journey = Journey(name, base_binary, branch_binary, sorted_source)
    journey.initialize()
    journey.branches()
    result = journey.merge()
    if recover:
        assert "report.py.lock" in result["conflicting_files"]
        result["recoveries"] = journey.recoveries()
    print(json.dumps({name: result}, indent=2), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "case",
        choices=(
            "baseline",
            "patched",
            "migration",
            "sorted-baseline",
            "sorted-patched",
            "normalized-base",
        ),
    )
    parser.add_argument("--base-uv", required=True)
    parser.add_argument("--patched-uv", required=True)
    parser.add_argument("--python")
    parser.add_argument("--git")
    parser.add_argument("--cases-directory", type=Path)
    options = parser.parse_args()
    for package in PACKAGES:
        artifact = ROOT / "catalog" / package["filename"]
        if not artifact.is_file():
            parser.error("Missing catalog. Run python3 download_packages.py first.")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != package["sha256"]:
            parser.error(f"Checksum mismatch: {artifact.name}")
    if options.base_uv:
        BASE_UV = str(Path(options.base_uv).resolve())
    if options.patched_uv:
        PATCHED_UV = str(Path(options.patched_uv).resolve())
    if options.python:
        PYTHON = str(Path(options.python).resolve())
    if options.git:
        GIT = shutil.which(options.git) or str(Path(options.git).resolve())
    if options.cases_directory:
        CASES_DIRECTORY = options.cases_directory.resolve()
    if options.case == "baseline":
        run_case("baseline", BASE_UV, BASE_UV, recover=True)
    elif options.case == "patched":
        run_case("patched", PATCHED_UV, PATCHED_UV)
    elif options.case == "migration":
        run_case("migration", BASE_UV, PATCHED_UV)
    elif options.case == "sorted-baseline":
        run_case("sorted-baseline", BASE_UV, BASE_UV, sorted_source=True)
    elif options.case == "sorted-patched":
        run_case("sorted-patched", PATCHED_UV, PATCHED_UV, sorted_source=True)
    else:
        journey = Journey("normalized-base", BASE_UV, PATCHED_UV)
        journey.initialize()
        original = (journey.repo / "report.py.lock").read_bytes()
        journey.uv("lock", "--script", "report.py")
        rewritten = (journey.repo / "report.py.lock").read_bytes()
        assert journey.versions() == BASE_VERSIONS
        assert tomllib.loads(original.decode()) == tomllib.loads(rewritten.decode())
        normalization = {
            "bytes_changed": original != rewritten,
            "extra_bytes": len(rewritten) - len(original),
            "versions": journey.versions(),
        }
        if original != rewritten:
            journey.git("add", "report.py.lock")
            journey.git("commit", "-m", "Adopt v2 declaration separators")
            journey.base_commit = journey.git("rev-parse", "HEAD").stdout.strip()
            shutil.copyfile(
                journey.repo / "report.py.lock", journey.directory / "base.lock"
            )
        (journey.directory / "normalization.json").write_text(
            json.dumps(normalization, indent=2) + "\n"
        )
        journey.branches()
        result = journey.merge()
        result["normalization"] = normalization
        print(json.dumps(result, indent=2), flush=True)
