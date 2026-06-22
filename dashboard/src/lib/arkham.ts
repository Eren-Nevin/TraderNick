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

// Internal HL wallet detail page (/wallet/hl/<addr>). Middle-clicking a wallet
// across the app opens this in a new tab — repointed from the old Coinglass
// middle-click so the wallet stays inside the dashboard.
export function walletHlUrl(addr: string, snapshot?: string | null): string {
  const base = '/wallet/hl/' + addr;
  // Optional ?snapshot=YYYY-MM-DD pre-selects that as-of day on the wallet page
  // (e.g. open a smart wallet at the lookback period's end). Today/empty → omit
  // so the page opens live as usual.
  return snapshot ? `${base}?snapshot=${encodeURIComponent(snapshot)}` : base;
}

/** auxclick handler: open the internal HL wallet page in a new tab on
 *  middle-click; ignore other buttons. Pair with onMouseDownSuppressMiddle.
 *  Optional `snapshot` (YYYY-MM-DD) pre-selects that as-of day. */
export function onAuxClickWalletHl(addr: string, snapshot?: string | null) {
  return (e: MouseEvent) => {
    if (e.button !== 1 || !addr) return;
    e.preventDefault();
    window.open(walletHlUrl(addr, snapshot), '_blank', 'noopener,noreferrer');
  };
}

// ── Shared address helpers ──────────────────────────────────────────────
/** True for an EVM-style address (0x + 40 hex). HL wallets are EVM addresses. */
export function isValidWalletAddress(addr: string): boolean {
  return /^0x[0-9a-fA-F]{40}$/.test(addr.trim());
}

/** Canonical lowercased form used as the storage/lookup key for a wallet. */
export function normalizeAddress(addr: string): string {
  return addr.trim().toLowerCase();
}

/** `0x1234…abcd` short form (was duplicated inline in every address table). */
export function truncateAddr(addr: string): string {
  if (!addr) return '';
  if (addr.length < 14) return addr;
  return addr.slice(0, 6) + '…' + addr.slice(-4);
}
