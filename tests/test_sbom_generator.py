from src.tools.sbom_generator import (
    Dependency,
    parse_pyproject_toml,
    parse_requirements_txt,
    to_cyclonedx,
    to_spdx,
)


def test_parse_requirements_txt_extracts_name_and_version():
    content = "requests==2.32.0\nflask>=3.0\n# a comment\n\n-e ./local-pkg\n"
    deps = parse_requirements_txt(content)
    names = {d.name: d.version for d in deps}
    assert names["requests"] == "2.32.0"
    assert names["flask"] == "3.0"
    assert "local-pkg" not in names  # -e lines are skipped


def test_parse_requirements_txt_handles_no_version_pin():
    deps = parse_requirements_txt("requests\n")
    assert deps[0].name == "requests"
    assert deps[0].version == "unknown"


def test_parse_requirements_txt_handles_extras_syntax():
    """uvicorn[standard]>=0.30 -- the version must still be captured past
    the [extras] bracket, not silently dropped to 'unknown'."""
    deps = parse_requirements_txt("uvicorn[standard]>=0.30\npsycopg[binary]>=3.2\n")
    names = {d.name: d.version for d in deps}
    assert names["uvicorn"] == "0.30"
    assert names["psycopg"] == "3.2"


def test_parse_pyproject_toml_extracts_dependencies():
    content = """
[project]
name = "myproject"
dependencies = [
    "requests>=2.32",
    "pydantic==2.7.0",
]
"""
    deps = parse_pyproject_toml(content)
    names = {d.name: d.version for d in deps}
    assert names["requests"] == "2.32"
    assert names["pydantic"] == "2.7.0"


def test_to_spdx_includes_all_packages():
    deps = [Dependency(name="requests", version="2.32.0")]
    doc = to_spdx(deps)
    assert doc["spdxVersion"] == "SPDX-2.3"
    assert doc["packages"][0]["name"] == "requests"
    assert doc["packages"][0]["versionInfo"] == "2.32.0"


def test_to_cyclonedx_includes_purl():
    deps = [Dependency(name="requests", version="2.32.0", ecosystem="PyPI")]
    doc = to_cyclonedx(deps)
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["components"][0]["purl"] == "pkg:pypi/requests@2.32.0"
