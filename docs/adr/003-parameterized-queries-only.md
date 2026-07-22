# ADR-003: Parameterized Queries Only

Status: accepted
Date: 2024-01-15

## Context

SQL injection is the #1 web vulnerability.
String formatting in SQL queries is never acceptable.

## Decision

- ALL database queries MUST use parameterized queries
- NEVER use f-strings, .format(), or % in SQL strings
- Use cursor.execute(query, (params,)) always
- ORM queries are acceptable and preferred

## Consequences

- No f-string SQL anywhere in the codebase
- Code review rejects any string-formatted SQL
