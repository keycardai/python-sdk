## 0.5.0-keycardai-fastmcp (2026-07-21)


- feat(keycardai-fastmcp): grant-as-dependency typed injection (deprecate get_state) (#204)
- * feat(keycardai-fastmcp): grant-as-dependency typed injection via fastmcp.dependencies
- AuthProvider.grant() now returns a GrantDependency, a fastmcp.dependencies
Dependency subclass. Declared as a typed parameter default
(access: AccessContext = auth_provider.grant(...)), FastMCP resolves it per
request: __aenter__ reads the caller token, performs the RFC 8693 exchanges,
and injects the populated AccessContext, hidden from the tool input schema.
Errors are recorded on the AccessContext, never raised, preserving the
existing error-capture contract.
- GrantDependency.__call__ keeps the @auth_provider.grant(...) decorator
spelling working from the same object. The decorator now injects into a
declared AccessContext parameter (hidden from the wrapper signature) and
emits a decoration-time DeprecationWarning, once per tool, when no
AccessContext parameter is declared; it keeps dual-writing
set_state("keycardai") during the deprecation window.
- Also:
- AccessContext.from_context(ctx) escape hatch for helpers called from
  inside tools that only hold the FastMCP Context.
- Fix the Context parameter check: resolve string annotations via
  get_type_hints and match Context inside unions (Context | None) and
  Annotated forms; a declared AccessContext parameter removes the
  ctx: Context requirement entirely.
- override_access_context(): public seam that forces grant resolution to a
  caller-supplied AccessContext (used by the testing utilities).
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- * refactor(keycardai-fastmcp): move mock_access_context onto the public override seam
- mock_access_context previously patched module internals
(keycardai.fastmcp.provider.AccessContext and get_access_token). It now
builds a real AccessContext, preloaded with the requested tokens or error
state, and installs it through the public override_access_context() seam,
so grant resolution short-circuits on both the injected-parameter and
decorator paths without any patching.
- The yielded object is now a real AccessContext (subclassed only to serve a
default token for arbitrary resources), so assertions exercise the same
error paths as production code. override_access_context is re-exported
from keycardai.fastmcp.testing for tests that want to build the
AccessContext themselves.
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- * docs(keycardai-fastmcp): flip README and examples to the injected-parameter grant form
- The README quick start, package docstrings, and both examples now declare
grants as typed AccessContext parameter defaults instead of the deprecated
decorator + ctx.get_state("keycardai") pattern. Adds README sections on
error capture, the from_context escape hatch, and the mock_access_context
testing utility.
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- * build(keycardai-fastmcp): raise fastmcp floor to >=3.1.0 for the Dependency extension point
- GrantDependency subclasses fastmcp.dependencies.Dependency. The
fastmcp.dependencies module exists since 3.0.0, but it only exports the
Dependency base class (via the uncalled-for DI engine) starting with
fastmcp 3.1.0; on 3.0.x the module exports Depends only, so
keycardai-fastmcp would fail to import. Raise the manifest floor in the
package and both examples accordingly (verified against the published
3.0.0/3.0.2 vs 3.1.0 wheels on PyPI).
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- * test(keycardai-fastmcp): end-to-end grant injection through a real FastMCP server
- Adds a round trip through FastMCP's in-memory client verifying the
injected AccessContext parameter is resolved per request and excluded
from the tool input schema. Ignores flake8-bugbear B008 in tests and
examples: auth_provider.grant(...) in a parameter default is the
intended injection spelling, matching how FastAPI treats Depends().
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- * refactor(keycardai-fastmcp): keep grant and AccessContext as the only primary nouns
- GrantDependency stays exported for type annotations but is documented as
the return type of AuthProvider.grant(), not a concept to learn.
override_access_context moves out of the package root: it is a testing
seam and lives in keycardai.fastmcp.testing only.
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- * docs(keycardai-fastmcp): document contracts surfaced by review
- All-or-nothing multi-resource grant behavior is now stated on
GrantDependency and _build_access_context. The mock's bare-token form
answers for any resource; the docstring and README steer strict tests
to resource_tokens. README gains a migration note covering warning
escalation (-W error) on the deprecated decorator path and a B008
per-file-ignore example for downstream linters. The string-annotation
fallback in _annotation_matches carries a comment on its narrow
false-positive window.
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- * fix(keycardai-fastmcp): type grant() as Any so both call idioms type-check
- A GrantDependency return annotation makes the documented parameter
default (access: AccessContext = auth_provider.grant(...)) fail type
checking, and AccessContext breaks the still-supported decorator form.
Any is the FastAPI Depends() convention: the parameter annotation
carries the type for the injected form and the decorator form stays
callable. Narrow to AccessContext when the decorator path is removed.
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- ---------
- Co-authored-by: GitHub Action <action@github.com>
Co-authored-by: Claude Fable 5 <noreply@anthropic.com>

## 0.4.0-keycardai-fastmcp (2026-06-02)


- refactor(keycardai-fastmcp): use generic scope in request_scopes examples
- Replace the databricks-specific scope with a generic "read" in the
grant() docstring and tests so the example is not tied to one provider.
- feat(keycardai-fastmcp): add request_scopes to grant()
- Forward an optional per-resource OAuth scope into the RFC 8693 token
exchange so scope-gated Keycard delegation policies can match. Accepts a
str, list[str], or per-resource dict; distinct from inbound
required_scopes. Sets scope on the request in both the application
credential and basic branches.

## 0.3.0-keycardai-fastmcp (2026-05-20)


- fix(keycardai-fastmcp): trigger version bump for audience and wif key_id fixes

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
