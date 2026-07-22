# ADR-005: Error Handling Policy

Status: accepted
Date: 2024-01-15

## Context

Bare except clauses hide bugs and make debugging impossible.
We need consistent error handling across the codebase.

## Decision

- NEVER use bare `except:` clauses
- ALWAYS catch specific exception types
- ALWAYS log exceptions with context
- NEVER swallow exceptions silently
- Use `except Exception as e: logger.error(f"Context: {e}")`

## Consequences

- All error handling is explicit and logged
- Bugs surface immediately rather than being hidden
