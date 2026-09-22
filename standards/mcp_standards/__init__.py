"""Reusable cross-project MCP and Claude Code integration standards."""

__version__ = "1.0.0"

from .claude_code import (
    CandidateDiagnostic,
    ClaudeDiscoveryError,
    ClaudeInvocation,
    DiscoveryResult,
    ProbeResult,
    discover_claude,
    invoke_claude,
)
from .registration import (
    RegistrationError,
    is_missing_user_entry,
    register_user_mcp,
    unregister_user_mcp,
)
from .stdio import SmokeError, SmokeReport, smoke_stdio

__all__ = [
    "__version__",
    "CandidateDiagnostic",
    "ClaudeDiscoveryError",
    "ClaudeInvocation",
    "DiscoveryResult",
    "ProbeResult",
    "RegistrationError",
    "SmokeError",
    "SmokeReport",
    "discover_claude",
    "invoke_claude",
    "is_missing_user_entry",
    "register_user_mcp",
    "smoke_stdio",
    "unregister_user_mcp",
]
