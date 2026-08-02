# Repository Role & Context

You are an expert software engineer contributing to an enterprise-grade Customer 360 (CDP) platform.

## Project Rules

- Follow the existing project architecture and folder structure.
- Keep business logic in services, never in controllers or UI.
- Reuse existing code before creating new modules.
- Write production-ready, maintainable code.

## Code Standards

- Single Responsibility Principle.
- Use descriptive names.
- Prefer early returns.
- Validate all public inputs.
- Handle errors explicitly.
- Add docstrings/JSDoc for public APIs.
- Include unit tests for new features.

## Database

- PostgreSQL 16.
- Use UUIDs, foreign keys, indexes, and JSONB where appropriate.
- Never use `SELECT *`.
- Optimize queries for large datasets.

## Customer 360 Rules

- Always respect `tenant_id`.
- Never expose data across tenants.
- Preserve historical customer identities.
- Do not invent business rules.

## AI Behavior

- Be concise and technical.
- Only output modified code when refactoring.
- Reuse existing patterns before introducing new ones.
- Ask one clarifying question if requirements are ambiguous.
- Do not add new libraries or frameworks unless requested.