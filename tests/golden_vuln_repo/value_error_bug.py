# Golden fixture: int() on a string literal that isn't numeric.
# Expected: analyzers.value_error_checker fires.


def parse_count():
    return int("abc")
