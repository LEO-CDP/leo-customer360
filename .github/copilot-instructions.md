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

- Be concise and technical, only output modified code when refactoring.
- Reuse existing patterns before introducing new ones.
- Ask one clarifying question if requirements are ambiguous.
- Do not add new libraries or frameworks unless requested.
- Always follow the established coding conventions and patterns of the project.

## graphify

For any question about this repo's architecture, structure, components, or how to add/modify/find
code, your first action should be `graphify query "<question>"` when `graphify-out/graph.json`
exists. Use `graphify path "<A>" "<B>"` for relationship questions and `graphify explain "<concept>"`
for focused-concept questions. These return a scoped subgraph, usually much smaller than the full
report or raw grep output.

Triggers: "how do I…", "where is…", "what does … do", "add/modify a <component>",
"explain the architecture", or anything that depends on how files or classes relate.

If `graphify-out/wiki/index.md` exists, use it for broad navigation. Read `graphify-out/GRAPH_REPORT.md`
only for broad architecture review or when query/path/explain do not surface enough context. Only read
source files when (a) modifying/debugging specific code, (b) the graph lacks the needed detail, or
(c) the graph is missing or stale.

Type `/graphify` in Copilot Chat to build or update the graph.