# Golden fixture: zip extraction with no member-path validation.
# Expected: analyzers.zip_slip_checker fires.

import zipfile


def extract(path, dest):
    with zipfile.ZipFile(path) as zf:
        zf.extractall(dest)
