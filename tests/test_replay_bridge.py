"""Recorded replay ownership, time association and shared-renderer regression checks."""
from pathlib import Path
import json
import subprocess
import time
import numpy as np
import pandas as pd
from native_dashboard.server import create_native_app
from native_dashboard.replay import ReplayHub, PROTOCOL
from native_dashboard.dataset import _local_time_seconds

TOKEN = "a" * 32
SESSION = "b" * 32


def clock(folder, **overrides):
    return dict(sessionId=SESSION, runDirectory=str(folder), originTicks="639000000000000000",
                seconds=1, duration=20, rate=1, playing=False, revision=1, ackCommand=0, **overrides)


def test_recorded_bridge_owner_and_command_ack(tmp_path):
    app = create_native_app(replay_token=TOKEN)
    client = app.test_client()
    headers = {"X-Matrex-Replay-Token": TOKEN}
    assert client.get("/api/replay/capabilities").json["protocol"] == PROTOCOL
    assert client.get("/api/replay/capabilities", environ_base={"REMOTE_ADDR":"192.168.1.4"}).status_code == 403
    assert client.get("/api/replay/snapshot").status_code == 403
    assert client.get("/api/replay/snapshot", headers={**headers,"Origin":"https://unrelated.example"}).status_code == 403
    assert client.post("/api/replay/state", json=clock(tmp_path), headers={**headers,"Origin":"http://localhost"}).status_code == 403
    assert client.post("/api/replay/state", json=clock(tmp_path), headers=headers).status_code == 200
    payload = dict(sessionId=SESSION, requestId="seek_1", kind="seek", seconds=12)
    result = client.post("/api/replay/command", json=payload, headers=headers)
    assert result.status_code == 200
    sequence = result.json["sequence"]
    assert client.post("/api/replay/command", json=payload, headers=headers).json["duplicate"]
    reply = client.post("/api/replay/state", json={**clock(tmp_path),"revision":2}, headers=headers).json
    assert len(reply["commands"]) == 1 and reply["commands"][0]["seconds"] == 12
    reply = client.post("/api/replay/state", json={**clock(tmp_path),"revision":3,"ackCommand":sequence}, headers=headers).json
    assert reply["commands"] == []
    assert client.post("/api/replay/command", json={**payload,"requestId":"stale","sessionId":"c"*32}, headers=headers).status_code == 409
    assert client.post("/api/replay/command", json={**payload,"requestId":"bad","seconds":float("nan")}, headers=headers).status_code == 409
    assert client.post("/api/replay/state", json={**clock(tmp_path),"revision":0}, headers=headers).status_code == 400
    # This route never touches the live telemetry receiver/topic.
    assert app.extensions["matrex_live"].endpoint == ""
    app.extensions["matrex_live"].close()


def test_bounded_seek_queue_and_stale_owner(tmp_path):
    hub = ReplayHub(TOKEN)
    hub.publish(clock(tmp_path))
    for i in range(200):
        hub.command(dict(sessionId=SESSION, requestId=f"seek_{i}", kind="seek", seconds=i % 20))
    assert len(hub.commands) == 1 and hub.commands[0]["seconds"] == 19
    hub.updated = time.monotonic() - 4
    try: hub.command(dict(sessionId=SESSION, requestId="late", kind="play"))
    except ValueError: pass
    else: raise AssertionError("Disconnected replay must not accept commands")


def test_local_analysis_time_unchanged():
    frame = pd.DataFrame({"Current Time": pd.to_datetime(["2026-01-01 00:00:10", "2026-01-01 00:00:11", "2026-01-01 00:01:10", "2026-01-01 00:01:12"])})
    np.testing.assert_array_equal(_local_time_seconds(frame, np.array([0,2]), np.array([2,4])), [0,1,0,2])


def test_replay_model_global_clock_and_no_segment_join():
    source = Path("native_dashboard/static/replay_model.js").resolve()
    script = r"""
import fs from 'node:fs';
const {buildReplayProduct,replayCursors}=await import('data:text/javascript;base64,'+fs.readFileSync(process.argv[1]).toString('base64'));
const assert=(v,label)=>{if(!v)throw Error(label);};
const header={replayTimeline:{originTicks:'639000000020000000'},categories:{scene:['Choice','Forest'],file:['a.csv','b.csv'],animal:['animal1@VR1']},segmentIds:['a:0:0','b:0:0']};
const data={x:Float32Array.from([0,1,2,10,12]),z:new Float32Array(5),orientation:Float32Array.from([0,0,0,179,-179]),replayTime:Float32Array.from([0,1,2,10,11]),segment:Uint32Array.from([0,0,0,1,1]),segmentScene:Uint16Array.from([0,1]),segmentFile:Uint16Array.from([0,1]),segmentAnimal:Uint16Array.from([0,0]),segmentTrial:Float32Array.from([0,0])};
const m=buildReplayProduct(header,data,'639000000000000000');
assert(m.product.links===3,'separate segments must never be joined');
assert(m.product.times[0]===2&&m.product.times[4]===12,'global run time must retain gaps and origin offset');
assert(replayCursors(m.tracks,1).length===0,'no pose before coverage');
const gap=replayCursors(m.tracks,8)[0];assert(gap.x===2&&gap.stale,'gap must not interpolate across segments');
const p=replayCursors(m.tracks,12.5)[0];assert(p.x===11&&Math.abs(p.yaw-180)<.001,'pose interpolation retains circular heading');
assert(replayCursors(m.tracks,14).length===0,'no pose after coverage');
assert(data.replayTime[0]===0,'source table remains immutable');
console.log('PASS: 7 recorded replay model checks');
"""
    subprocess.run(["node", "--input-type=module", "-e", script, str(source)], check=True, capture_output=True, text=True)
