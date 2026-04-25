"""Local-first integrations that bypass the Smithery gateway.

Modules here implement inbound channels (e.g. Telegram DM bot) that deliver
user messages to the agent and surface approval prompts. They share the
AgentSession plumbing via `api.stream.build_agent_session` and key sessions
on `Conversation.id` so the existing `/approve` endpoint keeps working.
"""
