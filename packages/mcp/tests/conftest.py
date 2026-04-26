"""Pytest configuration and shared fixtures for mcp tests."""

# ---------------------------------------------------------------------------
# Block implicit ``.env`` discovery before any test module imports a transitive
# dependency that calls ``load_dotenv()`` at import time.
#
# Why this exists:
#   ``crewai`` (imported transitively by tests under
#   ``tests/keycardai/mcp/client/integrations/``) calls ``dotenv.load_dotenv()``
#   in ``crewai/llm.py`` and ``crewai/project/crew_base.py`` at module import
#   time. With no path argument, ``python-dotenv`` walks upward from the
#   *caller frame's* ``__file__`` (i.e. ``site-packages/crewai/...``) looking
#   for a ``.env``. On developer machines this routinely climbs out of the
#   checkout and into the user's home tree, picking up arbitrary ``.env``
#   files (e.g. ``~/Code/.../.env`` containing ``KEYCARD_ZONE_URL=...``).
#   Once those land in ``os.environ``, ``AuthProvider.__init__`` reads them as
#   defaults (``zone_url = zone_url or os.getenv("KEYCARD_ZONE_URL")``) and
#   silently overrides the values the test fixtures pass in, producing
#   confusing assertion mismatches that depend on the developer's filesystem.
#
# Fix:
#   Replace ``dotenv.load_dotenv`` and ``dotenv.main.load_dotenv`` with a
#   wrapper that short-circuits the no-argument case (the only one crewai
#   uses) and forwards explicit ``dotenv_path``/``stream`` calls to the real
#   implementation. This must run before crewai is imported -- importing it
#   here in the package-level conftest guarantees that, since pytest loads
#   conftest modules before collecting test files.
import dotenv as _dotenv
import dotenv.main as _dotenv_main

_real_load_dotenv = _dotenv_main.load_dotenv


def _block_implicit_load_dotenv(
    dotenv_path=None, stream=None, *args, **kwargs
):
    if dotenv_path is None and stream is None:
        return False
    return _real_load_dotenv(dotenv_path=dotenv_path, stream=stream, *args, **kwargs)


_dotenv.load_dotenv = _block_implicit_load_dotenv
_dotenv_main.load_dotenv = _block_implicit_load_dotenv

from .fixtures.auth_provider import *  # noqa: E402, F403, F401
