/** The import flow: a drop target, the stages the conductor reports, and the extractor's licence note. */

export type StageStatus = "waiting" | "working" | "done" | "failed";
export type Outcome = "running" | "failed" | "done";

export interface StageInfo {
  readonly id: string;
  readonly label: string;
}

/** Upload is the page's own step; the rest are the stage names the conductor reports. */
export const IMPORT_STAGES: readonly StageInfo[] = [
  { id: "upload", label: "Uploading" },
  { id: "validate", label: "Checking the file" },
  { id: "extract", label: "Extracting chords" },
  { id: "complete", label: "Building the chart" },
];

export interface ImportRun {
  readonly reached: number;
  readonly outcome: Outcome;
}

export const START: ImportRun = { reached: 0, outcome: "running" };

const LAST = IMPORT_STAGES.length - 1;

/** Move to a stage by name; a stage the page does not know leaves the run as it is. */
export function advance(run: ImportRun, stage: string): ImportRun {
  const index = IMPORT_STAGES.findIndex((item) => item.id === stage);
  if (run.outcome !== "running" || index <= run.reached) {
    return run;
  }
  return { ...run, reached: index };
}

export function fail(run: ImportRun): ImportRun {
  return run.outcome === "running" ? { ...run, outcome: "failed" } : run;
}

/** The chart arrives only after the last stage; an earlier state frame is not the result. */
export function finish(run: ImportRun): ImportRun {
  return run.outcome === "running" && run.reached === LAST
    ? { ...run, outcome: "done" }
    : run;
}

export function stageStatuses(run: ImportRun): readonly StageStatus[] {
  return IMPORT_STAGES.map((_, index) => {
    if (run.outcome === "done" || index < run.reached) {
      return "done";
    }
    if (index > run.reached) {
      return "waiting";
    }
    return run.outcome === "failed" ? "failed" : "working";
  });
}

const STATUS_WORDS: Readonly<Record<StageStatus, string>> = {
  waiting: "waiting",
  working: "in progress",
  done: "done",
  failed: "failed",
};

/** Say each stage's state in words so colour alone never carries it. */
export function stageText(label: string, status: StageStatus): string {
  return `${label}: ${STATUS_WORDS[status]}`;
}

/** The conductor sends these as errors, but the import carries on after them. */
export function isImportNotice(code: string): boolean {
  return code === "long" || code === "fallback";
}

/** SheetSage2 weights are non-commercial only; say so wherever the extractor is named. */
export const SHEETSAGE2_NOTE =
  "SheetSage2: weights licensed CC-BY-NC-4.0, for non-commercial use only";

const BADGES: Readonly<Record<string, string>> = {
  sheetsage2: SHEETSAGE2_NOTE,
  permissive: "Permissive extractor",
  midi: "Read from a MIDI file",
  manual: "Written by hand",
};

export function extractorBadge(extractor: string | null | undefined): string {
  return extractor === null || extractor === undefined
    ? ""
    : (BADGES[extractor] ?? extractor);
}

export const DROP_PROMPT = "Drop a song file here to import it.";
export const DROP_ACTIVE = "Release to import this file.";
export const DROP_ONE_FILE = "Drop one file at a time.";

/** Take the single dropped file, or say why not. */
export function droppedFile(
  files: ArrayLike<File> | null,
): { readonly file: File } | { readonly problem: string } {
  if (files === null || files.length === 0) {
    return { problem: DROP_PROMPT };
  }
  const file = files[0];
  return files.length === 1 && file !== undefined
    ? { file }
    : { problem: DROP_ONE_FILE };
}

export interface ImportPanel {
  readonly element: HTMLElement;
  begin(): void;
  progress(stage: string): void;
  fail(): void;
  chartLoaded(title: string | null, extractor: string | null): void;
}

export function createImportPanel(
  document: Document,
  choose: (file: File) => void,
  problem: (text: string) => void,
): ImportPanel {
  const element = document.createElement("div");
  element.className = "import";
  const zone = document.createElement("p");
  zone.className = "import-drop";
  zone.textContent = DROP_PROMPT;
  const summary = document.createElement("p");
  summary.className = "import-summary";
  summary.setAttribute("role", "status");
  const list = document.createElement("ol");
  list.className = "import-stages";
  list.setAttribute("aria-label", "Import stages");
  list.hidden = true;
  const badge = document.createElement("p");
  badge.className = "import-extractor";
  element.append(zone, summary, list, badge);

  let run: ImportRun = START;
  let started = false;
  const draw = (): void => {
    const statuses = stageStatuses(run);
    list.hidden = false;
    list.replaceChildren(
      ...IMPORT_STAGES.map((stage, index) => {
        const item = document.createElement("li");
        const status = statuses[index] ?? "waiting";
        item.dataset.status = status;
        item.textContent = stageText(stage.label, status);
        return item;
      }),
    );
  };

  element.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.textContent = DROP_ACTIVE;
  });
  element.addEventListener("dragleave", () => {
    zone.textContent = DROP_PROMPT;
  });
  element.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.textContent = DROP_PROMPT;
    const dropped = droppedFile(event.dataTransfer?.files ?? null);
    if ("file" in dropped) {
      choose(dropped.file);
    } else {
      problem(dropped.problem);
    }
  });

  return {
    element,
    begin() {
      run = START;
      started = true;
      summary.textContent = "";
      draw();
    },
    progress(stage) {
      if (started) {
        run = advance(run, stage);
        draw();
      }
    },
    fail() {
      const next = fail(run);
      if (started && next !== run) {
        run = next;
        summary.textContent = "Import failed.";
        draw();
      }
    },
    chartLoaded(title, extractor) {
      badge.textContent = extractorBadge(extractor);
      const next = finish(run);
      if (started && next !== run) {
        run = next;
        summary.textContent = `Chart ready: ${title ?? "untitled"}`;
        draw();
      }
    },
  };
}
