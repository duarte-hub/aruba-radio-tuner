/* Aruba Radio Tuner — self-contained Lovelace card */
class ArubaRadioTunerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "tune";
    this._scheduleVisible = false;
    this._scheduleAt = "";
    this._form = {
      mode: "power", start_power_dbm: 8, max_power_dbm: 13,
      step_dbm: 2, channel_test_power_dbm: 13,
      channels: "1, 6, 11", recovery_seconds: 120,
    };
  }

  setConfig(config) {
    if (!config?.entity) throw new Error("Set entity to the Aruba Radio Tuner sensor.");
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 7; }

  _captureForm() {
    const root = this.shadowRoot;
    if (!root?.querySelector("#mode")) return;
    this._form = {
      mode: root.querySelector("#mode").value,
      start_power_dbm: Number(root.querySelector("#start").value),
      max_power_dbm: Number(root.querySelector("#maximum").value),
      step_dbm: Number(root.querySelector("#step").value),
      channel_test_power_dbm: Number(root.querySelector("#channel-power").value),
      channels: root.querySelector("#channels").value,
      recovery_seconds: Number(root.querySelector("#recovery").value),
      ap_ip: root.querySelector("#ap")?.value,
    };
    this._scheduleVisible = !root.querySelector("#schedule-form")?.hidden;
    this._scheduleAt = root.querySelector("#run-at")?.value || this._scheduleAt;
  }

  _render() {
    if (!this._config || !this._hass) return;
    this._captureForm();
    const state = this._hass.states[this._config.entity];
    if (!state) {
      this.shadowRoot.innerHTML = `<ha-card><div class="pad">Select the Aruba Radio Tuner sensor in this card's configuration.</div></ha-card>`;
      return;
    }
    const attrs = state.attributes || {};
    const aps = attrs.aps || [];
    if (!this._form.ap_ip && aps.length) this._form.ap_ip = aps[0].ip;
    const active = attrs.active_run;
    const html = `
      <style>
        :host { display:block; --aruba-blue:#0b73b7; --aruba-navy:#17324d; --aruba-line:#d8e0e8; color:var(--primary-text-color,#1d2733); font:14px/1.45 Roboto,"Helvetica Neue",Arial,sans-serif; }
        ha-card { overflow:hidden; border:1px solid var(--aruba-line); border-radius:8px; background:var(--card-background-color,#fff); }
        header { display:flex; align-items:center; justify-content:space-between; padding:16px 20px; background:var(--aruba-navy); color:#fff; }
        .brand { display:flex; align-items:center; gap:12px; } .mark { width:32px;height:32px;border:2px solid #fff;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:12px; }
        h1 { margin:0;font-size:17px;font-weight:600; } .subtitle { opacity:.78;font-size:12px; }
        .state { border:1px solid #ffffff55;border-radius:20px;padding:4px 10px;font-size:12px;text-transform:capitalize; }
        nav { display:flex; gap:4px; padding:10px 18px 0; border-bottom:1px solid var(--aruba-line); }
        nav button { color:#536272;background:transparent;border:0;padding:10px 12px;border-bottom:3px solid transparent;cursor:pointer;font:inherit; }
        nav button.active { color:var(--aruba-blue);border-color:var(--aruba-blue);font-weight:600; }
        main { padding:18px 20px 20px; } .grid { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px; }
        .field { display:flex;flex-direction:column;gap:5px; } .wide { grid-column:1/-1; } label { font-size:12px;font-weight:600;color:#526273; }
        input,select { box-sizing:border-box;width:100%;min-height:38px;border:1px solid #bac6d1;border-radius:5px;padding:7px 9px;background:var(--card-background-color,#fff);color:var(--primary-text-color,#1d2733);font:inherit; }
        .hint,.muted { color:#687786;font-size:12px; } .actions { display:flex;flex-wrap:wrap;gap:9px;margin-top:17px; }
        button.action { border:0;border-radius:5px;padding:9px 14px;background:var(--aruba-blue);color:white;font:600 13px Roboto,"Helvetica Neue",Arial,sans-serif;cursor:pointer; }
        button.secondary { color:var(--aruba-navy);background:#e8eff5;border:1px solid #d0dbe5; }
        button.danger { background:#a33838; } button:disabled { opacity:.5;cursor:not-allowed; }
        .banner { padding:11px 13px;border-radius:5px;background:#edf5fa;border-left:4px solid var(--aruba-blue);margin-bottom:14px; }
        .error { background:#fff0ef;border-color:#b33a31;color:#832b25; } .success { background:#edf8f0;border-color:#37884b; }
        .progress { height:7px;background:#e5ebf0;border-radius:8px;overflow:hidden;margin-top:8px; } .progress i { display:block;height:100%;background:var(--aruba-blue); }
        .cards { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:16px; }
        .metric { border:1px solid var(--aruba-line);border-radius:6px;padding:10px 12px; } .metric strong { display:block;font-size:19px;color:var(--aruba-navy); } .metric span { color:#687786;font-size:11px; }
        .table-wrap { overflow-x:auto;border:1px solid var(--aruba-line);border-radius:6px; } table { width:100%;border-collapse:collapse;white-space:nowrap;font-size:12px; }
        th { text-align:left;color:#536272;background:#f2f5f8;font-weight:600; } td,th { padding:8px 10px;border-bottom:1px solid #e3e8ed; } tr:last-child td { border-bottom:0; }
        .result { border:1px solid var(--aruba-line);border-radius:7px;margin:12px 0;overflow:hidden; } .result summary { cursor:pointer;padding:12px 14px;background:#f7f9fb;font-weight:600; }
        .result-body { padding:14px; } .right { text-align:right; } .notice { padding:12px;border:1px dashed #bdc9d4;border-radius:6px;color:#627282; }
        .viz { display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;margin:0 0 14px; } .viz h3 { margin:0 0 6px;font-size:12px;color:#526273;text-transform:uppercase;letter-spacing:.04em; }
        .panel { border:1px solid var(--aruba-line);border-radius:6px;padding:10px 12px; } .heat { display:grid;gap:3px;font-size:11px;align-items:stretch; } .heat div { text-align:center;padding:6px 2px;border-radius:3px;min-height:20px; }
        .heat .axis { color:#687786;font-weight:600;background:none;padding:6px 2px; } .heat .best { outline:2px solid var(--aruba-navy);outline-offset:-2px;font-weight:700; } .heat .none { background:#eef1f4;color:#9aa6b2; }
        .legend { display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;font-size:11px;color:#687786; } .legend i { display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px; }
        .running { display:flex;align-items:center;gap:16px; } .ring { flex:none; } .ring text { fill:var(--aruba-navy);font-weight:700; }
        svg.chart { width:100%;height:auto;display:block; } svg.chart text { fill:#687786;font-size:10px; }
        @media(max-width:560px) { .viz { grid-template-columns:1fr; } }
        @media(max-width:560px) { .grid,.cards { grid-template-columns:1fr; } header,main { padding-left:14px;padding-right:14px; } }
      </style>
      <ha-card>
        <header><div class="brand"><div class="mark">A</div><div><h1>Aruba Radio Tuner</h1><div class="subtitle">Instant · 2.4 GHz radio optimization</div></div></div><span class="state">${esc(state.state)}</span></header>
        <nav><button data-tab="tune" class="${this._tab === "tune" ? "active" : ""}">Radio tuning</button><button data-tab="results" class="${this._tab === "results" ? "active" : ""}">Results &amp; schedules</button></nav>
        ${this._tab === "tune" ? this._renderTune(attrs, aps, active) : this._renderResults(attrs)}
      </ha-card>`;
    this.shadowRoot.innerHTML = html;
    this.shadowRoot.querySelectorAll("nav button").forEach((button) => button.addEventListener("click", () => { this._captureForm(); this._tab = button.dataset.tab; this._render(); }));
    this._bindButtons(attrs);
  }

  _renderTune(attrs, aps, active) {
    const selected = this._form.ap_ip || aps[0]?.ip || "";
    const apOptions = aps.map((ap) => `<option value="${esc(ap.ip)}" ${ap.ip === selected ? "selected" : ""}>${esc(ap.name)} · ${esc(ap.ip)} · ${esc(ap.model || "AP")}</option>`).join("");
    const recovery = this._form.recovery_seconds ?? 120;
    const progress = active?.total ? Math.round(100 * active.completed / active.total) : 0;
    const selectedMode = this._form.mode;
    const selectedChannels = (this._form.channels || "1,6,11").split(",").filter((value) => value.trim()).length;
    const powerTests = Math.max(1, Math.ceil((Number(this._form.max_power_dbm) - Number(this._form.start_power_dbm)) / Math.max(1, Number(this._form.step_dbm))) + 1);
    const testCount = selectedMode === "power" ? powerTests : selectedMode === "channels" ? selectedChannels : selectedChannels * powerTests;
    const estimatedMinutes = Math.ceil(testCount * Number(recovery) / 60);
    return `<main>
      ${attrs.last_error ? `<div class="banner error">${esc(attrs.last_error)}</div>` : ""}
      ${active ? `<div class="banner running">${ring(progress)}<div style="flex:1;min-width:0"><strong>Test running:</strong> ${esc(active.step || "Working")} · ${active.completed || 0} of ${active.total || "…"}<div class="progress"><i style="width:${progress}%"></i></div><div class="muted">${active.baseline_clients ?? "—"} baseline 2.4 GHz clients · recovery timeout ${recovery}s</div></div></div>` : ""}
      <div class="cards"><div class="metric"><strong>${esc(attrs.api_version ? "Connected" : "—")}</strong><span>Instant API · ${esc(attrs.api_version || "version unavailable")}</span></div><div class="metric"><strong>${aps.length}</strong><span>Access points discovered</span></div><div class="metric"><strong>${(attrs.results || []).length}</strong><span>Saved comparison runs</span></div></div>
      <div class="grid">
        <div class="field wide"><label for="ap">Access point (only this AP's 2.4 GHz radio is changed)</label><select id="ap">${apOptions || `<option value="">No AP inventory returned</option>`}</select></div>
        <div class="field"><label for="mode">Test</label><select id="mode"><option value="power" ${this._form.mode === "power" ? "selected" : ""}>Power sweep</option><option value="channels" ${this._form.mode === "channels" ? "selected" : ""}>Channel comparison</option><option value="combined" ${this._form.mode === "combined" ? "selected" : ""}>Combined: each power on each channel</option></select></div>
        <div class="field"><label for="recovery">Client recovery timeout (seconds)</label><input id="recovery" type="number" min="0" max="900" step="15" value="${recovery}"><span class="hint">Checks every 15 seconds; captures once most baseline clients return or timeout.</span></div>
        <div class="field"><label for="start">Power sweep start (dBm)</label><input id="start" type="number" min="0" max="30" value="${this._form.start_power_dbm}"></div>
        <div class="field"><label for="maximum">Power sweep maximum (dBm)</label><input id="maximum" type="number" min="0" max="30" value="${this._form.max_power_dbm}"><span class="hint">Default 13 dBm. AP/regulatory limits may clamp the live value.</span></div>
        <div class="field"><label for="step">Power increment (dB)</label><input id="step" type="number" min="1" max="10" value="${this._form.step_dbm}"></div>
        <div class="field"><label for="channel-power">Channel test power (dBm)</label><input id="channel-power" type="number" min="0" max="30" value="${this._form.channel_test_power_dbm}"><span class="hint">Used for channel-only comparison.</span></div>
        <div class="field wide"><label for="channels">Channels (comma-separated)</label><input id="channels" value="${esc(this._form.channels)}"><span class="hint">Default: 1, 6, 11. Combined mode tests every configured power on every channel.</span></div>
      </div>
      <div class="hint" style="margin-top:10px">Maximum estimated run time: about ${estimatedMinutes} minute${estimatedMinutes === 1 ? "" : "s"} (${testCount} conditions × ${recovery}s recovery timeout).</div>
      <div class="actions"><button class="action" data-action="start" ${active ? "disabled" : ""}>Start test</button><button class="action secondary" data-action="schedule" ${active ? "disabled" : ""}>Schedule automatic test</button>${active ? `<button class="action danger" data-action="cancel">Cancel test</button>` : ""}</div>
      <div id="schedule-form" ${this._scheduleVisible ? "" : "hidden"} class="grid" style="margin-top:16px"><div class="field wide"><label for="run-at">Start time (your Home Assistant time zone)</label><input id="run-at" type="datetime-local" value="${esc(this._scheduleAt)}"></div><div class="actions wide"><button class="action" data-action="confirm-schedule">Save schedule</button></div></div>
      <p class="muted">Manual tests restore the starting setting and wait for you to apply the recommendation. Scheduled tests run unattended and apply their recommendation when at least 75% of baseline clients return; otherwise they restore the starting setting and mark the results for review.</p>
    </main>`;
  }

  _renderResults(attrs) {
    const schedules = attrs.schedules || [];
    const results = attrs.results || [];
    const scheduleHtml = schedules.length ? schedules.slice().reverse().map((item) => `<tr><td>${esc(formatDate(item.run_at))}</td><td>${esc(item.status)}</td><td>${esc(item.config?.mode || "")}</td><td>${esc(item.config?.ap_ip || "")}</td><td>${item.status === "scheduled" ? `<button class="action secondary" data-action="cancel-schedule" data-id="${esc(item.id)}">Cancel</button>` : ""}</td></tr>`).join("") : `<tr><td colspan="5">No scheduled runs.</td></tr>`;
    const resultHtml = results.length ? results.map((run) => this._resultCard(run)).join("") : `<div class="notice">No saved results yet. Start a test from the Radio tuning view.</div>`;
    return `<main><h2 style="margin:0 0 6px;font-size:16px">Scheduled tests</h2><p class="muted">Automatic runs apply the recommendation when completed.</p><div class="table-wrap"><table><thead><tr><th>Start</th><th>Status</th><th>Test</th><th>Access point</th><th></th></tr></thead><tbody>${scheduleHtml}</tbody></table></div><h2 style="margin:22px 0 6px;font-size:16px">Saved results</h2>${resultHtml}</main>`;
  }

  _resultCard(run) {
    const rec = run.recommendation || {};
    const samples = (run.samples || []).map((sample) => {
      const clientSummary = (sample.clients || []).map((client) => `${esc(client.name || client.mac)} ${client.signal ?? "—"}/${client.rate ?? "—"}`).join(", ") || "No clients";
      return `<tr><td>${esc(sample.channel_requested)}</td><td>${esc(sample.power_requested_dbm)} / ${esc(sample.power_live_dbm ?? "—")}</td><td>${esc(sample.channel_live ?? "—")}</td><td>${esc(sample.client_count)}</td><td>${esc(sample.utilization_percent ?? "—")}</td><td>${esc(sample.noise_dbm ?? "—")}</td><td>${clientSummary}</td></tr>`;
    }).join("");
    const canApply = !run.applied && (rec.baseline_clients || 0) > 0;
    return `<details class="result"><summary>${esc(run.ap_name)} · ${esc(run.mode)} · ${esc(formatDate(run.started_at))} · recommended ch ${esc(rec.channel)} @ ${esc(rec.power_dbm)} dBm ${run.applied ? "· applied" : ""}</summary><div class="result-body"><div class="banner ${run.applied ? "success" : ""}"><strong>Recommendation:</strong> channel ${esc(rec.channel)}, ${esc(rec.power_dbm)} dBm · score ${esc(rec.score)}<br><span class="muted">${esc(rec.confidence || "")}. ${esc(rec.reason || "")}</span>${canApply ? `<div class="actions"><button class="action" data-action="apply" data-id="${esc(run.id)}">Apply recommendation</button></div>` : ""}</div>${this._charts(run)}<div class="table-wrap"><table><thead><tr><th>Requested ch</th><th>Power requested / live</th><th>Channel live</th><th>Clients</th><th>Utilization</th><th>Noise</th><th>Client signal / PHY (dB / Mbps)</th></tr></thead><tbody>${samples}</tbody></table></div><div class="actions"><button class="action secondary" data-action="delete-result" data-id="${esc(run.id)}">Delete result</button></div></div></details>`;
  }

  _charts(run) {
    const samples = run.samples || [];
    if (!samples.length) return "";
    const rec = run.recommendation || {};
    const isBest = (x) => String(x.channel_requested) === String(rec.channel) && Number(x.power_requested_dbm) === Number(rec.power_dbm);
    const base = rec.baseline_clients || run.baseline_clients || Math.max(...samples.map((x) => Number(x.client_count) || 0), 1);
    // Heatmap: channel rows x power columns, colour = utilization, label = clients
    const chans = [...new Set(samples.map((x) => x.channel_requested))].sort((a, b) => a - b);
    const powers = [...new Set(samples.map((x) => Number(x.power_requested_dbm)))].sort((a, b) => a - b);
    const cell = (ch, pw) => samples.find((x) => String(x.channel_requested) === String(ch) && Number(x.power_requested_dbm) === pw);
    let heat = `<div class="axis"></div>` + powers.map((pw) => `<div class="axis">${pw} dBm</div>`).join("");
    chans.forEach((ch) => {
      heat += `<div class="axis">Ch ${esc(ch)}</div>`;
      powers.forEach((pw) => {
        const x = cell(ch, pw);
        if (!x) { heat += `<div class="none">–</div>`; return; }
        const u = Number(x.utilization_percent);
        const colour = Number.isFinite(u) ? utilColour(u) : "#eef1f4";
        heat += `<div class="${isBest(x) ? "best" : ""}" style="background:${colour}" title="Ch ${esc(ch)} @ ${pw} dBm: ${esc(x.client_count)}/${esc(base)} clients, ${esc(x.utilization_percent ?? "—")}% util, ${esc(x.noise_dbm ?? "—")} dBm noise">${esc(x.client_count)}/${esc(base)}</div>`;
      });
    });
    // Bar chart: utilization (bars) + client return % (line) per condition
    const W = 320, H = 130, L = 26, B = 28, T = 8, n = samples.length;
    const bw = (W - L - 6) / n, ph = H - B - T;
    let bars = "", line = [], labels = "";
    samples.forEach((x, i) => {
      const u = Math.min(100, Math.max(0, Number(x.utilization_percent) || 0));
      const bx = L + i * bw + bw * 0.15, w = bw * 0.7, h = ph * u / 100;
      bars += `<rect x="${bx.toFixed(1)}" y="${(T + ph - h).toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="${isBest(x) ? "#17324d" : "#0b73b7"}" opacity="${isBest(x) ? 1 : .55}"><title>Ch ${esc(x.channel_requested)} @ ${esc(x.power_requested_dbm)} dBm · ${esc(x.utilization_percent ?? "—")}% utilization</title></rect>`;
      const ret = Math.min(1, (Number(x.client_count) || 0) / base);
      line.push(`${(bx + w / 2).toFixed(1)},${(T + ph - ph * ret).toFixed(1)}`);
      if (n <= 14 || i % Math.ceil(n / 14) === 0) labels += `<text x="${(bx + w / 2).toFixed(1)}" y="${H - 16}" text-anchor="middle">${esc(x.channel_requested)}</text><text x="${(bx + w / 2).toFixed(1)}" y="${H - 5}" text-anchor="middle">${esc(x.power_requested_dbm)}</text>`;
    });
    const grid = [0, 50, 100].map((v) => `<line x1="${L}" x2="${W}" y1="${T + ph - ph * v / 100}" y2="${T + ph - ph * v / 100}" stroke="#e3e8ed"/><text x="${L - 4}" y="${T + ph - ph * v / 100 + 3}" text-anchor="end">${v}</text>`).join("");
    const dots = line.map((pt) => `<circle cx="${pt.split(",")[0]}" cy="${pt.split(",")[1]}" r="2.5" fill="#37884b"/>`).join("");
    const chart = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Utilization and client return per condition">${grid}${bars}<polyline points="${line.join(" ")}" fill="none" stroke="#37884b" stroke-width="2"/>${dots}${labels}<text x="${L - 4}" y="${H - 16}" text-anchor="end">ch</text><text x="${L - 4}" y="${H - 5}" text-anchor="end">dBm</text></svg>`;
    const score = Math.max(0, Math.min(100, Number(rec.score) || 0));
    return `<div class="viz">
      <div class="panel"><h3>Channel × power heatmap</h3><div class="heat" style="grid-template-columns:auto repeat(${powers.length},minmax(0,1fr))">${heat}</div>
        <div class="legend"><span><i style="background:${utilColour(10)}"></i>low util</span><span><i style="background:${utilColour(50)}"></i>medium</span><span><i style="background:${utilColour(90)}"></i>high</span><span>cell = clients returned / baseline · outlined = recommended</span></div></div>
      <div class="panel"><h3>Per-condition results</h3>${chart}
        <div class="legend"><span><i style="background:#0b73b7"></i>utilization %</span><span><i style="background:#37884b"></i>client return</span><span><i style="background:#17324d"></i>recommended</span></div></div>
    </div>
    <div class="panel" style="margin-bottom:14px"><h3>Recommendation score</h3><svg class="chart" viewBox="0 0 320 22" role="img" aria-label="Score ${score}"><rect x="0" y="6" width="320" height="10" rx="5" fill="#e5ebf0"/><rect x="0" y="6" width="${(3.2 * score).toFixed(1)}" height="10" rx="5" fill="${score >= 66 ? "#37884b" : score >= 33 ? "#d49a1c" : "#b33a31"}"/></svg><div class="muted">${esc(rec.score)} / 100 · ${esc(rec.confidence || "")}</div></div>`;
  }

  _bindButtons(attrs) {
    const root = this.shadowRoot;
    root.querySelector('[data-action="start"]')?.addEventListener("click", () => this._start(false));
    root.querySelector('[data-action="schedule"]')?.addEventListener("click", () => {
      this._captureForm();
      const form = root.querySelector("#schedule-form");
      form.hidden = false;
      this._scheduleVisible = true;
      const at = root.querySelector("#run-at");
      if (!at.value) { const when = new Date(Date.now() + 3600000); when.setMinutes(0, 0, 0); at.value = `${when.getFullYear()}-${String(when.getMonth()+1).padStart(2,"0")}-${String(when.getDate()).padStart(2,"0")}T${String(when.getHours()).padStart(2,"0")}:${String(when.getMinutes()).padStart(2,"0")}`; }
      this._scheduleAt = at.value;
    });
    root.querySelector('[data-action="confirm-schedule"]')?.addEventListener("click", () => this._start(true));
    root.querySelector('[data-action="cancel"]')?.addEventListener("click", () => this._service("cancel_test", { config_entry_id: attrs.entry_id }));
    root.querySelectorAll('[data-action="apply"]').forEach((button) => button.addEventListener("click", () => this._service("apply_recommendation", { config_entry_id: attrs.entry_id, run_id: button.dataset.id })));
    root.querySelectorAll('[data-action="cancel-schedule"]').forEach((button) => button.addEventListener("click", () => this._service("cancel_schedule", { config_entry_id: attrs.entry_id, schedule_id: button.dataset.id })));
    root.querySelectorAll('[data-action="delete-result"]').forEach((button) => button.addEventListener("click", () => this._service("delete_result", { config_entry_id: attrs.entry_id, run_id: button.dataset.id })));
  }

  _readForm() {
    const root = this.shadowRoot;
    const channels = (root.querySelector("#channels")?.value || "1,6,11").split(",").map((value) => Number(value.trim())).filter(Number.isFinite);
    return {
      config_entry_id: this._hass.states[this._config.entity]?.attributes?.entry_id,
      ap_ip: root.querySelector("#ap")?.value,
      mode: root.querySelector("#mode")?.value || "power",
      start_power_dbm: Number(root.querySelector("#start")?.value || 8),
      max_power_dbm: Number(root.querySelector("#maximum")?.value || 13),
      step_dbm: Number(root.querySelector("#step")?.value || 2),
      channel_test_power_dbm: Number(root.querySelector("#channel-power")?.value || 13),
      channels,
      recovery_seconds: Number(root.querySelector("#recovery")?.value || 120),
    };
  }

  async _start(scheduled) {
    const data = this._readForm();
    if (!data.ap_ip) return this._notify("Choose an access point first.");
    if (!data.channels.length || data.channels.some((channel) => channel < 1 || channel > 14)) return this._notify("Enter valid 2.4 GHz channels, such as 1, 6, 11.");
    if (data.max_power_dbm < data.start_power_dbm || data.step_dbm < 1) return this._notify("Check the power start, maximum, and increment.");
    if (scheduled) {
      const local = this.shadowRoot.querySelector("#run-at")?.value;
      if (!local) return this._notify("Choose a start date and time.");
      this._scheduleAt = local;
      data.run_at = new Date(local).toISOString();
      await this._service("schedule_test", data);
      this._scheduleVisible = false;
      this._tab = "results";
      this._render();
    } else {
      await this._service("start_test", data);
    }
  }

  async _service(name, data) {
    try {
      await this._hass.callService("aruba_radio_tuner", name, data);
      this._notify(name === "start_test" ? "Test started." : name === "schedule_test" ? "Automatic test scheduled." : "Request sent.");
    } catch (error) {
      this._notify(error?.message || "Home Assistant could not run that action.");
    }
  }

  _notify(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }
}

function esc(value) {
  return String(value ?? "—").replace(/[&<>"']/g, (char) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" })[char]);
}
function ring(percent) {
  const r = 26, c = 2 * Math.PI * r, off = c * (1 - Math.min(100, Math.max(0, percent)) / 100);
  return `<svg class="ring" width="64" height="64" viewBox="0 0 64 64" role="img" aria-label="${percent}% complete"><circle cx="32" cy="32" r="${r}" fill="none" stroke="#e5ebf0" stroke-width="6"/><circle cx="32" cy="32" r="${r}" fill="none" stroke="#0b73b7" stroke-width="6" stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90 32 32)"/><text x="32" y="36" text-anchor="middle" font-size="13">${percent}%</text></svg>`;
}
function utilColour(u) {
  const t = Math.min(1, Math.max(0, u / 100));
  const hue = 130 - 130 * t; // green -> red
  return `hsl(${hue.toFixed(0)} 55% 78%)`;
}
function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? esc(value) : date.toLocaleString();
}

if (!customElements.get("aruba-radio-tuner")) customElements.define("aruba-radio-tuner", ArubaRadioTunerCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "aruba-radio-tuner", name: "Aruba Radio Tuner", description: "Power and channel optimization for Aruba Instant APs." });
