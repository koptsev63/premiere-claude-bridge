// Panel requests the bridge gave up on while Premiere still runs them.
// A timeout on the node side does not stop the script: ExtendScript runs one
// script at a time, so everything sent after it waits behind it (measured on
// 26.0.2: a script waited 108 s behind two abandoned ones). While one is
// outstanding, a new request is refused at once instead of being queued.

export class PanelQueue {
  constructor() { this.abandoned = new Map(); } // request id -> { fn, since, seq }

  // since: when it was sent. seq: its place in the order of sending.
  timedOut(id, fn, since = Date.now(), seq = 0) { this.abandoned.set(id, { fn, since, seq }); }

  // The late answer of a request nobody waits for any more: only forget it,
  // and with it everything sent before it.
  answered(id) {
    const a = this.abandoned.get(id);
    if (!a) return false;
    this.abandoned.delete(id);
    this.answeredAfter(a.seq);
    return true;
  }

  // A request sent as number `seq` has answered. Scripts run one at a time in
  // the order sent, so every abandoned one sent before it has finished, even
  // if its own late answer got lost.
  answeredAfter(seq) {
    for (const [id, a] of this.abandoned) if (a.seq < seq) this.abandoned.delete(id);
  }

  // The panel socket closed or was replaced: its answers will never come.
  reset() { this.abandoned.clear(); }

  // The oldest outstanding script, or null.
  busy(now = Date.now()) {
    let oldest = null;
    for (const a of this.abandoned.values()) if (!oldest || a.since < oldest.since) oldest = a;
    if (!oldest) return null;
    return { count: this.abandoned.size, fn: oldest.fn, since: oldest.since, ageSec: Math.max(0, Math.round((now - oldest.since) / 1000)) };
  }

  // Throws PANEL_BUSY while a script is outstanding, unless this is a probe.
  check({ probe = false } = {}, now = Date.now()) {
    if (probe) return;
    const b = this.busy(now);
    if (b) throw busyError(b);
  }
}

export function busyError(b) {
  return Object.assign(
    new Error(`Premiere is still running a script the bridge sent ${b.ageSec} s ago and gave up on (${b.fn}). Nothing new was sent. Try again shortly; if it does not clear, run pr_dialog_check.`),
    { code: "PANEL_BUSY" }
  );
}
