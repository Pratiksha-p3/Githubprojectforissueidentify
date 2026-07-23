# Logging and PII Handling

## Rules

- NEVER log full request/response bodies that may contain PII (email, phone, SSN, address, DOB).
- ALWAYS redact or hash user identifiers before logging (log a user_id, not an email).
- MUST NOT log raw authentication tokens, passwords, or API keys, even at debug level.
- SHOULD use structured logging (key=value) instead of free-text string concatenation.
