# Golden fixture: assert on a literal that's always falsy.
# Expected: analyzers.assertion_checker fires.


def check():
    assert False
