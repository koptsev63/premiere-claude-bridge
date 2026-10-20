// Export preset sanity check.
//
// 20.10.2026: exportAsMediaDirect with IngestPresets/Proxy/02_H.264 MP4.epr
// turned a 1080x1920 vertical sequence into 1280x720. Ingest and proxy
// presets carry a fixed frame size for making proxies; they are not for
// exporting a sequence. Use a "Match Source" preset instead.
// Returns null when the preset looks fine, else {kind, message}.

export function exportPresetProblem(eprPath) {
  const p = String(eprPath || "");
  if (!/\.epr$/i.test(p)) return { kind: "not-epr", message: `Not an .epr preset: ${p}` };
  if (/[\\/]IngestPresets[\\/]/i.test(p) || /[\\/]Proxy[\\/]/i.test(p) || /proxy/i.test(p.split(/[\\/]/).pop())) {
    return { kind: "proxy", message: "This is an ingest/proxy preset: it forces its own frame size " +
      "(a 1080x1920 sequence came out 1280x720). For a sequence use a Match Source " +
      "preset, e.g. .../Contents/MediaIO/systempresets/4E49434B_48323634/00 - Match Source - High bitrate.epr (H.264). " +
      "Pass allowProxyPreset: true only if you really want the proxy size." };
  }
  return null;
}
