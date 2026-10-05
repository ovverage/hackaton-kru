import { spawn } from "node:child_process";
const python =
  process.platform === "win32"
    ? ".venv/Scripts/python.exe"
    : ".venv/bin/python";
const child = spawn(python, ["-m", "agent.client"], { stdio: "inherit" });
child.on("exit", (code) => process.exit(code ?? 1));
child.on("error", (error) => {
  console.error(error.message);
  process.exit(1);
});
