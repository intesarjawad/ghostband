/** Setup recovery and downloads from the retained local workspace. */
import {
  errorText,
  parseServerEvent,
  type SessionState,
  type SetupEvent,
} from "./state";
import { TOKEN_HEADER } from "./ws";

export interface Recording {
  readonly id: string;
  readonly download_url: string;
}

export function parseRecordings(value: unknown): readonly Recording[] {
  if (
    typeof value !== "object" ||
    value === null ||
    !("recordings" in value) ||
    !Array.isArray(value.recordings)
  ) {
    throw new Error("Invalid recordings reply. Reload to try again.");
  }
  return value.recordings.map((item: unknown) => {
    if (
      typeof item !== "object" ||
      item === null ||
      !("id" in item) ||
      typeof item.id !== "string" ||
      !/^[0-9a-f]{32}$/.test(item.id) ||
      !("download_url" in item) ||
      item.download_url !== `/api/recordings/${item.id}`
    ) {
      throw new Error("Invalid recording download. Reload to try again.");
    }
    return { id: item.id, download_url: item.download_url };
  });
}

export function createWorkspace(document: Document, token: string) {
  const node = <K extends keyof HTMLElementTagNameMap>(
    tag: K,
    text = "",
  ): HTMLElementTagNameMap[K] => {
    const result = document.createElement(tag);
    result.textContent = text;
    return result;
  };
  const setup = node("section");
  setup.setAttribute("aria-label", "Setup");
  setup.hidden = true;
  const heading = node("h2", "Setup");
  const status = node("p");
  status.setAttribute("role", "status");
  const progress = node("progress");
  progress.setAttribute("aria-label", "Setup download");
  const bytes = node("p");
  const detail = node("p");
  detail.setAttribute("role", "alert");
  const retry = node("button", "Retry setup");
  retry.type = "button";
  setup.append(heading, status, progress, bytes, detail, retry);

  async function request(path: string, method = "GET"): Promise<Response> {
    const response = await fetch(path, {
      method,
      headers: { [TOKEN_HEADER]: token },
    });
    if (!response.ok) {
      const event = parseServerEvent(await response.json().catch(() => null));
      throw new Error(
        event?.event === "error"
          ? errorText(event)
          : `Request failed (HTTP ${String(response.status)}). Try again.`,
      );
    }
    return response;
  }

  let setupVersion = 0;
  function updateSetup(event: SetupEvent): void {
    setupVersion += 1;
    setup.hidden = event.state === "ready";
    status.textContent =
      event.state === "failed"
        ? `Setup failed: ${event.stage}`
        : event.state === "not_started"
          ? "Setup has not started."
          : `Installing: ${event.stage}`;
    progress.hidden = event.state !== "installing";
    if (event.total_bytes > 0) {
      progress.max = event.total_bytes;
      progress.value = event.done_bytes;
    } else {
      progress.removeAttribute("value");
    }
    bytes.textContent = `${String(event.done_bytes)} / ${String(event.total_bytes)} bytes`;
    detail.textContent = event.detail;
    retry.hidden = !event.retryable;
    retry.disabled = false;
  }

  async function loadSetup(): Promise<void> {
    const version = setupVersion;
    try {
      const event = parseServerEvent(
        await (await request("/api/setup")).json(),
      );
      if (event?.event !== "setup_status")
        throw new Error("Invalid setup reply. Reload to try again.");
      // A replay or progress event is newer than a status request already in flight.
      if (version === setupVersion) updateSetup(event);
    } catch (error) {
      if (version === setupVersion) {
        setup.hidden = false;
        detail.textContent = String(error);
        retry.hidden = false;
      }
    }
  }

  retry.addEventListener("click", () => {
    retry.disabled = true;
    detail.textContent = "";
    const version = setupVersion;
    void (async () => {
      try {
        const event = parseServerEvent(
          await (await request("/api/setup/retry", "POST")).json(),
        );
        if (event?.event !== "setup_status")
          throw new Error("Invalid setup reply. Retry setup.");
        if (version === setupVersion) updateSetup(event);
      } catch (error) {
        if (version === setupVersion) detail.textContent = String(error);
      } finally {
        retry.disabled = false;
      }
    })();
  });

  const downloads = node("section");
  downloads.setAttribute("aria-label", "Downloads");
  const saved = node(
    "p",
    "Chart edits and stopped recordings are saved locally across restarts. Download a copy to keep elsewhere. Stop an active recording before quitting.",
  );
  const chart = node("button", "Download current chart");
  const latest = node("button", "Download latest recording");
  chart.type = latest.type = "button";
  chart.disabled = latest.disabled = true;
  const list = node("ul");
  const empty = node("p", "No stopped recordings yet.");
  const listError = node("p");
  listError.setAttribute("role", "alert");
  const refresh = node("button", "Refresh recordings");
  refresh.type = "button";
  downloads.append(
    node("h2", "Downloads"),
    saved,
    chart,
    latest,
    list,
    empty,
    listError,
    refresh,
  );

  async function download(
    path: string,
    filename: string,
    control: HTMLButtonElement,
  ): Promise<void> {
    control.disabled = true;
    listError.textContent = "";
    try {
      const response = await request(path);
      const url = URL.createObjectURL(await response.blob());
      const link = node("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      // Allow the browser to consume the blob before releasing it.
      setTimeout(() => {
        URL.revokeObjectURL(url);
      }, 60_000);
    } catch (error) {
      listError.textContent =
        error instanceof Error ? error.message : String(error);
    } finally {
      control.disabled =
        control === chart
          ? !hasChart
          : control === latest
            ? recording || !hasRecordings
            : false;
    }
  }
  chart.addEventListener("click", () => {
    void download("/api/export/chart", "chart.json", chart);
  });
  latest.addEventListener("click", () => {
    void download("/api/export/wav", "take.wav", latest);
  });
  let hasChart = false;
  let hasRecordings = false;
  let recording = false;
  let loaded = false;
  let listVersion = 0;
  async function refreshRecordings(): Promise<void> {
    const version = ++listVersion;
    try {
      const recordings = parseRecordings(
        await (await request("/api/recordings")).json(),
      );
      if (version !== listVersion) return;
      hasRecordings = recordings.length > 0;
      latest.disabled = recording || !hasRecordings;
      empty.hidden = hasRecordings;
      listError.textContent = "";
      list.replaceChildren(
        ...recordings.map((item, index) => {
          const row = node("li");
          const control = node(
            "button",
            `Download recording ${String(index + 1)} (${item.id.slice(0, 8)})`,
          );
          control.type = "button";
          control.addEventListener("click", () => {
            void download(item.download_url, `${item.id}.wav`, control);
          });
          row.append(control);
          return row;
        }),
      );
    } catch (error) {
      if (version === listVersion)
        listError.textContent = `${String(error)} Use Refresh recordings to retry.`;
    }
  }
  refresh.addEventListener("click", () => {
    void refreshRecordings();
  });

  function update(state: SessionState): void {
    hasChart = state.chart !== null;
    chart.disabled = !hasChart;
    const stopped = recording && !state.recording;
    recording = state.recording;
    latest.disabled = recording || !hasRecordings;
    if (!loaded || stopped) {
      loaded = true;
      void refreshRecordings();
    }
  }
  window.addEventListener("beforeunload", (event) => {
    if (recording) {
      event.preventDefault();
    }
  });
  void loadSetup();
  return { setup, downloads, update, updateSetup };
}
