import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import {
  endExam,
  sendDeviceCommand,
  sendGroupCommand,
} from "../src/commands.ts";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});
const immediate = async () => {};
const receive = () => {};
function device(id = "pc-1", overrides = {}) {
  return {
    id,
    name: id,
    student: id,
    simulated: false,
    online: true,
    exam_id: "exam-1",
    state: {
      lifecycle: "RUNNING",
      access: "LOCKED",
      version: 2,
      lock_id: "lock-1",
    },
    ...overrides,
  };
}
function snapshot(devices, commands = []) {
  return { devices, commands, exams: [], events: [] };
}
function respond(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}
function historicalExam(devices) {
  return {
    id: "exam-1",
    participants: Object.fromEntries(
      devices.map(({ id, name, student, state, simulated }) => [
        id,
        { id, name, student, state, simulated },
      ]),
    ),
  };
}

test("end whole exam uses its identity when participants have no exam_id", async () => {
  const active = device();
  const completed = device("pc-2", {
    state: { ...active.state, lifecycle: "COMPLETED" },
  });
  const exam = historicalExam([active, completed]);
  assert.equal(exam.participants[active.id].exam_id, undefined);
  const sent = [];
  globalThis.fetch = async (url, options) => {
    if (url === "/api/snapshot")
      return respond(
        snapshot(
          [active, completed],
          sent.map((c) => ({ ...c, status: "APPLIED" })),
        ),
      );
    const body = JSON.parse(options.body);
    assert.equal(url, "/api/devices/pc-1/commands");
    assert.equal(body.exam_id, exam.id);
    assert.equal(body.type, "END_AND_RELEASE");
    assert.equal(body.expected_version, 2);
    sent.push({ id: "cmd-1", device_id: active.id, status: "PENDING" });
    return respond(sent[0]);
  };
  await endExam(exam, receive, immediate);
  assert.equal(sent.length, 1);
});

test("old exam end never reaches a computer reassigned to a new exam", async () => {
  const old = device();
  let posts = 0;
  globalThis.fetch = async (url) => {
    if (url !== "/api/snapshot") posts++;
    return respond(snapshot([device("pc-1", { exam_id: "exam-2" })]));
  };
  await assert.rejects(
    endExam(historicalExam([old]), receive, immediate),
    /Сеанс компьютера изменился/,
  );
  assert.equal(posts, 0);
});

test("bulk start reports the failed computer and actual localized cause while preserving successes", async () => {
  const devices = [device("pc-1"), device("pc-2")];
  const posted = [];
  globalThis.fetch = async (url) => {
    if (url === "/api/snapshot")
      return respond(
        snapshot(
          devices,
          posted.map((id) => ({
            id,
            status: id === "pc-1" ? "APPLIED" : "REJECTED",
            error: id === "pc-2" ? "CAMERA_FRAME_STALE" : "",
          })),
        ),
      );
    const id = url.split("/")[3];
    posted.push(id);
    return respond({ id, status: "PENDING" });
  };
  await assert.rejects(
    sendGroupCommand(devices, "START", "", receive, immediate),
    /1 из 2 компьютеров\. pc-2: Изображение камеры не обновляется/,
  );
  assert.deepEqual(posted.sort(), ["pc-1", "pc-2"]);
});

test("unlock rejects a newly replaced lock before sending", async () => {
  globalThis.fetch = async () =>
    respond(
      snapshot([
        device("pc-1", {
          state: { ...device().state, lock_id: "new-lock", version: 3 },
        }),
      ]),
    );
  await assert.rejects(
    sendDeviceCommand(device(), "UNLOCK", "Проверено", receive, immediate),
    /новая блокировка/,
  );
});

test("end tolerates another teacher already completing the same exam", async () => {
  let posts = 0;
  globalThis.fetch = async (url) => {
    if (url !== "/api/snapshot") posts++;
    return respond(
      snapshot([
        device("pc-1", {
          state: { ...device().state, lifecycle: "COMPLETED" },
        }),
      ]),
    );
  };
  await sendDeviceCommand(
    device(),
    "END_AND_RELEASE",
    "Конец",
    receive,
    immediate,
  );
  assert.equal(posts, 0);
});

test("expired commands are errors rather than optimistic completion", async () => {
  let sent = false;
  globalThis.fetch = async (url) => {
    if (url === "/api/snapshot")
      return respond(
        snapshot([device()], sent ? [{ id: "cmd", status: "EXPIRED" }] : []),
      );
    sent = true;
    return respond({ id: "cmd", status: "PENDING" });
  };
  await assert.rejects(
    sendDeviceCommand(device(), "END_AND_RELEASE", "Конец", receive, immediate),
    /не ответил/,
  );
});
