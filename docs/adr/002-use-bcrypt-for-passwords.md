# ADR-002: Use bcrypt for Password Hashing

Status: accepted
Date: 2024-01-15

## Context

MD5 and SHA1 are cryptographically broken for password storage.
We need a consistent, secure approach across the codebase.

## Decision

- ALL password hashing MUST use bcrypt
- NEVER use MD5, SHA1, or SHA256 for password storage
- NEVER store passwords in plaintext
- Use `bcrypt.hashpw(password.encode(), bcrypt.gensalt())`
- Minimum work factor: 12

## Consequences

- All authentication code uses bcrypt
- Password comparison uses bcrypt.checkpw()
- hashlib MUST NOT be used for passwords
