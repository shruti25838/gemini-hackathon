// Room colour palettes and the typed-command parser.
//
// Split out of scene.js on purpose: scene.js imports three.js and touches the
// DOM on load, so nothing in it can be executed by a test. This file imports
// nothing and touches nothing, so tests/backend/test_room_colors.py runs the
// real parser rather than grepping for strings that look like one.
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

// Every swatch name, plus the spellings and near-misses somebody will type.
export const COLOR_NAMES = new Map([
  ...WALL_COLORS.map(([name, hex]) => [name.toLowerCase(), hex]),
  ...FLOOR_COLORS.map(([name, hex]) => [name.toLowerCase(), hex]),
  ["warm gray", "#d6cfc4"],
  ["light gray", "#c9c9c4"],
  ["dark gray", "#6a6d6b"],
  ["grey", "#d6cfc4"],
  ["gray", "#d6cfc4"],
  ["green", "#a8b89a"],
  ["blue", "#2b3a55"],
  ["orange", "#b5653f"],
  ["brown", "#6b4a2f"],
  ["black", "#3a3d3c"],
  ["cream", "#f4f2ed"],
  ["off white", "#f4f2ed"],
]);

export const UNKNOWN_COLOR = "I don't know that color.";

/**
 * Read "make the walls olive", "floor walnut", "sage walls" or a bare hex.
 *
 * Returns {target, hex, name} or {error}. Longest name first, so "light grey"
 * is not read as "grey", and "warm grey" is not read as "grey" either.
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
