# Golden fixture: an import never referenced anywhere in the file.
# Expected: analyzers.unused_import_checker fires.

import os


def greet():
    return "hello"
