"""Why a fixture cannot be built, in a form both a person and a service can
read: a stable code, a message for the step log, and a hint for the app's
author. Printed as one FIXTURES_ERROR line and written to
OUTPUT_DIR/.pennsieve-fixtures/error.json."""
from __future__ import annotations

import json

REVIEW_DOCS = "https://github.com/Pennsieve/app-deploy-service/blob/main/docs/app-manifest-review.md"

# The app declares no usable test data: its author must change app.yml.
NO_REVIEW_BLOCK = "no_review_block"
NO_SOURCE_FOR_PORT = "no_source_for_port"
UNKNOWN_PORT = "unknown_port"
INVALID_REVIEW_BLOCK = "invalid_review_block"
# The declared test data could not be built here.
CLONE_FAILED = "clone_failed"
GENERATOR_FAILED = "generator_failed"
GENERATOR_IMAGE_UNAVAILABLE = "generator_image_unavailable"
DATASET_UNAVAILABLE = "dataset_unavailable"
DATASET_TOO_LARGE = "dataset_too_large"
FILES_NOT_FOUND = "files_not_found"
EMPTY_PORT = "empty_port"
# Anything else.
INTERNAL = "internal"

# Codes that mean the app's declaration is the problem (exit 3), with the
# hint for its author; every other code is a build failure (exit 1).
DECLARATION = {
    NO_REVIEW_BLOCK: "Add a `review` block to app.yml naming test inputs for each required input port.",
    NO_SOURCE_FOR_PORT: "Give the port a source under `review.inputs.<port>` (generate, files or dataset) or a top-level `review.generate`.",
    UNKNOWN_PORT: "The review block (or PORTS) names an input port app.yml does not declare.",
    INVALID_REVIEW_BLOCK: "Fix the review block so it validates against the app manifest schema.",
    FILES_NOT_FOUND: "The `files` patterns match nothing in the repository at this tag.",
}

MARKER = "FIXTURES_ERROR "


class FixtureError(Exception):
    """A fixture cannot be built; the message says why, for the step log."""

    def __init__(self, message: str, code: str = INTERNAL, port: str | None = None):
        super().__init__(message)
        self.code = code
        self.port = port

    @property
    def declaration_problem(self) -> bool:
        return self.code in DECLARATION

    def as_dict(self, target: dict | None = None) -> dict:
        d = {"code": self.code, "message": str(self), "declarationProblem": self.declaration_problem}
        if self.port:
            d["port"] = self.port
        if self.declaration_problem:
            d["hint"] = DECLARATION[self.code]
            d["docs"] = REVIEW_DOCS
        if target:
            d["target"] = target
        return d

    def marker_line(self, target: dict | None = None) -> str:
        return MARKER + json.dumps(self.as_dict(target), separators=(",", ":"))
