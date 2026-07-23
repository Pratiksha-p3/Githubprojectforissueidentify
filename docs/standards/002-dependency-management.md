# Dependency Management

## Rules

- ALL new third-party dependencies MUST be pinned to an exact version in requirements.txt.
- NEVER add a dependency with a known critical CVE without an approved exception.
- MUST NOT vendor a package's source directly into the repo instead of using the package manager.
- New dependencies SHOULD prefer actively maintained packages (a commit within the last 12 months).
