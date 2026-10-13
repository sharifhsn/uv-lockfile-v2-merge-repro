# Separate dependency declarations in uv lockfile v2

Two branches can update different dependencies, run successfully on their own, and still conflict in the generated v2 lockfile. If you delete that lockfile to get past the conflict, you can also lose an unrelated version selection. The new lockfile is valid, so the script can work without making that change obvious.

I want this scoped to Charlie's v2 work. The request is to put a blank line between adjacent dependency declarations, using the existing shared writer. The follow-up patch is [astral-sh/uv#22502](https://github.com/astral-sh/uv/pull/22502), based on [#22138](https://github.com/astral-sh/uv/pull/22138).

This is an AI-assisted writeup and executable repro, prepared with Codex at my request. The results come from actual local commands. It is a controlled example with real PyPI packages, not a report of a production incident.

Think of a small report script. HTTPX handles requests, Rich prints the table, and Tomli reads its configuration. The dependency header permits a range of versions:

```python
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx>=0.27,<0.28",
#     "tomli>=2,<3",
#     "rich>=13.7,<14",
# ]
# ///
```

The lockfile records the specific versions uv selected. It starts with HTTPX 0.27.0, Rich 13.7.1, and Tomli 2.0.1. Tomli is the unrelated selection we want to preserve. Its requirement is still `>=2,<3`, so 2.0.2 is permitted too. There is no explicit `tomli==2.0.1` requirement.

Both preview features are enabled throughout:

```sh
export UV_PREVIEW_FEATURES=lockfile-v2,lock-without-metadata
```

The metadata-free case matters here. Removing package metadata avoids the original regular-project repro, but script declarations remain in `[workspace].dependencies`. This script case still conflicts with both features enabled.

The user journey is ordinary Git work:

```sh
git init -b main
uv lock --script report.py
uv run --locked --script report.py
git add report.py report.py.lock
git commit -m 'Add locked report script'

git switch -c upgrade-httpx
uv add --script report.py 'httpx>=0.27.2,<0.28'
uv run --locked --script report.py
git add report.py report.py.lock
git commit -m 'Require the HTTPX update'

git switch main
git switch -c upgrade-rich
uv add --script report.py 'rich>=13.8,<14'
uv run --locked --script report.py
git add report.py report.py.lock
git commit -m 'Require the Rich update'

git switch upgrade-httpx
git merge --no-ff --no-edit upgrade-rich
uv run --locked --script report.py
```

The executable repro supplies a local wheel directory, offline mode, and an isolated cache for these commands. It exposes newer releases after the first commit. That keeps the result repeatable without depending on today's PyPI contents.

Each branch changes only its intended package version. Both scripts run. But the original v2 writer sorts the lockfile declarations alphabetically, which puts HTTPX and Rich next to each other. Tomli separates those edits in the Python source, so Git merges the script successfully. The generated lockfile conflicts:

```toml
[workspace]
dependencies = [
<<<<<<< HEAD
    { name = "httpx", specifier = ">=0.27.2,<0.28" },
    { name = "rich", specifier = ">=13.7,<14" },
=======
    { name = "httpx", specifier = ">=0.27,<0.28" },
    { name = "rich", specifier = ">=13.8,<14" },
>>>>>>> upgrade-rich
    { name = "tomli", specifier = ">=2,<3" },
]
```

Git exits with status 1. uv then exits with status 2:

```text
error: Failed to parse `uv.lock`
  cause: TOML parse error at line 7, column 5
           |
         7 |     { name = "httpx", specifier = ">=0.27.2,<0.28" },
           |     ^
         missing comma between array elements, expected `,`
```

The actual file is `report.py.lock`. The error names `uv.lock` and points at a dependency entry instead of the first conflict marker. That can look like a uv syntax problem to someone new to the tool. This repro establishes the output, not how often people interpret it that way.

With separators in the starting lockfile, the same Git merge succeeds. The unchanged blank line lets Git distinguish the two declaration edits:

```toml
[workspace]
dependencies = [
    { name = "httpx", specifier = ">=0.27.2,<0.28" },

    { name = "rich", specifier = ">=13.8,<14" },

    { name = "tomli", specifier = ">=2,<3" },
]
```

The merged lockfile passes `uv lock --script report.py --check` without a byte change. The script imports and uses all three packages, then reports the installed versions:

```text
VERSIONS={"httpx": "0.27.2", "rich": "13.8.0", "tomli": "2.0.1"}
```

I also wanted to know what happens if someone tries to recover from the original conflict. Deleting `report.py.lock` and running `uv lock` again preserves both requested updates, but upgrades Tomli to 2.0.2. Keeping the current branch's lockfile with `git checkout --ours`, then running `uv lock`, preserves Tomli in this case. The combined script requirement forces the missing Rich update.

| State | HTTPX | Rich | Tomli |
|---|---|---|---|
| Initial commit | 0.27.0 | 13.7.1 | 2.0.1 |
| HTTPX branch | 0.27.2 | 13.7.1 | 2.0.1 |
| Rich branch | 0.27.0 | 13.8.0 | 2.0.1 |
| Merge with separators | 0.27.2 | 13.8.0 | **2.0.1** |
| Delete and regenerate | 0.27.2 | 13.8.0 | **2.0.2** |
| Keep one lockfile, then reconcile | 0.27.2 | 13.8.0 | **2.0.1** |

The two recovery paths print the same report. Only the version log exposes the Tomli change. Regeneration produces a valid resolution, but loses the previous selection that neither branch meant to change. The `--ours` recovery works for these compatible minimum-version edits. It is not a general solution for every dependency conflict.

There are two limits to the separator change. The common starting commit needs the separators: using the patched writer only after both branches diverge still conflicts. A plain patched `uv lock` does not rewrite a valid old lockfile, so it does not establish that starting layout either.

Also, alphabetical declarations in this Python source cause a source conflict. With the patch, only `report.py` conflicts, while `report.py.lock` merges cleanly. Resolving the source header with both updates produces the same successful lockfile check and preserves Tomli. The proposal reduces the generated-file conflict; it does not promise to resolve source edits or incompatible dependency updates.

The production change in #22502 reuses `LockWriter::multiline_array`. It adds one `blank_lines` argument, then writes an extra newline before each later entry when the flag is true. Requirement declarations enable it for v2; other callers keep their format. The serializer diff is 14 added lines and 5 removed lines, with no second array writer. V1 output stays unchanged.

The existing upstream integration test uses a regular project. This repo supplies the stronger script journey with both preview features enabled. That distinction matters if v2 eventually implies metadata-free lockfiles. The appropriate upstream regression is the script case: both updates survive, and the unrelated selection stays fixed.

Can we include this declaration layout in v2, with that script regression? The useful change is a format that lets Git preserve independent edits and the existing selections. A clearer parse error alone still leaves the merge to resolve.

The exact comparison is Charlie's [`f9b3c68`](https://github.com/astral-sh/uv/commit/f9b3c6848fd47edbf7dd914a5c873bba6389a322) against the follow-up [`ade756b`](https://github.com/sharifhsn/uv/commit/ade756b7a1385ab965304cbd7ba7a733ae54e9f9). The recorded runs use Git 2.56.0 and Python 3.12.13 on macOS ARM64. The six cases cover the original writer, patched writer, old starting layout, alphabetical source, and the ordinary `uv lock` conversion attempt.

The initial and branch lockfiles have identical parsed contents between the original and patched runs. The script uses HTTPX MockTransport with a fixed response. All wheels are actual PyPI releases with SHA-256 checks, and all uv operations after the download are offline. The initial lock contains 12 packages. This is controlled package availability, not a reconstruction of historical PyPI release dates.

To reproduce the results, use Python 3.12, Git, and the two development uv binaries. Python 3.12+ can run the driver; `--python` selects Python 3.12 for the script.

```sh
git clone https://github.com/sharifhsn/uv-lockfile-v2-merge-repro.git
cd uv-lockfile-v2-merge-repro
python3 download_packages.py
python3 reproduce.py baseline \
  --base-uv /absolute/path/to/original-v2/uv \
  --patched-uv /absolute/path/to/patched-v2/uv \
  --python /absolute/path/to/python3.12
python3 reproduce.py patched \
  --base-uv /absolute/path/to/original-v2/uv \
  --patched-uv /absolute/path/to/patched-v2/uv \
  --python /absolute/path/to/python3.12
```

`--git` selects a Git executable. `--cases-directory` selects a fresh output directory for a rerun. The runner refuses to overwrite a previous case directory. The other cases are `migration`, `sorted-baseline`, `sorted-patched`, and `normalized-base`.

To build the pinned binaries from source, use the repository's Rust 1.99.0 toolchain and the development profile:

```sh
git clone https://github.com/astral-sh/uv.git uv-src
git -C uv-src fetch https://github.com/sharifhsn/uv.git codex/v2-declaration-separators
git -C uv-src worktree add --detach ../uv-v2-base f9b3c6848fd47edbf7dd914a5c873bba6389a322
git -C uv-src worktree add --detach ../uv-v2-patched ade756b7a1385ab965304cbd7ba7a733ae54e9f9
cargo +1.99.0 build --locked --manifest-path uv-v2-base/Cargo.toml -p uv
cargo +1.99.0 build --locked --manifest-path uv-v2-patched/Cargo.toml -p uv
```

With the default Cargo target directory, the binaries are under each worktree's `target/debug/uv`. Git worktrees share history but keep separate source directories.

The [original transcript](evidence/baseline/TRANSCRIPT.txt), [patched transcript](evidence/patched/TRANSCRIPT.txt), and [audit](evidence/verification.json) contain the recorded results. Each evidence directory includes the initial, branch, and merged lockfiles. The [manifest](package-manifest.json) records the wheel URLs and checksums. Local machine paths in the published command records use `<results>`, `<base-uv>`, and `<patched-uv>` placeholders.

The official [uv script guide](https://docs.astral.sh/uv/guides/scripts/#locking-dependencies) describes script lockfiles. The official [HTTPX transport guide](https://www.python-httpx.org/advanced/transports/#mock-transports) describes the fixed-response transport used here.
