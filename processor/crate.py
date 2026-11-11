"""The provenance crate: an RO-Crate 1.1 describing what was built for each
port and where it came from, with schema.org terms (as views-service's
crates), written to OUTPUT_DIR/.pennsieve-fixtures/ro-crate-metadata.json and
printed as one FIXTURES_PROVENANCE line for the step log."""
from __future__ import annotations

import json
from datetime import datetime, timezone

CRATE_DIR = ".pennsieve-fixtures"
CRATE_FILE = "ro-crate-metadata.json"
MARKER = "FIXTURES_PROVENANCE "


def build(target: dict, ports: list[dict], version: str) -> dict:
    """target: {name, repository, tag, commit}; ports: one per built port:
    {name, placement, source: {kind, ...}, files: [{path, size, sha256}]}."""
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    software = {
        "@id": f"{target['repository']}/tree/{target['tag']}",
        "@type": "SoftwareSourceCode",
        "name": target["name"] or target["repository"].rsplit("/", 1)[-1],
        "codeRepository": target["repository"],
        "version": target["tag"],
        "identifier": target["commit"],
    }
    graph = [
        {
            "@id": CRATE_FILE,
            "@type": "CreativeWork",
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
            "about": {"@id": "./"},
        },
        {
            "@id": "./",
            "@type": "Dataset",
            "name": f"Test inputs for {software['name']} {target['tag']}",
            "description": (
                "Inputs built by Pennsieve's app-fixtures from the app's app.yml review block, "
                "laid out as the app reads its input directory. Generated for a sample run; nothing here is a published dataset."
            ),
            "dateCreated": now,
            "isBasedOn": {"@id": software["@id"]},
            "hasPart": [{"@id": _port_id(p)} for p in ports],
            "creator": {"@id": "#app-fixtures"},
        },
        software,
        {
            "@id": "#app-fixtures",
            "@type": "SoftwareApplication",
            "name": "App Fixtures",
            "softwareVersion": version,
            "codeRepository": "https://github.com/pennsieve-apps/app-fixtures",
        },
    ]
    for p in ports:
        entity = {
            "@id": _port_id(p),
            "@type": "Dataset",
            "name": p["name"],
            "description": _describe(p["source"]),
            "pennsieve:placement": p["placement"],
            "pennsieve:source": p["source"],
            "contentSize": sum(f["size"] for f in p["files"]),
            "numberOfItems": len(p["files"]),
        }
        src = p["source"]
        if src.get("doi"):
            entity["isBasedOn"] = {"@id": f"https://doi.org/{src['doi']}"}
            graph.append({"@id": f"https://doi.org/{src['doi']}", "@type": "Dataset", "name": src.get("identifier") or src["doi"], "url": src.get("landing")})
        elif src.get("landing"):
            entity["isBasedOn"] = {"@id": src["landing"]}
        graph.append(entity)
    graph.append({
        "@id": "#pennsieve-terms",
        "@type": "DefinedTermSet",
        "name": "Terms schema.org lacks, used by this crate",
        "hasDefinedTerm": [
            {"@id": "pennsieve:placement", "@type": "DefinedTerm", "name": "placement", "description": "Where the port sits in the app's INPUT_DIR: root or subfolder."},
            {"@id": "pennsieve:source", "@type": "DefinedTerm", "name": "source", "description": "How the port's data was obtained: a generator command, committed files or a public dataset."},
        ],
    })
    return {"@context": ["https://w3id.org/ro/crate/1.1/context", {"pennsieve": "https://schemas.pennsieve.io/terms/"}], "@graph": graph}


def _port_id(p: dict) -> str:
    return f"{p['name']}/" if p["placement"] == "subfolder" else f"./#port-{p['name']}"


def _describe(src: dict) -> str:
    kind = src.get("kind")
    if kind == "generate":
        where = f", from {src['identifier']}" if src.get("identifier") else ""
        return f"Written by the app's own generator: {src.get('command')}{where}."
    if kind == "files":
        return f"Copied from the repository: {', '.join(src.get('patterns', []))}."
    if kind == "dataset":
        return f"Fetched from {src.get('identifier')}."
    return "Unknown source."


def marker_line(crate: dict, summary: dict) -> str:
    return MARKER + json.dumps({"summary": summary, "crate": crate}, separators=(",", ":"))
