# ADR-001: No Direct Database Calls in Controllers

Status: accepted
Date: 2024-01-15

## Context

Controllers are handling HTTP request/response lifecycle.
Mixing database logic into controllers creates tight coupling,
makes testing hard, and violates separation of concerns.

## Decision

- Controllers MUST NOT contain direct database queries
- All database access MUST go through the service layer
- Service classes MUST be in the `services/` directory
- NEVER call `db.execute()`, `conn.cursor()`, or ORM queries directly in views/controllers

## Consequences

- All controllers import from services/, never from models/ directly
- Database migrations do not affect controller logic
- Controllers are testable without a real database
