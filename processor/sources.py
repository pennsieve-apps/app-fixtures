"""Where a port's data comes from: a generator command, committed files, or
a tree already fetched. Each returns what it placed, for the provenance
crate."""
from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .errors import FILES_NOT_FOUND, GENERATOR_FAILED, GENERATOR_IMAGE_UNAVAILABLE, INVALID_REVIEW_BLOCK, FixtureError
from .manifest import Generate

log = logging.getLogger("fixtures")

GENERATOR_IMAGES = ("python",)


def run_generate(gen: Generate, repo: Path, source_dir: Path | None, timeout: int, scratch: Path) -> Path:
    """Run a generator command from the repository root and return the
    directory holding what it wrote (its `root` within $REVIEW_INPUT_DIR)."""
    if gen.image not in GENERATOR_IMAGES:
        raise FixtureError(
            f"generator image {gen.image!r} is not available in this app yet (only {', '.join(GENERATOR_IMAGES)})",
            GENERATOR_IMAGE_UNAVAILABLE,
        )
    if gen.source and source_dir is None:
        raise FixtureError("generator names a source but none was fetched", GENERATOR_FAILED)
    out = Path(tempfile.mkdtemp(prefix="gen-", dir=scratch))
    env = dict(os.environ)
    env["REVIEW_INPUT_DIR"] = str(out)
    if source_dir is not None:
        env["REVIEW_SOURCE_DIR"] = str(source_dir)
    log.info("generate: sh -c %r (cwd %s)", gen.command, repo)
    try:
        proc = subprocess.run(
            ["sh", "-c", gen.command],
            cwd=repo,
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        raise FixtureError(f"generator did not finish within {timeout} s: {gen.command}", GENERATOR_FAILED)
    if proc.stdout.strip():
        log.info("generator stdout:\n%s", proc.stdout.rstrip()[-4000:])
    if proc.stderr.strip():
        log.info("generator stderr:\n%s", proc.stderr.rstrip()[-4000:])
    if proc.returncode != 0:
        raise FixtureError(f"generator exited {proc.returncode}: {gen.command}", GENERATOR_FAILED)
    if not out.is_dir():
        raise FixtureError("generator removed $REVIEW_INPUT_DIR; it must write into it", GENERATOR_FAILED)
    root = out / gen.root if gen.root else out
    if gen.root and (".." in Path(gen.root).parts or Path(gen.root).is_absolute()):
        raise FixtureError(f"generate.root {gen.root!r} must be a relative path within what the generator wrote", INVALID_REVIEW_BLOCK)
    if not root.is_dir():
        raise FixtureError(f"generator wrote no {gen.root!r} folder under $REVIEW_INPUT_DIR", GENERATOR_FAILED)
    if not any(root.rglob("*")):
        raise FixtureError(f"generator wrote nothing under {root}", GENERATOR_FAILED)
    return root


def _static_prefix(pattern: str) -> Path:
    """The directory part of a glob pattern before its first wildcard, so
    matched files keep their paths below it."""
    parts = []
    for part in Path(pattern).parts:
        if any(ch in part for ch in "*?["):
            break
        parts.append(part)
    if parts and parts == list(Path(pattern).parts):
        parts = parts[:-1]  # a plain file path: its parent
    return Path(*parts) if parts else Path()


def copy_files(repo: Path, patterns: tuple[str, ...], dest: Path) -> list[str]:
    """Copy committed files or globs into dest, keeping paths below each
    pattern's static prefix. Returns the paths written, relative to dest."""
    written: list[str] = []
    for pattern in patterns:
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            raise FixtureError(f"files pattern {pattern!r} must be relative, without ..", INVALID_REVIEW_BLOCK)
        matches = sorted(glob.glob(str(repo / pattern), recursive=True))
        if not matches:
            raise FixtureError(f"files pattern {pattern!r} matches nothing in the repository", FILES_NOT_FOUND)
        prefix = repo / _static_prefix(pattern)
        for m in matches:
            src = Path(m)
            if ".git" in src.relative_to(repo).parts:
                continue
            if src.is_dir():
                for f in sorted(p for p in src.rglob("*") if p.is_file()):
                    written.append(_copy_one(f, dest / f.relative_to(prefix)))
            elif src.is_file():
                written.append(_copy_one(src, dest / src.relative_to(prefix)))
    return written


def _copy_one(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return str(dst)


def copy_tree(src: Path, dst: Path) -> list[Path]:
    """Merge src into dst (files replaced, other files kept). Returns the files written."""
    written = []
    for f in sorted(p for p in src.rglob("*") if p.is_file() or p.is_symlink()):
        rel = f.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if f.is_symlink() and not f.exists():
            continue
        shutil.copy2(f, target, follow_symlinks=True)
        written.append(target)
    return written
