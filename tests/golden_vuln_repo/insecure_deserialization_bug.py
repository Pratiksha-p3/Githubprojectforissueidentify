# Golden fixture: pickle.load() on untrusted input.
# Expected: analyzers.insecure_deserialization_checker fires.

import pickle


def load_session(file_handle):
    return pickle.load(file_handle)
