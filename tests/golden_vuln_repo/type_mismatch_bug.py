# Golden fixture: string literal added to an int literal.
# Expected: analyzers.type_mismatch_checker fires.


def describe(count):
    return "count: " + 5
