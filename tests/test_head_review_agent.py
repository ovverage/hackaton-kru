"""Actual Agent -> journal -> existing server compatibility for head-only review."""

from types import SimpleNamespace
import math

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from agent.client import Agent, atomic_json
from backend.proctor.app import create_app
from shared.head_pose import HeadPoseObserver


@pytest.fixture
def connected_agent(tmp_path, monkeypatch):
    server = TestClient(create_app(tmp_path / "server"), headers={"X-Requested-With": "Qorgau"})
    server.post("/api/auth/setup", json={"name": "Teacher", "password": "test-password"})
    code = server.post("/api/pairings", json={}).json()["code"]
    registered = server.post("/api/agent/enroll", json={"code": code, "name": "Head test PC"}).json()
    executable = tmp_path / "test-app"
    executable.write_text("test placeholder; never launched")
    target = {"id": "test-app", "kind": "APP", "name": "Test app", "executable": str(executable)}
    folder = tmp_path / "agent"
    atomic_json(folder / "config.json", {
        **registered, "server": "http://testserver", "targets": [target],
    })

    def forward(request):
        return server.request(request.method, request.url.path,
                              content=request.content, headers=dict(request.headers))

    agent = Agent(folder, transport=httpx.MockTransport(forward))
    agent.sync()
    exam = server.post("/api/exams", json={
        "title": "Independent head review", "require_camera": False,
        "group": "A", "room": "1", "device_ids": [registered["device_id"]],
        "environment": {"kind": "APP", "target_id": "test-app"},
    })
    assert exam.status_code == 200, exam.text
    agent.sync()
    agent.engine.start()
    agent.camera = SimpleNamespace()
    agent.capabilities.update(camera=True, gaze=True)
    clock = [agent.origin + .1]
    monkeypatch.setattr("agent.client.time.monotonic", lambda: clock[0])
    yield agent, clock, server
    agent.http.close()
    server.close()


def head_observation(agent, clock, *, faces=1, ready=True):
    agent.observe(direction="UNKNOWN", faces=faces, captured_at=clock[0], gaze_diagnostics={
        "source": "public_gaze_model", "reference_ready": True,
        "head_direction": "LEFT", "head_reference_ready": ready, "head_away": True,
        "head_yaw": -32., "head_pitch": 2.,
    })
    clock[0] += .2


def reviews(agent):
    return [event for event in agent.journal["events"] if event.get("type") == "HEAD_TURN_REVIEW"]


def test_actual_agent_head_only_review_syncs_without_gaze_strikes(connected_agent):
    agent, clock, server = connected_agent
    for _ in range(26):
        head_observation(agent, clock)
    event, = reviews(agent)
    assert event["category"] == "REVIEW"
    assert event["source"] == "head_pose"
    assert event["direction"] == "LEFT" and event["duration"] == 5.
    assert agent.gaze_diagnostics["direction"] == "UNKNOWN"
    assert agent.engine.state.counts() == {"DOWN": 0, "LEFT": 0, "RIGHT": 0}
    assert agent.engine.state.access == "OPEN"
    for _ in range(30):
        head_observation(agent, clock)
    assert len(reviews(agent)) == 1
    agent.sync()
    assert not agent.journal["events"]
    saved, = server.get("/api/snapshot").json()["events"]
    assert saved["type"] == "HEAD_TURN_REVIEW"
    assert saved["source"] == "head_pose" and saved["category"] == "REVIEW"
    assert saved["decision"] == "PENDING"


@pytest.mark.parametrize("faces", [0, 2])
def test_missing_or_multiple_faces_break_actual_agent_head_review(connected_agent, faces):
    agent, clock, _ = connected_agent
    for _ in range(22):
        head_observation(agent, clock)
    head_observation(agent, clock, faces=faces)
    for _ in range(24):
        head_observation(agent, clock)
    assert not reviews(agent)
    assert not any(agent.engine.state.counts().values())


def test_paused_agent_discards_partial_head_episode_and_requires_fresh_frames(connected_agent):
    agent, clock, _ = connected_agent
    for _ in range(22):
        head_observation(agent, clock)
    agent.engine.lock("TEACHER_REQUEST")
    for _ in range(30):
        head_observation(agent, clock)
    assert not reviews(agent)
    assert agent.recognition_paused and agent.head_review.start is None
    agent.engine.unlock(agent.engine.state.lock_id, agent.engine.state.version)
    for _ in range(25):
        head_observation(agent, clock)
    assert not reviews(agent)
    head_observation(agent, clock)
    assert len(reviews(agent)) == 1
    assert not any(agent.engine.state.counts().values())


def test_uncalibrated_head_signal_cannot_create_review_in_actual_agent(connected_agent):
    agent, clock, _ = connected_agent
    for _ in range(40):
        head_observation(agent, clock, ready=False)
    assert not reviews(agent)
    assert agent.engine.state.access == "OPEN"


def pose_observer():
    observer = HeadPoseObserver()
    assert observer.set_reference_rotations([np.eye(3)] * 25)
    return observer


def strong_turn(observer):
    angle = math.radians(70)
    rotation = np.array([
        [math.cos(angle), 0., math.sin(angle)],
        [0., 1., 0.],
        [-math.sin(angle), 0., math.cos(angle)],
    ])
    return observer.observe_rotation(rotation)


def pose_observation(agent, clock, diagnostics, *, faces=1):
    agent.observe(direction="UNKNOWN", faces=faces, captured_at=clock[0],
                  gaze_diagnostics={"source": "public_gaze_model", "reference_ready": True,
                                    **diagnostics})
    clock[0] += .2


def test_actual_70_degree_pose_then_loss_syncs_as_review_without_invented_current_angles(connected_agent):
    agent, clock, server = connected_agent
    observer = pose_observer()
    measured = strong_turn(observer)
    assert measured['head_yaw'] == pytest.approx(70)
    assert measured['head_extreme'] is True
    pose_observation(agent, clock, measured)
    missing = observer.observe_rotation(None)
    assert missing['head_tracking_status'] == 'unavailable'
    for _ in range(11):
        pose_observation(agent, clock, missing)
    event, = reviews(agent)
    assert event['category'] == 'REVIEW' and event['source'] == 'head_tracking_loss'
    assert event['direction'] is event['head_yaw'] is event['head_pitch'] is None
    assert event['last_known_head_yaw'] == pytest.approx(70)
    assert event['last_known_head_pitch'] == 0
    assert event['duration'] == 2
    assert agent.gaze_diagnostics['head_yaw'] is None
    assert agent.gaze_diagnostics['head_direction'] == 'UNKNOWN'
    assert not any(agent.engine.state.counts().values())
    assert agent.engine.state.access == 'OPEN'
    for _ in range(20):
        pose_observation(agent, clock, missing)
    assert len(reviews(agent)) == 1
    agent.sync()
    assert not agent.journal['events']
    saved, = server.get('/api/snapshot').json()['events']
    assert saved['type'] == 'HEAD_TURN_REVIEW'
    assert saved['source'] == 'head_tracking_loss' and saved['category'] == 'REVIEW'
    assert saved['decision'] == 'PENDING'
    assert saved['direction'] is saved['head_yaw'] is saved['head_pitch'] is None


def test_actual_frontal_pose_loss_never_creates_head_turn_review(connected_agent):
    agent, clock, _ = connected_agent
    observer = pose_observer()
    pose_observation(agent, clock, observer.observe_rotation(np.eye(3)))
    for _ in range(30):
        pose_observation(agent, clock, observer.observe_rotation(None))
    assert not reviews(agent)
    assert not any(agent.engine.state.counts().values())
    assert agent.engine.state.access == 'OPEN'


@pytest.mark.parametrize('faces', [0, 2])
def test_actual_face_loss_or_second_face_clears_extreme_turn_anchor(connected_agent, faces):
    agent, clock, _ = connected_agent
    observer = pose_observer()
    pose_observation(agent, clock, strong_turn(observer))
    missing = observer.observe_rotation(None)
    for _ in range(8):
        pose_observation(agent, clock, missing)
    pose_observation(agent, clock, missing, faces=faces)
    for _ in range(30):
        pose_observation(agent, clock, missing)
    assert not reviews(agent)
    assert not any(agent.engine.state.counts().values())


def test_actual_pause_clears_tracking_loss_and_requires_new_extreme_pose(connected_agent):
    agent, clock, _ = connected_agent
    observer = pose_observer()
    pose_observation(agent, clock, strong_turn(observer))
    missing = observer.observe_rotation(None)
    for _ in range(8):
        pose_observation(agent, clock, missing)
    agent.engine.lock('TEACHER_REQUEST')
    pose_observation(agent, clock, missing)
    assert agent.head_review.extreme_anchor is None
    assert agent.head_review.loss_start is None
    agent.engine.unlock(agent.engine.state.lock_id, agent.engine.state.version)
    for _ in range(30):
        pose_observation(agent, clock, missing)
    assert not reviews(agent)
    assert not any(agent.engine.state.counts().values())
