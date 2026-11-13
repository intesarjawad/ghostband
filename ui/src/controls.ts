/** The preset picker: bundled styles first, then user presets marked unmeasured. */

export interface Preset {
  readonly name: string;
  readonly a: string;
  readonly b: string;
  readonly measured: boolean;
}

export interface PresetChoice {
  readonly value: string;
  readonly label: string;
  readonly title: string;
}

export interface PresetControl {
  readonly element: HTMLLabelElement;
  update(presets: readonly Preset[], selected: number): void;
}

/** Say in words that a preset has no measurements, so the mark is not colour alone. */
export function presetLabel(preset: Preset): string {
  return preset.measured ? preset.name : `${preset.name} (unmeasured)`;
}

export function presetChoices(
  presets: readonly Preset[],
): readonly PresetChoice[] {
  return presets.map((preset, index) => ({
    value: String(index),
    label: presetLabel(preset),
    title: `${preset.a} / ${preset.b}`,
  }));
}

export function createPresetControl(
  document: Document,
  choose: (index: number) => void,
): PresetControl {
  const element = document.createElement("label");
  element.className = "controls-preset";
  element.textContent = "Preset";
  const select = document.createElement("select");
  select.name = "preset";
  select.addEventListener("change", () => {
    choose(select.selectedIndex);
  });
  element.append(select);
  const update = (presets: readonly Preset[], selected: number): void => {
    const options = presetChoices(presets).map((choice) => {
      const option = document.createElement("option");
      option.value = choice.value;
      option.textContent = choice.label;
      option.title = choice.title;
      return option;
    });
    select.replaceChildren(...options);
    select.value = String(selected);
  };
  return { element, update };
}

export interface MetronomeControl {
  readonly element: HTMLLabelElement;
  update(enabled: boolean): void;
}

/** Name the state in words so the click's on/off never rests on the tick alone. */
export function metronomeLabel(enabled: boolean): string {
  return enabled ? "Metronome on (K)" : "Metronome off (K)";
}

export function createMetronomeControl(
  document: Document,
  toggle: (enabled: boolean) => void,
): MetronomeControl {
  const element = document.createElement("label");
  element.className = "controls-metronome";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.name = "metronome";
  input.setAttribute("aria-keyshortcuts", "K");
  const text = document.createElement("span");
  element.append(input, text);
  const update = (enabled: boolean): void => {
    input.checked = enabled;
    text.textContent = metronomeLabel(enabled);
  };
  input.addEventListener("change", () => {
    update(input.checked);
    toggle(input.checked);
  });
  update(false);
  return { element, update };
}

export interface ReferenceControl {
  readonly element: HTMLLabelElement;
  update(enabled: boolean, tempoFactor: number): void;
}

/** The recording has no time stretch, so name the reason instead of failing silently. */
export const REFERENCE_TEMPO_LABEL = "Reference plays at 100 percent tempo";

export function referenceLabel(enabled: boolean, tempoFactor: number): string {
  if (tempoFactor !== 1) {
    return REFERENCE_TEMPO_LABEL;
  }
  return enabled ? "Reference on (O)" : "Reference off (O)";
}

export function createReferenceControl(
  document: Document,
  toggle: (enabled: boolean) => void,
): ReferenceControl {
  const element = document.createElement("label");
  element.className = "controls-reference";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.name = "reference";
  input.setAttribute("aria-keyshortcuts", "O");
  const text = document.createElement("span");
  element.append(input, text);
  const update = (enabled: boolean, tempoFactor: number): void => {
    const available = tempoFactor === 1;
    input.disabled = !available;
    input.checked = enabled && available;
    text.textContent = referenceLabel(enabled, tempoFactor);
  };
  input.addEventListener("change", () => {
    toggle(input.checked);
  });
  update(false, 1);
  return { element, update };
}
