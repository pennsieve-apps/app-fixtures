"""The target app's app.yml: its input ports and its review block.

Only what building fixtures needs is read; the rest of the manifest is left
to app-deploy-service, which validated it at registration.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import INVALID_REVIEW_BLOCK, UNKNOWN_PORT, FixtureError

PORT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Port:
    name: str
    placement: str  # "root" or "subfolder"
    required: bool


@dataclass(frozen=True)
class Dataset:
    doi: str | None = None
    url: str | None = None
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class Generate:
    command: str
    image: str = "python"
    root: str | None = None
    source: Dataset | None = None


# How committed files and dataset files are laid out in the port, as the
# file paths of workflow and notebook data sources: "flat" puts every file at
# the port's root (the default), "source" keeps its path (below the pattern's
# fixed part for committed files; within the dataset for a dataset).
FILE_PATHS = ("flat", "source")


@dataclass(frozen=True)
class PortSource:
    kind: str  # "generate" | "files" | "dataset"
    generate: Generate | None = None
    files: tuple[str, ...] = ()
    dataset: Dataset | None = None
    file_paths: str = "flat"


@dataclass(frozen=True)
class Manifest:
    name: str
    inputs: tuple[Port, ...]
    review_version: int | None
    generate: Generate | None
    port_sources: dict[str, PortSource] = field(default_factory=dict)
    parameters: dict[str, object] = field(default_factory=dict)

    def port(self, name: str) -> Port:
        for p in self.inputs:
            if p.name == name:
                return p
        raise FixtureError(f"app.yml declares no input {name!r}", UNKNOWN_PORT, port=name)

    @property
    def has_review(self) -> bool:
        return self.review_version is not None


def _dataset(raw: dict, where: str) -> Dataset:
    if not isinstance(raw, dict):
        raise FixtureError(f"{where}: dataset must be a mapping", INVALID_REVIEW_BLOCK)
    doi, url = raw.get("doi"), raw.get("url")
    if bool(doi) == bool(url):
        raise FixtureError(f"{where}: dataset needs exactly one of doi or url", INVALID_REVIEW_BLOCK)
    files = tuple(str(f) for f in (raw.get("files") or []))
    return Dataset(doi=doi, url=url, files=files)


def _generate(raw: dict, where: str) -> Generate:
    if not isinstance(raw, dict) or not raw.get("command"):
        raise FixtureError(f"{where}: generate needs a command", INVALID_REVIEW_BLOCK)
    source = raw.get("source")
    return Generate(
        command=str(raw["command"]),
        image=str(raw.get("image") or "python"),
        root=str(raw["root"]) if raw.get("root") else None,
        source=_dataset(source, where + ".source") if source else None,
    )


def _port_source(name: str, raw: dict) -> PortSource:
    where = f"review.inputs.{name}"
    if not isinstance(raw, dict):
        raise FixtureError(f"{where}: exactly one of generate, files or dataset", INVALID_REVIEW_BLOCK)
    raw = dict(raw)
    file_paths = raw.pop("filePaths", None)
    if len(raw) != 1:
        raise FixtureError(f"{where}: exactly one of generate, files or dataset", INVALID_REVIEW_BLOCK)
    (kind, value), = raw.items()
    if file_paths is not None:
        if kind not in ("files", "dataset"):
            raise FixtureError(f"{where}: filePaths applies to files and dataset only", INVALID_REVIEW_BLOCK)
        if file_paths not in FILE_PATHS:
            raise FixtureError(f"{where}: filePaths must be flat or source", INVALID_REVIEW_BLOCK)
    file_paths = file_paths or "flat"
    if kind == "generate":
        return PortSource("generate", generate=_generate(value, where))
    if kind == "files":
        patterns = tuple(str(p) for p in (value or []))
        if not patterns:
            raise FixtureError(f"{where}: files must list at least one pattern", INVALID_REVIEW_BLOCK)
        for p in patterns:
            if p.startswith("/") or ".." in Path(p).parts:
                raise FixtureError(f"{where}: {p!r} must be relative, without ..", INVALID_REVIEW_BLOCK)
        return PortSource("files", files=patterns, file_paths=file_paths)
    if kind == "dataset":
        return PortSource("dataset", dataset=_dataset(value, where), file_paths=file_paths)
    raise FixtureError(f"{where}: unknown source {kind!r}", INVALID_REVIEW_BLOCK)


def parse(text: str) -> Manifest:
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise FixtureError("app.yml is not a mapping", INVALID_REVIEW_BLOCK)
    app = doc.get("application") or {}
    inputs = []
    for raw in doc.get("inputs") or []:
        name = str(raw.get("name", ""))
        if not PORT_NAME.match(name):
            raise FixtureError(f"input name {name!r} is not a port name", INVALID_REVIEW_BLOCK)
        placement = str(raw.get("placement") or "root")
        if placement not in ("root", "subfolder"):
            raise FixtureError(f"input {name}: placement must be root or subfolder", INVALID_REVIEW_BLOCK)
        inputs.append(Port(name=name, placement=placement, required=bool(raw.get("required", True))))
    names = [p.name for p in inputs]
    if len(set(names)) != len(names):
        raise FixtureError("app.yml declares an input twice", INVALID_REVIEW_BLOCK)

    review = doc.get("review")
    if review is None:
        return Manifest(name=str(app.get("name") or ""), inputs=tuple(inputs), review_version=None, generate=None)
    if not isinstance(review, dict) or review.get("version") != 1:
        raise FixtureError("review.version must be 1", INVALID_REVIEW_BLOCK)
    generate = _generate(review["generate"], "review.generate") if review.get("generate") else None
    sources = {}
    for name, raw in (review.get("inputs") or {}).items():
        if name not in names:
            raise FixtureError(f"review.inputs names {name!r}, which app.yml does not declare", UNKNOWN_PORT, port=str(name))
        sources[str(name)] = _port_source(str(name), raw)
    params = dict(review.get("parameters") or {})
    return Manifest(
        name=str(app.get("name") or ""),
        inputs=tuple(inputs),
        review_version=1,
        generate=generate,
        port_sources=sources,
        parameters=params,
    )


def load(path: Path) -> Manifest:
    if not path.is_file():
        raise FixtureError(f"{path.name} not found at the repository root", INVALID_REVIEW_BLOCK)
    return parse(path.read_text())
