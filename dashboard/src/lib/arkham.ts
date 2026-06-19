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

// Coinglass Hyperliquid wallet page — the per-address perp dashboard used by
// the smart-wallet finder. Same shape as the Arkham helper so a Hyperliquid
// table can swap Arkham for Coinglass on middle-click.
export const COINGLASS_HL_BASE = 'https://www.coinglass.com/hyperliquid/';

export function coinglassHlUrl(addr: string): string {
  return COINGLASS_HL_BASE + addr;
}

/** auxclick handler: open the wallet's Coinglass Hyperliquid page in a new tab
 *  on middle-click; ignore other buttons. Pair with onMouseDownSuppressMiddle. */
export function onAuxClickCoinglassHl(addr: string) {
  return (e: MouseEvent) => {
    if (e.button !== 1 || !addr) return;
    e.preventDefault();
    window.open(coinglassHlUrl(addr), '_blank', 'noopener,noreferrer');
  };
}
