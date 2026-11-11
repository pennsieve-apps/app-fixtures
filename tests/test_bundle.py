"""BUNDLE_URL mode: a bundle the release pipeline built is unpacked instead of
cloning and generating."""
import io
import json
import tarfile
from pathlib import Path

from processor import main


def make_bundle(tmp_path: Path, files: dict[str, str], name="bundle.tar.gz") -> Path:
    src = tmp_path / "bundle-src"
    for rel, content in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    archive = tmp_path / name
    with tarfile.open(archive, "w:gz") as tar:
        for rel in files:
            tar.add(src / rel, arcname=rel)
    return archive


def run(monkeypatch, tmp_path, url, **env):
    out = tmp_path / "output"
    monkeypatch.setenv("OUTPUT_DIR", str(out))
    monkeypatch.setenv("REPOSITORY", "https://github.com/pennsieve-apps/demo-app")
    monkeypatch.setenv("TAG", "v0.1.0")
    monkeypatch.setenv("BUNDLE_URL", url)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return main.main(), out


def test_bundle_is_unpacked_as_the_output(monkeypatch, tmp_path, capsys):
    archive = make_bundle(tmp_path, {
        "cells/cells.h5ad": "cells",
        "labels/labels.csv": "a,b",
        ".pennsieve-fixtures/ro-crate-metadata.json": json.dumps({"@graph": []}),
    })
    code, out = run(monkeypatch, tmp_path, f"file://{archive}")
    assert code == 0
    assert (out / "cells" / "cells.h5ad").read_text() == "cells"
    assert (out / "labels" / "labels.csv").read_text() == "a,b"
    assert (out / ".pennsieve-fixtures" / "ro-crate-metadata.json").is_file()
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith(main.BUNDLE_MARKER))
    info = json.loads(line[len(main.BUNDLE_MARKER):])
    assert info["files"] == 3
    assert info["target"] == {"repository": "https://github.com/pennsieve-apps/demo-app", "tag": "v0.1.0"}
    assert len(info["sha256"]) == 64


def test_a_failed_build_in_the_bundle_is_reported_as_such(monkeypatch, tmp_path, capsys):
    archive = make_bundle(tmp_path, {
        ".pennsieve-fixtures/error.json": json.dumps({"code": "no_review_block", "message": "the app's app.yml has no review block", "declarationProblem": True}),
    })
    code, out = run(monkeypatch, tmp_path, f"file://{archive}")
    assert code == 3
    err = json.loads((out / ".pennsieve-fixtures" / "error.json").read_text())
    assert err["code"] == "no_review_block"
    assert "FIXTURES_ERROR " in capsys.readouterr().out


def test_bundle_too_large(monkeypatch, tmp_path):
    archive = make_bundle(tmp_path, {"big/file.bin": "x" * 5000})
    code, out = run(monkeypatch, tmp_path, f"file://{archive}", MAX_DATASET_BYTES="100")
    assert code == 1
    assert json.loads((out / ".pennsieve-fixtures" / "error.json").read_text())["code"] == "bundle_too_large"


def test_bundle_unavailable_and_invalid(monkeypatch, tmp_path):
    code, out = run(monkeypatch, tmp_path, f"file://{tmp_path}/missing.tar.gz")
    assert code == 1
    assert json.loads((out / ".pennsieve-fixtures" / "error.json").read_text())["code"] == "bundle_unavailable"

    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a tarball")
    second = tmp_path / "second"
    second.mkdir()
    code, out = run(monkeypatch, second, f"file://{bad}")
    assert code == 1
    assert json.loads((out / ".pennsieve-fixtures" / "error.json").read_text())["code"] == "bundle_invalid"


def test_unsafe_members_are_skipped(monkeypatch, tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"fine"
        info = tarfile.TarInfo("ok.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        escape = tarfile.TarInfo("../escape.txt")
        escape.size = len(data)
        tar.addfile(escape, io.BytesIO(data))
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    code, out = run(monkeypatch, tmp_path, f"file://{archive}")
    assert code == 0
    assert (out / "ok.txt").read_text() == "fine"
    assert not (out.parent / "escape.txt").exists()
    assert not (out / "link").exists()
