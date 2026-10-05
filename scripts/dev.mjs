import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
const python =
  process.platform === "win32"
    ? ".venv/Scripts/python.exe"
    : ".venv/bin/python";
if (!existsSync(python)) {
  console.error("Сначала установите Python-зависимости по README.");
  process.exit(1);
}
const children = [
  spawn(
    python,
    [
      "-m",
      "uvicorn",
      "backend.proctor.app:app",
      "--host",
      "127.0.0.1",
      "--port",
      "8000",
    ],
    { stdio: "inherit" },
  ),
  spawn(
    process.execPath,
    [resolve("web/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1"],
    { stdio: "inherit", cwd: "web" },
  ),
];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  children.forEach((p) => p.kill());
  setTimeout(() => process.exit(code), 300);
}
children.forEach((p) => {
  p.on("exit", (code) => stop(code || 0));
  p.on("error", (error) => {
    console.error(error.message);
    stop(1);
  });
});
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
