"""Public datasets a review block may name: a DOI or an HTTPS URL on one of
the allowed hosts (Zenodo, Figshare, OSF, OpenNeuro), optionally narrowed to
some of its files. Resolved to a file list, then downloaded within a byte
limit, keeping each file's path within the dataset."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .errors import DATASET_TOO_LARGE, DATASET_UNAVAILABLE, INVALID_REVIEW_BLOCK, FixtureError
from .manifest import Dataset

log = logging.getLogger("fixtures")

USER_AGENT = "pennsieve-app-fixtures/0.1 (+https://github.com/pennsieve-apps/app-fixtures)"
TIMEOUT = 60
ALLOWED_HOSTS = ("zenodo.org", "figshare.com", "osf.io", "openneuro.org", "s3.amazonaws.com")

DOI_ZENODO = re.compile(r"^10\.5281/zenodo\.(\d+)$")
DOI_OPENNEURO = re.compile(r"^10\.18112/openneuro\.(ds\d+)\.v([\w.]+)$")
DOI_FIGSHARE = re.compile(r"^10\.6084/m9\.figshare\.(\d+)(?:\.v(\d+))?$", re.IGNORECASE)
DOI_OSF = re.compile(r"^10\.17605/osf\.io/(\w+)$", re.IGNORECASE)


@dataclass(frozen=True)
class RemoteFile:
    path: str  # within the dataset
    url: str
    size: int | None


@dataclass
class Resolved:
    host: str
    identifier: str  # what was resolved, for provenance
    landing: str  # a page a person can open
    files: list[RemoteFile]


# --- HTTP ------------------------------------------------------------------


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    host = urllib.parse.urlparse(url).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        raise FixtureError(f"{url}: host {host!r} is not an allowed dataset host ({', '.join(ALLOWED_HOSTS)})", INVALID_REVIEW_BLOCK)
    if urllib.parse.urlparse(url).scheme != "https":
        raise FixtureError(f"{url}: only https is allowed", INVALID_REVIEW_BLOCK)
    return urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(_request(url), timeout=TIMEOUT) as r:
        return json.load(r)


def _get_text(url: str) -> str:
    with urllib.request.urlopen(_request(url), timeout=TIMEOUT) as r:
        return r.read().decode("utf-8")


def _head_size(url: str) -> int | None:
    try:
        with urllib.request.urlopen(_request(url, "HEAD"), timeout=TIMEOUT) as r:
            length = r.headers.get("Content-Length")
            return int(length) if length else None
    except Exception:  # a host that refuses HEAD: the size is checked while streaming
        return None


# --- hosts -----------------------------------------------------------------


def _zenodo(record_id: str) -> Resolved:
    rec = _get_json(f"https://zenodo.org/api/records/{record_id}")
    files = []
    for f in rec.get("files") or []:
        key = f.get("key") or f.get("filename")
        url = (f.get("links") or {}).get("self") or f"https://zenodo.org/records/{record_id}/files/{key}?download=1"
        files.append(RemoteFile(path=key, url=url, size=f.get("size")))
    return Resolved("zenodo.org", f"zenodo record {record_id}", f"https://zenodo.org/records/{record_id}", files)


def _figshare(article_id: str, version: str | None) -> Resolved:
    url = f"https://api.figshare.com/v2/articles/{article_id}"
    if version:
        url += f"/versions/{version}"
    art = _get_json(url)
    files = [
        RemoteFile(path=f["name"], url=f["download_url"], size=f.get("size"))
        for f in art.get("files") or []
        if f.get("download_url")
    ]
    landing = art.get("figshare_url") or f"https://figshare.com/articles/{article_id}"
    return Resolved("figshare.com", f"figshare article {article_id}", landing, files)


def _osf(node_id: str) -> Resolved:
    files: list[RemoteFile] = []

    def walk(url: str, prefix: str) -> None:
        while url:
            page = _get_json(url)
            for item in page.get("data") or []:
                attrs = item.get("attributes") or {}
                name = attrs.get("name", "")
                if attrs.get("kind") == "folder":
                    nested = (((item.get("relationships") or {}).get("files") or {}).get("links") or {}).get("related")
                    href = nested.get("href") if isinstance(nested, dict) else nested
                    if href:
                        walk(href, prefix + name + "/")
                else:
                    download = (item.get("links") or {}).get("download")
                    if download:
                        files.append(RemoteFile(path=prefix + name, url=download, size=attrs.get("size")))
            url = ((page.get("links") or {}).get("next")) or ""

    walk(f"https://api.osf.io/v2/nodes/{node_id}/files/osfstorage/", "")
    return Resolved("osf.io", f"OSF project {node_id}", f"https://osf.io/{node_id}", files)


S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _openneuro(dataset: str, version: str, patterns: tuple[str, ...]) -> Resolved:
    """OpenNeuro's public S3 bucket holds the latest snapshot of each dataset;
    the version in the DOI is recorded, not pinned. Plain paths are fetched
    directly; a pattern lists the bucket under the dataset."""
    base = f"https://s3.amazonaws.com/openneuro.org/"
    files: list[RemoteFile] = []
    wild = [p for p in patterns if any(ch in p for ch in "*?[")]
    plain = [p for p in patterns if p not in wild]
    for p in plain:
        files.append(RemoteFile(path=p, url=base + urllib.parse.quote(f"{dataset}/{p}"), size=None))
    if wild or not patterns:
        token = None
        while True:
            q = {"list-type": "2", "prefix": f"{dataset}/", "max-keys": "1000"}
            if token:
                q["continuation-token"] = token
            xml = _get_text(f"https://s3.amazonaws.com/openneuro.org?{urllib.parse.urlencode(q)}")
            root = ET.fromstring(xml)
            for c in root.findall(S3_NS + "Contents"):
                key = c.findtext(S3_NS + "Key") or ""
                rel = key[len(dataset) + 1 :]
                if not rel or rel.endswith("/"):
                    continue
                if not patterns or _matches(rel, wild):
                    size = c.findtext(S3_NS + "Size")
                    files.append(RemoteFile(path=rel, url=base + urllib.parse.quote(key), size=int(size) if size else None))
            if root.findtext(S3_NS + "IsTruncated") == "true":
                token = root.findtext(S3_NS + "NextContinuationToken")
            else:
                break
    return Resolved("openneuro.org", f"OpenNeuro {dataset} v{version}", f"https://openneuro.org/datasets/{dataset}/versions/{version}", files)


# --- resolution ------------------------------------------------------------


def _matches(path: str, patterns) -> bool:
    name = path.rsplit("/", 1)[-1]
    for p in patterns:
        if fnmatch.fnmatchcase(path, p) or ("/" not in p and fnmatch.fnmatchcase(name, p)):
            return True
    return False


def resolve(ds: Dataset) -> Resolved:
    """The dataset's files, narrowed to ds.files when given."""
    if ds.doi:
        doi = ds.doi.strip()
        if m := DOI_ZENODO.match(doi):
            res = _zenodo(m.group(1))
        elif m := DOI_OPENNEURO.match(doi):
            return _openneuro(m.group(1), m.group(2), ds.files)
        elif m := DOI_FIGSHARE.match(doi):
            res = _figshare(m.group(1), m.group(2))
        elif m := DOI_OSF.match(doi):
            res = _osf(m.group(1))
        else:
            raise FixtureError(f"DOI {doi} is not on an allowed host (Zenodo, Figshare, OSF, OpenNeuro)", INVALID_REVIEW_BLOCK)
        res.identifier = f"doi:{doi}"
    else:
        url = (ds.url or "").strip()
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path
        if host.endswith("zenodo.org") and (m := re.search(r"/records?/(\d+)", path)):
            res = _zenodo(m.group(1))
        elif host.endswith("figshare.com") and (m := re.search(r"/(\d+)(?:/(\d+))?/?$", path)):
            res = _figshare(m.group(1), m.group(2))
        elif host.endswith("osf.io") and (m := re.match(r"^/(\w+)/?$", path)):
            res = _osf(m.group(1))
        else:
            # A single file by URL on an allowed host.
            _request(url)
            name = path.rsplit("/", 1)[-1] or "download"
            res = Resolved(host, url, url, [RemoteFile(path=name, url=url, size=_head_size(url))])
    if ds.files:
        res.files = [f for f in res.files if _matches(f.path, ds.files)]
    if not res.files:
        raise FixtureError(f"{res.identifier}: no files match {list(ds.files) or 'the dataset'}", DATASET_UNAVAILABLE)
    return res


def download(res: Resolved, dest: Path, max_bytes: int) -> list[dict]:
    """Download the resolved files into dest, keeping their paths. Refuses
    before starting when the known sizes exceed max_bytes, and while streaming
    when the total does."""
    known = sum(f.size for f in res.files if f.size)
    if known > max_bytes:
        raise FixtureError(f"{res.identifier}: {known} bytes exceed MAX_DATASET_BYTES={max_bytes}", DATASET_TOO_LARGE)
    total = 0
    out = []
    for f in res.files:
        if ".." in Path(f.path).parts or f.path.startswith("/"):
            raise FixtureError(f"{res.identifier}: refusing path {f.path!r}", DATASET_UNAVAILABLE)
        target = dest / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        log.info("download %s", f.url)
        with urllib.request.urlopen(_request(f.url), timeout=TIMEOUT) as r, open(target, "wb") as w:
            while chunk := r.read(1 << 20):
                size += len(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise FixtureError(f"{res.identifier}: download exceeds MAX_DATASET_BYTES={max_bytes}", DATASET_TOO_LARGE)
                digest.update(chunk)
                w.write(chunk)
        out.append({"path": f.path, "url": f.url, "size": size, "sha256": digest.hexdigest()})
    return out
