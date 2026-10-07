import assert from "node:assert/strict";
import { test } from "node:test";
import {
  controlSignals,
  preparationIssue,
  sessionSummary,
  pendingEvents,
} from "../src/sessionStatus.ts";

function device(overrides = {}) {
  return {
    id: "pc-1",
    online: true,
    state: { lifecycle: "RUNNING", access: "OPEN" },
    capabilities: {
      camera: true,
      recording: true,
      gaze: true,
      window_guard: true,
      guard_active: true,
    },
    targets: [{ id: "qorgau-browser", kind: "BROWSER" }],
    ...overrides,
  };
}
test("built-in browser is ready without a selected external window", () => {
  assert.equal(preparationIssue(device(), "BROWSER"), null);
  assert.match(preparationIssue(device(), "WINDOW"), /Выберите окно/);
});
test("external browser window cannot stand in for restricted browser", () => {
  const pc = device();
  pc.capabilities.selected_window = true;
  pc.capabilities.desktop_monitor = true;
  pc.targets.push({ id: "primary-window", kind: "APP", guardable: false });
  assert.match(preparationIssue(pc, "WINDOW"), /Qorgau Browser/);
  assert.equal(preparationIssue(pc, "BROWSER"), null);
});
test("stale or completed device does not advertise live controls", () => {
  for (const pc of [
    device({ online: false }),
    device({ state: { lifecycle: "COMPLETED", access: "OPEN" } }),
  ]) {
    assert.ok(controlSignals(pc).every((signal) => signal.tone === "idle"));
  }
});
test("guard capability alone does not confirm active protection", () => {
  const pc = device();
  pc.capabilities.guard_active = false;
  assert.equal(controlSignals(pc)[2].tone, "attention");
  pc.capabilities.guard_active = true;
  pc.capabilities.guard_fault = "TARGET_CLOSED";
  assert.equal(controlSignals(pc)[2].tone, "attention");
});
test("camera fault disables both camera and gaze status", () => {
  const pc = device();
  pc.capabilities.camera_fault = true;
  assert.ok(
    controlSignals(pc)
      .slice(0, 2)
      .every((signal) => signal.tone === "attention"),
  );
  assert.match(preparationIssue(pc, "BROWSER"), /камеру/);
});
test("summary distinguishes disconnected and locked participants from running", () => {
  assert.deepEqual(
    sessionSummary(
      [
        device(),
        device({ online: false }),
        device({ state: { lifecycle: "RUNNING", access: "LOCKED" } }),
        device({ state: { lifecycle: "READY", access: "OPEN" } }),
      ],
      [{ decision: "PENDING" }, { decision: "REJECTED" }],
    ),
    {
      online: 3,
      running: 1,
      locked: 1,
      pending: 1,
    },
  );
});
test("review queue is newest first, omits decided events and does not reorder source", () => {
  const events = [
    { id: "old", created_at: 1, decision: "PENDING" },
    { id: "reviewed", created_at: 9, decision: "CONFIRMED" },
    { id: "new", created_at: 5, decision: "PENDING" },
  ];
  assert.deepEqual(
    pendingEvents(events, 1).map((event) => event.id),
    ["new"],
  );
  assert.equal(events[0].id, "old");
});
