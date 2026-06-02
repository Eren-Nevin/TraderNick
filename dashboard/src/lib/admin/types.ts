// Shared types for the admin layout + pages + extracted components.
// Wire shape mirrors what /api/admin/streams + /api/admin/jobs return.

export type StreamRow = {
  name: string;
  group: string;
  cadence_s: number | null;
  kind: 'stream' | 'group';
  module: string;
  pid: number | null;
  running: boolean;
  started_at: number | null;
  crash_count: number;
  last_exit_code: number | null;
  requested_stop: boolean;
  enabled: boolean;
  status: {
    last_tick_at?: string;
    last_rows?: number;
    total_rows_since_start?: number;
    tick_count?: number;
    crash_count?: number;
    last_error?: string | null;
    last_error_at?: string | null;
    last_success_at?: string | null;
    last_live_duration_s?: number | null;
    last_sweep_duration_s?: number | null;
    tick_in_progress?: boolean;
    tick_started_at?: string | null;
  };
};

export type JobRow = {
  job_id: string;
  job_type: string;
  args: Record<string, unknown>;
  status: string;
  progress: number;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  updated_at: string;
  subprocess_alive?: boolean;
};

// Four-state lifecycle (see admin overview docstring in earlier monolith):
//   OFF       — user disabled it. enabled=false.
//   STARTING  — enabled, no live subprocess this instant (jitter / backoff).
//   ON        — subprocess alive, sleeping between ticks.
//   RUNNING   — subprocess actively inside a fetch tick.
export type Lifecycle = 'OFF' | 'STARTING' | 'ON' | 'RUNNING';

export function lifecycle(r: StreamRow): Lifecycle {
  if (!r.enabled || r.requested_stop) return 'OFF';
  if (!r.running) return 'STARTING';
  return r.status?.tick_in_progress ? 'RUNNING' : 'ON';
}

export type StreamAction = 'start' | 'stop' | 'restart';

// Object passed via Svelte context. Layout owns the state + polling loop;
// pages read .streams / .jobs and call .streamAction / .cancelJob.
export type AdminContext = {
  readonly streams: StreamRow[];
  readonly jobs: JobRow[];
  readonly streamsErr: string | null;
  readonly jobsErr: string | null;
  readonly lastRefresh: number | null;
  refresh(): Promise<void>;
  streamAction(name: string, action: StreamAction): Promise<void>;
  cancelJob(id: string): Promise<void>;
};

export const ADMIN_CTX_KEY = Symbol('admin-ctx');
