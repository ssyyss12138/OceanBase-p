import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

/**
 * The seekdb embedded engine ships as a native shared library. On Linux, Node may
 * need it preloaded to reserve its static TLS block before other modules load.
 * Re-exec the current script with LD_PRELOAD set when the library is available.
 */
export function ensurePreload() {
  if (process.platform !== "linux") return;
  if (process.env.SEEKDB_HOST) return;
  if (process.env.__SEEKDB_PRELOADED) return;
  const home = process.env.HOME || "";
  const root = path.join(home, ".seekdb", "bindings");
  if (!fs.existsSync(root)) return;
  let library = null;
  for (const hash of fs.readdirSync(root)) {
    for (const arch of ["linux-arm64", "linux-x64"]) {
      const candidate = path.join(root, hash, arch, "libseekdb.so");
      if (fs.existsSync(candidate)) {
        library = candidate;
        break;
      }
    }
    if (library) break;
  }
  if (!library) return;
  const result = spawnSync(process.execPath, process.argv.slice(1), {
    stdio: "inherit",
    env: { ...process.env, LD_PRELOAD: library, __SEEKDB_PRELOADED: "1" },
  });
  process.exit(result.status ?? 0);
}
