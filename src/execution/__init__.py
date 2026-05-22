"""Broker execution layer.

Defines a backend-agnostic Broker port plus implementations. The simulated
backend is the default; a Schwab backend will be added behind the same
interface so the agent-facing tool layer never sees broker-specific details.
"""
