# Live experiment monitoring

This current-source addition is independent of the recorded-data numerical
pipeline. It does not alter `_seg_id = SourceFile + CurrentTrial + CurrentStep`,
filters, occupancy, statistics or saved-view semantics.

## Transport

The server owns one ZeroMQ SUB on a worker thread. Default input is an
operator-selected TCP endpoint and topic `matrex.telemetry.v2`. Unity sends
two multipart UTF-8 frames: exact topic, then JSON. Snapshots have
`schemaVersion: 2` and a `rigs` array. Current Unity source additionally sends
`schemaVersion: 1`, `kind: "matrex.context"` on the exact `.context` topic.
The prefix subscription is filtered to these two exact topics.

Context repeats every two seconds by default, independently of snapshot
frequency. It includes the run/session, scene, trial, block, config reference,
scene-unit scale and animal/species/backend metadata. The dashboard also uses
the lightweight snapshot `experimentContext` while waiting for a heartbeat.
Older MATREX 0.3 snapshots remain accepted; no heartbeat is invented for them.

The bridge retains one snapshot and one context, with HWM 8 and a 2 MB frame
limit. JSON depth, collection size and finite numbers are checked. Each socket
is created, polled and disposed on its own worker; reconnect first stops and
joins that worker. The SSE route emits the latest state at most ten times per
second and repeats status when idle. Slow views cannot build an acquisition
queue. Source-sequence gaps, publisher restarts, receiver errors and three-second
staleness remain visible. Snapshot and context ages are reported separately, so
an arriving context heartbeat cannot make an old pose or tracker sample appear
fresh. Browser history is capped at 1,800 samples per rig
and 32 visible rigs; it clears on endpoint/session/scene/trial/block changes
and sequence restart. Histories are an observation window, not a recording.

World X/Z are displayed without changing coordinates. When supplied,
`worldUnitsPerMetre` identifies metres (1), centimetres (100), or another
explicit scene scale. Heading is world yaw in degrees. Tracker freshness and
telemetry freshness are separate indicators.

## Server and remote viewing

`python native_app.py --telemetry tcp://127.0.0.1:9880 --open-browser`
starts a loopback-only server on port 8060. `--host 0.0.0.0 --live-only`
explicitly exposes a read-only viewer to the lab network. Open
`http://BRIDGE-COMPUTER:8060/live/` from another computer; no installation
is required there. The server, not the browser, connects to Unity.
Do not confuse the tracker → Unity input ports (commonly 9871 onward) with
Unity → monitor telemetry (default 9880). Both interfaces must be reachable
when they cross computers. This service has no public-internet authentication.

Routes:

| Route | Purpose |
| --- | --- |
| `/live/` | Live viewer with offline ECharts assets |
| `/api/live/snapshot` | Latest context, snapshot and receiver diagnostics |
| `/api/live/stream` | Same-origin Server-Sent Events |
| `POST /api/live/connect` | Loopback operator selects TCP publisher and topic |
| `/api/health` | HTTP liveness |

Remote connection changes return 403. Live-only mode blocks `/api/load`,
`/api/resolve-drop` and `/native-config.json`, preventing remote recorded-file
selection. The original local analysis application remains available without
`--live-only`. Waitress serves 12 threads; allow headroom for control requests
when choosing the number of simultaneous SSE viewers.

## Standalone packaging and validation

Run `python tools/build_native.py` after installing `requirements-native.txt`
and PyInstaller 6. The analysis reference is deliberately constrained to pandas
2.x: pandas 3 changes datetime integer units and writable-array semantics, which
would alter durations and some existing calculations. CI exercises the complete
reference suite and the packaged smoke now also checks a recorded trajectory.
Build on the target OS; PyInstaller does not cross-compile.
The ZIP contains the entire one-folder application, Python/runtime libraries,
ZeroMQ and the static browser assets. Preserve the whole directory. The native
launcher opens a browser automatically unless passed `--no-browser`. macOS and
Linux also include a shell launcher. Unsigned builds do not add code signing
or installers. GitHub Actions stores the four platform artifacts without
creating a release. Passing main-branch packages are additionally published
at [the static download hub](https://pvnkmrksk.github.io/trajectory-dashboard/)
with commit and checksum evidence, so downloading does not require a GitHub
account or depend on artifact retention.

`python tools/smoke_native.py dist/DaariDeepa/DaariDeepa` checks the built
application with Python removed from its PATH: real ZeroMQ handshake, context
for a late joiner, telemetry data, HTTP static assets and SSE. Use the `.exe`
path on Windows. Source tests cover packet bounds, stale/gap/restart state,
reconnect cleanup, local/remote access and canonical/legacy animal metadata.

## Companion tools

[MATREX VR](https://github.com/pvnkmrksk/matrexVR) controls the virtual
environment. [Rekkefly](https://github.com/pvnkmrksk/rekkefly) tracks flight;
[HejjéTrac](https://github.com/pvnkmrksk/hejjetrak) tracks walking. The
[system manual](https://pvnkmrksk.github.io/matrexVR/manual/integrations.html)
explains units, ports, versions and archived legacy adapters.
