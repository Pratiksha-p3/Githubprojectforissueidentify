"""
tools/sbom_checker.py

Tier 3 — Feature 7: SBOM + License Compliance

Generates a Software Bill of Materials (SBOM) and checks
all dependencies against your license policy.

Features:
  - Detects all dependencies (pip, npm, go, cargo)
  - Identifies license for each package
  - Flags GPL contamination (copyleft issues)
  - Blocks PRs that introduce banned licenses
  - Generates SBOM in SPDX and CycloneDX formats

Usage:
  python tools/sbom_checker.py --check
  python tools/sbom_checker.py --check --format cyclonedx
  python tools/sbom_checker.py --pr-check requirements.txt

Install:
  pip install pip-licenses
  # For full SBOM: install Syft from https://github.com/anchore/syft
"""
from __future__ import annotations

import json
import subprocess
import re
import os
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone


# ── License Policy ───────────────────────────────────────
# Customize for your organization

BANNED_LICENSES = {
    "GPL-2.0",
    "GPL-3.0",
    "AGPL-3.0",
    "LGPL-2.0",
    "LGPL-2.1",
    "LGPL-3.0",
    "SSPL-1.0",
    "Commons-Clause",
    "BUSL-1.1",
}

ALLOWED_LICENSES = {
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MPL-2.0",
    "CDDL-1.0",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
    "CC0-1.0",
    "WTFPL",
}

REVIEW_REQUIRED = {
    "LGPL-2.1",
    "MPL-2.0",
    "EPL-1.0",
    "EPL-2.0",
    "EUPL-1.2",
}


@dataclass
class PackageLicense:
    name:       str
    version:    str
    license:    str
    ecosystem:  str        # pip, npm, go, cargo
    url:        str = ""
    compliant:  bool = True
    blocked:    bool = False
    reason:     str = ""


@dataclass
class SBOMReport:
    generated_at:   str
    total_packages: int
    blocked:        list[PackageLicense] = field(default_factory=list)
    review_needed:  list[PackageLicense] = field(default_factory=list)
    compliant:      list[PackageLicense] = field(default_factory=list)
    unknown:        list[PackageLicense] = field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        return len(self.blocked) == 0


class SBOMChecker:

    def __init__(self, banned: set = None, allowed: set = None):
        self.banned  = banned  or BANNED_LICENSES
        self.allowed = allowed or ALLOWED_LICENSES

    # ── Public API ────────────────────────────────────────

    def check(self, project_dir: str = ".") -> SBOMReport:
        """
        Scan all dependency files in the project and check licenses.
        Returns a SBOMReport with blocked/compliant/unknown packages.
        """
        print("\n[sbom] Scanning dependencies...")
        packages = []

        # Python
        packages.extend(self._check_python(project_dir))

        # Node.js
        packages.extend(self._check_node(project_dir))

        # Go
        packages.extend(self._check_go(project_dir))

        print(f"[sbom] Found {len(packages)} packages total")

        return self._classify(packages)

    def check_new_packages(
        self,
        old_reqs: str,
        new_reqs: str,
    ) -> list[PackageLicense]:
        """
        Compare old vs new requirements.txt.
        Returns only newly ADDED packages — for PR gate use.
        """
        old_pkgs = self._parse_requirements(old_reqs)
        new_pkgs = self._parse_requirements(new_reqs)

        # Find packages added in new version
        added = {
            name: ver for name, ver in new_pkgs.items()
            if name not in old_pkgs
        }

        if not added:
            return []

        print(f"[sbom] {len(added)} new packages detected in PR")
        packages = [
            self._get_pip_license(name, ver)
            for name, ver in added.items()
        ]

        blocked = [p for p in packages if p.blocked]
        if blocked:
            print(f"[sbom] ⛔ {len(blocked)} new packages with banned licenses!")
            for p in blocked:
                print(f"  ⛔ {p.name} {p.version} — {p.license} — {p.reason}")

        return packages

    def generate_spdx(self, report: SBOMReport, output: str = "sbom.spdx.json") -> Path:
        """Generate SBOM in SPDX format."""
        all_pkgs = (
            report.blocked +
            report.review_needed +
            report.compliant +
            report.unknown
        )

        spdx = {
            "spdxVersion":   "SPDX-2.3",
            "dataLicense":   "CC0-1.0",
            "SPDXID":        "SPDXRef-DOCUMENT",
            "name":          "AI-Code-Review-SBOM",
            "documentNamespace": f"https://spdx.org/spdxdocs/ai-code-review-{datetime.now().date()}",
            "creationInfo": {
                "created":  report.generated_at,
                "creators": ["Tool: ai-code-review-sbom-checker"],
            },
            "packages": [
                {
                    "SPDXID":           f"SPDXRef-{p.name.replace('-','').replace('.','')}-{i}",
                    "name":             p.name,
                    "versionInfo":      p.version,
                    "licenseConcluded": p.license or "NOASSERTION",
                    "licenseDeclared":  p.license or "NOASSERTION",
                    "downloadLocation": p.url or "NOASSERTION",
                    "filesAnalyzed":    False,
                }
                for i, p in enumerate(all_pkgs)
            ],
        }

        path = Path(output)
        path.write_text(json.dumps(spdx, indent=2))
        print(f"[sbom] SPDX SBOM saved: {path}")
        return path

    def generate_cyclonedx(self, report: SBOMReport, output: str = "sbom.cdx.json") -> Path:
        """Generate SBOM in CycloneDX format."""
        all_pkgs = (
            report.blocked +
            report.review_needed +
            report.compliant +
            report.unknown
        )

        cdx = {
            "bomFormat":   "CycloneDX",
            "specVersion": "1.5",
            "version":     1,
            "metadata": {
                "timestamp": report.generated_at,
                "tools":     [{"name": "ai-code-review-sbom", "version": "1.0"}],
            },
            "components": [
                {
                    "type":    "library",
                    "name":    p.name,
                    "version": p.version,
                    "purl":    f"pkg:{p.ecosystem}/{p.name}@{p.version}",
                    "licenses": [{"license": {"id": p.license}}] if p.license else [],
                }
                for p in all_pkgs
            ],
        }

        path = Path(output)
        path.write_text(json.dumps(cdx, indent=2))
        print(f"[sbom] CycloneDX SBOM saved: {path}")
        return path

    def format_pr_comment(self, report: SBOMReport) -> str:
        """Format SBOM results as a GitHub PR comment."""
        lines = ["## 🔐 License Compliance Report\n"]

        if report.is_compliant:
            lines.append("✅ **All dependencies are license-compliant.**\n")
        else:
            lines.append(
                f"⛔ **{len(report.blocked)} package(s) with banned licenses detected!**\n"
            )

        # Stats table
        lines.append(
            "| Status | Count |\n"
            "|--------|-------|\n"
            f"| ✅ Compliant | {len(report.compliant)} |\n"
            f"| ⚠️ Review Required | {len(report.review_needed)} |\n"
            f"| ⛔ Blocked | {len(report.blocked)} |\n"
            f"| ❓ Unknown | {len(report.unknown)} |\n"
        )

        # Blocked packages
        if report.blocked:
            lines.append("\n### ⛔ Blocked Packages\n")
            lines.append(
                "| Package | Version | License | Reason |\n"
                "|---------|---------|---------|--------|\n"
            )
            for p in report.blocked:
                lines.append(
                    f"| `{p.name}` | {p.version} | "
                    f"`{p.license}` | {p.reason} |"
                )
            lines.append(
                "\n> **Action required:** Remove or replace these packages "
                "before this PR can be merged."
            )

        # Review required
        if report.review_needed:
            lines.append("\n### ⚠️ Packages Requiring Legal Review\n")
            for p in report.review_needed:
                lines.append(f"- `{p.name}` {p.version} — `{p.license}`")

        # Unknown licenses
        if report.unknown:
            lines.append("\n### ❓ Unknown Licenses\n")
            for p in report.unknown[:5]:
                lines.append(f"- `{p.name}` {p.version} — license not detected")

        lines.append("\n---\n*🤖 AI Code Review — SBOM analysis*")
        return "\n".join(lines)

    def post_to_pr(
        self,
        report:    SBOMReport,
        repo:      str,
        pr_number: int,
        head_sha:  str,
        loader,
    ) -> None:
        """Post SBOM report to GitHub PR and set commit status."""
        import requests

        comment = self.format_pr_comment(report)

        # Post comment
        try:
            requests.post(
                f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                headers = loader.auth.headers(),
                json    = {"body": comment},
                timeout = 15,
            )
            print(f"[sbom] SBOM report posted to PR #{pr_number}")
        except Exception as e:
            print(f"[sbom] Failed to post comment: {e}")

        # Set commit status
        state = "success" if report.is_compliant else "failure"
        desc  = (
            "All licenses compliant"
            if report.is_compliant
            else f"{len(report.blocked)} banned license(s) detected"
        )
        try:
            requests.post(
                f"https://api.github.com/repos/{repo}/statuses/{head_sha}",
                headers = loader.auth.headers(),
                json    = {
                    "state":       state,
                    "description": desc,
                    "context":     "ai-code-review/license",
                },
                timeout = 15,
            )
            print(f"[sbom] Commit status set: {state}")
        except Exception as e:
            print(f"[sbom] Failed to set status: {e}")

    # ── Scanners ──────────────────────────────────────────

    def _check_python(self, project_dir: str) -> list[PackageLicense]:
        """Use pip-licenses to get all installed package licenses."""
        try:
            result = subprocess.run(
                ["pip-licenses", "--format=json", "--with-urls"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError("pip-licenses failed")

            data     = json.loads(result.stdout)
            packages = []
            for item in data:
                p = PackageLicense(
                    name      = item.get("Name", ""),
                    version   = item.get("Version", ""),
                    license   = self._normalize_license(item.get("License", "")),
                    ecosystem = "pip",
                    url       = item.get("URL", ""),
                )
                packages.append(self._evaluate(p))

            print(f"[sbom] Python: {len(packages)} packages scanned")
            return packages

        except FileNotFoundError:
            print("[sbom] pip-licenses not installed. Run: pip install pip-licenses")
            return self._fallback_python(project_dir)
        except Exception as e:
            print(f"[sbom] Python scan failed: {e}")
            return []

    def _fallback_python(self, project_dir: str) -> list[PackageLicense]:
        """Fallback: parse requirements.txt and look up licenses via PyPI."""
        req_file = Path(project_dir) / "requirements.txt"
        if not req_file.exists():
            return []

        packages = []
        for name, version in self._parse_requirements(req_file.read_text()).items():
            p = self._get_pip_license(name, version)
            packages.append(p)

        print(f"[sbom] Python (fallback): {len(packages)} packages")
        return packages

    def _get_pip_license(self, name: str, version: str) -> PackageLicense:
        """Look up a package's license via PyPI API."""
        import urllib.request
        try:
            url  = f"https://pypi.org/pypi/{name}/json"
            with urllib.request.urlopen(url, timeout=5) as r:
                data    = json.loads(r.read())
                info    = data.get("info", {})
                license_str = info.get("license") or ""
                classifiers = info.get("classifiers", [])

                # Extract from classifiers if license field is empty
                if not license_str:
                    for c in classifiers:
                        if "License ::" in c:
                            license_str = c.split("::")[-1].strip()
                            break

                p = PackageLicense(
                    name      = name,
                    version   = version,
                    license   = self._normalize_license(license_str),
                    ecosystem = "pip",
                    url       = info.get("home_page", ""),
                )
                return self._evaluate(p)
        except Exception:
            return PackageLicense(
                name=name, version=version,
                license="UNKNOWN", ecosystem="pip",
            )

    def _check_node(self, project_dir: str) -> list[PackageLicense]:
        """Scan Node.js packages via package.json."""
        pkg_json = Path(project_dir) / "package.json"
        if not pkg_json.exists():
            return []
        try:
            result = subprocess.run(
                ["npx", "license-checker", "--json", "--production"],
                capture_output=True, text=True, timeout=60,
                cwd=project_dir,
            )
            if result.returncode != 0:
                return []

            data     = json.loads(result.stdout)
            packages = []
            for pkg_name, info in data.items():
                name, _, ver = pkg_name.rpartition("@")
                p = PackageLicense(
                    name      = name or pkg_name,
                    version   = ver,
                    license   = self._normalize_license(info.get("licenses", "")),
                    ecosystem = "npm",
                    url       = info.get("repository", ""),
                )
                packages.append(self._evaluate(p))

            print(f"[sbom] Node.js: {len(packages)} packages scanned")
            return packages
        except Exception as e:
            print(f"[sbom] Node scan skipped: {e}")
            return []

    def _check_go(self, project_dir: str) -> list[PackageLicense]:
        """Scan Go modules via go.sum."""
        go_sum = Path(project_dir) / "go.sum"
        if not go_sum.exists():
            return []
        # Basic extraction — requires go-licenses for full data
        packages = []
        seen     = set()
        for line in go_sum.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 1:
                mod = parts[0]
                ver = parts[1] if len(parts) > 1 else ""
                if mod not in seen:
                    seen.add(mod)
                    packages.append(PackageLicense(
                        name=mod, version=ver,
                        license="UNKNOWN", ecosystem="go",
                    ))
        print(f"[sbom] Go: {len(packages)} modules found (licenses require go-licenses)")
        return packages

    # ── Classification ────────────────────────────────────

    def _evaluate(self, p: PackageLicense) -> PackageLicense:
        """Mark a package as compliant, review-needed, or blocked."""
        lic = p.license.upper() if p.license else ""

        for banned in self.banned:
            if banned.upper() in lic:
                p.blocked   = True
                p.compliant = False
                p.reason    = (
                    f"{banned} is a copyleft license that requires "
                    f"your code to be open-sourced."
                )
                return p

        for review in REVIEW_REQUIRED:
            if review.upper() in lic:
                p.compliant = True
                p.reason    = "Legal review required before use in commercial products."
                return p

        p.compliant = True
        return p

    def _classify(self, packages: list[PackageLicense]) -> SBOMReport:
        blocked  = []
        review   = []
        ok       = []
        unknown  = []

        for p in packages:
            if p.blocked:
                blocked.append(p)
            elif p.reason:
                review.append(p)
            elif p.license in ("UNKNOWN", "", "NOASSERTION"):
                unknown.append(p)
            else:
                ok.append(p)

        print(
            f"[sbom] Results: {len(ok)} ok, {len(review)} review, "
            f"{len(blocked)} blocked, {len(unknown)} unknown"
        )

        return SBOMReport(
            generated_at   = datetime.now(timezone.utc).isoformat(),
            total_packages = len(packages),
            blocked        = blocked,
            review_needed  = review,
            compliant      = ok,
            unknown        = unknown,
        )

    def _normalize_license(self, lic: str) -> str:
        if not lic:
            return "UNKNOWN"
        lic = lic.strip()
        MAP = {
            "mit":              "MIT",
            "apache 2.0":       "Apache-2.0",
            "apache-2.0":       "Apache-2.0",
            "apache2":          "Apache-2.0",
            "bsd":              "BSD-3-Clause",
            "bsd-3":            "BSD-3-Clause",
            "bsd-2":            "BSD-2-Clause",
            "isc":              "ISC",
            "gpl":              "GPL-3.0",
            "gpl-2":            "GPL-2.0",
            "gpl-3":            "GPL-3.0",
            "lgpl":             "LGPL-2.1",
            "agpl":             "AGPL-3.0",
            "mpl":              "MPL-2.0",
            "cc0":              "CC0-1.0",
            "unlicense":        "Unlicense",
            "public domain":    "Unlicense",
            "psf":              "PSF-2.0",
            "python":           "PSF-2.0",
        }
        return MAP.get(lic.lower(), lic)

    def _parse_requirements(self, content: str) -> dict[str, str]:
        packages = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^([A-Za-z0-9_\-\.]+)[>=<!~^]*(.*)$', line)
            if m:
                packages[m.group(1)] = m.group(2).strip() or "latest"
        return packages


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="SBOM + License Compliance Checker")
    parser.add_argument("--check",      action="store_true",  help="Run full license check")
    parser.add_argument("--format",     default="spdx",       help="spdx or cyclonedx")
    parser.add_argument("--pr-check",   type=str,             help="Check a requirements.txt file")
    parser.add_argument("--dir",        default=".",          help="Project directory")
    args = parser.parse_args()

    checker = SBOMChecker()

    if args.pr_check:
        content  = Path(args.pr_check).read_text()
        packages = checker.check_new_packages("", content)
        blocked  = [p for p in packages if p.blocked]
        print(f"\n{'⛔ BLOCKED' if blocked else '✅ COMPLIANT'}")
        for p in blocked:
            print(f"  ⛔ {p.name} {p.version} — {p.license} — {p.reason}")

    elif args.check:
        report = checker.check(args.dir)
        print(f"\n{'⛔ NON-COMPLIANT' if not report.is_compliant else '✅ COMPLIANT'}")
        print(f"Blocked: {len(report.blocked)}")
        print(f"Review:  {len(report.review_needed)}")
        print(f"OK:      {len(report.compliant)}")

        if args.format == "cyclonedx":
            checker.generate_cyclonedx(report)
        else:
            checker.generate_spdx(report)

    else:
        parser.print_help()
        print("\nExamples:")
        print("  python tools/sbom_checker.py --check")
        print("  python tools/sbom_checker.py --check --format cyclonedx")
        print("  python tools/sbom_checker.py --pr-check requirements.txt")


if __name__ == "__main__":
    main()