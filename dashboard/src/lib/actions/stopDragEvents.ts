/**
 * Svelte action that stops mousedown / touchstart events from bubbling
 * up to ancestor elements. Use this on chart-body containers (e.g.,
 * table views) to keep svelte-dnd-action's chart-card-level listeners
 * from initiating a drag when the user clicks/scrolls inside the body.
 *
 * Why an action (not a Svelte 5 `onmousedown={…}` handler):
 *   Svelte 5 delegates `mousedown`/`touchstart` to a single document-
 *   level listener and dispatches via target-walk. svelte-dnd-action
 *   attaches its listeners directly to each draggable element with
 *   `addEventListener`. The dnd-action listener fires in the bubble
 *   phase BEFORE the event ever reaches the document, so a delegated
 *   `onmousedown` handler runs too late to call stopPropagation. An
 *   action that calls addEventListener directly on the node fires at
 *   the right point in the bubble path — between the click target and
 *   the dndzone item — so the dnd listener never sees the event.
 */
export function stopDragEvents(node: HTMLElement) {
  const stop = (e: Event) => e.stopPropagation();
  node.addEventListener('mousedown', stop);
  node.addEventListener('touchstart', stop, { passive: true });
  return {
    destroy() {
      node.removeEventListener('mousedown', stop);
      node.removeEventListener('touchstart', stop);
    }
  };
}
