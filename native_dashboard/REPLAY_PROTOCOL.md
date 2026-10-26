# Recorded replay link

MATREX VR may open a separate Daari Deepa process on loopback port 8061. The ordinary analysis app and live viewer on 8060 keep their existing behavior. The recorded view opens at `/replay/#token=<owner nonce>`; it uses the same compiled native dataset and `TrajectoryRenderer`, with a faint complete path, bright past path and current heading cursor.

Unity owns the clock. The browser sends seek/play/pause requests and displays acknowledged Unity state. It does not run a competing playback timer or publish acquisition telemetry. Spatial pan/zoom stays local; Fit restores trajectory bounds. Loading errors require an explicit Reload rather than retrying expensive dataset compilation repeatedly.

A dedicated process starts with `--host 127.0.0.1 --port 8061 --replay-token <32–64 hex digits> --no-browser`. The launcher checks `/api/replay/capabilities` for `matrex.replay.v1`, version 1 and a nonce-derived instance ID. An old or unrelated process is never killed or replaced automatically.

All replay API routes require a loopback client and the `X-Matrex-Replay-Token` header. Cross-origin calls are rejected. Only the native owner may POST `/api/replay/state` without a browser Origin header. Its payload contains `sessionId`, `runDirectory`, `originTicks` (decimal string), `seconds`, `duration`, `playing`, `rate`, monotonically increasing `revision` and `ackCommand`. The response returns unacknowledged bounded commands. A new run or origin needs a new session identity.

Browser `/api/replay/command` accepts `sessionId`, unique `requestId`, `kind` (`seek`, `play`, `pause`) and finite in-range `seconds`. Commands are numbered, duplicate IDs are ignored and pending seeks coalesce. A disconnected owner cannot receive new requests. `/api/replay/snapshot` returns the last acknowledged state and connectivity age. `/api/replay/dataset?sessionId=...` compiles only the owner-selected saved run.

The binary adds `replayTime` relative to `replayTimeline.originTicks` in the recorded local-wall-clock tick domain. The browser subtracts the explicit Unity origin using integer arithmetic before converting the difference to seconds. This preserves scene gaps and segment start offsets. Existing segment-local `time`, `_seg_id` grouping, filters and statistics remain unchanged. The linked trajectory view is compiled/retained analysis data, with a 240,000-point drawing budget; it is not a claim that every raw camera frame or every raw log row is displayed. It never joins separate segments. Camera synchronization is a separate media-manifest contract.

Validation: the shared analysis/live regression suite, dedicated bridge ownership/acknowledgment tests, pure browser model tests, and an actual loopback HTTP smoke with a generated CSV. Native packaged UI rendering is validated by the MATREX integration build separately.
