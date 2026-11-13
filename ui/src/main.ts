/** The page: every control sends one message and redraws only from the conductor's reply. */

/// <reference types="vite/client" />
import "./theme.css";
import { createWorkspace } from "./workspace";
import { createMetronomeControl, createReferenceControl } from "./controls";
import { createImportPanel, isImportNotice, SHEETSAGE2_NOTE } from "./import";
import {
  clockBeat,
  createHeardRow,
  createLane,
  createPlayhead,
  createShortcutList,
  createTransport,
  keyAction,
  keyTarget,
  NO_LOOP,
  type LaneAction,
  type LoopRegion,
  type Playhead,
} from "./lane";
import {
  EXTRACTORS,
  PROMPT_SLOTS,
  RIGHTS,
  errorText,
  laneMessage,
  mergeLoop,
  type ClientMessage,
  type Rights,
  type ServerEvent,
  type SessionState,
} from "./state";
import {
  connect,
  pageToken,
  socketUrl,
  uploadFile,
  type Connection,
} from "./ws";

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text = "",
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function labelled(text: string, control: HTMLElement): HTMLLabelElement {
  control.setAttribute("aria-label", text);
  const label = element("label", text);
  label.append(" ", control);
  return label;
}

function button(text: string, onClick: () => void): HTMLButtonElement {
  const node = element("button", text);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function section(name: string, ...children: Node[]): HTMLElement {
  const node = element("section");
  node.setAttribute("aria-label", name);
  node.append(element("h2", name), ...children);
  return node;
}

function options(select: HTMLSelectElement, values: readonly string[]): void {
  select.replaceChildren(
    ...values.map((value, index) => {
      const option = element("option", value);
      option.value = String(index);
      return option;
    }),
  );
}

const root = element("main");
const status = element("p");
status.setAttribute("role", "status");
const alert = element("p");
alert.setAttribute("role", "alert");
root.append(element("h1", "Ghostband"), status, alert);
document.body.append(root);

const token = pageToken(location.search);
let state: SessionState | null = null;
let loop: LoopRegion = NO_LOOP;
let beat = 0;
let playhead: Playhead = createPlayhead(1);
let beatsPerSecond = 1;

const showError = (text: string): void => {
  alert.textContent = text;
};

const workspace = token === null ? null : createWorkspace(document, token);
if (workspace !== null) root.append(workspace.setup);

let connection: Connection | null = null;

const send = (message: ClientMessage): void => {
  alert.textContent = "";
  connection?.send(message);
};

const act = (action: LaneAction): void => {
  if (action === "shortcuts") {
    shortcuts.hidden = !shortcuts.hidden;
    return;
  }
  if (state === null) {
    return;
  }
  if (
    action === "play-toggle" ||
    action === "loop-start" ||
    action === "loop-end" ||
    action === "loop-clear"
  ) {
    playhead.reset();
  }
  const result = laneMessage(action, { state, beat, loop });
  loop = result.loop;
  if (result.message !== null) {
    send(result.message);
  }
};

const upload = async (
  input: HTMLInputElement,
  message: (id: string) => ClientMessage,
): Promise<void> => {
  const file = input.files?.[0];
  if (file === undefined || token === null) {
    showError("Choose a file first.");
    return;
  }
  try {
    send(message(await uploadFile(file, token)));
  } catch (error) {
    showError(error instanceof Error ? error.message : String(error));
  }
};

const importSong = async (file: File): Promise<void> => {
  if (token === null) {
    showError("This page needs its access token before it can import.");
    return;
  }
  importPanel.begin();
  try {
    const id = await uploadFile(file, token);
    importPanel.progress("validate");
    send({
      type: "import",
      upload_id: id,
      extractor: EXTRACTORS[extractor.selectedIndex] ?? "permissive",
    });
  } catch (error) {
    importPanel.fail();
    showError(error instanceof Error ? error.message : String(error));
  }
};

const importPanel = createImportPanel(
  document,
  (file) => {
    void importSong(file);
  },
  showError,
);
const songFile = element("input");
songFile.type = "file";
songFile.name = "song";
songFile.accept = ".wav,.mp3,.mid,.midi,.json";
const extractor = element("select");
extractor.name = "extractor";
options(extractor, EXTRACTORS);
const licence = element("p", SHEETSAGE2_NOTE);
licence.hidden = true;
extractor.addEventListener("change", () => {
  licence.hidden = EXTRACTORS[extractor.selectedIndex] !== "sheetsage2";
});
const title = element("p", "No song loaded.");
const meter = element("p");
root.append(
  section(
    "Song",
    labelled("Song file", songFile),
    labelled("Extractor", extractor),
    licence,
    button("Import", () => {
      const file = songFile.files?.[0];
      if (file === undefined) {
        showError("Choose a file first.");
        return;
      }
      void importSong(file);
    }),
    importPanel.element,
    title,
    meter,
  ),
);

const lane = createLane(document);
const heard = createHeardRow(document);
const transport = createTransport(document, {
  act,
  tempo(factor) {
    send({ type: "tempo_factor", value: factor });
  },
  seek(fraction) {
    const length = state?.chart?.duration_beats ?? 0;
    playhead.reset();
    send({ type: "transport", action: "seek", value: fraction * length });
  },
});
const shortcuts = createShortcutList(document);
shortcuts.hidden = true;
root.append(lane.element, heard.element, transport.element, shortcuts);

const slider = element("input");
slider.type = "range";
slider.name = "slider";
slider.min = "0";
slider.max = "100";
slider.step = "5";
slider.addEventListener("change", () => {
  send({ type: "slider", value: slider.valueAsNumber / 100 });
});
const preset = element("select");
preset.name = "preset";
options(preset, [
  "Warm jazz / chiptune",
  "Folk / metal",
  "Bossa nova / synthwave",
  "String quartet / lo-fi",
]);
preset.addEventListener("change", () => {
  send({ type: "preset", value: Number(preset.value) });
});
const customPreset = element("input");
customPreset.type = "number";
customPreset.min = "4";
customPreset.step = "1";
customPreset.addEventListener("change", () => {
  if (customPreset.valueAsNumber >= 4)
    send({ type: "preset", value: customPreset.valueAsNumber });
});
const metronome = createMetronomeControl(document, (enabled) => {
  send({ type: "toggle", name: "click", enabled });
});
const reference = createReferenceControl(document, (enabled) => {
  send({ type: "toggle", name: "reference", enabled });
});
root.append(
  section(
    "Style",
    labelled("Style percent", slider),
    labelled("Preset", preset),
    labelled("Custom preset number", customPreset),
    metronome.element,
    reference.element,
  ),
);

const slot = element("select");
slot.name = "slot";
options(
  slot,
  Array.from(
    { length: PROMPT_SLOTS },
    (_, index) => `Slot ${String(index + 1)}`,
  ),
);
const promptText = element("input");
promptText.name = "prompt";
const promptFile = element("input");
promptFile.type = "file";
promptFile.name = "prompt-audio";
promptFile.accept = ".wav,.mp3";
const slots = element("ol");
slots.setAttribute("aria-label", "Prompt slots");
root.append(
  section(
    "Prompts",
    labelled("Slot", slot),
    labelled("Prompt text", promptText),
    button("Set prompt", () => {
      send({
        type: "prompt",
        slot: slot.selectedIndex,
        text: promptText.value,
      });
    }),
    labelled("Prompt audio", promptFile),
    button("Use prompt audio", () => {
      void upload(promptFile, (id) => ({
        type: "prompt_audio",
        upload_id: id,
        slot: slot.selectedIndex,
      }));
    }),
    slots,
  ),
);

const rights = element("select");
rights.name = "rights";
options(rights, RIGHTS);
rights.addEventListener("change", () => {
  send({
    type: "set_rights",
    rights: RIGHTS[rights.selectedIndex] ?? ("unknown" satisfies Rights),
  });
});
const chord = element("select");
chord.name = "chord";
const symbol = element("input");
symbol.name = "symbol";
const part = element("select");
part.name = "section";
const partName = element("input");
partName.name = "section-name";
const chordEdit = element("div");
chordEdit.className = "chart-edit";
chordEdit.setAttribute("role", "group");
chordEdit.setAttribute("aria-label", "Edit chord");
chordEdit.append(
  labelled("Chord", chord),
  labelled("New symbol", symbol),
  button("Change chord", () => {
    send({
      type: "edit_chord",
      index: chord.selectedIndex,
      symbol: symbol.value,
    });
  }),
);
const sectionEdit = element("div");
sectionEdit.className = "chart-edit";
sectionEdit.setAttribute("role", "group");
sectionEdit.setAttribute("aria-label", "Edit section");
sectionEdit.append(
  labelled("Section", part),
  labelled("New name", partName),
  button("Rename section", () => {
    send({
      type: "rename_section",
      index: part.selectedIndex,
      name: partName.value,
    });
  }),
);
root.append(
  section("Chart", labelled("Rights", rights), chordEdit, sectionEdit),
);

const engine = element("p");
const model = element("p");
root.append(section("Settings", engine, model));
if (workspace !== null) root.append(workspace.downloads);

document.addEventListener("keydown", (event) => {
  const action = keyAction({
    key: event.key,
    metaKey: event.metaKey,
    ctrlKey: event.ctrlKey,
    altKey: event.altKey,
    shiftKey: event.shiftKey,
    target: keyTarget(document.activeElement),
  });
  if (action !== null) {
    event.preventDefault();
    act(action);
  }
});

function drawLane(): void {
  const chart = state?.chart;
  const geometry = state?.chart_geometry;
  if (
    chart === undefined ||
    chart === null ||
    geometry === undefined ||
    geometry === null
  ) {
    return;
  }
  const current = chart.chords.find(
    (item) => item.start_beat <= beat && beat < item.end_beat,
  );
  lane.update({
    beat,
    geometry: {
      widthPx: Math.max(1, lane.element.clientWidth),
      beatsPerBar: geometry.beats_per_bar,
      beatPhase: geometry.beat_phase,
      barOrigin: geometry.bar_origin,
      zoom: 1,
    },
    sections: chart.sections.map((item) => ({
      name: item.name,
      startBeat: item.start_beat,
      endBeat: item.end_beat,
    })),
    chord: current?.symbol ?? null,
    reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
  });
  transport.setPosition(beat / chart.duration_beats);
}

function render(next: SessionState): void {
  if (next.transport === "playing" && state?.transport !== "playing") {
    heard.clear();
  }
  state = next;
  workspace?.update(next);
  loop = mergeLoop(loop, next);
  const chart = next.chart;
  title.textContent =
    chart === null ? "No song loaded." : `${chart.title} (${chart.source})`;
  const geometry = next.chart_geometry;
  meter.textContent =
    chart === null || geometry === null
      ? ""
      : `${chart.time_signature.join("/")}: ${String(geometry.beats_per_bar)} beats per bar, ${String(geometry.pulses_per_beat)} pulses per beat.`;
  transport.setPlaying(next.transport === "playing");
  transport.setTempoBounds(next.tempo_range);
  transport.setTempo(next.tempo_factor);
  const rate = chart === null ? 1 : (chart.tempo_bpm / 60) * next.tempo_factor;
  if (rate !== beatsPerSecond) {
    beatsPerSecond = rate;
    playhead = createPlayhead(rate);
  }
  transport.setPressed("melody", next.melody);
  transport.setPressed("drums", next.drums);
  transport.setPressed("record", next.recording);
  slider.value = String(Math.round(next.slider * 100));
  if (next.preset >= 4) {
    options(preset, [
      "Warm jazz / chiptune",
      "Folk / metal",
      "Bossa nova / synthwave",
      "String quartet / lo-fi",
      `Custom preset ${String(next.preset)}`,
    ]);
    const customOption = preset.options[4];
    if (customOption) customOption.value = String(next.preset);
    preset.value = String(next.preset);
    customPreset.value = String(next.preset);
  } else {
    preset.value = String(next.preset);
  }
  metronome.update(next.click);
  reference.update(next.reference, next.tempo_factor);
  slots.replaceChildren(
    ...next.slots.map((text, index) =>
      element(
        "li",
        `Slot ${String(index + 1)}: ${text === "" ? "empty" : text}`,
      ),
    ),
  );
  importPanel.chartLoaded(chart?.title ?? null, chart?.extractor ?? null);
  rights.value = String(RIGHTS.indexOf(chart?.rights ?? "unknown"));
  const keep = (select: HTMLSelectElement, labels: readonly string[]): void => {
    const selected = select.selectedIndex;
    options(select, labels);
    select.selectedIndex = Math.min(Math.max(selected, 0), labels.length - 1);
  };
  keep(
    chord,
    (chart?.chords ?? []).map(
      (item) => `Beat ${String(item.start_beat)}: ${item.symbol}`,
    ),
  );
  keep(
    part,
    (chart?.sections ?? []).map(
      (item) => `Beat ${String(item.start_beat)}: ${item.name}`,
    ),
  );
  engine.textContent = next.engine_health.online
    ? "Engine online."
    : "Engine offline until playback starts.";
  model.textContent = next.base_model.message;
  drawLane();
}

function receive(event: ServerEvent): void {
  switch (event.event) {
    case "setup_status":
      workspace?.updateSetup(event);
      break;
    case "state":
      status.textContent = "";
      render(event);
      break;
    case "clock":
      // A loop wrap is the only large backward step the conductor makes deliberately.
      if (event.audible_beat + 0.5 < beat) {
        playhead.reset();
      }
      beat = clockBeat(playhead, event.now_s, event.audible_beat);
      drawLane();
      break;
    case "heard":
      heard.update(event);
      break;
    case "import_progress":
      if (event.stage === "complete") heard.clear();
      importPanel.progress(event.stage);
      break;
    case "error":
      showError(errorText(event));
      if (!isImportNotice(event.code)) {
        importPanel.fail();
      }
      // A refused toggle has already moved its checkbox; put it back.
      if (state !== null) {
        render(state);
      }
      break;
  }
}

if (token === null) {
  showError(
    "This page needs its access token. Open the address ghostband prints, which ends in ?t=.",
  );
} else {
  connection = connect(socketUrl(location), token, {
    event: receive,
    closed() {
      status.textContent =
        "Disconnected from Ghostband. Restart ghostband and open the address it prints.";
    },
  });
}
