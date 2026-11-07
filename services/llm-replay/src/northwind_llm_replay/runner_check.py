"""`make runner-check`: measured from inside the sandbox, nothing the runner loads is writable.

The runner is only as clean as what it imports. This asks the runner's own Python where it loads
code from, then tries to write in each place. It has to run sandboxed: from a terminal every write
succeeds, so it reports failure there, correctly (CC-27, decision 0005).

It also proves the runner can still price a call. Prices moved to `northwind_common` (RAG-13), and
`.venv-runner` holds only that package and llm-replay. A broken import stops the runner starting.

Each file is measured in place. Replacing a file through its folder is not measured, and a parent
check would be a false alarm: the parents of `.git` and `gitdir` are writable by design, because a
sandboxed commit writes beside them. Decision 0006's rig covers that move for the pointer file.

It also reports what would make a `git status` outside the sandbox run a child git in a folder,
with that folder's config: a gitlink, a `.gitmodules` or a nested `.git` (AUTO-2, decision 0011).
The rule in the sandbox stops one being written in place, not a folder moved in already holding one.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import subprocess
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path

from northwind_common import localenv
from northwind_llm_replay.scan import printable

PROBE = (
    "import json, sys; print(json.dumps({'paths': [sys.executable, sys.prefix, sys.base_prefix,"
    " *sys.path]}))"
)

# Imports the runner itself and prices a token. Passes before and after `runner-sync`; the printed
# module shows which side of the move the clone is on.
PRICING_PROBE = (
    "import northwind_llm_replay.ledger, northwind_llm_replay.runner; "
    "from northwind_llm_replay.pricing import cost_usd; "
    "cost_usd('claude-sonnet-5-5', {'input_tokens': 1}); print(cost_usd.__module__)"
)


MISSING, DENIED, SYMLINK, FILE, DIRECTORY, OTHER = (
    "missing",
    "denied",
    "symlink",
    "file",
    "directory",
    "other",
)
# What a refusal looks like. Anything else, a full disk say, proves nothing about the sandbox, so it
# is raised and becomes an ERROR line, not a clean result.
REFUSED = (errno.EPERM, errno.EACCES, errno.EROFS)
REPLACEMENT_LIMIT = (
    "each file is measured in place: replacing a file through its folder is not measured here"
)

# Compared case-folded: on a case-insensitive volume git finds `sub/.GIT`, and the child runs
# (measured).
GIT_FOLDER = ".git"
GITMODULES = ".gitmodules"
GITLINK_MODE = "160000"
CHILD_GIT_SUMMARY = "searched {} checkout(s) for a gitlink, a .gitmodules or a nested .git"


def kind(path: Path) -> str:
    """What is at `path`, without following a symlink. `stat` would read a dangling link as absent,
    and a sandbox denial as absent too: neither proves anything."""
    try:
        mode = path.lstat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return MISSING
    except PermissionError:
        return DENIED
    if stat.S_ISLNK(mode):
        return SYMLINK
    if stat.S_ISREG(mode):
        return FILE
    return DIRECTORY if stat.S_ISDIR(mode) else OTHER


def load_paths(python: Path) -> list[Path]:
    """Every directory `python -I` loads from, and where its interpreter really lives."""
    done = subprocess.run(  # noqa: S603 - the runner's own interpreter, fixed arguments
        [str(python), "-I", "-c", PROBE], capture_output=True, text=True, check=True, timeout=30
    )
    found = {python.parent, python.resolve().parent}
    for name in json.loads(done.stdout)["paths"]:
        if name:
            path = Path(name)
            found.update({path, path.resolve()})
    tool = shutil.which("uv")
    if tool:
        found.add(Path(tool).resolve().parent)
    # A missing entry such as python312.zip can still be created in its parent.
    return sorted({p if p.is_dir() else p.parent for p in found})


def pricing_module(python: Path) -> tuple[str | None, list[str]]:
    """The module the runner's `cost_usd` comes from, or the problem that stopped it loading."""
    done = subprocess.run(  # noqa: S603 - the runner's own interpreter, fixed arguments
        [str(python), "-I", "-c", PRICING_PROBE],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if done.returncode != 0:
        last = done.stderr.strip().splitlines()[-1:] or ["no error text"]
        return None, [f"PRICING the runner cannot load its prices: {last[0]}"]
    return done.stdout.strip(), []


def remove_probe(path: Path, remove: Callable[[Path], None]) -> None:
    """A probe that cannot be removed must not stop the check, and must not go unmentioned."""
    try:
        remove(path)
    except OSError:
        print(printable(f"LEFT BEHIND {path}"))


def can_create(path: Path) -> bool:
    """For a file that should not exist yet. Removed again at once when the attempt works. Only
    this call's own file is removed: `O_EXCL` fails on anything already there, even a symlink."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        os.close(os.open(path, flags, 0o600))
    except OSError as error:
        if error.errno in (*REFUSED, errno.EEXIST):
            return False
        raise
    remove_probe(path, os.unlink)
    return True


def can_mkdir(path: Path) -> bool:
    try:
        path.mkdir()
    except OSError as error:
        if error.errno in (*REFUSED, errno.EEXIST):
            return False
        raise
    remove_probe(path, os.rmdir)
    return True


def dir_is_writable(directory: Path) -> bool:
    return can_create(directory / f".runner-check-{uuid.uuid4().hex}")


def file_is_writable(path: Path) -> bool:
    """Open for append and close: nothing changes even when this succeeds. `O_NONBLOCK` because a
    FIFO with no reader would hang here, and no `O_CREAT` because this must not make the file."""
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NONBLOCK | os.O_NOFOLLOW))
    except OSError as error:
        if error.errno in (*REFUSED, errno.ENOENT, errno.ENXIO, errno.ELOOP):
            return False
        raise
    return True


def first_missing(path: Path) -> Path:
    """The highest missing component of an absent path. Creating a file under an absent folder fails
    with ENOENT, which is no refusal, so only this component says whether the path could exist. A
    denied parent stops the walk: it is not known to be missing."""
    while kind(path.parent) == MISSING and path.parent != path:
        path = path.parent
    return path


def link_is_dangling(path: Path) -> bool:
    """`Path.exists` raises on a sandbox denial. A denied target is not a missing one."""
    try:
        path.stat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def blocked_by(path: Path) -> list[str]:
    """What stops an absent path being probed at all. ENOENT through a dangling link and ENOTDIR
    under a file are no refusals: nothing was measured there, so that is the finding."""
    parent = first_missing(path).parent
    found = kind(parent)
    if found == SYMLINK and link_is_dangling(parent):
        return [f"SYMLINK {parent}"]
    return [f"NOT A DIRECTORY {parent}"] if found in (FILE, OTHER) else []


def absent_file_is_creatable(path: Path) -> bool:
    top = first_missing(path)
    return can_create(path) if top == path else can_mkdir(top)


def directory_problems(directory: Path) -> list[str]:
    """Load paths may be symlinks, as uv's Python folders are, and `load_paths` lists both sides.
    So a link is followed here, and only a dangling one is reported."""
    found = kind(directory)
    if found == MISSING:
        blocked = blocked_by(directory)
        if blocked:
            return blocked
        return [f"WRITABLE {directory}"] if can_mkdir(first_missing(directory)) else []
    if found in (FILE, OTHER):
        return [f"NOT A DIRECTORY {directory}"]
    if found == SYMLINK and link_is_dangling(directory):
        return [f"SYMLINK {directory}"]
    return [f"WRITABLE {directory}"] if dir_is_writable(directory) else []


def file_problems(path: Path) -> list[str]:
    found = kind(path)
    if found == SYMLINK:
        return [f"SYMLINK {path}"]
    if found == FILE:
        return [f"WRITABLE {path}"] if file_is_writable(path) else []
    if found == DENIED:  # it may or may not exist, so both ways in are tried
        return [f"WRITABLE {path}"] if file_is_writable(path) or can_create(path) else []
    if found == MISSING:
        blocked = blocked_by(path)
        if blocked:
            return blocked
        return [f"WRITABLE {path}"] if absent_file_is_creatable(path) else []
    return [f"NOT A FILE {path}"]


def measured(step: Callable[[Path], list[str]], path: Path) -> list[str]:
    """One path that cannot be measured is a line, and must not cost the paths after it."""
    try:
        return step(path)
    except OSError as error:
        return [f"ERROR {path}: {type(error).__name__}: {error.strerror}"]


def git_redirect_files(git_dir: Path, worktrees: Iterable[Path]) -> tuple[list[Path], list[Path]]:
    """What redirects or configures git for a linked worktree, as (existing, absent).

    Your terminal's git trusts these, so a write here is code outside (decision 0006). HEAD, index,
    refs and logs are left out on purpose: a sandboxed commit writes them. A main checkout's `.git`
    is a directory, and a commit needs it, so only a pointer file is ever listed. Only a missing
    `worktrees` folder counts as none: any other failure raises, so `main` reports it.
    """
    pointers = [tree / ".git" for tree in linked(git_dir, worktrees)]
    pointers = [p for p in pointers if kind(p) in (FILE, DENIED, SYMLINK)]
    try:
        names = sorted((git_dir / "worktrees").iterdir())
    except FileNotFoundError:
        names = []
    files = [*pointers, *(d / n for d in names for n in ("gitdir", "commondir", "config.worktree"))]
    return [f for f in files if kind(f) != MISSING], [f for f in files if kind(f) == MISSING]


def linked(git_dir: Path, worktrees: Iterable[Path]) -> list[Path]:
    """Every checkout but the main one, the only one whose `.git` is meant to be a directory."""
    main = git_dir.parent.resolve()
    return [tree for tree in worktrees if tree.resolve() != main]


def pointer_problems(git_dir: Path, worktrees: Iterable[Path]) -> list[str]:
    """A linked worktree must have a pointer file. `git_redirect_files` lists the ones that can be
    measured, so a `.git` of any other shape would otherwise drop out of the check unseen."""
    pointers = [tree / ".git" for tree in linked(git_dir, worktrees)]
    return [f"NOT A FILE {p}" for p in pointers if kind(p) not in (FILE, DENIED, SYMLINK)]


def is_hidden(path: Path) -> bool:
    """A stat only: this never opens the key file. A sandbox denial raises PermissionError. A
    file that simply is not there proves nothing, so `check` reports it."""
    try:
        path.stat()
    except PermissionError:
        return True
    except FileNotFoundError:
        raise
    return False


def present(path: Path) -> bool:
    """A sandbox denial of a stat raises on Python 3.12, and a denied path is not a problem."""
    try:
        path.stat()
    except OSError:
        return False
    return True


def excluded_commands(settings_file: Path, *, must_enable: bool = True) -> list[str]:
    """Problems with one settings file. An exclusion runs code the session can edit outside the
    sandbox, where it can read the key: that is F1 again."""
    try:
        sandbox = json.loads(settings_file.read_text()).get("sandbox", {})
    except (OSError, ValueError):
        return [f"UNREADABLE {settings_file}"]
    problems = []
    if sandbox.get("excludedCommands"):
        problems.append(f"EXCLUSIONS {settings_file}: commit the new settings, then merge them")
    if must_enable and not sandbox.get("enabled"):
        problems.append(f"NO SANDBOX {settings_file}")
    return problems


def checkouts() -> list[Path]:
    done = subprocess.run(  # noqa: S603 - fixed git arguments, no shell
        ["git", "worktree", "list", "--porcelain"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return [Path(line[9:]) for line in done.stdout.splitlines() if line.startswith("worktree ")]


def tracked_entries(tree: Path) -> list[str]:
    """Each `<mode> ...<TAB><path>` in the index and at HEAD. Neither command reads the working
    tree, so neither can start a child. Replace refs are off: the sandbox can write them."""
    entries: list[str] = []
    for command in (("ls-files", "-s", "-z"), ("ls-tree", "-r", "-z", "HEAD")):
        fixed = ["--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(tree)]
        done = subprocess.run(  # noqa: S603 - fixed git arguments, no shell
            ["git", *fixed, *command],  # noqa: S607
            capture_output=True,
            check=True,
            timeout=30,
        )
        entries += done.stdout.decode(errors="replace").split("\0")
    return [entry for entry in entries if entry]


def tracked_problems(tree: Path) -> list[str]:
    try:
        entries = tracked_entries(tree)
    except subprocess.SubprocessError as error:
        return [f"ERROR {tree}: {type(error).__name__}: git could not list what is tracked"]
    problems: set[str] = set()
    for entry in entries:
        meta, _, path = entry.partition("\t")
        if meta.split(" ", 1)[0] == GITLINK_MODE:
            problems.add(f"GITLINK {tree}: {path}")
        if path.rsplit("/", 1)[-1].casefold() == GITMODULES:
            problems.add(f"GITMODULES {tree}: {path}")
    return sorted(problems)


def walked_problems(tree: Path) -> list[str]:
    """Every folder, ignored ones too: a gitlink can point anywhere. Names links, follows none."""
    problems: list[str] = []

    def unreadable(error: OSError) -> None:
        problems.append(f"ERROR {error.filename}: {type(error).__name__}: {error.strerror}")

    for root, folders, files in os.walk(tree, onerror=unreadable):
        here = Path(root)
        if (
            here == tree
        ):  # the checkout's own `.git`: a folder in the main one, a file in a linked one
            folders[:] = [name for name in folders if name != GIT_FOLDER]
            files = [name for name in files if name != GIT_FOLDER]
        for name in sorted([*folders, *files]):
            if name.casefold() == GIT_FOLDER:
                problems.append(f"NESTED GIT {here / name}")
            elif name.casefold() == GITMODULES:
                problems.append(f"GITMODULES {here / name}")
        folders[:] = [name for name in folders if name.casefold() != GIT_FOLDER]
    return problems


def child_git_problems(tree: Path) -> list[str]:
    return [*measured(tracked_problems, tree), *measured(walked_problems, tree)]


def check(directories: Iterable[Path], files: Iterable[Path], hidden: Iterable[Path]) -> list[str]:
    """One line per writable or visible path; empty means every attempt was refused."""
    problems = [line for path in directories for line in measured(directory_problems, path)]
    problems += [line for path in files for line in measured(file_problems, path)]
    for path in hidden:
        try:
            if not is_hidden(path):
                problems.append(f"VISIBLE {path}")
        except FileNotFoundError:
            problems.append(f"MISSING {path}: nothing was proved about it")
    return problems


def run_stage(name: str, problems: list[str], step: Callable[[], list[str]]) -> None:
    """One stage failing must not drop what the others found, so it becomes a line of its own."""
    try:
        problems += step()
    except Exception as error:  # noqa: BLE001 - reported, and the exit code stays 1
        problems.append(f"ERROR {name}: {type(error).__name__}: {error}")


def settings_problems(trees: Iterable[Path]) -> list[str]:
    problems: list[str] = []
    for checkout in trees:
        problems += excluded_commands(checkout / ".claude" / "settings.json")
        local = checkout / ".claude" / "settings.local.json"
        if present(local):
            problems += excluded_commands(local, must_enable=False)
    return problems


def main(python: Path) -> int:
    if not os.access(python, os.X_OK):
        where = printable(str(python))
        print(f"error: no runner interpreter at {where}: clone and `make runner-sync` first")
        return 2
    home = localenv.northwind_home()
    git_dir = localenv.main_checkout() / ".git"
    problems: list[str] = []
    run_stage(
        "git dir",
        problems,
        lambda: [] if kind(git_dir) == DIRECTORY else [f"NOT A DIRECTORY {git_dir}"],
    )
    clone_evals = home / "runner" / "evals"
    directories = [
        home,
        home / "runner",
        clone_evals / "src",  # what the clone's promote loads and trusts (5b)
        clone_evals / "src" / "northwind_evals",
        clone_evals / "fixtures",
        clone_evals / "baselines",
        home / "uv-cache",
        git_dir / "hooks",  # your terminal's git runs these, so a write here is code outside
        Path.home() / ".config" / "uv",
    ]
    trees: list[Path] = []
    pricing: str | None = None

    def add_load_paths() -> list[str]:
        directories[:0] = load_paths(python)
        return []

    def add_checkouts() -> list[str]:
        trees.extend(checkouts())
        return []

    def redirects() -> list[str]:
        existing, absent = git_redirect_files(git_dir, trees)
        return [*pointer_problems(git_dir, trees), *check([], [*existing, *absent], [])]

    def measure() -> list[str]:
        ledger = localenv.ledger_path()
        files = [
            ledger,
            ledger.with_suffix(".lock"),
            git_dir / "config",
            git_dir / "config.worktree",
            Path.home() / ".gitconfig",
            Path.home() / ".config" / "git" / "config",
        ]
        return check(directories, files, [localenv.env_file()])

    def prices() -> list[str]:
        nonlocal pricing
        pricing, pricing_problems = pricing_module(python)
        return pricing_problems

    run_stage("load paths", problems, add_load_paths)
    run_stage("checkouts", problems, add_checkouts)
    run_stage("measure", problems, measure)
    run_stage("redirect files", problems, redirects)
    run_stage("pricing", problems, prices)
    run_stage("settings", problems, lambda: settings_problems(trees))
    run_stage("child git", problems, lambda: [p for t in trees for p in child_git_problems(t)])
    for line in problems:
        print(printable(line))
    print(f"runner-check: {len(directories)} directories, {len(problems)} problem(s)")
    print(f"runner prices from: {printable(pricing or 'unavailable')}")
    print(REPLACEMENT_LIMIT)
    print(CHILD_GIT_SUMMARY.format(len(trees)))
    print("this proves the settings of this checkout: run it in each one")
    return 1 if problems else 0
