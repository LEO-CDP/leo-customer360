"""AI provider abstraction for email-template generation, plus the content
safety checks run over generated drafts (see base.py).

Not database-facing (no SQLAlchemy Session dependency), so kept as a
sibling package to routers/schemas/repositories/models rather than inside
any of them -- see specs/001-ai-email-template-authoring/research.md §2.
"""
