"""Ad personalisation engine for the Customer 360 Promotions API.

Business logic only: no HTTP handlers and no SQL. Persistence lives in
``repository/`` and HTTP wiring in ``core/application.py``.

Design notes: specs/003-ad-personalization-engine/plan.md and research.md.
"""
