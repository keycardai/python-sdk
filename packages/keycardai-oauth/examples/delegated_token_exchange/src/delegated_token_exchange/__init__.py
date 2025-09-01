"""
Google Calendar MCP Server with Delegated Token Exchange

This package demonstrates how to build a real-world MCP server that uses the 
KeyCard OAuth SDK for delegated token exchange to access Google Calendar 
on behalf of authenticated users.

Example Usage:
    python -m delegated_token_exchange.server
    
    Or using the installed script:
    mcp-calendar-server
"""

__version__ = "0.1.0"
__author__ = "KeyCard AI"
__email__ = "hello@keycard.ai"

from .server import main

__all__ = ["main"]
