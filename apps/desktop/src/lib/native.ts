/** Bridges to the Tauri shell (engine management + auto-update). No-ops in a normal browser. */

export const isTauri = (): boolean => "__TAURI_INTERNALS__" in window;

export interface EngineStatus {
  mode: "dev" | "managed" | "missing";
  running: boolean;
  installed_version: string | null;
  bundled_version: string | null;
  needs_setup: boolean;
  app_version: string;
}

export interface SetupEvent {
  step: number;
  total: number;
  title: string;
  detail: string;
  done: boolean;
  error: string | null;
}

export async function engineStatus(): Promise<EngineStatus | null> {
  if (!isTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<EngineStatus>("engine_status");
}

export async function startEngine(): Promise<void> {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("start_engine");
}

export async function runEngineSetup(onEvent: (e: SetupEvent) => void): Promise<() => void> {
  const { invoke } = await import("@tauri-apps/api/core");
  const { listen } = await import("@tauri-apps/api/event");
  const unlisten = await listen<SetupEvent>("engine-setup", (e) => onEvent(e.payload));
  await invoke("setup_engine");
  return unlisten;
}

export interface AvailableUpdate {
  version: string;
  currentVersion: string;
  notes: string;
  date: string | null;
  install: (onProgress: (downloaded: number, total: number | null) => void) => Promise<void>;
}

export async function checkForUpdate(): Promise<AvailableUpdate | null> {
  if (!isTauri()) return null;
  const { check } = await import("@tauri-apps/plugin-updater");
  const update = await check();
  if (!update) return null;
  return {
    version: update.version,
    currentVersion: update.currentVersion,
    notes: update.body ?? "",
    date: update.date ?? null,
    install: async (onProgress) => {
      let total: number | null = null;
      let downloaded = 0;
      await update.downloadAndInstall((event) => {
        if (event.event === "Started") {
          total = event.data.contentLength ?? null;
          onProgress(0, total);
        } else if (event.event === "Progress") {
          downloaded += event.data.chunkLength;
          onProgress(downloaded, total);
        } else if (event.event === "Finished") {
          onProgress(total ?? downloaded, total ?? downloaded);
        }
      });
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    },
  };
}

export async function appVersion(): Promise<string | null> {
  if (!isTauri()) return null;
  const { getVersion } = await import("@tauri-apps/api/app");
  return getVersion();
}
