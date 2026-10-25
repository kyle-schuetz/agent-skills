#!/usr/bin/env python3
"""Check spec, task files, and tests against the evidence-based review tool's own rules.

Run it with the review tool's environment, so the checks are the tool's code, not a copy:

    uv run --project /home/kyleschuetz/evidence-based-review python \
        /home/kyleschuetz/.agents/skills/spec-driven-development/scripts/review-readiness.py <stage> ...

Stages:
    spec <spec-file> [--worktree <path>]
        Parse the spec as review setup does. Reads the file on disk.
    plan --task-file <path>... [--worktree <path>]
        Check every task file and its spec at HEAD of a clean worktree. A task that has not
        started may have an empty starting commit; every other rule applies.
    review <level> [<target>] --candidate <ref> --task-file <path>... [--worktree <path>]
        The review command's own setup, then the named tests: each is collected exactly once
        as a bare <test file>::<test name>, and the test folders hold no parametrized tests,
        test classes, skips, or xfails. Same arguments as `evidence-based-review review`.

    staged
        The Git pre-commit hook (scripts/pre-commit). Checks the commit being made: every staged
        spec (a SPEC*.md file, or any file with a `## Requirements` heading), every task file in the
        staged tree (TASKS*.md, tasks/todo.md, or any file with a `**Spec:**` line) with the `plan`
        rules, and every added line of a staged .py file for parametrize, test classes, skips, and xfails.

Prints one line per check, then READY or NOT READY. Exit code 0 only when READY.
"""

from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from evidence_based_review.checks import read_check
from evidence_based_review.records import Failure
from evidence_based_review.scope import (_baseline, _check_verifications, _git, _lines, _requirements,
                                         _tasks_in_scope, check_source, parse_spec, read_work, resolve_commit,
                                         review_scope)
from evidence_based_review.agents import project_commands

FORBIDDEN = r"parametrize|^class Test|pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail|importorskip)\("
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
    check_spec(worktree, resolve_commit(worktree, "HEAD"), path, (worktree / path).read_text())


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

    def clean_head():
        commit = resolve_commit(worktree, "HEAD")
        check_source(worktree, commit)
        return commit, f"HEAD {commit} has no uncommitted or untracked changes"

    commit = report("clean checkout", clean_head)
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
    commit = _git(worktree, "commit-tree", tree, *(["-p", parent] if parent else []),
                  "-m", "review readiness check of the staged tree").strip()
    staged = _git(worktree, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0")
    markdown = [path for path in _git(worktree, "ls-tree", "-r", "--name-only", "-z", commit).split("\0")
                if path.endswith(".md")]
    task_files, specs = [], []
    for path in markdown:
        lines = list(_lines(_git(worktree, "show", f"{commit}:{path}")))
        name = posixpath.basename(path)
        if (fnmatchcase(name, "TASKS*.md") or path == "tasks/todo.md" or path.endswith("/tasks/todo.md")
                or any(line.startswith("**Spec:**") for line in lines)):
            task_files.append(path)
        if path in staged and (fnmatchcase(name, "SPEC*.md") or any(line.rstrip() == "## Requirements"
                                                                     for line in lines)):
            specs.append(path)
    for path in specs:
        check_spec(worktree, commit, path, _git(worktree, "show", f"{commit}:{path}"))
    if task_files and any(path in staged for path in task_files + specs):
        report("task files and specs in this commit", lambda: check_work(worktree, commit, task_files))
    added = [line for line in _git(worktree, "diff", "--cached", "-U0", "--", "*.py").splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    report("no parametrize, test classes, skips, or xfails added",
           lambda: forbidden_lines([line[1:] for line in added]))


def forbidden_lines(lines: list[str]):
    found = [line for line in lines if re.search(FORBIDDEN, line)]
    if found:
        raise Failure(f"{len(found)} added line(s); every test is one plain function, run once:\n" + "\n".join(found))
    return None, f"{len(lines)} added .py lines checked"


def review_stage(args) -> None:
    worktree = Path(args.worktree).resolve()

    def setup():
        d = review_scope(args.level, args.target, args.candidate, args.task_files, worktree)
        return d, f"{len(d['requirements'])} requirements, candidate {d['candidate']}, baseline {d['baseline']}"

    definition = report("review setup", setup)
    if definition is None:
        return
    named: dict[str, list[str]] = {}
    for requirement in definition["requirements"]:
        check = read_check(requirement["verification"])
        named.setdefault(check["test_file"], [])
        named[check["test_file"]] += [t for t in check["tests"] if t not in named[check["test_file"]]]
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for test_file, tests in named.items():
        report(f"collect {test_file}", lambda: collect(worktree, env, test_file, tests))
    folders = sorted({str(PurePosixPath(test_file).parent) for test_file in named})
    report("no parametrize, test classes, skips, or xfails in " + ", ".join(folders),
           lambda: forbidden(worktree, definition["candidate"], folders))

    def clean():
        check_source(worktree, definition["candidate"])
        return None, "no changes"

    report("worktree still clean", clean)


def collect(worktree: Path, env: dict, test_file: str, tests: list[str]):
    if PurePosixPath(test_file).suffix != ".py":
        raise Failure(f"only .py test files can be collected here: {test_file}")
    command = ["uv", "run", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", test_file]
    run = subprocess.run(command, cwd=worktree, env=env, capture_output=True, text=True)
    nodes = [line.strip() for line in run.stdout.splitlines() if "::" in line]
    if run.returncode != 0:
        raise Failure(f"{' '.join(command)} exited with {run.returncode}:\n{(run.stdout + run.stderr)[-3000:]}")
    problems = []
    for test in tests:
        bare = f"{test_file}::{test}"
        exact = nodes.count(bare)
        others = [node for node in nodes if node != bare and node.split("::")[-1].split("[")[0] == test]
        if exact != 1 or others:
            problems.append(f"{test}: {bare} collected {exact} time(s)"
                            + (f"; {len(others)} other node(s) with this name, such as {', '.join(others[:3])}" if others else ""))
    if problems:
        raise Failure("each named test must be collected exactly once as a bare <test file>::<test name>\n"
                      + "\n".join(problems))
    return None, ", ".join(f"{test_file}::{test}" for test in tests)


def forbidden(worktree: Path, commit: str, folders: list[str]):
    command = ["git", "-C", str(worktree), "grep", "-n", "-E", FORBIDDEN, commit, "--", *folders]
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
