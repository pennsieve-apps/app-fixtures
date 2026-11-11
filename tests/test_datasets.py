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
        datasets.resolve(Dataset(doi="10.26275/lzhy-ox20"))
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
