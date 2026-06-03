// Helpers for opening wallet addresses on Arkham Intelligence. Middle-click
// any address chip / button across the app to open its Arkham explorer page
// in a new tab — the on-click behaviour (usually copy-to-clipboard) is
// preserved for primary clicks.
//
// The short canonical URL is `https://intel.arkm.com/explorer/address/<addr>`.
// Arkham resolves both EVM addresses and labelled entities from the same path.

export const ARKHAM_BASE = 'https://intel.arkm.com/explorer/address/';

export function arkhamUrl(addr: string): string {
  return ARKHAM_BASE + addr;
}

/** auxclick handler: open Arkham in a new tab on middle-click; ignore other
 *  buttons. Use as `onauxclick={onAuxClickArkham(addr)}` on a <button>. */
export function onAuxClickArkham(addr: string) {
  return (e: MouseEvent) => {
    if (e.button !== 1 || !addr) return;
    e.preventDefault();
    window.open(arkhamUrl(addr), '_blank', 'noopener,noreferrer');
  };
}

/** Suppress the default middle-click scroll-anchor on mousedown so the
 *  auxclick handler runs cleanly. Pair with `onAuxClickArkham`. */
export function onMouseDownSuppressMiddle(e: MouseEvent) {
  if (e.button === 1) e.preventDefault();
}
