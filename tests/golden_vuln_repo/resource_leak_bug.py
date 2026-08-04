# Golden fixture: open() result never closed, returned, or passed elsewhere.
# Expected: analyzers.resource_leak_checker fires.


def load_session(file_name):
    file = open(file_name, "rb")
    return file.read()
