import pytest

from processor.errors import FixtureError
from processor.manifest import parse

BASE = """
schemaVersion: "1.0"
application: {name: Demo, type: processor}
inputs:
  - name: cells
    placement: subfolder
  - name: metadata
    placement: subfolder
    required: false
  - name: images
outputs:
  - name: out
"""


def test_without_review_block():
    m = parse(BASE)
    assert m.name == "Demo"
    assert [p.name for p in m.inputs] == ["cells", "metadata", "images"]
    assert m.port("images").placement == "root"
    assert m.port("metadata").required is False
    assert not m.has_review


def test_top_level_generate_and_parameters():
    m = parse(BASE + """
review:
  version: 1
  generate:
    command: python -m tests.make_fixture "$REVIEW_INPUT_DIR"
    root: linked
  parameters:
    MAX_EVENTS: 2000
""")
    assert m.has_review
    assert m.generate.command.startswith("python -m")
    assert m.generate.root == "linked"
    assert m.generate.image == "python"
    assert m.parameters == {"MAX_EVENTS": 2000}
    assert m.port_sources == {}


def test_per_port_sources():
    m = parse(BASE + """
review:
  version: 1
  inputs:
    cells:
      files: [sample-input/*.csv]
    images:
      dataset:
        doi: 10.18112/openneuro.ds000117.v2.0.0
        files: [sub-01/ses-mri/anat/sub-01_ses-mri_T1w.nii.gz]
    metadata:
      generate:
        command: python tests/meta.py "$REVIEW_SOURCE_DIR" "$REVIEW_INPUT_DIR"
        source: {doi: 10.5281/zenodo.15388817, files: ["B0[1-6]_*.fcs"]}
""")
    assert m.port_sources["cells"].kind == "files"
    assert m.port_sources["cells"].files == ("sample-input/*.csv",)
    assert m.port_sources["images"].dataset.doi == "10.18112/openneuro.ds000117.v2.0.0"
    assert m.port_sources["metadata"].generate.source.files == ("B0[1-6]_*.fcs",)
    # Files are laid out flat unless the port says source.
    assert m.port_sources["cells"].file_paths == "flat" and m.port_sources["images"].file_paths == "flat"


def test_file_paths():
    m = parse(BASE + """
review:
  version: 1
  inputs:
    cells:
      files: [sample-input/*.csv]
      filePaths: flat
    images:
      dataset: {doi: 10.18112/openneuro.ds000117.v2.0.0, files: [sub-01/ses-mri/anat/sub-01_ses-mri_T1w.nii.gz]}
      filePaths: source
""")
    assert m.port_sources["cells"].file_paths == "flat"
    assert m.port_sources["images"].file_paths == "source"


@pytest.mark.parametrize(
    "review, message",
    [
        ("review:\n  version: 2\n", "review.version must be 1"),
        ("review:\n  version: 1\n  inputs:\n    nope: {files: [a]}\n", "does not declare"),
        ("review:\n  version: 1\n  inputs:\n    cells: {files: [../a]}\n", "without .."),
        ("review:\n  version: 1\n  inputs:\n    cells: {files: [a], dataset: {doi: 10.1/x}}\n", "exactly one"),
        ("review:\n  version: 1\n  inputs:\n    cells: {dataset: {doi: 10.1/x, url: https://zenodo.org/x}}\n", "exactly one of doi or url"),
        ("review:\n  version: 1\n  generate: {root: linked}\n", "needs a command"),
        ("review:\n  version: 1\n  inputs:\n    cells: {files: [a], filePaths: nested}\n", "flat or source"),
        ("review:\n  version: 1\n  inputs:\n    cells: {generate: {command: x}, filePaths: flat}\n", "files and dataset only"),
        ("review:\n  version: 1\n  inputs:\n    cells: {filePaths: flat}\n", "exactly one"),
    ],
)
def test_rejects(review, message):
    with pytest.raises(FixtureError, match=message):
        parse(BASE + review)


def test_rejects_bad_port_name():
    with pytest.raises(FixtureError, match="not a port name"):
        parse('inputs:\n  - name: "My Port"\n')
