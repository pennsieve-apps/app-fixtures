"""Build an app's test inputs from its app.yml review block.

Pennsieve runs it with OUTPUT_DIR and the parameters in app.yml as
environment variables. It writes OUTPUT_DIR laid out as the target app reads
INPUT_DIR: ports with placement: subfolder in their own folders, the rest at
the root. A step that depends only on this one gets this directory as its
INPUT_DIR unchanged.

Exit 0 when every required port has data; 1 when a fixture cannot be built
(the log says which); 2 for a bad configuration.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import crate, datasets, sources
from .errors import FixtureError
from .manifest import Dataset, Manifest, Port, load

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("fixtures")

VERSION = "0.1.0"


def clone(repository: str, tag: str, scratch: Path) -> tuple[Path, str]:
    """A shallow clone of the repository at the tag, and its commit."""
    dest = scratch / "repo"
    cmd = ["git", "clone", "--quiet", "--depth", "1", "--branch", tag, repository, str(dest)]
    log.info("clone %s at %s", repository, tag)
    proc = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if proc.returncode != 0:
        raise FixtureError(f"git clone failed: {proc.stderr.strip() or proc.stdout.strip()}")
    commit = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    return dest, commit


def port_dir(output: Path, port: Port) -> Path:
    return output / port.name if port.placement == "subfolder" else output


def files_under(root: Path, exclude: set[str]) -> list[Path]:
    out = []
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = f.relative_to(root)
        if rel.parts and rel.parts[0] in exclude:
            continue
        out.append(f)
    return out


def describe_files(root: Path, files: list[Path]) -> list[dict]:
    out = []
    for f in files:
        h = hashlib.sha256()
        with open(f, "rb") as r:
            while chunk := r.read(1 << 20):
                h.update(chunk)
        out.append({"path": str(f.relative_to(root)), "size": f.stat().st_size, "sha256": h.hexdigest()})
    return out


def fetch_source(ds: Dataset, scratch: Path, max_bytes: int) -> tuple[Path, dict]:
    res = datasets.resolve(ds)
    dest = Path(tempfile.mkdtemp(prefix="source-", dir=scratch))
    got = datasets.download(res, dest, max_bytes)
    prov = {"identifier": res.identifier, "landing": res.landing, "files": got}
    if ds.doi:
        prov["doi"] = ds.doi
    return dest, prov


def build(manifest: Manifest, repo: Path, output: Path, scratch: Path, *, ports: list[str] | None, max_bytes: int, timeout: int) -> list[dict]:
    if not manifest.has_review:
        raise FixtureError("the app's app.yml has no review block, so it declares no test inputs")
    wanted = [p for p in manifest.inputs if not ports or p.name in ports]
    if ports:
        unknown = sorted(set(ports) - {p.name for p in manifest.inputs})
        if unknown:
            raise FixtureError(f"PORTS names inputs the app does not declare: {', '.join(unknown)}")
    own = {p.name: manifest.port_sources[p.name] for p in wanted if p.name in manifest.port_sources}
    from_generate = [p for p in wanted if p.name not in own]
    built: dict[str, dict] = {}

    # The top-level generator writes the whole input directory at once.
    if from_generate and manifest.generate is not None:
        gen = manifest.generate
        source_dir, source_prov = (None, None)
        if gen.source:
            source_dir, source_prov = fetch_source(gen.source, scratch, max_bytes)
        root = sources.run_generate(gen, repo, source_dir, timeout, scratch)
        # Ports with their own source are taken from it, not from here.
        for p in wanted:
            if p.name in own and p.placement == "subfolder":
                shutil.rmtree(root / p.name, ignore_errors=True)
        sources.copy_tree(root, output)
        prov = {"kind": "generate", "command": gen.command, "image": gen.image, "root": gen.root}
        if source_prov:
            prov.update({"identifier": source_prov["identifier"], "landing": source_prov["landing"], "doi": source_prov.get("doi"), "sourceFiles": source_prov["files"]})
        for p in from_generate:
            built[p.name] = prov
    elif from_generate:
        missing = [p for p in from_generate if p.required]
        optional = [p.name for p in from_generate if not p.required]
        if optional:
            log.info("no review source for optional input(s) %s; left empty", ", ".join(optional))
        if missing:
            raise FixtureError(
                "the review block gives no source for required input(s): "
                + ", ".join(p.name for p in missing)
                + " (add review.generate or review.inputs.<port>)"
            )

    # Ports with their own source.
    for p in wanted:
        src = own.get(p.name)
        if src is None:
            continue
        dest = port_dir(output, p)
        dest.mkdir(parents=True, exist_ok=True)
        if src.kind == "files":
            sources.copy_files(repo, src.files, dest)
            built[p.name] = {"kind": "files", "patterns": list(src.files)}
        elif src.kind == "generate":
            gen = src.generate
            source_dir, source_prov = (None, None)
            if gen.source:
                source_dir, source_prov = fetch_source(gen.source, scratch, max_bytes)
            root = sources.run_generate(gen, repo, source_dir, timeout, scratch)
            sources.copy_tree(root, dest)
            prov = {"kind": "generate", "command": gen.command, "image": gen.image, "root": gen.root}
            if source_prov:
                prov.update({"identifier": source_prov["identifier"], "landing": source_prov["landing"], "doi": source_prov.get("doi"), "sourceFiles": source_prov["files"]})
            built[p.name] = prov
        elif src.kind == "dataset":
            res = datasets.resolve(src.dataset)
            got = datasets.download(res, dest, max_bytes)
            built[p.name] = {"kind": "dataset", "identifier": res.identifier, "landing": res.landing, "doi": src.dataset.doi, "url": src.dataset.url, "patterns": list(src.dataset.files), "sourceFiles": got}

    # Verify and describe.
    subfolders = {p.name for p in manifest.inputs if p.placement == "subfolder"}
    report = []
    for p in wanted:
        if p.name not in built:
            continue
        if p.placement == "subfolder":
            files = files_under(output / p.name, set()) if (output / p.name).is_dir() else []
        else:
            files = files_under(output, subfolders | {crate.CRATE_DIR})
        if not files and p.required:
            raise FixtureError(f"required input {p.name!r} received no files from its source")
        if not files:
            log.info("optional input %s received no files; left out", p.name)
            continue
        report.append({"name": p.name, "placement": p.placement, "source": built[p.name], "files": describe_files(output, files)})
        log.info("input %s: %d file(s), %s", p.name, len(files), built[p.name]["kind"])
    return report


def main() -> int:
    output = os.environ.get("OUTPUT_DIR")
    repository = (os.environ.get("REPOSITORY") or "").strip()
    tag = (os.environ.get("TAG") or "").strip()
    if not output or not repository or not tag:
        log.error("OUTPUT_DIR, REPOSITORY and TAG must be set")
        return 2
    if not repository.startswith(("https://", "file://")) and not Path(repository).is_dir():
        log.error("REPOSITORY must be an https:// URL")
        return 2
    ports = [p.strip() for p in (os.environ.get("PORTS") or "").split(",") if p.strip()] or None
    try:
        max_bytes = int(os.environ.get("MAX_DATASET_BYTES") or 2 * 1024**3)
        timeout = int(os.environ.get("GENERATE_TIMEOUT") or 900)
    except ValueError:
        log.error("MAX_DATASET_BYTES and GENERATE_TIMEOUT must be integers")
        return 2
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="fixtures-"))
    try:
        repo, commit = clone(repository, tag, scratch)
        manifest = load(repo / "app.yml")
        report = build(manifest, repo, out, scratch, ports=ports, max_bytes=max_bytes, timeout=timeout)
        target = {"name": manifest.name, "repository": repository, "tag": tag, "commit": commit}
        doc = crate.build(target, report, VERSION)
        crate_dir = out / crate.CRATE_DIR
        crate_dir.mkdir(exist_ok=True)
        (crate_dir / crate.CRATE_FILE).write_text(json.dumps(doc, indent=2))
        summary = {
            "target": target,
            "ports": [{"name": r["name"], "placement": r["placement"], "kind": r["source"]["kind"], "files": len(r["files"]), "bytes": sum(f["size"] for f in r["files"])} for r in report],
            "parameters": manifest.parameters,
        }
        print(crate.marker_line(doc, summary), flush=True)
        log.info("built %d input(s) for %s %s into %s", len(report), manifest.name or repository, tag, out)
        return 0
    except FixtureError as e:
        log.error("%s", e)
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
