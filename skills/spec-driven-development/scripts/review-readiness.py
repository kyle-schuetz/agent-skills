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

Prints one line per check, then READY or NOT READY. Exit code 0 only when READY.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import subprocess
import sys
from pathlib import Path, PurePosixPath

from evidence_based_review.checks import read_check
from evidence_based_review.records import Failure
from evidence_based_review.scope import (_baseline, _check_verifications, _requirements, _tasks_in_scope,
                                         check_source, parse_spec, read_work, resolve_commit, review_scope)
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

    def parse():
        requirements = parse_spec(path, (worktree / path).read_text())
        return requirements, f"{len(requirements)} requirements: " + ", ".join(
            r.rsplit("#", 1)[1] for r in requirements)

    requirements = report(f"parse {path}", parse)
    if requirements is None:
        return

    def verifications():
        tests = project_commands(worktree, resolve_commit(worktree, "HEAD"))["tests"]
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

    report("Verification lines", verifications)


def plan_stage(args) -> None:
    worktree = Path(args.worktree).resolve()

    def check():
        commit = resolve_commit(worktree, "HEAD")
        check_source(worktree, commit)
        work = read_work(worktree, commit, args.task_files)
        tasks = _tasks_in_scope("full_work", None, work["task_files"])
        requirements = _requirements(tasks, work["specs"], commit)
        _check_verifications(requirements, worktree, commit)
        started = [task for task in tasks if task["starting_commit"] is not None]
        if started:
            _baseline(worktree, started, commit)
        phases = sum(len(task_file["phases"]) for task_file in work["task_files"])
        return None, (f"at {commit}: {phases} phases, {len(tasks)} tasks, {len(requirements)} requirements"
                      f" in scope, {len(started)} tasks with a starting commit, {len(tasks) - len(started)}"
                      " not started")

    report("task files and specs at HEAD", check)


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
    args = parser.parse_args()
    {"spec": spec_stage, "plan": plan_stage, "review": review_stage}[args.stage](args)
    print("READY" if not failures else f"NOT READY: {len(failures)} failed: {', '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
