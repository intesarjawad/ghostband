import { ROWS, STEP, type Roll } from "./roll";

/** Where the roll sits on screen, in CSS pixels. "Along" is the direction the paper travels. */
export interface Layout {
  readonly vertical: boolean;
  readonly width: number;
  readonly height: number;
  readonly dpr: number;
  /** Tracker bar, along the travel axis. */
  readonly bar: number;
  /** Paper edges and the band of pitch rows inside them, across the travel axis. */
  readonly paper: readonly [number, number];
  readonly zone: readonly [number, number];
  readonly perBeat: number;
  /** Where the paper meets the spool, along the travel axis, and the spool's bare and flange radii. */
  readonly spool: readonly [number, number, number];
  /** Paper thickness in pixels, for how fast the spool's radius changes. */
  readonly thickness: number;
  /** First beat of the paper (the leader's tip), where the tab reaches full width, its half width at the tip. */
  readonly leader: readonly [number, number, number];
  readonly holeRadius: readonly [number, number];
  /** One hole punched in the leader where it rests over the bar: its beat, radius and offset from the middle. */
  readonly pilot: readonly [number, number, number];
}

export interface Frame {
  readonly now: number;
  /** Loudness of the take in the low, middle and high registers, 0 to 1. */
  readonly energy: readonly [number, number, number];
  /** 0 before the lamp has warmed up, 1 once it is on. */
  readonly lamp: number;
  /** Room light: 1 to read by, 0 while the take plays and only the lamp is on. */
  readonly house: number;
  /** How long a lit hole keeps glowing after it crosses the bar: 1 normally, 0 under reduced motion. */
  readonly afterglow: number;
}

const VERTEX = `#version 300 es
void main() {
  vec2 p = vec2(gl_VertexID == 1 ? 3.0 : -1.0, gl_VertexID == 2 ? 3.0 : -1.0);
  gl_Position = vec4(p, 0.0, 1.0);
}`;

const SCENE = `#version 300 es
precision highp float;
precision highp int;
uniform vec2 uView;
uniform float uScale;
uniform float uVertical;
uniform float uBar;
uniform vec2 uPaper;
uniform vec2 uZone;
uniform float uPpb;
uniform float uNow;
uniform vec4 uSpool;
uniform vec4 uLeader;
uniform vec2 uHoleR;
uniform vec3 uPilot;
uniform vec3 uEnergy;
uniform float uLamp;
uniform float uHouse;
uniform float uAfterglow;
uniform int uPass;
uniform highp sampler2D uHoles;
uniform int uSteps;
uniform sampler2D uBloomA;
out vec4 color;

const float ROWS = ${String(ROWS)}.0;
const float STEP = ${String(STEP)};
const vec3 PAPER = vec3(0.925, 0.882, 0.776);
const vec3 CASE = vec3(0.071, 0.051, 0.035);
const vec3 HOLLOW = vec3(0.045, 0.031, 0.02);
const vec3 BRASS = vec3(0.79, 0.62, 0.33);

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
// Short paper fibres, a little longer along the roll than across it; long streaks read as wood.
float fibre(float along, float across) {
  return 0.5 * noise(vec2(along * 0.07, across * 0.2)) + 0.3 * noise(vec2(along * 0.25, across * 0.6))
    + 0.2 * noise(vec2(along * 0.8, across * 1.4));
}
vec3 light(vec3 base, vec3 l) { return base + (1.0 - base) * (1.0 - exp(-l)); }

void main() {
  vec2 px = vec2(gl_FragCoord.x, uView.y * uScale - gl_FragCoord.y) / uScale;
  bool vert = uVertical > 0.5;
  float a = vert ? px.y : px.x;
  float c = vert ? px.x : px.y;
  float dir = vert ? -1.0 : 1.0;
  float aa = 0.6 / uScale;
  float sC = uSpool.x, R = uSpool.y;
  float mid = 0.5 * (uPaper.x + uPaper.y);

  // Which beat of the paper is under this pixel: flat in front of the spool, wrapped round it behind.
  float b = -1e5;
  float facing = 1.0;
  bool wrapped = false;
  if (a >= sC) {
    b = uNow + dir * (a - uBar) / uPpb;
  } else if (a > sC - R) {
    float u = (sC - a) / R;
    facing = sqrt(max(0.0, 1.0 - u * u));
    b = uNow + dir * (sC - uBar) / uPpb - dir * R * asin(u) / uPpb;
    wrapped = true;
  }
  float halfWidth = mix(uLeader.z, 0.5 * (uPaper.y - uPaper.x), clamp((b - uLeader.x) / (uLeader.y - uLeader.x), 0.0, 1.0));
  // The tab's grommet scales with the tab, so a short phone tab keeps it clear of the bar.
  float tab = uLeader.z / 70.0;
  float grommet = length(vec2((b - uLeader.x) * uPpb - 52.0 * tab, c - mid)) / tab;
  float paper = step(uLeader.x, b) * step(b, uLeader.w)
    * smoothstep(halfWidth + aa, halfWidth - aa, abs(c - mid))
    * smoothstep(17.5 - aa, 17.5 + aa, grommet);

  // Behind the paper: the case, the brass tracker bar, the spool and its flanges.
  float da = a - uBar;
  vec3 back = CASE * (0.85 + 0.3 * noise(px * vec2(0.004, 0.08)));
  // The tracker bar runs under the paper and out past both edges to its brackets.
  float beyond = max(uPaper.x - c, c - uPaper.y);
  float dark = mix(0.3, 1.0, uHouse);
  if (abs(da) < 7.0 && beyond < 24.0) {
    float t = da / 7.0;
    float shine = 0.35 + 0.75 * exp(-pow((t + 0.2) * 2.2, 2.0));
    back = BRASS * shine * (0.75 + 0.25 * cos(t * 1.57)) * dark;
  }
  if (beyond > 12.0 && beyond < 24.0 && abs(da) < 12.0) {
    float bevel = smoothstep(12.0, 10.0, abs(da)) * smoothstep(24.0, 22.0, beyond) * smoothstep(12.0, 14.0, beyond);
    back = BRASS * (0.32 + 0.3 * bevel) * dark;
    if (length(vec2(da, beyond - 18.0)) < 2.4) back = vec3(0.1, 0.07, 0.04);
  }
  float sr = (a - sC) / R;
  bool onSpool = abs(sr) < 1.0 && c > uPaper.x - 4.0 && c < uPaper.y + 4.0;
  if (onSpool) {
    float nz = sqrt(1.0 - sr * sr);
    float arc = asin(sr) * R + uSpool.z;
    vec3 wood = vec3(0.23, 0.14, 0.08) * (0.7 + 0.5 * noise(vec2(arc * 0.6, c * 0.03)));
    vec3 wound = PAPER * (0.82 + 0.1 * fibre(arc, c));
    vec3 body = uSpool.z > 1.0 ? wound : wood;
    // Bare wood takes a highlight; wound paper only a faint one, so its edge fades into the sheet.
    float shine = (uSpool.z > 1.0 ? 0.05 : 0.25) * pow(max(0.0, 1.0 - abs(sr + 0.4) * 3.5), 3.0);
    back = (body * (0.18 + 0.82 * pow(nz, 0.9)) + vec3(1.0, 0.9, 0.75) * shine) * mix(0.35, 1.0, uHouse);
    // A brass rim light on both edges keeps the spool's shape when the room is dark.
    back += BRASS * 0.9 * pow(abs(sr), 4.0);
  }
  float fr = (a - sC) / uSpool.w;
  bool flange = abs(fr) < 1.0 && ((c > uPaper.x - 14.0 && c < uPaper.x - 4.0) || (c > uPaper.y + 4.0 && c < uPaper.y + 14.0));
  if (flange) {
    float nz = sqrt(1.0 - fr * fr);
    back = (BRASS * (0.2 + 0.85 * nz) + vec3(0.3) * pow(max(0.0, 1.0 - abs(fr + 0.35) * 4.0), 4.0)) * dark;
  }
  if (abs(fr) < 0.12 && (abs(c - uPaper.x + 19.0) < 5.0 || abs(c - uPaper.y - 19.0) < 5.0)) back = vec3(0.12, 0.09, 0.06);

  // The paper face, its fibre, its curl at the edges and its bow over the bar.
  float along = b * uPpb;
  float fib = fibre(along, c);
  vec3 face = PAPER * (0.95 + 0.04 * fib) - 0.012 * hash(floor(px * uScale));
  float edge = min(c - uPaper.x, uPaper.y - c);
  face *= 1.0 - 0.2 * exp(-edge / 6.0);
  float eyelet = abs(grommet - 19.5);
  if (b > uLeader.x && eyelet < 2.2) face = BRASS * (0.55 + 0.55 * smoothstep(2.2, 0.0, eyelet));
  face *= mix(0.2, 1.0, pow(facing, 0.8));

  // The holes. Each one is lit only by the lamp behind the bar, by the take's loudness in its register.
  float row = vert ? (c - uZone.x) : (uZone.y - c);
  row = row / ((uZone.y - uZone.x) / ROWS);
  int r = int(floor(row));
  float acrossPx = (row - float(r) - 0.5) * (uZone.y - uZone.x) / ROWS;
  float hole = 0.0, glow = 0.0, wide = 0.0, white = 0.0, halo = 0.0;
  // On a phone only every other step is punched, so a chain stays 4 px holes with gaps, not a bar.
  int skip = vert ? 2 : 1;
  // The bar's slot is narrower than the gap between holes in a chain, so one hole of a row lights at a time.
  float slot = 0.45 * STEP * float(skip) * uPpb;
  // A lit hole's core is at least 6 px across, however small the hole.
  float core = vert ? 3.0 : 0.0;
  // On a phone the afterglow stays short, so it never runs under the readout below the bar.
  float trail = vert ? 10.0 : 40.0;
  if (r >= 0 && r < int(ROWS) && paper > 0.0) {
    int k0 = int(floor(b / (STEP * float(skip)) + 0.5)) * skip;
    for (int k = k0 - skip; k <= k0 + skip; k += skip) {
      if (k < 0 || k >= uSteps) continue;
      float kind = texelFetch(uHoles, ivec2(k, r), 0).r * 255.0;
      if (kind < 0.5) continue;
      float radius = kind > 1.5 ? uHoleR.y : uHoleR.x;
      float d = length(vec2((b - float(k) * STEP) * uPpb, acrossPx));
      float cover = smoothstep(radius + aa, radius - aa, d);
      // Ahead of the bar is positive; a hole that has crossed keeps a tail as long as the take is loud.
      float bar = wrapped ? 1e3 : (float(k) * STEP - uNow) * uPpb;
      float energy = kind > 1.5 ? uEnergy.x : uEnergy.y;
      float flare = uLamp * exp(-0.5 * bar * bar / (slot * slot)) * (0.6 + 2.0 * energy);
      // Once across, a hole fades like phosphor: smoothly, over a distance set by how loud the take is.
      float tail = bar < 0.0 ? uLamp * uAfterglow * exp(bar / (6.0 + trail * energy)) * (0.15 + 0.9 * energy) : 0.0;
      float lit = max(radius, core);
      float burning = smoothstep(lit + aa, lit - aa, d);
      float profile = 1.0 - clamp(d / lit, 0.0, 1.0);
      hole = max(hole, cover);
      glow = max(glow, 0.7 * cover * tail * (0.55 + 0.9 * profile));
      // The hole on the bar burns solid white, edge to edge.
      white = max(white, burning * min(flare * 2.0, 1.0));
      // The afterglow blooms too, so a lit chain smears into one fading streak instead of a row of dots.
      wide = max(wide, (flare + 0.45 * tail) * exp(-0.5 * d * d / (2.0 * lit * lit)));
    }
  }
  if (paper > 0.0) {
    float d = length(vec2((b - uPilot.x) * uPpb, c - mid - uPilot.z));
    float bar = wrapped ? 1e3 : (uPilot.x - uNow) * uPpb;
    float lit = uLamp * 1.4 * exp(-bar * bar / 200.0);
    float cover = smoothstep(uPilot.y + aa, uPilot.y - aa, d);
    hole = max(hole, cover);
    glow = max(glow, cover * lit * (0.55 + 0.9 * (1.0 - clamp(d / uPilot.y, 0.0, 1.0))));
    wide = max(wide, cover * lit);
    // A small halo of its own, so the pilot reads as lit even with the room lights up.
    halo = lit * (1.0 - cover) * exp(-max(d - uPilot.y, 0.0) / (0.7 * uPilot.y));
  }
  // Paper wound on a roll shows only its outer layer, so its holes stop where it leaves the roll.
  if (wrapped) hole = 0.0;
  hole *= paper;
  glow *= paper;
  white *= paper;
  halo *= paper;

  if (uPass == 1) {
    // The treble, which no hole plays, widens the glow round the holes that are lit.
    color = vec4(vec3(wide * (3.5 + 2.0 * uEnergy.z)), 1.0);
    return;
  }
  // With the room lights down the case goes near black and the lamp turns the paper into a
  // lightbox, its fibres showing and the holes still to come as silhouettes.
  vec3 room = mix(vec3(0.62, 0.61, 0.68), vec3(1.0), uHouse);
  // Dimmed, the paper keeps its warmth instead of going grey.
  vec3 sheet = mix(vec3(0.045, 0.032, 0.022), vec3(1.0), uHouse) * mix(vec3(1.0, 0.93, 0.8), vec3(1.0), smoothstep(0.75, 1.0, uHouse));
  // Hot along the bar, where the lamp is, falling off along the roll; the grain is fibres 2 to 4 px long.
  float grain = 0.55 * noise(vec2(along * 0.33, c * 0.5)) + 0.45 * noise(vec2(along * 0.7, c * 1.1));
  float band = exp(-abs(da) / (vert ? 260.0 : 520.0));
  vec3 lightbox = vec3(1.0, 0.76, 0.48) * (0.55 + 0.4 * band) * (0.86 + 0.28 * grain);
  float behind = (1.0 - uHouse) * uLamp;
  // The paper wound on the spool has no lamp behind it, only what spills off the lightbox.
  vec3 vellum = face * (sheet + lightbox * behind * (wrapped ? 0.35 : 1.0));
  vec3 col = mix(back * room, mix(vellum, HOLLOW * sheet, hole), paper);
  // A hole in the afterglow runs from ember to white as more light comes through it.
  vec3 hot = mix(vec3(1.0, 0.5, 0.12), vec3(1.0, 0.98, 0.92), smoothstep(0.3, 1.1, glow));
  col = mix(col, hot, clamp(glow * 1.8, 0.0, 1.0));
  // The wide halo: each hole on the bar lights the vellum around it, about three times the bloom's
  // reach, warm white near the hole and reddening at its edge as a bright source fringes on film.
  float wash = 0.0;
  if (paper > 0.0 && !wrapped) {
    float rowPx = (uZone.y - uZone.x) / ROWS;
    float sigma = vert ? 10.0 : 20.0;
    int kb = int(floor(uNow / (STEP * float(skip)) + 0.5)) * skip;
    for (int dr = -4; dr <= 4; dr++) {
      int rr = r + dr;
      if (rr < 0 || rr >= int(ROWS)) continue;
      for (int k = kb - skip; k <= kb + skip; k += skip) {
        if (k < 0 || k >= uSteps) continue;
        float kind = texelFetch(uHoles, ivec2(k, rr), 0).r * 255.0;
        if (kind < 0.5) continue;
        float bar = (float(k) * STEP - uNow) * uPpb;
        float energy = kind > 1.5 ? uEnergy.x : uEnergy.y;
        vec2 dv = vec2((b - float(k) * STEP) * uPpb, acrossPx - float(dr) * rowPx);
        wash += exp(-0.5 * bar * bar / (slot * slot)) * (0.6 + 2.0 * energy) * exp(-0.5 * dot(dv, dv) / (sigma * sigma));
      }
    }
    wash *= uLamp * paper * mix(1.0, 0.25, uHouse) * (0.7 + 0.6 * grain);
  }
  vec3 fringe = mix(vec3(1.0, 0.42, 0.18), vec3(1.0, 0.86, 0.62), smoothstep(0.1, 1.2, wash));
  col = light(col, fringe * wash * 0.4);
  vec2 uv = gl_FragCoord.xy / (uView * uScale);
  // The halo spreads about two hole pitches into the paper, brighter where the fibres are thin.
  // With the lights up a glow cannot read as light on cream, so the cores carry it alone.
  float bloom = texture(uBloomA, uv).r * mix(1.0, 0.25, uHouse) * mix(1.0, 0.7 + 0.6 * grain, paper);
  col = mix(col, vec3(1.0, 0.6, 0.22), smoothstep(0.15, 0.8, bloom) * 0.35);
  col = light(col, vec3(1.0, 0.78, 0.5) * bloom * 3.0);
  col = mix(col, vec3(1.0, 0.55, 0.16), clamp(halo, 0.0, 1.0) * 0.75);
  col = mix(col, vec3(1.0, 0.99, 0.96), clamp(white, 0.0, 1.0));
  // The brass rim light catches the far edge of the paper wound on the spool,
  // and a soft sheen across its middle rounds the wound paper into a cylinder.
  if (wrapped) {
    float u = (sC - a) / R;
    col += paper * (BRASS * 0.6 * pow(u, 4.0) * (1.0 - 0.6 * uHouse) + vec3(0.09, 0.07, 0.05) * exp(-pow((u - 0.45) / 0.3, 2.0)));
  }
  // The bar's lips cross the paper either side of the slot, over the leader too: each a dark brass
  // edge with a bright highlight along its inner side, where it catches the lamp.
  float inner = abs(da) - uPilot.y - 3.0;
  vec3 lipColor = mix(BRASS * 0.5, vec3(1.0, 0.88, 0.6), smoothstep(0.2, -0.4, inner)) * mix(0.8, 1.0, uHouse);
  if (!wrapped && paper > 0.0) col = mix(col, lipColor, paper * smoothstep(1.6, 1.0, abs(inner + 0.3)));
  color = vec4(col + (hash(gl_FragCoord.xy) - 0.5) / 255.0, 1.0);
}`;

const BLUR = `#version 300 es
precision highp float;
uniform sampler2D uSource;
uniform vec2 uStep;
uniform vec2 uSize;
out vec4 color;
void main() {
  vec2 uv = gl_FragCoord.xy / uSize;
  float w[5] = float[](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);
  vec3 sum = texture(uSource, uv).rgb * w[0];
  for (int i = 1; i < 5; i++) {
    sum += texture(uSource, uv + uStep * float(i)).rgb * w[i];
    sum += texture(uSource, uv - uStep * float(i)).rgb * w[i];
  }
  color = vec4(sum, 1.0);
}`;

interface Target {
  texture: WebGLTexture;
  buffer: WebGLFramebuffer;
  width: number;
  height: number;
}

export interface Renderer {
  draw(layout: Layout, frame: Frame): void;
  dispose(): void;
}

function compile(gl: WebGL2RenderingContext, fragment: string): WebGLProgram {
  const program = gl.createProgram();
  for (const [type, source] of [
    [gl.VERTEX_SHADER, VERTEX],
    [gl.FRAGMENT_SHADER, fragment],
  ] as const) {
    const shader = gl.createShader(type);
    if (!shader) throw new Error("WebGL could not create a shader");
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(shader) ?? "shader did not compile");
    }
    gl.attachShader(program, shader);
  }
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) ?? "shader did not link");
  }
  return program;
}

/** Draw the roll with WebGL2, or return null where WebGL2 is not available. */
export function createRenderer(
  canvas: HTMLCanvasElement,
  roll: Roll,
): Renderer | null {
  const gl = canvas.getContext("webgl2", { antialias: false, alpha: false });
  if (!gl) return null;
  const half = gl.getExtension("EXT_color_buffer_float") !== null;
  const scene = compile(gl, SCENE);
  const blur = compile(gl, BLUR);
  const uniform = (
    program: WebGLProgram,
    name: string,
  ): WebGLUniformLocation | null => gl.getUniformLocation(program, name);

  const holes = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, holes);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.texImage2D(
    gl.TEXTURE_2D,
    0,
    gl.R8,
    roll.steps,
    ROWS,
    0,
    gl.RED,
    gl.UNSIGNED_BYTE,
    roll.holes,
  );
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);

  const targets: Target[] = [];
  const target = (width: number, height: number): Target => {
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texStorage2D(
      gl.TEXTURE_2D,
      1,
      half ? gl.RGBA16F : gl.RGBA8,
      width,
      height,
    );
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const buffer = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, buffer);
    gl.framebufferTexture2D(
      gl.FRAMEBUFFER,
      gl.COLOR_ATTACHMENT0,
      gl.TEXTURE_2D,
      texture,
      0,
    );
    return { texture, buffer, width, height };
  };
  let size = "";
  let emission!: Target, nearA!: Target, nearB!: Target;
  const resize = (width: number, height: number): void => {
    const key = `${String(width)}x${String(height)}`;
    if (key === size) return;
    size = key;
    for (const t of targets.splice(0)) {
      gl.deleteTexture(t.texture);
      gl.deleteFramebuffer(t.buffer);
    }
    const quarter = [
      Math.max(1, width >> 2),
      Math.max(1, height >> 2),
    ] as const;
    emission = target(...quarter);
    nearA = target(...quarter);
    nearB = target(...quarter);
    targets.push(emission, nearA, nearB);
  };

  const sceneUniforms = (
    layout: Layout,
    frame: Frame,
    scale: number,
    pass: number,
  ): void => {
    gl.useProgram(scene);
    const set2 = (name: string, x: number, y: number): void => {
      gl.uniform2f(uniform(scene, name), x, y);
    };
    const set1 = (name: string, x: number): void => {
      gl.uniform1f(uniform(scene, name), x);
    };
    const wound = layout.vertical
      ? Math.max(0, roll.end - frame.now) * layout.perBeat
      : Math.max(
          0,
          frame.now -
            layout.leader[0] -
            (layout.bar - layout.spool[0]) / layout.perBeat,
        ) * layout.perBeat;
    const radius = Math.sqrt(
      layout.spool[1] ** 2 + (wound * layout.thickness) / Math.PI,
    );
    set2("uView", layout.width, layout.height);
    set1("uScale", scale);
    set1("uVertical", layout.vertical ? 1 : 0);
    set1("uBar", layout.bar);
    set2("uPaper", ...layout.paper);
    set2("uZone", ...layout.zone);
    set1("uPpb", layout.perBeat);
    set1("uNow", frame.now);
    gl.uniform4f(
      uniform(scene, "uSpool"),
      layout.spool[0],
      radius,
      wound,
      layout.spool[2],
    );
    gl.uniform4f(uniform(scene, "uLeader"), ...layout.leader, roll.end + 3);
    set2("uHoleR", ...layout.holeRadius);
    gl.uniform3f(uniform(scene, "uPilot"), ...layout.pilot);
    gl.uniform3f(uniform(scene, "uEnergy"), ...frame.energy);
    set1("uLamp", frame.lamp);
    set1("uHouse", frame.house);
    set1("uAfterglow", frame.afterglow);
    gl.uniform1i(uniform(scene, "uPass"), pass);
    gl.uniform1i(uniform(scene, "uSteps"), roll.steps);
    gl.uniform1i(uniform(scene, "uHoles"), 0);
    gl.uniform1i(uniform(scene, "uBloomA"), 1);
  };

  let spread = 1;
  const blurPass = (from: Target, to: Target, dx: number, dy: number): void => {
    gl.bindFramebuffer(gl.FRAMEBUFFER, to.buffer);
    gl.viewport(0, 0, to.width, to.height);
    gl.useProgram(blur);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, from.texture);
    gl.uniform1i(uniform(blur, "uSource"), 0);
    gl.uniform2f(
      uniform(blur, "uStep"),
      (dx * spread) / to.width,
      (dy * spread) / to.height,
    );
    gl.uniform2f(uniform(blur, "uSize"), to.width, to.height);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };

  return {
    draw(layout, frame) {
      const width = Math.round(layout.width * layout.dpr);
      const height = Math.round(layout.height * layout.dpr);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      resize(width, height);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, holes);

      gl.bindFramebuffer(gl.FRAMEBUFFER, emission.buffer);
      gl.viewport(0, 0, emission.width, emission.height);
      sceneUniforms(layout, frame, emission.width / layout.width, 1);
      gl.drawArrays(gl.TRIANGLES, 0, 3);

      // Four taps a side of 5 CSS px (2.8 on a phone, where rows sit closer) spread the halo about two hole pitches.
      spread = (layout.vertical ? 0.7 : 1.25) * layout.dpr;
      blurPass(emission, nearA, 1, 0);
      blurPass(nearA, nearB, 0, 1);

      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, width, height);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, holes);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, nearB.texture);
      sceneUniforms(layout, frame, layout.dpr, 0);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
    dispose() {
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    },
  };
}
