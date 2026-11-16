import { useState } from "react";

const load = (key, fallback) => {
  try { return Number(localStorage.getItem(key)) || fallback; } catch { return fallback; }
};
const save = (key, v) => { try { localStorage.setItem(key, String(v)); } catch {} };

// A column width the user drags. `side` is which screen edge the column is anchored to.
// Returns [width, props for the drag handle]. Double-click the handle to reset.
export function useResizable(key, { initial, min, max, side }) {
  const [width, setWidth] = useState(() => Math.min(max, Math.max(min, load(key, initial))));

  const onPointerDown = (e) => {
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    let w = width;
    const move = (ev) => {
      const raw = side === "left" ? ev.clientX : window.innerWidth - ev.clientX;
      w = Math.round(Math.min(max, Math.max(min, raw)));
      setWidth(w);
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      document.body.classList.remove("resizing");
      save(key, w);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  };

  const reset = () => { setWidth(initial); save(key, initial); };
  return [width, { onPointerDown, onDoubleClick: reset, role: "separator", "aria-orientation": "vertical",
                   title: "Drag to resize · double-click to reset" }];
}
