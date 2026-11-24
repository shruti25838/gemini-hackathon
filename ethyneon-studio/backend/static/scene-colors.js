// Room colour palettes, the name lookup, the typed-command parser, and the
// hex / HSV conversions the wheel needs.
//
// Split out of scene.js on purpose: scene.js imports three.js and touches the
// DOM on load, so nothing in it can be executed by a test. This file imports
// nothing and touches nothing, so tests/backend/test_room_colors.py runs the
// real parser and the real conversions rather than grepping for strings that
// look like them.
//
// No model is involved. Colour names are a lookup, and a name that is not in
// it gets told so rather than guessed at.

export const WALL_COLORS = [
  ["White", "#f4f2ed"],
  ["Warm grey", "#d6cfc4"],
  ["Sage", "#a8b89a"],
  ["Olive", "#6f7a3f"],
  ["Navy", "#2b3a55"],
  ["Terracotta", "#b5653f"],
  ["Charcoal", "#3a3d3c"],
];

export const FLOOR_COLORS = [
  ["Oak", "#c9a16b"],
  ["Walnut", "#6b4a2f"],
  ["Light grey", "#c9c9c4"],
  ["Dark grey", "#6a6d6b"],
  ["Concrete", "#9a9a93"],
];

// Every swatch name resolves to that swatch's own hex, so typing "olive"
// lights up the olive swatch rather than landing a shade beside it. The rest
// are ordinary names somebody will reach for; the wheel covers everything
// else. "pink" was the one that started this: the lookup did not have it.
export const COLOR_NAMES = new Map([
  ...WALL_COLORS.map(([name, hex]) => [name.toLowerCase(), hex]),
  ...FLOOR_COLORS.map(([name, hex]) => [name.toLowerCase(), hex]),

  ["pink", "#ffc0cb"],
  ["hot pink", "#ff69b4"],
  ["light pink", "#ffd9e2"],
  ["rose", "#d4607a"],
  ["red", "#c0392b"],
  ["maroon", "#6d1f2b"],
  ["orange", "#e07b39"],
  ["peach", "#ffd2b3"],
  ["yellow", "#e8c547"],
  ["mustard", "#c9a227"],
  ["lime", "#a3d94a"],
  ["green", "#4f8f52"],
  ["mint", "#a8e6c4"],
  ["forest", "#2f5d3a"],
  ["teal", "#2f7d7d"],
  ["aqua", "#7fd8d8"],
  ["sky blue", "#8fc7e8"],
  ["blue", "#2b5fa8"],
  ["indigo", "#4b3f91"],
  ["purple", "#7a4f9e"],
  ["lavender", "#cdb8e8"],
  ["magenta", "#b53f8f"],
  ["beige", "#e4d8c3"],
  ["cream", "#f2ebdc"],
  ["tan", "#c9a16b"],
  ["brown", "#6b4a2f"],
  ["black", "#1f2220"],
  // A plain neutral, not the warm grey swatch: somebody asking for "grey"
  // is not asking for the beige one.
  ["grey", "#a6a6a3"],
  ["silver", "#c9c9c4"],

  // Spellings and near-misses somebody will actually type.
  ["warm gray", "#d6cfc4"],
  ["light gray", "#c9c9c4"],
  ["dark gray", "#6a6d6b"],
  ["gray", "#a6a6a3"],
  ["off white", "#f4f2ed"],
  ["sky", "#8fc7e8"],
  ["forest green", "#2f5d3a"],
  ["dark green", "#2f5d3a"],
]);

export const UNKNOWN_COLOR = "I don't know that color.";
export const HEX_PATTERN = /^#[0-9a-f]{6}$/;

/**
 * Read "make the walls olive", "floor walnut", "sage walls" or a bare hex.
 *
 * Returns {target, hex, name} or {error}. Longest name first, so "light grey"
 * is not read as "grey", "hot pink" is not read as "pink", and "sky blue" is
 * not read as "blue".
 */
export function parseColorCommand(text) {
  const cleaned = String(text ?? "").toLowerCase();
  const words = " " + cleaned.replace(/[^a-z\s#0-9]/g, " ").replace(/\s+/g, " ").trim() + " ";
  if (!words.trim()) return {error: "Type something like: make the walls olive."};

  const target = /\s(?:floors?|ground)\s/.test(words) ? "floor"
    : /\s(?:walls?|wall)\s/.test(words) ? "walls" : null;
  if (!target) return {error: "Say whether you mean the walls or the floor."};

  const hex = cleaned.match(/#[0-9a-f]{6}\b/)?.[0];
  if (hex) return {target, hex, name: hex};

  const names = [...COLOR_NAMES.keys()].sort((a, b) => b.length - a.length);
  const found = names.find((name) => words.includes(" " + name + " "));
  if (!found) return {error: UNKNOWN_COLOR};
  return {target, hex: COLOR_NAMES.get(found), name: found};
}

/** The name of a colour if one of ours matches it exactly, else the hex. */
export function colorLabel(hex) {
  const value = String(hex ?? "").toLowerCase();
  for (const [name, candidate] of COLOR_NAMES) {
    if (candidate === value) return name;
  }
  return value;
}

// --------------------------------------------------------------------------
// Hex and HSV. The wheel works in HSV: hue around the ring, saturation and
// value across the square. Plain arithmetic, so a test can check the round
// trip rather than trust it.
// --------------------------------------------------------------------------

export function hexToRgb(hex) {
  const value = String(hex ?? "").toLowerCase();
  if (!HEX_PATTERN.test(value)) return null;
  return {
    r: parseInt(value.slice(1, 3), 16),
    g: parseInt(value.slice(3, 5), 16),
    b: parseInt(value.slice(5, 7), 16),
  };
}

export function rgbToHex({r, g, b}) {
  const byte = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
  return `#${byte(r)}${byte(g)}${byte(b)}`;
}

/** {h: 0-360, s: 0-1, v: 0-1}, or null when the hex is not a hex. */
export function hexToHsv(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return null;
  const r = rgb.r / 255;
  const g = rgb.g / 255;
  const b = rgb.b / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const span = max - min;
  let h = 0;
  if (span !== 0) {
    if (max === r) h = 60 * (((g - b) / span) % 6);
    else if (max === g) h = 60 * ((b - r) / span + 2);
    else h = 60 * ((r - g) / span + 4);
  }
  if (h < 0) h += 360;
  return {h, s: max === 0 ? 0 : span / max, v: max};
}

export function hsvToHex({h, s, v}) {
  const hue = ((h % 360) + 360) % 360;
  const saturation = Math.max(0, Math.min(1, s));
  const value = Math.max(0, Math.min(1, v));
  const c = value * saturation;
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
  const m = value - c;
  const [r, g, b] = hue < 60 ? [c, x, 0]
    : hue < 120 ? [x, c, 0]
    : hue < 180 ? [0, c, x]
    : hue < 240 ? [0, x, c]
    : hue < 300 ? [x, 0, c]
    : [c, 0, x];
  return rgbToHex({r: (r + m) * 255, g: (g + m) * 255, b: (b + m) * 255});
}
