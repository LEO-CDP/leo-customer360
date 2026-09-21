"""AI provider abstraction + campaign-planning logic for the AI Agent service.

Provider-agnostic (Gemini / OpenAI / Anthropic Claude / any OpenAI-compatible
local LLM) selected by config -- see config.py. All providers run through one
SDK, LiteLLM (see base.complete_with_litellm). Not database-facing; moved out
of customer360-api's core/ai_providers so the agent runs as a standalone service.
"""
