"""
src/tools/license_policy.py

Enforces a banned-license policy over a dependency list — blocking a PR
that introduces a banned copyleft license (GPL/AGPL/SSPL by default,
configurable) the same way a critical security finding would.

License data has to come from somewhere external (PyPI package
metadata, `pip-licenses`, etc.) — this module takes a
{package_name: license_string} mapping as input rather than fetching it
itself, so the policy-checking logic is testable independent of any
particular license-data source.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.core.models import ConfidenceTier, Finding, Severity

DEFAULT_BANNED_LICENSES = frozenset(
    {
        "GPL-2.0",
        "GPL-3.0",
        "AGPL-3.0",
        "SSPL-1.0",
        "GPL-2.0-only",
        "GPL-3.0-only",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "GPL-2.0-or-later",
        "GPL-3.0-or-later",
    }
)


@dataclass
class LicenseCheckResult:
    package_name: str
    license: str
    banned: bool


def check_licenses(
    package_licenses: dict[str, str],
    *,
    banned_licenses: frozenset[str] = DEFAULT_BANNED_LICENSES,
) -> list[LicenseCheckResult]:
    return [
        LicenseCheckResult(
            package_name=name, license=license_name, banned=license_name in banned_licenses
        )
        for name, license_name in package_licenses.items()
    ]


def to_findings(
    results: list[LicenseCheckResult], filename: str = "requirements.txt"
) -> list[Finding]:
    return [
        Finding(
            file=filename,
            line=0,
            category="license",
            severity=Severity.CRITICAL,
            message=(
                f"'{r.package_name}' uses banned license '{r.license}' — remove or "
                f"replace this dependency before merge."
            ),
            confidence=ConfidenceTier.MEDIUM,
            source="license_policy",
        )
        for r in results
        if r.banned
    ]
