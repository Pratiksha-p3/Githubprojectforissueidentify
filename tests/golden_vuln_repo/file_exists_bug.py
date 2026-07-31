# Golden fixture: open() with no guard against a missing file.
# Expected: analyzers.file_exists_checker fires on line 3.


def load_config(path):
    return open(path).read()
