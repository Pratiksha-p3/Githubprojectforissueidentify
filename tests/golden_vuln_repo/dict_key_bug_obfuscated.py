# Adversarial variant of dict_key_bug.py: same bug shape (unguarded dict
# access on a parameter), but renamed identifiers and wrapped in an extra
# layer of logic, to confirm the checker catches the *pattern*, not a
# literal match against the simple fixture.
# Expected: analyzers.dict_key_checker still fires.


def process_webhook_event(event_data):
    kind = event_data["event_type"]
    if kind == "push":
        return f"push event: {event_data['event_type']}"
    return "unknown"
