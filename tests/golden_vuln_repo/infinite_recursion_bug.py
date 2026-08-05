# Golden fixture: a function that calls itself with no base case.
# Expected: analyzers.infinite_recursion_checker fires.


def recurse():
    recurse()
