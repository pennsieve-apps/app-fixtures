import io
import json

import pytest

from processor import datasets
from processor.errors import FixtureError
from processor.manifest import Dataset


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, length: bool = True):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))} if length else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_http(monkeypatch, routes: dict):
    """routes: url -> bytes | dict | str. Records requested URLs."""
    seen = []

    def urlopen(req, timeout=None):
        url = req.full_url
        seen.append((req.get_method(), url))
        for key, body in routes.items():
            if url.startswith(key):
                if isinstance(body, dict):
                    body = json.dumps(body).encode()
                elif isinstance(body, str):
                    body = body.encode()
                return FakeResponse(body)
        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(datasets.urllib.request, "urlopen", urlopen)
    return seen


def test_zenodo_record_narrowed_by_pattern(monkeypatch):
    fake_http(monkeypatch, {
        "https://zenodo.org/api/records/15388817": {
            "files": [
                {"key": "B01_x.fcs", "size": 3, "links": {"self": "https://zenodo.org/api/records/15388817/files/B01_x.fcs/content"}},
                {"key": "B07_x.fcs", "size": 3, "links": {"self": "https://zenodo.org/api/records/15388817/files/B07_x.fcs/content"}},
                {"key": "notes.txt", "size": 1, "links": {"self": "https://zenodo.org/api/records/15388817/files/notes.txt/content"}},
            ]
        }
    })
    res = datasets.resolve(Dataset(doi="10.5281/zenodo.15388817", files=("B0[1-6]_*.fcs",)))
    assert [f.path for f in res.files] == ["B01_x.fcs"]
    assert res.identifier == "doi:10.5281/zenodo.15388817"


def test_openneuro_plain_paths_need_no_listing(monkeypatch):
    seen = fake_http(monkeypatch, {})
    res = datasets.resolve(Dataset(doi="10.18112/openneuro.ds000117.v2.0.0", files=("sub-01/ses-mri/anat/sub-01_ses-mri_T1w.nii.gz",)))
    assert seen == []
    assert res.files[0].url == "https://s3.amazonaws.com/openneuro.org/ds000117/sub-01/ses-mri/anat/sub-01_ses-mri_T1w.nii.gz"
    assert "ds000117" in res.landing


def test_openneuro_pattern_lists_bucket(monkeypatch):
    xml = """<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
      <IsTruncated>false</IsTruncated>
      <Contents><Key>ds000117/sub-01/anat/a_T1w.nii.gz</Key><Size>10</Size></Contents>
      <Contents><Key>ds000117/sub-02/anat/b_T1w.nii.gz</Key><Size>11</Size></Contents>
      <Contents><Key>ds000117/README</Key><Size>5</Size></Contents>
    </ListBucketResult>"""
    fake_http(monkeypatch, {"https://s3.amazonaws.com/openneuro.org?": xml})
    res = datasets.resolve(Dataset(doi="10.18112/openneuro.ds000117.v2.0.0", files=("sub-*/anat/*_T1w.nii.gz",)))
    assert [f.path for f in res.files] == ["sub-01/anat/a_T1w.nii.gz", "sub-02/anat/b_T1w.nii.gz"]
    assert res.files[1].size == 11


def test_unknown_doi_host_and_disallowed_url():
    with pytest.raises(FixtureError, match="not on an allowed host"):
        datasets.resolve(Dataset(doi="10.1000/xyz123"))
    with pytest.raises(FixtureError, match="not an allowed dataset host"):
        datasets.resolve(Dataset(url="https://example.org/data.zip"))


def test_download_keeps_paths_and_checks_size(monkeypatch, tmp_path):
    fake_http(monkeypatch, {"https://zenodo.org/api/records/1/files/": b"hello world"})
    res = datasets.Resolved("zenodo.org", "doi:x", "https://zenodo.org/records/1", [
        datasets.RemoteFile("sub/a.txt", "https://zenodo.org/api/records/1/files/a/content", 11),
    ])
    got = datasets.download(res, tmp_path, max_bytes=100)
    assert (tmp_path / "sub" / "a.txt").read_text() == "hello world"
    assert got[0]["size"] == 11 and len(got[0]["sha256"]) == 64
    with pytest.raises(FixtureError, match="exceed MAX_DATASET_BYTES"):
        datasets.download(res, tmp_path, max_bytes=5)
    res.files[0] = datasets.RemoteFile("sub/a.txt", res.files[0].url, None)
    with pytest.raises(FixtureError, match="exceeds MAX_DATASET_BYTES"):
        datasets.download(res, tmp_path, max_bytes=5)


DISCOVER = "https://api.pennsieve.io/discover"
S3 = "https://pennsieve-prod-discover-publish50-use1.s3.amazonaws.com/705"


def discover_manifest(*files):
    return {"header": {"count": len(files), "size": sum(f[2] for f in files)},
            "data": [{"fileName": name, "path": path, "url": f"{S3}/{'/'.join(path + [name])}?X-Amz-Signature=s", "size": size}
                     for name, path, size in files]}


def test_discover_doi_with_a_pattern_lists_the_dataset(monkeypatch):
    seen = fake_http(monkeypatch, {
        f"{DISCOVER}/datasets/doi/10.26275/har8-ytia": {"id": 705, "version": 1, "status": "PUBLISH_SUCCEEDED", "embargo": False},
        f"{DISCOVER}/datasets/705/versions/1/files/browse": {"totalCount": 2, "files": [
            {"name": "primary", "path": "primary", "type": "Directory"}, {"name": "readme.md", "path": "readme.md", "type": "File"}]},
        f"{DISCOVER}/datasets/705/versions/1/files/download-manifest": discover_manifest(
            ("sub-1.nwb", ["primary", "sub-1"], 10), ("sub-2.nwb", ["primary", "sub-2"], 12), ("readme.md", [], 3)),
    })
    res = datasets.resolve(Dataset(doi="10.26275/har8-ytia", files=("*.nwb",)))
    assert [f.path for f in res.files] == ["primary/sub-1/sub-1.nwb", "primary/sub-2/sub-2.nwb"]
    assert [f.size for f in res.files] == [10, 12]
    assert res.files[0].url.startswith(S3 + "/primary/sub-1/sub-1.nwb")
    assert res.identifier == "doi:10.26275/har8-ytia" and res.landing == "https://discover.pennsieve.io/datasets/705/version/1"
    post = [u for m, u in seen if m == "POST"]
    assert post == [f"{DISCOVER}/datasets/705/versions/1/files/download-manifest"]


def test_discover_plain_paths_need_no_listing(monkeypatch):
    # Routes match by prefix: the longer one first.
    seen = fake_http(monkeypatch, {
        f"{DISCOVER}/datasets/705/versions/3/files/download-manifest": discover_manifest(
            ("a.csv", ["primary"], 5), ("b.csv", ["derivative", "sub-1"], 6)),
        f"{DISCOVER}/datasets/705": {"id": 705, "version": 3, "status": "PUBLISH_SUCCEEDED", "embargo": False},
    })
    res = datasets.resolve(Dataset(url="https://discover.pennsieve.io/datasets/705", files=("primary/a.csv", "derivative")))
    assert [f.path for f in res.files] == ["primary/a.csv", "derivative/sub-1/b.csv"], "a plain path names a file or a folder"
    assert not any("browse" in u for _, u in seen)


def test_discover_url_with_a_version_and_refusals(monkeypatch):
    fake_http(monkeypatch, {
        f"{DISCOVER}/datasets/705/versions/2/files/browse": {"totalCount": 1, "files": [{"path": "x.csv"}]},
        f"{DISCOVER}/datasets/705/versions/2/files/download-manifest": discover_manifest(("x.csv", [], 1)),
        f"{DISCOVER}/datasets/doi/10.26275/aaaa-bbbb": {"id": 9, "version": 1, "status": "PUBLISH_SUCCEEDED", "embargo": True},
    })
    res = datasets.resolve(Dataset(url="https://discover.pennsieve.io/datasets/705/version/2"))
    assert [f.path for f in res.files] == ["x.csv"]
    with pytest.raises(FixtureError, match="not publicly available"):
        datasets.resolve(Dataset(doi="10.26275/aaaa-bbbb"))


def test_discover_dataset_too_large_to_list(monkeypatch):
    monkeypatch.setattr(datasets, "MAX_DISCOVER_FILES", 1)
    fake_http(monkeypatch, {
        f"{DISCOVER}/datasets/705/versions/1/files/browse": {"totalCount": 1, "files": [{"path": "primary"}]},
        f"{DISCOVER}/datasets/705/versions/1/files/download-manifest": discover_manifest(("a", [], 1), ("b", [], 1)),
    })
    with pytest.raises(FixtureError, match="name the files"):
        datasets.resolve(Dataset(url="https://discover.pennsieve.io/datasets/705/version/1", files=("*.csv",)))
