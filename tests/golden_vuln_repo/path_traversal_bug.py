# Golden fixture: file path built from a caller-controlled parameter,
# joined with no traversal check.
# Expected: analyzers.path_traversal_checker fires.

import os


def read_file(base_dir, filename):
    path = os.path.join(base_dir, filename)
    return open(path).read()
