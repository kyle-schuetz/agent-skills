#!/usr/bin/env python3
"""Check spec, task files, and tests against the evidence-based review tool's own rules.

Run it with the review tool's environment, so the checks are the tool's code, not a copy:

    uv run --project /home/kyleschuetz/evidence-based-review python \
        /home/kyleschuetz/.agents/skills/spec-driven-development/scripts/review-readiness.py <stage> ...

Stages:
    spec <spec-file> [--worktree <path>]
        Parse the spec as committed at HEAD of a clean worktree, as review setup does.
    plan --task-file <path>... [--worktree <path>]
        Check every task file and its spec at HEAD of a clean worktree. A task that has not
        started may have an empty starting commit; every other rule applies.
    review <level> [<target>] --candidate <ref> --task-file <path>... [--worktree <path>]
        The review command's own setup, then each requirement's check exactly as the review runs it
        (project setup, the build and test command declared for the test file's type, the report
        matched to each named test), in a temporary worktree at the candidate. Then no tracked Python
        test file may hold a parametrized test, test class, skip, or xfail. Same arguments as
        `evidence-based-review review`.

    staged
        The Git pre-commit hook (scripts/pre-commit). Checks the commit being made: each staged spec,
        each staged task file and each task file that links a staged spec, with the `plan` rules, and
        every added line of a staged Python test file for parametrize, test classes, skips, and xfails. Files
        that the commit does not touch are not checked.

Prints one line per check, then READY or NOT READY. Exit code 0 only when READY.
"""

from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from evidence_based_review.checks import read_check, run_check
from evidence_based_review.records import Failure, RecordStore
from evidence_based_review.scope import (_PHASE, _SPEC_LINK, _baseline, _check_verifications, _git, _lines,
                                         _requirements,
                                         _tasks_in_scope, check_source, parse_spec, read_work, resolve_commit,
                                         review_scope)
from evidence_based_review.agents import build_directory, project_commands, run_build, run_commands, run_setup

FORBIDDEN = r"parametrize|^class Test|pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail|importorskip)\("
TEST_FILES = [":(glob)**/test_*.py", ":(glob)**/*_test.py", ":(glob)**/conftest.py", ":(glob)**/tests/**/*.py",
              ":(glob)**/test/**/*.py"]
failures: list[str] = []


def report(name: str, check) -> object:
    """Run one check; print PASS with its summary or FAIL with the tool's message."""
    try:
        result, summary = check()
    except Failure as failure:
        failures.append(name)
        print(f"FAIL {name}\n" + "\n".join(f"     {line}" for line in str(failure).splitlines()))
        return None
    print(f"PASS {name}: {summary}")
    return result


def spec_stage(args) -> None:
    worktree = Path(args.worktree).resolve()
    path = posixpath.normpath(args.spec)
    commit = report("clean checkout", lambda: clean_head(worktree))
    if commit is not None:
        check_spec(worktree, commit, path, _git(worktree, "show", f"{commit}:{path}"))


def clean_head(worktree: Path):
    commit = resolve_commit(worktree, "HEAD")
    check_source(worktree, commit)
    return commit, f"HEAD {commit} has no uncommitted or untracked changes"


def check_spec(worktree: Path, commit: str, path: str, text: str) -> None:
    def parse():
        requirements = parse_spec(path, text)
        return requirements, f"{len(requirements)} requirements: " + ", ".join(
            r.rsplit("#", 1)[1] for r in requirements)

    requirements = report(f"parse {path}", parse)
    if requirements is None:
        return

    def verifications():
        tests = project_commands(worktree, commit)["tests"]
        unreadable = []
        for qualified, requirement in requirements.items():
            try:
                suffix = PurePosixPath(read_check(requirement["verification"])["test_file"]).suffix
                if suffix not in tests:
                    raise Failure(f"no test command for {suffix} files")
            except Failure as failure:
                unreadable.append(f"{qualified}: {failure}")
        if unreadable:
            raise Failure("\n".join(unreadable))
        return None, f"all {len(requirements)} Verification lines name tests and one test file"

    report(f"Verification lines in {path}", verifications)


def plan_stage(args) -> None:
    worktree = Path(args.worktree).resolve()
    commit = report("clean checkout", lambda: clean_head(worktree))
    if commit is not None:
        report("task files and specs at HEAD", lambda: check_work(worktree, commit, args.task_files))


def check_work(worktree: Path, commit: str, task_files: list[str]):
    """The review tool's setup rules for all tasks at `commit`, except that a task not started may have an empty
    starting commit."""
    work = read_work(worktree, commit, task_files)
    tasks = _tasks_in_scope("full_work", None, work["task_files"])
    requirements = _requirements(tasks, work["specs"], commit)
    _check_verifications(requirements, worktree, commit)
    started = [task for task in tasks if task["starting_commit"] is not None]
    if started:
        _baseline(worktree, started, commit)
    phases = sum(len(task_file["phases"]) for task_file in work["task_files"])
    return None, (f"{', '.join(task_files)}: {phases} phases, {len(tasks)} tasks, {len(requirements)} requirements,"
                  f" {len(started)} tasks with a starting commit, {len(tasks) - len(started)} not started")


def staged_stage(args) -> None:
    worktree = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    tree = _git(worktree, "write-tree").strip()
    parent = subprocess.run(["git", "-C", str(worktree), "rev-parse", "--verify", "-q", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    parents = [parent] if parent else []
    merging = subprocess.run(["git", "-C", str(worktree), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                             capture_output=True, text=True).returncode == 0
    if merging:
        merge_head = Path(_git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "MERGE_HEAD").strip())
        parents += merge_head.read_text().split()
    commit = _git(worktree, "commit-tree", tree, *[arg for sha in parents for arg in ("-p", sha)],
                  "-m", "review readiness check of the staged tree").strip()
    staged = _git(worktree, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0")
    check_documents(worktree, commit, staged)
    added = [line for line in _git(worktree, "diff", "--cached", "-U0", "--", *TEST_FILES).splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    report("no parametrize, test classes, skips, or xfails added",
           lambda: forbidden_lines([line[1:] for line in added]))


def check_documents(worktree: Path, commit: str, staged: list[str]) -> None:
    """Check the staged specs and the task files this commit touches: the staged task files, and the task files
    that link a staged spec. A task file is a TASKS*.md file, a tasks/todo.md, or a file with a `**Spec:**` line and
    a `## Phase` heading. A spec is a SPEC*.md file or a file a task file links."""
    links = {}
    for path in _git(worktree, "ls-tree", "-r", "--name-only", "-z", commit).split("\0"):
        if not path.endswith(".md"):
            continue
        lines = list(_lines(_git(worktree, "show", f"{commit}:{path}")))
        if (fnmatchcase(posixpath.basename(path), "TASKS*.md") or path == "tasks/todo.md"
                or path.endswith("/tasks/todo.md")
                or (any(line.startswith("**Spec:**") for line in lines) and any(_PHASE.match(line) for line in lines))):
            targets = [posixpath.normpath(posixpath.join(posixpath.dirname(path), match.group(1)))
                       for match in map(_SPEC_LINK.match, lines) if match]
            links[path] = targets
    linked = {target for targets in links.values() for target in targets}
    specs = [path for path in staged if fnmatchcase(posixpath.basename(path), "SPEC*.md") or path in linked]
    task_files = [path for path, targets in links.items() if path in staged or set(targets) & set(specs)]
    for path in specs:
        check_spec(worktree, commit, path, _git(worktree, "show", f"{commit}:{path}"))
    if task_files:
        report("task files in this commit", lambda: check_work(worktree, commit, task_files))


def forbidden_lines(lines: list[str]):
    found = [line for line in lines if re.search(FORBIDDEN, line)]
    if found:
        raise Failure(f"{len(found)} added line(s); every test is one plain function, run once:\n" + "\n".join(found))
    return None, f"{len(lines)} added Python test lines checked"


def review_stage(args) -> None:
    worktree = Path(args.worktree).resolve()

    def setup():
        d = review_scope(args.level, args.target, args.candidate, args.task_files, worktree)
        return d, f"{len(d['requirements'])} requirements, candidate {d['candidate']}, baseline {d['baseline']}"

    definition = report("review setup", setup)
    if definition is None:
        return
    run_named_checks(worktree, definition)
    report("no parametrize, test classes, skips, or xfails in any Python test file",
           lambda: forbidden(worktree, definition["candidate"]))

    def clean():
        check_source(worktree, definition["candidate"])
        return None, "no changes"

    report("worktree still clean", clean)


def run_named_checks(repo: Path, definition: dict) -> None:
    """Run each requirement's check as the review runs it: in a detached worktree at the candidate, the project's
    setup before each check, the build of the test file's suffix once, then the review tool's own `run_check`, which
    runs the declared test command for the suffix and matches each named test to one report case. Records go to a
    temporary store, never to a review run."""
    commit = definition["candidate"]
    commands = project_commands(repo, commit)
    temp = Path(tempfile.mkdtemp(prefix="review-readiness-"))
    worktree = temp / f"review-{commit}"
    store = RecordStore(temp / "records")
    store.put("review_definition", definition)
    _git(repo, "worktree", "add", "--quiet", "--detach", str(worktree), commit)
    try:
        for requirement in definition["requirements"]:
            report(f"check {requirement['id']}", lambda: one_check(store, requirement, worktree, commit, commands))
    finally:
        def teardown():
            if store.list("setup_run"):
                run_commands(store, "teardown", worktree, commands["teardown"])
            _git(repo, "worktree", "remove", "--force", str(worktree))
            shutil.rmtree(temp)
            return None, f"{len(commands['teardown'])} teardown commands; temporary worktree removed"

        report("teardown", teardown)
        if failures[-1:] == ["teardown"]:
            print(f"     the temporary worktree stays at {worktree}")


def one_check(store: RecordStore, requirement: dict, worktree: Path, commit: str, commands: dict):
    run_setup(store, worktree, commands["setup"])
    check = read_check(requirement["verification"])
    suffix = PurePosixPath(check["test_file"]).suffix
    build = None
    if "build" in commands["tests"].get(suffix, {}):
        build = build_directory(worktree, suffix)
        if not build.exists():
            run_build(store, worktree, suffix, commands)
    body = store.get(run_check(store, requirement["id"], worktree, commit=commit, tests=commands["tests"],
                               build=build))
    results = ", ".join(f"{test} {result}" for test, result in body["results"].items())
    if body["status"] != "passed":
        raise Failure(f"{check['test_file']}: {results}\n{body['output'][-2000:]}")
    return None, f"{check['test_file']}: {results}"


def forbidden(worktree: Path, commit: str):
    command = ["git", "-C", str(worktree), "grep", "-n", "-E", FORBIDDEN, commit, "--", *TEST_FILES]
    run = subprocess.run(command, capture_output=True, text=True)
    if run.returncode == 1:
        return None, "git grep found nothing"
    if run.returncode != 0:
        raise Failure(f"git grep failed: {run.stderr.strip()}")
    lines = run.stdout.splitlines()
    raise Failure(f"{len(lines)} line(s) found; every test is one plain function, run once:\n"
                  + "\n".join(line.removeprefix(f"{commit}:") for line in lines))


def main() -> int:
    parser = argparse.ArgumentParser(prog="review-readiness")
    stages = parser.add_subparsers(dest="stage", required=True)
    spec = stages.add_parser("spec")
    spec.add_argument("spec")
    plan = stages.add_parser("plan")
    plan.add_argument("--task-file", action="append", required=True, dest="task_files")
    review = stages.add_parser("review")
    review.add_argument("level")
    review.add_argument("target", nargs="?")
    review.add_argument("--candidate", required=True)
    review.add_argument("--task-file", action="append", required=True, dest="task_files")
    for stage in (spec, plan, review):
        stage.add_argument("--worktree", default=".")
    stages.add_parser("staged")
    args = parser.parse_args()
    {"spec": spec_stage, "plan": plan_stage, "review": review_stage, "staged": staged_stage}[args.stage](args)
    print("READY" if not failures else f"NOT READY: {len(failures)} failed: {', '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
