# ADR-004: Environment Variables for All Secrets

Status: accepted
Date: 2024-01-15

## Context

Hardcoded secrets in code get committed to version control
and can be exposed in logs, errors, and history.

## Decision

- ALL secrets MUST be stored in environment variables
- NEVER hardcode API keys, passwords, tokens, or connection strings
- Use os.getenv() or python-dotenv
- Secret names MUST be in UPPER_SNAKE_CASE
- NEVER commit .env files to version control

## Consequences

- All secrets loaded via os.getenv() at startup
- .env is in .gitignore
- CI/CD uses secret management (GitHub Secrets, Vault)
