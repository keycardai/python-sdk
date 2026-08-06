# MCP release lines

`keycardai-mcp` tracks the upstream `mcp` package's major version, so its major
version tells you which protocol SDK generation it pairs with:

| keycardai-mcp | pairs with | released from |
|---|---|---|
| `1.x` | `mcp >= 1.28, < 2.0` | `release/mcp-v1` |
| `2.x` | `mcp >= 2.0, < 3.0` | `main` |

PyPI has no dist-tags, so nothing here changes how versions are published; the
dependency ranges above are what steer resolvers to the right line. Users stay
on 1.x with `keycardai-mcp<2`, which is also what dependents that still pin
`mcp<2.0` (fastmcp 3.x, and the agent frameworks) resolve to naturally.

## Branches

- `main` is the 2.x line. Normal development happens here.
- `release/mcp-v1` publishes fixes for the 1.x line. It receives
  security/critical fixes only, no features. To land a fix there,
  cherry-pick it into a PR targeting `release/mcp-v1`; the merge-to-branch
  workflow auto-bumps and publishes exactly as `main` does. Only
  `keycardai-mcp` bumps from this branch — the other packages release from
  `main` regardless of line. (This differs from typescript-sdk, where the
  aggregate `@keycardai/sdk` also releases from the maintenance branch; the
  Python root `keycardai` package does not depend on `keycardai-mcp`.)

## Forced increments

Use the **Bump Package Version** workflow (workflow_dispatch) when a forced
increment is required — deliberate majors, or establishing a new release line.
`auto` derives the increment from conventional commits as usual.

| input | maintenance release | major cut |
|---|---|---|
| `package_name` | `keycardai-mcp` | `keycardai-mcp` |
| `package_dir` | `packages/mcp` | `packages/mcp` |
| `target_branch` | `release/mcp-v1` | `main` |
| `increment` | `auto` | `major` |

## Establishing the lines (one-time sequence)

1. Release `keycardai-mcp@1.0.0` from `main` (forced `major`) while `main`
   still carries the `mcp<2.0` constraint.
2. Create `release/mcp-v1` at the 1.0.0 release commit.
3. Merge the MCP 2.0 cutover into `main`; its `feat!` commit makes cz cut
   `2.0.0` automatically.
4. Update `packages/fastmcp`'s `keycardai-mcp` constraint to `>=1,<2` in the
   same window (it pairs with fastmcp 3.x / `mcp<2.0` until the fastmcp 4.x
   bump).

Do not create the maintenance branch before 1.0.0 exists; the branch must
contain the 1.0.0 tag's commit so cz derives 1.x patches from it.
