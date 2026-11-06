# Monitor implementation results

Implemented on `codex/refinery-monitor`, forked from main at `2fbf0c7` in a
managed worktree. The main checkout and ongoing migration work were not changed.

## Delivered

- Bundled React Flow dashboard, local `refinery monitor` command and SSE transport.
- Profile and migration journals, historical topology, invocation correlation,
  individual visits, activity timeline, recipe totals and error/status panels.
- Confined state reads, reconnect replay, corruption diagnostics and graceful
  shutdown with an open event stream.
- Production assets and third-party notices included in the Python distribution.

## Verification

- Full Python suite: 444 passed, 1 skipped.
- Frontend: 8 tests passed; TypeScript and production build passed.
- Mypy and targeted Ruff passed; Git diff whitespace checks passed.
- Wheel installed into a clean temporary environment; UI, API, Last-Event-ID
  stream and SIGINT shutdown passed without Node at runtime.
- Browser verified parent/child navigation, node details, profile child totals,
  stable graph display and narrow-screen overflow; console had no warnings/errors.
- One independent review; all Important findings fixed with reproducing tests.

## Decisions and tradeoffs

1. Tokens/cost remain unavailable until reliable local usage records exist,
   as allowed by the approved design. No fabricated historical totals.
2. Profile graph builders accept an absent runner for effect-free topology
   inspection, matching migration builders. This widens the internal type;
   execution nodes still require a real runner.
3. Mocked AI repair tests need a non-secret placeholder credential for client
   initialization. No real API credential or live model request was used.
4. Unmatched starts retain unknown worker liveness. A connected browser stream
   is not proof of a running worker; there is no verified running animation yet.
5. The independent review covered code correctness, not a dependency/license
   completeness audit. Package notices are generated, but are not an audit.
6. Packaging and browser verification were performed by the implementer; the
   reviewer inspected configuration/source rather than duplicating those checks.
   Those checks therefore do not have an independent second observer.

## Deferred minor

A resumed child migration remains associated with its original parent execution
visit rather than every later recovery visit. The child run remains navigable
from the parent and preserves its execution history.
