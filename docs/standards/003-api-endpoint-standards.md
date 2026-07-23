# Public API Endpoint Standards

## Rules

- ALL public-facing endpoints MUST validate and sanitize input before processing.
- NEVER return raw internal exception messages or stack traces in an API response.
- ALL endpoints that accept file uploads MUST enforce a maximum file size and type allowlist.
- SHOULD apply rate limiting to any endpoint that triggers an expensive operation (LLM call, DB write, external API call).
