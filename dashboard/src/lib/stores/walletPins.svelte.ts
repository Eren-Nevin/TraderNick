// Pinned wallets + wallet groups. A "pin" is a tag relating a wallet to one or
// more groups; a permanent "Default" group always exists. Persisted server-side
// in ClickHouse (user_id-scoped) via /api/wallet_pins — GET to load, POST to save
// a full snapshot. The store keeps an in-memory reactive mirror so every getter
// stays synchronous; mutations update it and fire a (serialised) background save.

const API_URL = '/api/wallet_pins';

export const DEFAULT_GROUP_ID = 'default';
/** Neutral zinc — the look of an uncoloured (color = null) group capsule. */
export const NEUTRAL_GROUP_COLOR = '#3f3f46';

export type WalletGroup = { id: string; name: string; color: string | null };
export type GroupMembership = { groupId: string; addedAt: number };
export type WalletPin = { address: string; groups: GroupMembership[] };

function defaultGroup(): WalletGroup {
  return { id: DEFAULT_GROUP_ID, name: 'Default', color: null };
}

let _groups = $state<WalletGroup[]>([defaultGroup()]);
let _pins = $state<WalletPin[]>([]);
let _hydrateP: Promise<void> | null = null;

// Each mutation is a GRANULAR write (one membership / one group) — never a full
// snapshot. So a stale/partial in-memory state can't drop another wallet's pin
// (the bug class). Writes are serialised through one chain so same-key toggles
// land in order, and use keepalive so they survive page navigation.
let _saveChain: Promise<void> = Promise.resolve();

function send(action: 'pin' | 'unpin' | 'group' | 'group_delete', body: object) {
  if (typeof fetch === 'undefined') return;
  const payload = JSON.stringify(body);
  _saveChain = _saveChain.then(() =>
    fetch(`${API_URL}/${action}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: payload,
      keepalive: true,
    })
      .then(() => undefined)
      .catch(() => undefined),
  );
}

/** Group payload for an upsert (granular /group write): name + color + index. */
function groupPayload(id: string) {
  const idx = _groups.findIndex((g) => g.id === id);
  const g = _groups[idx];
  return { id, name: g?.name ?? '', color: g?.color ?? null, sort: idx < 0 ? 0 : idx };
}

function newId(): string {
  return `g-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

function norm(addr: string): string {
  return addr.trim().toLowerCase();
}

function sanitizeGroup(raw: unknown): WalletGroup | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== 'string' || typeof r.name !== 'string') return null;
  const color = typeof r.color === 'string' ? r.color : null;
  return { id: r.id, name: r.name, color };
}

function sanitizePin(raw: unknown): WalletPin | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.address !== 'string') return null;
  const groups = Array.isArray(r.groups)
    ? r.groups
        .map((g): GroupMembership | null => {
          if (!g || typeof g !== 'object') return null;
          const gg = g as Record<string, unknown>;
          if (typeof gg.groupId !== 'string') return null;
          const addedAt = typeof gg.addedAt === 'number' ? gg.addedAt : 0;
          return { groupId: gg.groupId, addedAt };
        })
        .filter((g): g is GroupMembership => g !== null)
    : [];
  if (groups.length === 0) return null;
  return { address: norm(r.address), groups };
}

export const walletPinsStore = {
  get groups(): WalletGroup[] {
    return _groups;
  },
  get pins(): WalletPin[] {
    return _pins;
  },

  // Returns the (shared) load promise so callers can AWAIT it before mutating —
  // mutating before the existing pins are loaded would build a snapshot POST
  // that drops everyone else (the pin-removes-another-pin bug). Idempotent: the
  // GET fires once; later callers await the same promise.
  hydrate(): Promise<void> {
    if (_hydrateP) return _hydrateP;
    _hydrateP = (async () => {
      if (typeof fetch === 'undefined') return;
      try {
        const res = await fetch(API_URL);
        if (!res.ok) return;
        const parsed = await res.json();
        const groups = (Array.isArray(parsed?.groups) ? parsed.groups : [])
          .map(sanitizeGroup)
          .filter((g: WalletGroup | null): g is WalletGroup => g !== null);
        // Default is permanent — always present, always exactly one.
        const withoutDefault = groups.filter((g: WalletGroup) => g.id !== DEFAULT_GROUP_ID);
        _groups = [defaultGroup(), ...withoutDefault];
        const knownIds = new Set(_groups.map((g) => g.id));
        _pins = (Array.isArray(parsed?.pins) ? parsed.pins : [])
          .map(sanitizePin)
          .filter((p: WalletPin | null): p is WalletPin => p !== null)
          // drop memberships pointing at groups that no longer exist
          .map((p: WalletPin) => ({
            ...p,
            groups: p.groups.filter((m) => knownIds.has(m.groupId)),
          }))
          .filter((p: WalletPin) => p.groups.length > 0);
      } catch {
        /* ignore — keep the in-memory default */
      }
    })();
    return _hydrateP;
  },

  /** Force a fresh load from CH (the Group widget's refresh button). */
  reload(): Promise<void> {
    _hydrateP = null;
    return this.hydrate();
  },

  groupById(id: string): WalletGroup | undefined {
    return _groups.find((g) => g.id === id);
  },

  /** Groups a wallet is pinned to (resolved objects, in stored group order). */
  groupsForWallet(address: string): WalletGroup[] {
    const a = norm(address);
    const pin = _pins.find((p) => p.address === a);
    if (!pin) return [];
    const ids = new Set(pin.groups.map((m) => m.groupId));
    return _groups.filter((g) => ids.has(g.id));
  },

  isPinned(address: string): boolean {
    const a = norm(address);
    return _pins.some((p) => p.address === a);
  },

  isInGroup(address: string, groupId: string): boolean {
    const a = norm(address);
    const pin = _pins.find((p) => p.address === a);
    return !!pin?.groups.some((m) => m.groupId === groupId);
  },

  /** Wallets pinned to a group, sorted by date added (oldest first). */
  walletsInGroup(groupId: string): Array<{ address: string; addedAt: number }> {
    return _pins
      .map((p) => {
        const m = p.groups.find((g) => g.groupId === groupId);
        return m ? { address: p.address, addedAt: m.addedAt } : null;
      })
      .filter((x): x is { address: string; addedAt: number } => x !== null)
      .sort((a, b) => a.addedAt - b.addedAt);
  },

  /** Add/remove a wallet's membership in a group. Stamps addedAt on add. */
  togglePin(address: string, groupId: string) {
    const a = norm(address);
    if (!_groups.some((g) => g.id === groupId)) return;
    const pin = _pins.find((p) => p.address === a);
    const inGroup = !!pin?.groups.some((m) => m.groupId === groupId);
    if (inGroup) {
      const groups = pin!.groups.filter((m) => m.groupId !== groupId);
      _pins = groups.length
        ? _pins.map((p) => (p.address === a ? { ...p, groups } : p))
        : _pins.filter((p) => p.address !== a);
      send('unpin', { address: a, groupId });
    } else {
      const addedAt = Date.now();
      _pins = pin
        ? _pins.map((p) => (p.address === a ? { ...p, groups: [...p.groups, { groupId, addedAt }] } : p))
        : [..._pins, { address: a, groups: [{ groupId, addedAt }] }];
      send('pin', { address: a, groupId, addedAt });
    }
  },

  /** Pin to the Default group (the quick-pin default action). */
  quickPin(address: string) {
    if (!this.isInGroup(address, DEFAULT_GROUP_ID)) {
      this.togglePin(address, DEFAULT_GROUP_ID);
    }
  },

  /** Remove a wallet from a group (no-op if not in it). */
  unpin(address: string, groupId: string) {
    if (this.isInGroup(address, groupId)) this.togglePin(address, groupId);
  },

  addGroup(name: string, color: string | null = null): WalletGroup {
    const trimmed = name.trim() || `Group ${_groups.length}`;
    const g: WalletGroup = { id: newId(), name: trimmed, color };
    _groups = [..._groups, g];
    send('group', groupPayload(g.id));
    return g;
  },

  renameGroup(id: string, name: string) {
    const trimmed = name.trim();
    if (!trimmed) return;
    _groups = _groups.map((g) => (g.id === id ? { ...g, name: trimmed } : g));
    send('group', groupPayload(id));
  },

  setGroupColor(id: string, color: string | null) {
    _groups = _groups.map((g) => (g.id === id ? { ...g, color } : g));
    send('group', groupPayload(id));
  },

  /** Delete a group (and strip it from every pin). Default can't be deleted. */
  removeGroup(id: string): boolean {
    if (id === DEFAULT_GROUP_ID) return false;
    if (!_groups.some((g) => g.id === id)) return false;
    _groups = _groups.filter((g) => g.id !== id);
    _pins = _pins
      .map((p) => ({ ...p, groups: p.groups.filter((m) => m.groupId !== id) }))
      .filter((p) => p.groups.length > 0);
    send('group_delete', { id });
    return true;
  },
};
