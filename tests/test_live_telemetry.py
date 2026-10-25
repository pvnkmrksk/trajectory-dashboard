import json
import time
import pytest
from native_dashboard.live import LiveHub, validate_endpoint
from native_dashboard import create_native_app


def packet(sequence=1):
    return {
        "schemaVersion": 2,
        "sequence": sequence,
        "timestampUtc": "2026-10-25T12:00:00Z",
        "system": {"sceneName": "Choice", "phase": "stimulus"},
        "rigs": [{"vrId": "VR1", "pose": {"x": 1, "y": 0, "z": 2, "yaw": 30}}],
    }


def test_late_joiner_context_and_bounded_latest_state():
    hub = LiveHub()
    for seq in range(1, 2001):
        assert hub.ingest("matrex.telemetry.v2", json.dumps(packet(seq)).encode())
    context = {
        "schemaVersion": 1,
        "kind": "matrex.context",
        "sessionId": "run-A",
        "animals": [
            {"vrId": "VR1", "animalId": "animal-A", "speciesName": "Agrotis infusa"}
        ],
    }
    assert hub.ingest("matrex.telemetry.v2.context", json.dumps(context).encode())
    s = hub.snapshot()
    assert s["telemetry"]["sequence"] == 2000
    assert s["context"] == context
    assert s["receiver"]["snapshots"] == 2000
    s["context"]["sessionId"] = "mutated"
    assert hub.snapshot()["context"]["sessionId"] == "run-A"
    event = next(hub.events())
    assert "run-A" in event and "2000" in event


def test_sequence_gaps_restarts_invalid_finite_and_stale():
    hub = LiveHub()
    hub.endpoint = "tcp://127.0.0.1:9880"
    for seq in (1, 4, 1):
        assert hub.ingest("matrex.telemetry.v2", json.dumps(packet(seq)).encode())
    assert hub.snapshot()["receiver"]["sequenceGaps"] == 2
    assert hub.snapshot()["receiver"]["publisherRestarts"] == 1
    assert not hub.ingest("x", b'{"schemaVersion":2,"rigs":[],"x":NaN}')
    assert not hub.ingest("x", b"{")
    assert not hub.ingest("x", b"[]")
    assert not hub.ingest("x", b'{"schemaVersion":999,"rigs":[]}')
    assert hub.snapshot()["telemetry"]["sequence"] == 1
    assert hub.ingest("matrex.telemetry.v2", json.dumps(packet(2)).encode())
    hub.last_received = time.monotonic() - 10
    assert hub.snapshot()["receiver"]["state"] == "stale"


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "tcp://*:9880",
        "tcp://host:0",
        "tcp://host:65536",
        "tcp://user:secret@host:98",
        "tcp://host:98; touch x",
    ],
)
def test_endpoint_rejects_unsupported_inputs(value):
    with pytest.raises(ValueError):
        validate_endpoint(value)


def test_routes_remote_read_only_and_recorded_analysis_unchanged():
    app = create_native_app(live_only=True)
    client = app.test_client()
    assert client.get("/").status_code == 302
    assert client.get("/live/").status_code == 200
    assert client.get("/api/live/snapshot").json["controlAvailable"]
    assert (
        client.get(
            "/api/live/snapshot", environ_base={"REMOTE_ADDR": "192.0.2.10"}
        ).json["controlAvailable"]
        is False
    )
    assert (
        client.post(
            "/api/live/connect",
            json={"endpoint": "tcp://host:9880"},
            environ_base={"REMOTE_ADDR": "192.0.2.10"},
        ).status_code
        == 403
    )
    assert client.post("/api/load", json={"source": "/private"}).status_code == 403
    assert client.get("/static/live.js").status_code == 200
    assert client.get("/api/health").status_code == 200
    normal = create_native_app().test_client()
    assert normal.get("/").status_code == 200
    assert normal.get("/native-config.json").status_code == 200


def test_real_zmq_delivery_and_reconnect_shutdown():
    zmq = pytest.importorskip("zmq")
    context = zmq.Context()
    pub = context.socket(zmq.XPUB)
    pub.linger = 0
    port = pub.bind_to_random_port("tcp://127.0.0.1")
    hub = LiveHub(f"tcp://127.0.0.1:{port}")
    try:
        assert pub.poll(3000), "Subscriber must complete the actual ZMQ handshake"
        assert pub.recv().startswith(b"\x01matrex.telemetry.v2")
        pub.send_multipart(
            [
                b"matrex.telemetry.v2.context",
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": "matrex.context",
                        "sessionId": "mid-run",
                    }
                ).encode(),
            ]
        )
        for seq in range(1, 21):
            pub.send_multipart(
                [b"matrex.telemetry.v2", json.dumps(packet(seq)).encode()]
            )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snap = hub.snapshot()
            if snap["context"] and snap["telemetry"]:
                break
            time.sleep(0.01)
        assert hub.snapshot()["context"]["sessionId"] == "mid-run"
        assert hub.snapshot()["telemetry"]["rigs"][0]["pose"]["yaw"] == 30
        hub.connect(f"tcp://127.0.0.1:{port}")
        assert pub.poll(3000)
    finally:
        hub.close()
        pub.close()
        context.term()
    assert hub.thread is None


def test_context_heartbeat_does_not_hide_stale_pose():
    hub = LiveHub()
    assert hub.ingest(hub.topic, json.dumps(packet()).encode())
    hub.last_frame_received = time.monotonic() - 10
    assert hub.ingest(
        hub.topic + ".context",
        json.dumps({"schemaVersion": 1, "kind": "matrex.context"}).encode(),
    )
    state = hub.snapshot()["receiver"]
    assert state["snapshotAgeSeconds"] >= 10
    assert state["contextAgeSeconds"] < 1
    assert state["ageSeconds"] < 1


def test_prefix_topic_does_not_mix_other_publishers():
    hub = LiveHub()
    assert not hub.ingest(hub.topic + ".other", json.dumps(packet()).encode())
    assert hub.snapshot()["telemetry"] is None
