from pathlib import Path

import pytest

from processor.errors import FixtureError
from processor.manifest import Generate
from processor.sources import copy_files, copy_tree, run_generate


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "sample-input" / "review").mkdir(parents=True)
    (repo / "sample-input" / "review" / "a.nwb").write_text("a")
    (repo / "sample-input" / "review" / "b.nwb").write_text("b")
    (repo / "sample-input" / "readme.txt").write_text("not matched")
    (repo / "tests" / "fixtures" / "bids" / "sub-01" / "anat").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "bids" / "sub-01" / "anat" / "t1.nii.gz").write_text("t1")
    (repo / "tests" / "fixtures" / "bids" / "dataset_description.json").write_text("{}")
    return repo


def test_copy_files_keeps_paths_below_the_static_prefix(tmp_path):
    repo = make_repo(tmp_path)
    dest = tmp_path / "out"
    copy_files(repo, ("sample-input/review/*.nwb",), dest)
    assert sorted(p.name for p in dest.iterdir()) == ["a.nwb", "b.nwb"]


def test_copy_files_recursive_tree(tmp_path):
    repo = make_repo(tmp_path)
    dest = tmp_path / "out"
    copy_files(repo, ("tests/fixtures/bids/**",), dest)
    assert (dest / "sub-01" / "anat" / "t1.nii.gz").read_text() == "t1"
    assert (dest / "dataset_description.json").exists()


def test_copy_files_plain_path_lands_by_name(tmp_path):
    repo = make_repo(tmp_path)
    dest = tmp_path / "out"
    copy_files(repo, ("sample-input/readme.txt",), dest)
    assert (dest / "readme.txt").exists()


def test_copy_files_refuses_no_match_and_escapes(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(FixtureError, match="matches nothing"):
        copy_files(repo, ("sample-input/*.edf",), tmp_path / "out")
    with pytest.raises(FixtureError, match="without"):
        copy_files(repo, ("../x",), tmp_path / "out")


def test_run_generate_uses_root_and_env(tmp_path):
    repo = make_repo(tmp_path)
    gen = Generate(command='mkdir -p "$REVIEW_INPUT_DIR/linked/cells" "$REVIEW_INPUT_DIR/flat" && echo hi > "$REVIEW_INPUT_DIR/linked/cells/c.csv" && pwd > "$REVIEW_INPUT_DIR/linked/cwd.txt"', root="linked")
    root = run_generate(gen, repo, None, timeout=30, scratch=tmp_path)
    assert (root / "cells" / "c.csv").read_text().strip() == "hi"
    assert Path((root / "cwd.txt").read_text().strip()).resolve() == repo.resolve()


def test_run_generate_failures(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(FixtureError, match="exited 3"):
        run_generate(Generate(command="exit 3"), repo, None, timeout=30, scratch=tmp_path)
    with pytest.raises(FixtureError, match="wrote no 'linked'"):
        run_generate(Generate(command='touch "$REVIEW_INPUT_DIR/x"', root="linked"), repo, None, timeout=30, scratch=tmp_path)
    with pytest.raises(FixtureError, match="wrote nothing"):
        run_generate(Generate(command="true"), repo, None, timeout=30, scratch=tmp_path)
    with pytest.raises(FixtureError, match="did not finish"):
        run_generate(Generate(command="sleep 5"), repo, None, timeout=1, scratch=tmp_path)
    with pytest.raises(FixtureError, match="not available"):
        run_generate(Generate(command="true", image="r"), repo, None, timeout=30, scratch=tmp_path)


def test_copy_tree_merges(tmp_path):
    src = tmp_path / "src"
    (src / "a").mkdir(parents=True)
    (src / "a" / "one.txt").write_text("1")
    dst = tmp_path / "dst"
    (dst / "b").mkdir(parents=True)
    (dst / "b" / "two.txt").write_text("2")
    copy_tree(src, dst)
    assert (dst / "a" / "one.txt").exists() and (dst / "b" / "two.txt").exists()
