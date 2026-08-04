# Golden fixture: hashlib.md5() for a security-relevant hash.
# Expected: analyzers.weak_crypto_checker fires.

import hashlib


def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()
