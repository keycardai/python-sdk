## 0.2.0-keycardai-fastmcp (2026-04-27)


- feat(keycardai-fastmcp): rename keycardai-mcp-fastmcp via bridge package (ACC-233) (#102)
- * feat(keycardai-fastmcp): rename keycardai-mcp-fastmcp via bridge package (ACC-233)
- The current name carries a redundant -mcp suffix (FastMCP only speaks MCP,
so the protocol tag adds no information). Renames to keycardai-fastmcp per
the revised KEP, with keycardai-mcp-fastmcp preserved as a deprecation
bridge so the customer in production on the old name keeps working
indefinitely.
- What ships:
- * New keycardai-fastmcp package at packages/fastmcp/, full implementation
  under the keycardai.fastmcp namespace. Tests, examples, README move with
  the source. Wired into the workspace and the release.yml tag filter.
* Deprecated keycardai-mcp-fastmcp now depends only on keycardai-fastmcp
  and re-exports every public symbol at the original
  keycardai.mcp.integrations.fastmcp.* paths. Importing the top-level
  module emits a DeprecationWarning pointing at the canonical name.
* Bridge contract test (test_bridge.py, 4 tests) asserts the
  DeprecationWarning fires and that bridge symbols are identity-equal to
  the canonical ones. The full behavioral suite lives in keycardai-fastmcp
  going forward.
- Customer impact: pip install keycardai-mcp-fastmcp keeps working; the
package transitively pulls keycardai-fastmcp. No forced removal timeline,
the bridge ships until every known caller migrates.
- Verified: ruff clean. fastmcp 51/51, mcp-fastmcp bridge 4/4, mcp 560/560,
oauth 208/208, starlette 49/49.
- Supersedes the canceled ACC-195 (which used the now-rejected
keycardai-fastmcp-mcp name).
- * fix(keycardai-fastmcp): bridge re-exports the full canonical surface
- Review caught that the bridge provider.py only re-exported a hand-enumerated
subset of the canonical surface, dropping documented public symbols
(get_token_debug_info, introspect, INTROSPECT, AuthProviderConfigurationError,
AuthProviderInternalError, AuthProviderRemoteError). Importing any of those
from keycardai.mcp.integrations.fastmcp.provider raised ImportError, breaking
the bridge contract for downstream callers using less common symbols.
- Fixes:
- - Add __all__ to keycardai.fastmcp.provider listing the 28-name public
  surface. Stdlib/typing helpers (logging, os, urlparse, wraps, Any,
  Callable, etc.) are deliberately excluded.
- Replace the bridge provider.py hand-enumeration with
  ``from keycardai.fastmcp.provider import *``, plus a re-export of __all__
  so future symbol additions to the canonical module flow through
  automatically.
- Add test_bridge_provider_exposes_full_public_surface: iterates the
  canonical __all__, asserts every symbol is present at the bridge path
  and identity-equal to the canonical reference. Regression test for the
  symbol-drop class of bug.
- Scrub em dashes from the renamed example READMEs (pre-existing prose,
  but new file paths shipping under our review).
- Verified: fastmcp 51/51, mcp-fastmcp bridge 5/5 (was 4 + 1 new). Smoke:
the six previously-missing symbols now import cleanly from the old path.
- * ci: pin extractions/setup-just version
- The action stopped resolving "latest" sometime today and started failing
with `no release for just matching version specifier`. Pinning unblocks
PR validation and the post-merge bump-and-publish pipeline.
- 1.50.0 is the current stable just release (April 2026).
- * ci: replace extractions/setup-just with the upstream install script
- extractions/setup-just@v2 is currently broken for both unpinned and
explicit-version requests ("no release for just matching version
specifier"). Pinning to 1.50.0 did not help because the action regression
is in its release lookup, not its version resolution.
- Switch to the just.systems install script (the project owners ship and
maintain it). Runs as a plain bash step with no third-party action
dependency and is unaffected by setup-just regressions.
