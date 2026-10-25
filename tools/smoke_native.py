"""Exercise a built standalone binary against a real PUB, without Python on its PATH."""

import argparse
import csv
import json
import os
from pathlib import Path
import socket
import subprocess
import struct
import tempfile
import time
from urllib.request import Request, urlopen
import zmq

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("executable", type=Path)
args = p.parse_args()
with socket.socket() as free:
    free.bind(("127.0.0.1", 0))
    http_port = free.getsockname()[1]
context = zmq.Context()
pub = context.socket(zmq.XPUB)
pub.linger = 0
port = pub.bind_to_random_port("tcp://127.0.0.1")
env = {
    k: v
    for k, v in os.environ.items()
    if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
}
env["PATH"] = (
    os.pathsep.join([os.path.join(os.environ.get("SystemRoot", "/"), "System32")])
    if os.name == "nt"
    else "/usr/bin:/bin"
)
command = [
    str(args.executable.resolve()),
    "--no-browser",
    "--port",
    str(http_port),
    "--telemetry",
    f"tcp://127.0.0.1:{port}",
]
process = subprocess.Popen(
    command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
)
base = f"http://127.0.0.1:{http_port}"
try:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Standalone exited: " + process.stdout.read())
        try:
            with urlopen(base + "/api/health", timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.2)
    else:
        raise AssertionError("Standalone HTTP server did not start")
    assert pub.poll(5000), "Actual ZMQ subscription did not arrive"
    assert pub.recv().startswith(b"\x01matrex.telemetry.v2")
    heartbeat = {
        "schemaVersion": 1,
        "kind": "matrex.context",
        "sessionId": "packaged-smoke",
        "animals": [
            {"vrId": "VR1", "animalId": "animal-7", "speciesName": "Agrotis infusa"}
        ],
    }
    packet = {
        "schemaVersion": 2,
        "sequence": 27,
        "timestampUtc": "2026-10-25T12:00:00Z",
        "system": {"sceneName": "Choice", "trialNumber": 3},
        "frameTiming": {"framesPerSecond": 120},
        "rigs": [{"vrId": "VR1", "pose": {"x": 2, "z": 4, "yaw": 90}}],
    }
    pub.send_multipart([b"matrex.telemetry.v2.context", json.dumps(heartbeat).encode()])
    pub.send_multipart([b"matrex.telemetry.v2", json.dumps(packet).encode()])
    for _ in range(100):
        with urlopen(base + "/api/live/snapshot", timeout=3) as response:
            data = json.load(response)
        if data.get("context") and data.get("telemetry"):
            break
        time.sleep(0.03)
    assert data["context"]["sessionId"] == "packaged-smoke"
    assert data["telemetry"]["sequence"] == 27
    for route in (
        "/live/",
        "/static/live.js",
        "/static/live.css",
        "/static/vendor/echarts.min.js",
    ):
        with urlopen(base + route, timeout=5) as response:
            assert response.status == 200 and len(response.read()) > 100
    with urlopen(base + "/api/live/stream", timeout=5) as response:
        assert response.readline() == b"event: snapshot\n"
        assert b"packaged-smoke" in response.readline()
    with tempfile.TemporaryDirectory(prefix="daari-native-analysis-") as folder:
        sample = Path(folder) / "sample_VR1_.csv"
        with sample.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "Current Time",
                    "CurrentTrial",
                    "CurrentStep",
                    "GameObjectPosX",
                    "GameObjectPosZ",
                    "AnimalID",
                ]
            )
            writer.writerow(["2026-10-25T12:00:00", 0, 0, 0, 0, "animal-7"])
            writer.writerow(["2026-10-25T12:00:02", 0, 0, 2, 0, "animal-7"])
        request = Request(
            base + "/api/load",
            data=json.dumps({"source": folder}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=15) as response:
            payload = response.read()
        length = struct.unpack_from("<I", payload, 0)[0]
        header = json.loads(payload[4 : 4 + length])
        assert header["counts"]["files"] == 1
        assert header["counts"]["retainedRows"] == 2
        assert header["playbackMax"] == 2.0, (
            "Recorded duration must remain seconds, not a changed datetime integer unit"
        )
        assert header["categories"]["animal"] == ["animal-7@VR1"]
    print(
        json.dumps(
            {
                "passed": True,
                "executable": str(args.executable),
                "pythonOnPath": False,
                "realZmqHandshake": True,
                "lateJoinContext": True,
                "sse": True,
                "staticAssets": True,
                "recordedDurationSeconds": header["playbackMax"],
                "canonicalAnimalIdentity": True,
            }
        )
    )
finally:
    process.terminate()
    try:
        process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
    pub.close()
    context.term()
