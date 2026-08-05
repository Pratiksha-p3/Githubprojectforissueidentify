"""
src/core/remediation_guide.py

A static reference: for every error/vulnerability category this project
has been asked about, the general, correct way to resolve it -- not tied
to any specific checker's output. This exists for the categories this
project's 17 deterministic checkers (src/analyzers/registry.py) do NOT
detect: knowing the fix and being able to safely automate it into a
zero-review AST rewrite are different bars, and most of this catalog
doesn't clear the second one (see each checker's own docstring for why,
where one exists). Rather than leave those categories with nothing at
all, `review-cli explain <name>` (src/cli/explain.py) surfaces the real
remediation guidance on demand, so "not auto-fixed" doesn't mean "no
guidance available."

Entries are intentionally general (how you'd fix ANY instance of this
category in Python), not code-generation templates -- the whole reason
most of these aren't auto-fixed by a checker is that the correct fix
depends on context this project has no way to infer.
"""
from __future__ import annotations

REMEDIATION_GUIDE: dict[str, str] = {
    "SyntaxError": (
        "Fix depends on the parser's exact message and line/column: insert a "
        "missing ':', close an unmatched bracket, or correct whatever token the "
        "error points at. review-cli's own missing-colon and misaligned-indent "
        "fixes (src/core/orchestrator.py) automate the two most common shapes."
    ),
    "IndentationError": (
        "Align the line to the correct block level -- one level (4 spaces) "
        "deeper than the enclosing if/def/for/while/class/try header, or "
        "dedent to match a real enclosing block's own level."
    ),
    "NameError": (
        "Define the missing name, fix a typo in it, add the import that "
        "should have provided it, or fix the scope it's used in (defined in "
        "one function/module but referenced from another)."
    ),
    "UnboundLocalError": (
        "Move the assignment before the first read, or rename the local so "
        "it no longer shadows the name you actually meant to reference. This "
        "happens because Python treats a name as local to a function's ENTIRE "
        "body the moment it's assigned anywhere in that function, even below "
        "the line that reads it -- the earlier read isn't finding a truly "
        "undefined name, just a local that hasn't been given a value yet."
    ),
    "TypeError": (
        "Convert or validate the argument's type before the operation/call, "
        "or fix the call site to pass the type/arity the function actually "
        "expects."
    ),
    "ValueError": (
        "Validate the value is in the expected domain/format before using it "
        "(e.g. confirm a string is numeric before int()), or catch it at the "
        "boundary where untrusted input enters the function."
    ),
    "IndexError": (
        "Check len(sequence) (or the specific bound) before indexing, or use "
        "a safe-access pattern (slicing, next(iter(seq), default)) instead of "
        "a bare literal index."
    ),
    "KeyError": (
        "Use dict.get(key, default) instead of dict[key], or check "
        "`if key in d:` before subscripting."
    ),
    "AttributeError": (
        "Guard against None (or the wrong type) before the attribute access "
        "-- an `is None` check right after any call that can return None is "
        "the most common fix -- or correct what's actually being passed in."
    ),
    "ImportError": (
        "Install the missing package, fix a typo in the import path, or "
        "restructure to break a circular import."
    ),
    "ModuleNotFoundError": (
        "Same as ImportError -- install the package, fix the module path, or "
        "confirm it's actually available in this environment."
    ),
    "FileNotFoundError": (
        "Check os.path.exists()/os.path.isfile() before opening, or wrap the "
        "open() in try/except FileNotFoundError and handle the missing-file "
        "case explicitly."
    ),
    "PermissionError": (
        "Catch it explicitly around the filesystem call and handle a denied "
        "write/read (fall back to another location, surface a clear error) "
        "rather than letting it propagate unhandled; verify the target path "
        "is actually writable/readable by the running process before relying "
        "on it."
    ),
    "ZeroDivisionError": (
        "Guard the denominator: `if denom == 0: <handle>` before dividing "
        "(or `if len(seq) == 0:` when dividing by a sequence's length)."
    ),
    "RuntimeError": (
        "Generic -- the fix is entirely specific to the message (e.g. "
        "\"dictionary changed size during iteration\" -> iterate over a copy "
        "of the keys/items instead of the live container)."
    ),
    "RecursionError": (
        "Add or fix a missing base case so recursion actually terminates; "
        "convert to an iterative implementation if the depth is inherently "
        "unbounded by input size."
    ),
    "MemoryError": (
        "Stream or chunk the data instead of loading it all at once -- "
        "generators, iterators, or paginated processing instead of building "
        "one huge in-memory structure."
    ),
    "OverflowError": (
        "Use a numeric type/library that supports the needed range, or add "
        "an explicit bound/clamp on the computation before it's performed."
    ),
    "AssertionError": (
        "Fix the underlying condition that's actually false, or remove an "
        "assertion that doesn't hold in a legitimate case it's currently "
        "rejecting."
    ),
    "NotImplementedError": (
        "Implement the method/function that's still a stub, or handle the "
        "case at the call site instead of reaching the stub at all."
    ),
    "TimeoutError": (
        "Set an explicit, reasonable timeout on the operation; retry with "
        "backoff if transient; investigate why the call is slow rather than "
        "just raising the timeout value."
    ),
    "ConnectionError": (
        "Retry with backoff for transient failures, check the remote "
        "service's actual availability, and handle the unavailable case "
        "explicitly rather than letting it crash the caller."
    ),
    "OSError": (
        "Catch the specific OS-level failure (disk full, invalid path, "
        "device unavailable, ...) and respond to it explicitly -- don't "
        "assume a filesystem/OS call will always succeed."
    ),
    "SQL Injection": (
        "Use parameterized queries / prepared statements "
        '(cursor.execute("... WHERE id = ?", (user_id,))) -- never build a '
        "query by formatting or concatenating untrusted input into the SQL "
        "string itself."
    ),
    "Command Injection": (
        "Don't use shell=True with a dynamically-built command; pass the "
        "command and its arguments as a list so the shell never re-parses "
        "them. If shell=True is unavoidable, shlex.quote every "
        "externally-influenced piece."
    ),
    "Path Traversal": (
        "Resolve the final path (os.path.realpath) and verify it's still "
        "inside the intended base directory before opening/writing it -- "
        "os.path.join() does not strip '..' segments on its own."
    ),
    "Zip Slip": (
        "Before extracting, resolve each archive member's target path and "
        "verify it stays inside the destination directory -- never call "
        "extractall()/extract() on an archive from an untrusted source "
        "without validating member paths first."
    ),
    "Cross-Site Scripting (XSS)": (
        "Auto-escape any user-supplied value before rendering it into HTML "
        "(use your template engine's default escaping -- e.g. Jinja2's "
        "autoescape -- rather than building HTML via raw string "
        "concatenation)."
    ),
    "Cross-Site Request Forgery (CSRF)": (
        "Require an anti-CSRF token on every state-changing request, and set "
        "session cookies to SameSite=Lax or Strict."
    ),
    "Hardcoded Credentials": (
        "Move the value to an environment variable or a secrets manager "
        "(Vault, AWS/Azure/GCP secrets) and read it at runtime -- never commit "
        "a real credential to source control."
    ),
    "Weak Cryptography": (
        "Use a modern, vetted algorithm: SHA-256 or better for general "
        "hashing, bcrypt/scrypt/argon2 specifically for password hashing "
        "(never a general-purpose digest, sha256 included), AES-GCM for "
        "symmetric encryption."
    ),
    "Insecure Deserialization": (
        "Avoid pickle (or an unrestricted yaml.load) on data from any source "
        "you don't fully trust -- use a data-only format (JSON) instead, or "
        "restrict the loader (yaml.safe_load, a custom Unpickler allowlist)."
    ),
    "Server-Side Request Forgery (SSRF)": (
        "Allowlist the destination host/IP server-side before making the "
        "request on the caller's behalf, and explicitly block internal/"
        "loopback/link-local address ranges."
    ),
    "Sensitive Data Exposure": (
        "Encrypt sensitive data at rest and in transit, never log secrets or "
        "full sensitive payloads, and minimize how long it's retained."
    ),
    "Broken Access Control": (
        "Enforce authorization server-side on every request that touches "
        "protected data or actions -- a UI-level check alone is not "
        "enforcement, since it can always be bypassed by calling the API "
        "directly."
    ),
    "Race Condition": (
        "Protect the shared mutable state with a proper lock/mutex, or "
        "restructure to use an atomic operation instead of a "
        "check-then-act sequence that another thread/process can interleave "
        "with."
    ),
    "Directory Traversal": (
        "Same remediation as Path Traversal -- resolve and verify the final "
        "path stays inside the intended directory before use."
    ),
    "Arbitrary File Write": (
        "Validate and constrain the destination path the same way as Path "
        "Traversal, and never let caller-controlled input choose the target "
        "path unconstrained."
    ),
    "Arbitrary File Read": (
        "Same constraint as Arbitrary File Write, applied to the path being "
        "read instead of written."
    ),
    "Arbitrary File Delete": (
        "Same constraint again, applied to a delete operation -- these are "
        "usually even higher severity, since there's no way to recover the "
        "file afterward."
    ),
    "Logic Error": (
        "Requires knowing the intended behavior -- there's no generic fix; "
        "trace the actual vs. expected output for a concrete input and "
        "correct the faulty condition/calculation/return value."
    ),
    "Resource Leak": (
        "Use a context manager (`with open(...) as f:`) so the resource is "
        "closed automatically even if an exception is raised partway "
        "through, instead of an explicit .close() that can be skipped."
    ),
    "Dead Code": (
        "Delete it. If it's genuinely unreachable, keeping it only adds "
        "confusion and maintenance cost."
    ),
    "Duplicate Code": (
        "Extract the shared logic into one function/method and have both "
        "call sites use it, rather than keeping two copies that can drift "
        "out of sync."
    ),
    "Unused Variable": (
        "Delete it, or if it's intentionally unused (e.g. a loop variable "
        "you don't need), rename it to `_` to make that explicit."
    ),
    "Unused Import": "Delete it.",
    "Missing Input Validation": (
        "Validate type, range, and format at every trust boundary (function "
        "arguments from external callers, request bodies, file contents) "
        "before using the value, not after."
    ),
    "Missing Exception Handling": (
        "Wrap the specific risky operation in try/except naming the actual "
        "exception type you expect, with real recovery or a clear re-raise -- "
        "not a bare `except:` that swallows everything indiscriminately."
    ),
    "Infinite Loop": (
        "Verify the loop's exit condition is actually reachable from every "
        "code path inside the loop body -- a common cause is forgetting to "
        "update the loop variable on some branch."
    ),
    "Performance Bottleneck": (
        "Profile first to find the actual hot path -- don't guess. Then fix "
        "the real cause: algorithmic complexity, an avoidable N+1 query/call "
        "pattern, missing caching, or unnecessary repeated work."
    ),
    "Concurrency Issue": (
        "Identify the specific shared state being accessed unsafely across "
        "threads/processes/coroutines and protect it with the appropriate "
        "primitive (lock, queue, atomic operation) -- there's no one generic "
        "fix; it depends on exactly what's being shared and how."
    ),
    "Thread Safety Issue": (
        "Same as Concurrency Issue -- protect the specific shared mutable "
        "state with a lock, or redesign so each thread owns its own copy "
        "instead of sharing it."
    ),
}


def get_remediation(name: str) -> str | None:
    """Case-insensitive lookup. Returns None if `name` isn't in the
    catalog -- callers decide how to present that (src/cli/explain.py
    lists close/available names rather than failing silently)."""
    for key, guidance in REMEDIATION_GUIDE.items():
        if key.lower() == name.lower():
            return guidance
    return None


# Maps a checker's Finding.source -> the catalog key that describes the
# general category of bug it detects -- used to attach real remediation
# guidance to a posted GitHub comment (src/cli/review_pr.py's
# post_no_fix_comments()/post_fix_suggestions(),
# src/integrations/publisher.py's summary comment), not just the
# checker's own per-finding message. undefined_name_checker is
# deliberately absent here -- it reports two different exceptions
# (NameError vs UnboundLocalError) distinguished only in the finding's
# own message text, so it's special-cased in
# get_remediation_for_finding() below instead of a fixed mapping.
_CHECKER_SOURCE_TO_CATEGORY: dict[str, str] = {
    "dict_key_checker": "KeyError",
    "division_guard_checker": "ZeroDivisionError",
    "file_exists_checker": "FileNotFoundError",
    "unstored_constructor_param_checker": "AttributeError",
    "http_timeout_checker": "TimeoutError",
    "hardcoded_secret_checker": "Hardcoded Credentials",
    "sql_injection_checker": "SQL Injection",
    "command_injection_checker": "Command Injection",
    "unsafe_yaml_checker": "Insecure Deserialization",
    "resource_leak_checker": "Resource Leak",
    "index_guard_checker": "IndexError",
    "none_attribute_checker": "AttributeError",
    "weak_crypto_checker": "Weak Cryptography",
    "insecure_deserialization_checker": "Insecure Deserialization",
    "path_traversal_checker": "Path Traversal",
    "zip_slip_checker": "Zip Slip",
    "unused_import_checker": "Unused Import",
    "type_mismatch_checker": "TypeError",
    "invalid_method_checker": "AttributeError",
    "value_error_checker": "ValueError",
    "assertion_checker": "AssertionError",
    "infinite_recursion_checker": "RecursionError",
}


def get_remediation_for_finding(source: str, message: str) -> str | None:
    """Looks up remediation guidance for a finding by its checker
    source (Finding.source) -- None if that source has no catalog entry
    (e.g. an LLM-sourced finding, or a checker whose bug class doesn't
    map cleanly onto one category). `message` is only consulted for
    undefined_name_checker, whose own message text already says which
    of the two exceptions applies."""
    if source == "undefined_name_checker":
        key = "UnboundLocalError" if "UnboundLocalError" in message else "NameError"
        return get_remediation(key)
    category = _CHECKER_SOURCE_TO_CATEGORY.get(source)
    if category is None:
        return None
    return get_remediation(category)
