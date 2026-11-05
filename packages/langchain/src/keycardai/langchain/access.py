"""Factories for the identity a run acts under.

`Access` lives in keycardai-oauth (`keycardai.oauth.server.agent_grant`) and is
re-exported here so `from keycardai.langchain.access import Access` keeps working.
"""

from keycardai.oauth.server.agent_grant import Access

__all__ = ["Access"]
