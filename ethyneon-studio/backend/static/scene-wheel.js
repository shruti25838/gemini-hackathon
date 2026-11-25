// A colour wheel drawn on a plain 2D canvas: a hue ring with a
// saturation-and-value square inside it.
//
// No library and no CDN. The only import is our own scene-colors.js, which is
// arithmetic and a lookup table. Everything here is canvas and pointer events.
//
// The wheel reports two different things, and the difference matters. While a
// pointer is down it calls onPreview on every move, which recolours the three
// materials already in the viewer so the room follows the thumb at frame rate.
// On release it calls onCommit once, which is what actually writes through the
// guarded edit route. Writing on every move would put a revision in the scene
// file for every pixel of a drag.

import { hexToHsv, hsvToHex } from "./scene-colors.js";

const RING_OUTER = 0.5;     // of the canvas width
const RING_INNER = 0.38;
const SQUARE = 0.5;         // side, as a fraction of the inner diameter
const DEFAULT = "#a8b89a";

export class ColorWheel {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{onPreview: (hex: string) => void, onCommit: (hex: string) => void}} handlers
   */
  constructor(canvas, {onPreview, onCommit} = {}) {
    this.canvas = canvas;
    this.onPreview = onPreview;
    this.onCommit = onCommit;
    this.hsv = hexToHsv(DEFAULT);
    this.dragging = null;
    this.ctx = canvas.getContext("2d");

    // Pointer coordinates come from the canvas rect, never from
    // devicePixelRatio or page coordinates, so the wheel is correct at any
    // browser zoom. The same mistake cost a working drag once already.
    const local = (event) => {
      const bounds = canvas.getBoundingClientRect();
      return {
        x: ((event.clientX - bounds.left) / bounds.width) * canvas.width,
        y: ((event.clientY - bounds.top) / bounds.height) * canvas.height,
      };
    };

    canvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const point = local(event);
      this.dragging = this.zoneAt(point);
      if (!this.dragging) return;
      canvas.setPointerCapture(event.pointerId);
      this.apply(point);
      event.preventDefault();
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!this.dragging) return;
      this.apply(local(event));
      event.preventDefault();
    });
    const release = (event) => {
      if (!this.dragging) return;
      this.dragging = null;
      if (canvas.hasPointerCapture?.(event.pointerId)) {
        canvas.releasePointerCapture(event.pointerId);
      }
      this.onCommit?.(this.hex());
    };
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", release);

    this.draw();
  }

  hex() {
    return hsvToHex(this.hsv);
  }

  /** Point the wheel at a colour without firing any handler. */
  setHex(hex) {
    const hsv = hexToHsv(hex);
    if (!hsv) return false;
    // A grey has no meaningful hue, so keep the hue the shopper last chose
    // rather than snapping the ring to red every time they pick white.
    this.hsv = hsv.s === 0 ? {...hsv, h: this.hsv.h} : hsv;
    this.draw();
    return true;
  }

  geometry() {
    const size = this.canvas.width;
    const half = size / 2;
    return {
      size, half,
      outer: size * RING_OUTER,
      inner: size * RING_INNER,
      side: size * RING_INNER * 2 * SQUARE,
    };
  }

  /** "ring", "square", or null for the gap between them. */
  zoneAt({x, y}) {
    const {half, outer, inner, side} = this.geometry();
    const distance = Math.hypot(x - half, y - half);
    if (distance <= outer && distance >= inner) return "ring";
    if (Math.abs(x - half) <= side / 2 && Math.abs(y - half) <= side / 2) return "square";
    return null;
  }

  apply({x, y}) {
    const {half, side} = this.geometry();
    if (this.dragging === "ring") {
      const angle = Math.atan2(y - half, x - half);
      this.hsv = {...this.hsv, h: ((angle * 180) / Math.PI + 360) % 360};
    } else {
      const left = half - side / 2;
      const top = half - side / 2;
      this.hsv = {
        ...this.hsv,
        s: Math.max(0, Math.min(1, (x - left) / side)),
        v: Math.max(0, Math.min(1, 1 - (y - top) / side)),
      };
    }
    this.draw();
    this.onPreview?.(this.hex());
  }

  draw() {
    const ctx = this.ctx;
    const {size, half, outer, inner, side} = this.geometry();
    ctx.clearRect(0, 0, size, size);

    // Hue ring, one degree at a time. A conic gradient would be fewer lines
    // but is not available everywhere and this is drawn once per change.
    const step = (Math.PI / 180) * 1.2;
    for (let angle = 0; angle < Math.PI * 2; angle += step) {
      ctx.beginPath();
      ctx.arc(half, half, (outer + inner) / 2, angle, angle + step * 1.3);
      ctx.strokeStyle = `hsl(${(angle * 180) / Math.PI} 100% 50%)`;
      ctx.lineWidth = outer - inner;
      ctx.stroke();
    }

    // Saturation and value square for the current hue: the pure hue, washed
    // out to white across, then down to black.
    const left = half - side / 2;
    const top = half - side / 2;
    ctx.fillStyle = hsvToHex({h: this.hsv.h, s: 1, v: 1});
    ctx.fillRect(left, top, side, side);
    const toWhite = ctx.createLinearGradient(left, 0, left + side, 0);
    toWhite.addColorStop(0, "rgba(255,255,255,1)");
    toWhite.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = toWhite;
    ctx.fillRect(left, top, side, side);
    const toBlack = ctx.createLinearGradient(0, top, 0, top + side);
    toBlack.addColorStop(0, "rgba(0,0,0,0)");
    toBlack.addColorStop(1, "rgba(0,0,0,1)");
    ctx.fillStyle = toBlack;
    ctx.fillRect(left, top, side, side);

    // Where we are: a marker on the ring and one in the square.
    const angle = (this.hsv.h * Math.PI) / 180;
    this.marker(
      half + Math.cos(angle) * ((outer + inner) / 2),
      half + Math.sin(angle) * ((outer + inner) / 2),
      7,
    );
    this.marker(left + this.hsv.s * side, top + (1 - this.hsv.v) * side, 6);
  }

  marker(x, y, radius) {
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.strokeStyle = "#101211";
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}
