"""
src/tools/sbom_generator.py

Generates a Software Bill of Materials for a Python project's
dependencies, in both SPDX-lite and CycloneDX-lite JSON shapes —
structurally recognizable, not a full spec-compliant implementation
(that would mean adopting a heavy external SBOM library; this project's
own use of the SBOM is CVE lookup + license policy, not conformance for
an external consumer).

Parses dependency name/version pairs from a requirements.txt-style file
or a pyproject.toml's [project.dependencies] list — the two dependency
manifest shapes this project itself has used across its history.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# The optional `\[[^\]]*\]` skips PEP 508 extras (e.g. `uvicorn[standard]`,
# `psycopg[binary]`) so the version group still matches what follows them
# instead of silently landing on "unknown" for every package that declares one.
_REQUIREMENT_LINE = re.compile(
    r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(?:[=<>!~]=?\s*([A-Za-z0-9_.\-]+))?"
)


@dataclass
class Dependency:
    name: str
    version: str
    ecosystem: str = "PyPI"


def parse_requirements_txt(content: str) -> list[Dependency]:
    deps = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = _REQUIREMENT_LINE.match(line)
        if not match:
            continue
        deps.append(Dependency(name=match.group(1), version=match.group(2) or "unknown"))
    return deps


def parse_pyproject_toml(content: str) -> list[Dependency]:
    data = tomllib.loads(content)
    raw_deps = data.get("project", {}).get("dependencies", [])
    deps = []
    for raw in raw_deps:
        match = _REQUIREMENT_LINE.match(raw.strip())
        if not match:
            continue
        deps.append(Dependency(name=match.group(1), version=match.group(2) or "unknown"))
    return deps


def dependencies_from_file(path: str) -> list[Dependency]:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    if file_path.name == "pyproject.toml":
        return parse_pyproject_toml(content)
    return parse_requirements_txt(content)


def to_spdx(deps: list[Dependency], document_name: str = "ai-code-review") -> dict:
    return {
        "spdxVersion": "SPDX-2.3",
        "name": document_name,
        "creationInfo": {
            "created": datetime.now(UTC).isoformat(),
            "creators": ["Tool: ai-code-review-sbom"],
        },
        "packages": [
            {
                "name": d.name,
                "versionInfo": d.version,
                "SPDXID": f"SPDXRef-Package-{d.name}",
                "downloadLocation": "NOASSERTION",
            }
            for d in deps
        ],
    }


def to_cyclonedx(deps: list[Dependency]) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "components": [
            {
                "type": "library",
                "name": d.name,
                "version": d.version,
                "purl": f"pkg:{d.ecosystem.lower()}/{d.name}@{d.version}",
            }
            for d in deps
        ],
    }
