"""End to end on a local git repository standing in for a pennsieve-apps repo."""
import json
import subprocess
from pathlib import Path

import pytest

from processor import crate, errors, main

APP_YML = """
schemaVersion: "1.0"
application: {name: Demo App, type: processor}
inputs:
  - name: cells
    placement: subfolder
  - name: labels
    placement: subfolder
  - name: notes
    placement: subfolder
    required: false
  - name: extra
    placement: subfolder
outputs:
  - name: out
review:
  version: 1
  generate:
    command: python3 tests/make_fixture.py "$REVIEW_INPUT_DIR"
    root: linked
  inputs:
    extra:
      files: [sample-input/*.txt]
  parameters:
    N: 3
"""

MAKE_FIXTURE = """
import os, sys
root = sys.argv[1]
for layout in ("linked", "flat"):
    os.makedirs(os.path.join(root, layout, "cells"), exist_ok=True)
    os.makedirs(os.path.join(root, layout, "labels"), exist_ok=True)
    os.makedirs(os.path.join(root, layout, "extra"), exist_ok=True)
    open(os.path.join(root, layout, "cells", "cells.h5ad"), "w").write("cells")
    open(os.path.join(root, layout, "labels", "labels.csv"), "w").write("a,b")
    open(os.path.join(root, layout, "extra", "generated.txt"), "w").write("should be replaced by files source")
"""


def git(repo: Path, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "demo-app"
    (repo / "tests").mkdir(parents=True)
    (repo / "sample-input").mkdir()
    (repo / "app.yml").write_text(APP_YML)
    (repo / "tests" / "make_fixture.py").write_text(MAKE_FIXTURE)
    (repo / "sample-input" / "one.txt").write_text("1")
    (repo / "sample-input" / "two.txt").write_text("2")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    git(repo, "tag", "v0.1.0")
    return repo


def run(monkeypatch, tmp_path, repo, **env):
    out = tmp_path / "output"
    monkeypatch.setenv("OUTPUT_DIR", str(out))
    monkeypatch.setenv("REPOSITORY", f"file://{repo}")
    monkeypatch.setenv("TAG", "v0.1.0")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return main.main(), out


def test_builds_the_target_input_layout(monkeypatch, tmp_path, repo, capsys):
    code, out = run(monkeypatch, tmp_path, repo)
    assert code == 0
    assert (out / "cells" / "cells.h5ad").read_text() == "cells"
    assert (out / "labels" / "labels.csv").read_text() == "a,b"
    # The port with its own source is taken from it, not from the generator.
    assert sorted(p.name for p in (out / "extra").iterdir()) == ["one.txt", "two.txt"]
    # The flat layout the generator also wrote is not copied.
    assert not (out / "flat").exists()
    # Optional port without a source is simply absent.
    assert not (out / "notes").exists()
    doc = json.loads((out / crate.CRATE_DIR / crate.CRATE_FILE).read_text())
    ids = {e["@id"]: e for e in doc["@graph"]}
    assert ids["./"]["hasPart"] == [{"@id": "cells/"}, {"@id": "labels/"}, {"@id": "extra/"}]
    assert ids["cells/"]["pennsieve:source"]["kind"] == "generate"
    assert ids["extra/"]["pennsieve:source"]["patterns"] == ["sample-input/*.txt"]
    sw = ids[f"file://{repo}/tree/v0.1.0"]
    assert sw["version"] == "v0.1.0" and len(sw["identifier"]) == 40
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith(crate.MARKER)]
    assert len(line) == 1
    marker = json.loads(line[0][len(crate.MARKER):])
    assert marker["summary"]["parameters"] == {"N": 3}
    assert [p["name"] for p in marker["summary"]["ports"]] == ["cells", "labels", "extra"]


def test_ports_subset(monkeypatch, tmp_path, repo):
    code, out = run(monkeypatch, tmp_path, repo, PORTS="extra")
    assert code == 0
    assert (out / "extra" / "one.txt").exists()
    assert not (out / "cells").exists()


def error_of(out, capsys):
    """The failure as a service reads it: the FIXTURES_ERROR line and error.json agree."""
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith(errors.MARKER)]
    assert len(lines) == 1
    marker = json.loads(lines[0][len(errors.MARKER):])
    on_disk = json.loads((out / crate.CRATE_DIR / "error.json").read_text())
    assert marker == on_disk
    return marker


def test_unknown_port_is_a_declaration_problem(monkeypatch, tmp_path, repo, capsys):
    code, out = run(monkeypatch, tmp_path, repo, PORTS="nope")
    assert code == 3
    e = error_of(out, capsys)
    assert e["code"] == "unknown_port" and e["port"] == "nope" and e["declarationProblem"] is True
    assert e["hint"] and e["docs"].startswith("https://") and e["target"]["tag"] == "v0.1.0"


def test_missing_tag_is_a_build_failure(monkeypatch, tmp_path, repo, capsys):
    code, out = run(monkeypatch, tmp_path, repo, TAG="v9.9.9")
    assert code == 1
    e = error_of(out, capsys)
    assert e["code"] == "clone_failed" and e["declarationProblem"] is False and "hint" not in e


def recommit(repo, app_yml):
    (repo / "app.yml").write_text(app_yml)
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "edit app.yml")
    git(repo, "tag", "-f", "v0.1.0")


def test_required_port_without_source(monkeypatch, tmp_path, repo, capsys):
    recommit(repo, APP_YML.replace("  generate:\n    command: python3 tests/make_fixture.py \"$REVIEW_INPUT_DIR\"\n    root: linked\n", ""))
    code, out = run(monkeypatch, tmp_path, repo)
    assert code == 3
    e = error_of(out, capsys)
    assert e["code"] == "no_source_for_port" and e["port"] == "cells" and "review.inputs" in e["hint"]


def test_no_review_block(monkeypatch, tmp_path, repo, capsys):
    recommit(repo, APP_YML.split("review:")[0])
    code, out = run(monkeypatch, tmp_path, repo)
    assert code == 3
    e = error_of(out, capsys)
    assert e["code"] == "no_review_block" and "review" in e["hint"] and e["target"]["commit"]


def test_generator_failure_is_a_build_failure(monkeypatch, tmp_path, repo, capsys):
    recommit(repo, APP_YML.replace('python3 tests/make_fixture.py "$REVIEW_INPUT_DIR"', "exit 7"))
    code, out = run(monkeypatch, tmp_path, repo)
    assert code == 1
    e = error_of(out, capsys)
    assert e["code"] == "generator_failed" and "exited 7" in e["message"]


def test_missing_configuration(monkeypatch):
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    monkeypatch.delenv("REPOSITORY", raising=False)
    assert main.main() == 2
