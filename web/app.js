/* global L, echarts */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const state = {
    config: null,
    overview: null,
    stations: [],
    storms: [],
    primary: null,
    rankings: [],
    rankMetric: "dp24",
    satellite: { channel: "ir", frames: [], index: 0, playing: false, speed: 1, timer: null },
    satLayer: null,
    imergLayer: null,
    stationLayer: null,
    stationRenderer: null,
    barbLayer: null,
    tcLayer: null,
    map: null,
    baseLayer: null,
    charts: {},
    selectedStation: null,
    drawerOpen: false,
    lastSatelliteKey: "",
  };

  const TYPE_LABEL = {
    airport: "机场站",
    land: "地面站",
    buoy: "浮标",
    platform: "海上平台",
    ship: "船舶",
    drifter: "漂流浮标",
    pws: "PWS",
    madis: "MADIS",
    satellite: "卫星反演",
  };

  const QUALITY_LABEL = {
    qnh: "QNH 气压",
    stale: "资料偏旧",
    delayed: "传输延迟",
    estimated: "时间插值",
    no_dp24: "无 24h 变压",
    no_dt24: "无 24h 变温",
    low_coverage: "覆盖不足",
    bad_change: "变化异常",
    windy: "Windy 补充",
  };

  const SOURCE_LABEL = {
    METAR: "METAR",
    SYNOP: "Ogimet",
    NDBC: "NDBC",
    SHIP: "SHIP",
    WINDY: "Windy",
    GDP: "GDP",
    CMA: "CMA",
    JMA: "JMA",
    SATELLITE: "SAT",
  };

  const DOMAIN = { west: 100, south: 5, east: 160, north: 40 };

  // ---------- utilities ----------
  const pad = (n) => String(n).padStart(2, "0");
  const num = (v, digits = 1) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(digits));
  const signed = (v, digits = 1) => {
    if (v === null || v === undefined) return "—";
    const n = Number(v);
    return `${n > 0 ? "+" : ""}${n.toFixed(digits)}`;
  };
  const timeShort = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}Z`;
  };
  const timeFull = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
  };
  const ageText = (min) => {
    if (min === null || min === undefined) return "—";
    if (min < 1) return "刚刚";
    if (min < 60) return `${Math.round(min)} 分钟前`;
    return `${Math.round(min / 60)} 小时前`;
  };
  const debounce = (fn, wait = 180) => {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), wait);
    };
  };
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, opts = {}) {
    const res = await fetch(path, { cache: "no-store", headers: { Accept: "application/json" }, ...opts });
    if (!res.ok) throw new Error(`${res.status} ${path}`);
    return res.json();
  }

  let toastTimer;
  function toast(message) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  function healthRank(s) {
    const status = String(s.status || "");
    if (status === "error") return 0;
    if (status === "running") return 1;
    if (status === "ok") return 2;
    return 3;
  }

  // ---------- map ----------
  function initMap() {
    const map = L.map("map", {
      center: [22, 130],
      zoom: 4,
      minZoom: 3,
      maxZoom: 8,
      zoomControl: false,
      attributionControl: false,
      preferCanvas: true,
      worldCopyJump: false,
    });
    state.map = map;

    state.baseLayer = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 8,
        minZoom: 3,
        attribution: "",
      }
    ).addTo(map);

    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 8, minZoom: 3, opacity: 0.72, pane: "shadowPane" }
    ).addTo(map);

    fetch("/static/vendor/land.json")
      .then((r) => r.json())
      .then((land) => {
        L.geoJSON(land, {
          style: { color: "rgba(67, 116, 139, .46)", weight: 0.55, fillColor: "#0a1a28", fillOpacity: 0.1 },
          interactive: false,
        }).addTo(map);
      })
      .catch(() => {});

    drawGraticule(map);
    addGeoLabels(map);

    state.tcLayer = L.layerGroup().addTo(map);
    state.stationLayer = L.layerGroup().addTo(map);
    state.barbLayer = L.layerGroup().addTo(map);
    state.stationRenderer = L.canvas({ padding: 0.4 });

    map.on("zoomend moveend", debounce(renderBarbs, 180));
    map.on("click", () => closeMobilePanels());
    setTimeout(() => map.invalidateSize(), 120);

    $("#zoom-in").addEventListener("click", () => map.zoomIn());
    $("#zoom-out").addEventListener("click", () => map.zoomOut());
    $("#fit-domain").addEventListener("click", () => {
      map.fitBounds([[DOMAIN.south, DOMAIN.west], [DOMAIN.north, DOMAIN.east]], { padding: [18, 18] });
    });
  }

  function drawGraticule(map) {
    const style = { color: "rgba(92, 145, 170, .12)", weight: 0.55, interactive: false };
    for (let lon = 100; lon <= 160; lon += 10) {
      L.polyline([[-10, lon], [50, lon]], style).addTo(map);
    }
    for (let lat = 0; lat <= 45; lat += 10) {
      L.polyline([[lat, 90], [lat, 170]], style).addTo(map);
    }
  }

  function addGeoLabels(map) {
    const labels = [
      ["中国", 34.5, 108.5],
      ["日本", 36.5, 138.2],
      ["菲律宾", 12.2, 122.2],
      ["越南", 16.2, 106.8],
      ["泰国", 15.2, 101.2],
      ["马来西亚", 6.0, 102.3],
      ["台湾", 23.7, 121.1],
      ["海南岛", 19.2, 109.7],
    ];
    labels.forEach(([text, lat, lon]) => {
      L.marker([lat, lon], {
        interactive: false,
        icon: L.divIcon({ className: "geo-label", html: text, iconSize: [60, 12], iconAnchor: [30, 6] }),
      }).addTo(map);
    });
  }

  // ---------- storm ----------
  function stormColor(cat) {
    const c = String(cat || "").toUpperCase();
    if (c.includes("SUPER")) return "#a61b45";
    if (c.includes("STY")) return "#c53030";
    if (c.includes("TY")) return "#b7791f";
    if (c.includes("STS")) return "#1e7bb8";
    return "#2f9e44";
  }

  function renderStormList() {
    const list = $("#storm-list");
    const storms = state.storms || [];
    $("#storm-count").textContent = storms.length || "0";
    if (!storms.length) {
      list.innerHTML = `<div class="no-storm">当前范围内没有活跃台风。<br>平台会持续检查 CMA / JMA 的实时台风接口。</div>`;
      return;
    }
    list.innerHTML = storms
      .map((s) => {
        const active = state.primary && s.id === state.primary.id;
        const m = s.metrics || {};
        return `
          <article class="storm-card ${active ? "active" : ""}" data-storm-id="${escapeHtml(s.id)}">
            <div class="storm-card-top">
              <div class="storm-name">${escapeHtml(s.name_zh || s.name_en || s.number)}<span>${escapeHtml(s.name_en || "")}</span></div>
              <span class="storm-cat" style="color:${stormColor(s.category)};border-color:${stormColor(s.category)}55">${escapeHtml(s.category || "TC")}</span>
            </div>
            <div class="storm-stats">
              <div class="storm-stat"><b>${num(s.pressure_hpa, 0)}</b><small>中心气压 hPa</small></div>
              <div class="storm-stat"><b>${num(s.wind_ms, 0)}</b><small>最大风速 m/s</small></div>
              <div class="storm-stat"><b>${signed(m.dp_24h, 1)}</b><small>24h 变压 hPa</small></div>
            </div>
            <div class="storm-foot">
              <span>${escapeHtml(s.motion_dir || "—")} ${num(s.motion_speed_kmh, 0)} km/h</span>
              <b>${m.rapid ? "快速加强" : (m.dp_24h > 0 ? "减弱中" : "监测中")}</b>
            </div>
          </article>`;
      })
      .join("");
    $$(".storm-card").forEach((el) => el.addEventListener("click", () => selectStorm(el.dataset.stormId)));
  }

  function selectStorm(id) {
    const s = state.storms.find((x) => x.id === id);
    if (!s) return;
    state.primary = s;
    renderStormList();
    renderStorm(s);
    focusStorm(s);
    closeMobilePanels();
  }

  function focusStorm(s) {
    if (!state.map || !s.points || !s.points.length) return;
    const pts = s.points.map((p) => [p.lat, p.lon]);
    const bounds = L.latLngBounds(pts);
    state.map.fitBounds(bounds.pad(0.35), { padding: [30, 30], maxZoom: 6 });
  }

  function renderStorm(s) {
    if (!state.tcLayer) return;
    state.tcLayer.clearLayers();
    if (!s || !s.points || !s.points.length) return;

    const observed = s.points.filter((p) => !p.forecast).slice().sort((a, b) => a.valid_time.localeCompare(b.valid_time));
    const forecast = s.points.filter((p) => p.forecast).slice().sort((a, b) => a.valid_time.localeCompare(b.valid_time));
    const currentPoint = observed[observed.length - 1] || s;

    // 风圈：只画当前时刻的 34/50/64 kt 四象限，避免历史风圈叠成蜘蛛网。
    const levels = [
      ["34", "#38bdf8", 0.07],
      ["50", "#fbbf24", 0.08],
      ["64", "#fb7185", 0.09],
    ];
    const radii = currentPoint.wind_radii || {};
    levels.forEach(([level, color, opacity]) => {
      let r = radii[level] || radii[`${level}0`] || radii[`${level}KTS`];
      if (!r && level === "34") r = radii["30"];
      if (!r || r.length < 4) return;
      for (let q = 0; q < 4; q++) {
        const start = q * 90;
        const end = start + 90;
        const pts = [[currentPoint.lat, currentPoint.lon]];
        for (let b = start; b <= end; b += 6) {
          const [la, lo] = destination(currentPoint.lat, currentPoint.lon, b, r[q]);
          pts.push([la, lo]);
        }
        L.polygon(pts, {
          color,
          weight: 0.9,
          opacity: 0.65,
          fillColor: color,
          fillOpacity: opacity,
          interactive: false,
        }).addTo(state.tcLayer);
      }
    });

    if (observed.length > 1) {
      L.polyline(observed.map((p) => [p.lat, p.lon]), {
        color: "#c084fc",
        weight: 2.6,
        opacity: 0.92,
        lineCap: "round",
      }).addTo(state.tcLayer);
    }
    if (forecast.length) {
      const last = observed[observed.length - 1] || forecast[0];
      const line = [[last.lat, last.lon], ...forecast.map((p) => [p.lat, p.lon])];
      L.polyline(line, { color: "#f0abfc", weight: 1.5, opacity: 0.8, dashArray: "5,6" }).addTo(state.tcLayer);
      forecast.forEach((p) => {
        L.circleMarker([p.lat, p.lon], {
          radius: 3.2,
          color: "#f0abfc",
          weight: 1,
          fillColor: "#1a1028",
          fillOpacity: 1,
        })
          .bindTooltip(
            `<b>${timeShort(p.valid_time)}</b> · ${num(p.wind_ms, 0)} m/s · ${num(p.pressure_hpa, 0)} hPa`,
            { direction: "top" }
          )
          .addTo(state.tcLayer);
      });
    }

    const cur = currentPoint;
    const icon = L.divIcon({
      className: "tc-marker",
      html: '<span class="ring1"></span><span class="ring2"></span><span class="eye"></span>',
      iconSize: [38, 38],
      iconAnchor: [19, 19],
    });
    L.marker([cur.lat, cur.lon], { icon, zIndexOffset: 900 })
      .bindTooltip(
        `<b>${escapeHtml(s.name_zh || s.name_en)}</b> ${escapeHtml(s.number || "")}<br>${num(s.pressure_hpa, 0)} hPa · ${num(s.wind_ms, 0)} m/s<br>${escapeHtml(s.motion_dir || "")} ${num(s.motion_speed_kmh, 0)} km/h`,
        { direction: "top", offset: [0, -12], permanent: false }
      )
      .addTo(state.tcLayer);

    L.marker([cur.lat, cur.lon], {
      interactive: false,
      icon: L.divIcon({
        className: "tc-label",
        html: `${escapeHtml(s.name_zh || s.name_en)} · ${num(s.pressure_hpa, 0)} hPa`,
        iconSize: [150, 18],
        iconAnchor: [0, -26],
      }),
    }).addTo(state.tcLayer);
  }

  // ---------- stations ----------
  function stationColor(s) {
    if (s.dp_24h !== null && s.dp_24h !== undefined) {
      const v = Number(s.dp_24h);
      if (v <= -10) return "#ff4d6d";
      if (v <= -5) return "#ff8a5c";
      if (v <= -1) return "#fbbf24";
      if (v < 1) return "#9bb1c0";
      return "#38bdf8";
    }
    return { airport: "#38bdf8", buoy: "#38e8c6", ship: "#fbbf24", drifter: "#22d3ee", platform: "#38e8c6", pws: "#b388ff", madis: "#a78bfa", satellite: "#b388ff" }[s.type] || "#cbd5e1";
  }

  function renderStations() {
    if (!state.stationLayer) return;
    state.stationLayer.clearLayers();
    if (!$("#layer-stations").checked) return;
    const stations = state.stations || [];
    stations.forEach((s) => {
      const color = stationColor(s);
      const radius = 2.7 + Math.min(3.2, (Number(s.wind_ms) || 0) / 18);
      L.circleMarker([s.lat, s.lon], {
        radius,
        color: "rgba(4, 12, 20, .85)",
        weight: 0.7,
        fillColor: color,
        fillOpacity: 0.88,
        renderer: state.stationRenderer,
      })
        .bindTooltip(stationTooltip(s), { direction: "top", sticky: true, opacity: 0.96 })
        .on("click", () => openStation(s.id))
        .addTo(state.stationLayer);
    });
    renderBarbs();
  }

  function stationTooltip(s) {
    const dp = s.dp_24h === null || s.dp_24h === undefined ? "—" : `${signed(s.dp_24h)} hPa`;
    return `<b>${escapeHtml(s.name)}</b><br>
      ${escapeHtml(TYPE_LABEL[s.type] || s.type)} · ${s.lat.toFixed(2)}°, ${s.lon.toFixed(2)}°<br>
      气压 ${num(s.pressure_hpa)} hPa · 24h ${dp}<br>
      气温 ${num(s.temp_c)}°C · 风 ${num(s.wind_ms)} m/s ${escapeHtml(s.wind_dir_text || "")}
      ${s.sea_temp_c != null ? `<br>海温 ${num(s.sea_temp_c)}°C` : ""}`;
  }

  function renderBarbs() {
    if (!state.barbLayer || !state.map) return;
    state.barbLayer.clearLayers();
    if (!$("#layer-barbs").checked || state.map.getZoom() < 5) return;
    const bounds = state.map.getBounds().pad(0.1);
    const visible = state.stations
      .filter((s) => s.wind_ms !== null && s.wind_ms !== undefined && bounds.contains([s.lat, s.lon]))
      .sort((a, b) => (b.wind_ms || 0) - (a.wind_ms || 0))
      .slice(0, 180);
    visible.forEach((s) => {
      L.marker([s.lat, s.lon], {
        interactive: false,
        zIndexOffset: 500,
        icon: L.divIcon({
          className: "wind-barb",
          html: windBarbSvg(s.wind_dir_deg, s.wind_ms),
          iconSize: [36, 36],
          iconAnchor: [18, 18],
        }),
      }).addTo(state.barbLayer);
    });
  }

  function windBarbSvg(dir, speed) {
    const d = Number.isFinite(Number(dir)) ? Number(dir) : 0;
    const kt = (Number(speed) || 0) * 1.94384;
    const color = speed >= 32 ? "#ff5c8a" : speed >= 17 ? "#fbbf24" : speed >= 10 ? "#38e8c6" : "#9fb6c6";
    const cx = 18, cy = 18, len = 11.5;
    const ang = ((d - 90) * Math.PI) / 180;
    const x2 = cx + len * Math.cos(ang);
    const y2 = cy + len * Math.sin(ang);
    let remaining = Math.round(kt / 5) * 5;
    let ticks = "";
    let pos = 0.78;
    const flags = Math.floor(remaining / 50);
    remaining -= flags * 50;
    for (let i = 0; i < flags; i++) {
      const t = 1 - pos;
      const bx = cx + (x2 - cx) * pos;
      const by = cy + (y2 - cy) * pos;
      const px = Math.cos(ang + Math.PI / 2);
      const py = Math.sin(ang + Math.PI / 2);
      ticks += `<path d="M${bx.toFixed(1)},${by.toFixed(1)} l${(px * 5).toFixed(1)},${(py * 5).toFixed(1)} l${(-px * 3.2).toFixed(1)},${(-py * 3.2).toFixed(1)} Z" fill="${color}" opacity=".95"/>`;
      pos += 0.12;
    }
    const full = Math.floor(remaining / 10);
    remaining -= full * 10;
    for (let i = 0; i < full; i++) {
      const bx = cx + (x2 - cx) * pos;
      const by = cy + (y2 - cy) * pos;
      const px = Math.cos(ang + Math.PI / 2);
      const py = Math.sin(ang + Math.PI / 2);
      ticks += `<line x1="${bx.toFixed(1)}" y1="${by.toFixed(1)}" x2="${(bx + px * 5).toFixed(1)}" y2="${(by + py * 5).toFixed(1)}" stroke="${color}" stroke-width="1.3"/>`;
      pos += 0.12;
    }
    if (remaining >= 5) {
      const bx = cx + (x2 - cx) * pos;
      const by = cy + (y2 - cy) * pos;
      const px = Math.cos(ang + Math.PI / 2);
      const py = Math.sin(ang + Math.PI / 2);
      ticks += `<line x1="${bx.toFixed(1)}" y1="${by.toFixed(1)}" x2="${(bx + px * 3).toFixed(1)}" y2="${(by + py * 3).toFixed(1)}" stroke="${color}" stroke-width="1.2"/>`;
    }
    return `<svg width="36" height="36" viewBox="0 0 36 36" xmlns="http://www.w3.org/2000/svg">
      <line x1="${cx}" y1="${cy}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${color}" stroke-width="1.25" stroke-linecap="round"/>
      ${ticks}
      <circle cx="${cx}" cy="${cy}" r="1.55" fill="${color}"/>
    </svg>`;
  }

  function destination(lat, lon, bearing, distanceKm) {
    const R = 6371.0088;
    const br = (bearing * Math.PI) / 180;
    const dr = distanceKm / R;
    const p1 = (lat * Math.PI) / 180;
    const l1 = (lon * Math.PI) / 180;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(dr) + Math.cos(p1) * Math.sin(dr) * Math.cos(br));
    const l2 = l1 + Math.atan2(Math.sin(br) * Math.sin(dr) * Math.cos(p1), Math.cos(dr) - Math.sin(p1) * Math.sin(p2));
    return [(p2 * 180) / Math.PI, ((l2 * 180) / Math.PI + 540) % 360 - 180];
  }

  // ---------- rankings ----------
  async function loadRankings(metric = state.rankMetric) {
    state.rankMetric = metric;
    $$("#rank-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.metric === metric));
    try {
      const data = await api(`/api/rankings?metric=${metric}&limit=14`);
      state.rankings = data;
      renderRankings();
    } catch (err) {
      console.error(err);
      $("#rank-list").innerHTML = `<div class="no-storm">排行加载失败。</div>`;
    }
  }

  function renderRankings() {
    const metric = state.rankMetric;
    const unit = { dp24: "hPa", dt24: "°C", wind: "m/s", rain: "mm" }[metric];
    const title = { dp24: "按 24h 变压升序", dt24: "按 24h 变温升序", wind: "按风速降序", rain: "按 24h 降水降序" }[metric];
    $("#rank-meta").textContent = title;
    $("#rank-updated").textContent = state.overview ? `更新 ${timeShort(state.overview.generated_at)}` : "";
    const rows = state.rankings || [];
    if (!rows.length) {
      $("#rank-list").innerHTML = `<div class="no-storm">暂无完整的 24h 序列。</div>`;
      return;
    }
    $("#rank-list").innerHTML = rows
      .map((s, i) => {
        let value, cls;
        if (metric === "dp24") {
          value = signed(s.dp_24h);
          cls = Number(s.dp_24h) < 0 ? "neg" : "pos";
        } else if (metric === "dt24") {
          value = signed(s.dt_24h);
          cls = Number(s.dt_24h) < 0 ? "neg" : "pos";
        } else if (metric === "wind") {
          value = num(s.wind_ms);
          cls = "wind";
        } else {
          value = num(s.precip_24h_mm, 0);
          cls = "rain";
        }
        const sub = [TYPE_LABEL[s.type] || s.type, s.country || ""]
          .filter(Boolean)
          .join(" · ");
        return `<div class="rank-item" data-station-id="${escapeHtml(s.id)}">
          <span class="rank-no ${i < 3 ? "top" : ""}">${String(i + 1).padStart(2, "0")}</span>
          <span class="rank-name"><b>${escapeHtml(s.name)}</b><small>${escapeHtml(sub)}</small></span>
          <span class="rank-val ${cls}"><b>${value}</b><small>${unit}</small></span>
        </div>`;
      })
      .join("");
    $$("#rank-list .rank-item").forEach((el) =>
      el.addEventListener("click", () => {
        const s = state.stations.find((x) => x.id === el.dataset.stationId);
        if (s) {
          state.map.flyTo([s.lat, s.lon], Math.max(6, state.map.getZoom()), { duration: 0.7 });
          openStation(s.id);
        }
      })
    );
  }

  // ---------- satellite ----------
  function satSpan(frames) {
    if (!frames || frames.length < 2) return "10 分钟帧";
    const a = new Date(frames[0].frame_time).getTime();
    const b = new Date(frames[frames.length - 1].frame_time).getTime();
    const hours = Math.max(1, Math.round((b - a) / 3600000));
    return `最近 ${hours} 小时 · 10 分钟帧`;
  }

  function frameBounds(frame) {
    return [[frame.south, frame.west], [frame.north, frame.east]];
  }

  async function loadSatellite(channel, keepIndex = false, allowFallback = true) {
    try {
      const data = await api(`/api/satellite/frames?channel=${channel}&hours=3`);
      const frames = data.frames || [];
      if (!frames.length && allowFallback && channel !== "ir") {
        await loadSatellite("ir", keepIndex, false);
        return;
      }
      state.satellite.channel = data.channel;
      state.satellite.frames = frames;
      if (!keepIndex || state.satellite.index >= frames.length) state.satellite.index = Math.max(0, frames.length - 1);
      $$("#channel-row button").forEach((b) => b.classList.toggle("active", b.dataset.channel === data.channel));
      if (!frames.length) {
        if (state.satLayer) {
          state.map.removeLayer(state.satLayer);
          state.satLayer = null;
        }
        $("#sat-badge-time").textContent = "云图接入中";
        $("#timeline-time").textContent = "—";
        $("#timeline-sub").textContent = "当前无可用帧";
        updateTimelineUI();
        return;
      }
      const first = frames[0];
      if (!state.satLayer) {
        state.satLayer = L.imageOverlay(frames[state.satellite.index].url, frameBounds(first), {
          opacity: Number($("#sat-opacity").value) / 100,
          className: "sat-overlay",
          interactive: false,
        }).addTo(state.map);
      }
      renderSatelliteFrame(state.satellite.index);
      updateTimelineUI();
      const latest = frames[frames.length - 1];
      $("#sat-badge-time").textContent = `${timeShort(latest.frame_time)} · ${frames.length} 帧`;
      $("#sat-badge-title").textContent = `卫星云图 · ${data.label}`;
      $("#timeline-sub").textContent = satSpan(frames);
    } catch (err) {
      console.error(err);
      $("#sat-badge-time").textContent = "云图加载失败";
    }
  }

  function renderSatelliteFrame(index) {
    const frames = state.satellite.frames;
    if (!frames.length) return;
    const safe = Math.max(0, Math.min(frames.length - 1, index));
    state.satellite.index = safe;
    const frame = frames[safe];
    if (state.satLayer) {
      state.satLayer.setUrl(frame.url);
      state.satLayer.setBounds(frameBounds(frame));
      state.satLayer.setOpacity(Number($("#sat-opacity").value) / 100);
    }
    $("#timeline-time").textContent = `${timeShort(frame.frame_time)} 卫星`;
    $("#timeline-sub").textContent = `${state.satellite.channel === "visible" ? "可见光" : "红外"} · ${satSpan(frames)}`;
    $("#frame-count").textContent = `${safe + 1} / ${frames.length}`;
    $("#timeline-slider").value = String(safe);
    $$("#timeline-ticks span").forEach((el, i) => el.classList.toggle("on", i <= safe));
  }

  function updateTimelineUI() {
    const frames = state.satellite.frames;
    const slider = $("#timeline-slider");
    slider.max = String(Math.max(0, frames.length - 1));
    slider.value = String(state.satellite.index);
    $("#timeline-slider").disabled = !frames.length;
    const labels = $("#timeline-labels");
    const ticks = $("#timeline-ticks");
    if (!frames.length) {
      labels.innerHTML = "";
      ticks.innerHTML = "";
      return;
    }
    const first = frames[0], last = frames[frames.length - 1];
    labels.innerHTML = `<span>${timeShort(first.frame_time)}</span><span>${timeShort(last.frame_time)}</span>`;
    ticks.innerHTML = frames.map((f, i) => `<span class="${i <= state.satellite.index ? "on" : ""}" style="left:${(i / Math.max(1, frames.length - 1)) * 100}%"></span>`).join("");
    $("#frame-count").textContent = `${state.satellite.index + 1} / ${frames.length}`;
  }

  function setPlayIcon(playing) {
    const use = document.querySelector("#play-icon use");
    if (use) use.setAttribute("href", playing ? "#i-pause" : "#i-play");
  }

  function stepFrame(delta) {
    const frames = state.satellite.frames;
    if (!frames.length) return;
    if (state.satellite.playing) togglePlay();
    const n = frames.length;
    renderSatelliteFrame((state.satellite.index + delta + n) % n);
    updateTimelineUI();
  }

  function togglePlay() {
    state.satellite.playing = !state.satellite.playing;
    setPlayIcon(state.satellite.playing);
    $("#play-btn").classList.toggle("playing", state.satellite.playing);
    clearInterval(state.satellite.timer);
    if (state.satellite.playing) {
      state.satellite.timer = setInterval(() => {
        const n = state.satellite.frames.length;
        if (!n) return;
        renderSatelliteFrame((state.satellite.index + 1) % n);
        updateTimelineUI();
      }, Math.round(900 / state.satellite.speed));
    }
  }

  function setChannel(channel) {
    if (channel === "visible" && state.overview?.default_channel === "ir") {
      toast("当前为夜间/可见光不可用，已保持红外云图");
      return;
    }
    if (channel === state.satellite.channel) return;
    clearInterval(state.satellite.timer);
    state.satellite.playing = false;
    setPlayIcon(false);
    loadSatellite(channel);
  }

  // ---------- stations data ----------
  async function loadStations() {
    try {
      const data = await api(`/api/stations?limit=2200`);
      state.stations = data;
      renderStations();
      const ships = data.filter((s) => s.type === "ship").length;
      const drifters = data.filter((s) => s.type === "drifter").length;
      $("#map-subtitle").textContent = `${data.length} 个站点${ships ? ` · ${ships} 条船报` : ""}${drifters ? ` · ${drifters} 个漂流浮标` : ""} · 卫星 + 实况`;
      $("#hero-stats").innerHTML = `
        <span><b>${data.length}</b><small>站点</small></span>
        <span><b>${ships}</b><small>船报</small></span>
        <span><b>${drifters}</b><small>浮标</small></span>`;
    } catch (err) {
      console.error(err);
      toast("站点数据加载失败");
    }
  }

  // ---------- overview ----------
  async function loadOverview() {
    try {
      const data = await api("/api/overview");
      state.overview = data;
      state.config = state.config || {};
      state.storms = [];
      state.primary = null;
      $("#map-title").textContent = "西北太平洋 · 实况";
      const defaultCh = "ir";
      if (!state.satellite.frames.length || state.satellite.channel !== defaultCh) {
        await loadSatellite(defaultCh);
      }
      // 卫星帧数量变化时刷新时间轴。
      const key = `${state.satellite.channel}:${state.satellite.frames.length}:${state.satellite.frames.at(-1)?.frame_time}`;
      if (state.lastSatelliteKey && state.lastSatelliteKey !== key && !state.satellite.playing) {
        await loadSatellite(state.satellite.channel, true);
      }
      state.lastSatelliteKey = key;
    } catch (err) {
      console.error(err);
      toast("总览数据加载失败，后台可能仍在接入");
    }
  }

  // ---------- drawer / charts ----------
  async function openStation(id) {
    state.selectedStation = id;
    state.drawerOpen = true;
    $("#drawer-backdrop").classList.add("open");
    $("#station-drawer").classList.add("open");
    $("#station-drawer").setAttribute("aria-hidden", "false");
    $("#drawer-name").textContent = "加载中…";
    $("#drawer-meta").textContent = id;
    $("#drawer-metrics").innerHTML = "";
    $("#drawer-foot").innerHTML = "";
    closeMobilePanels();
    try {
      const data = await api(`/api/stations/${encodeURIComponent(id)}?hours=72`);
      renderDrawer(data);
    } catch (err) {
      console.error(err);
      $("#drawer-name").textContent = "站点加载失败";
      toast("站点详情加载失败");
    }
  }

  function closeDrawer() {
    state.drawerOpen = false;
    $("#drawer-backdrop").classList.remove("open");
    $("#station-drawer").classList.remove("open");
    $("#station-drawer").setAttribute("aria-hidden", "true");
    Object.values(state.charts).forEach((c) => c && c.dispose());
    state.charts = {};
  }

  function renderDrawer(data) {
    const s = data.station || {};
    const name = s.name || "站点";
    $("#drawer-name").textContent = name;
    const location = [TYPE_LABEL[s.type] || s.type, s.country || "", s.lat?.toFixed(2), s.lon?.toFixed(2)]
      .filter(Boolean)
      .join(" · ");
    $("#drawer-meta").textContent = location;
    const dpClass = s.dp_24h === null || s.dp_24h === undefined ? "" : Number(s.dp_24h) < 0 ? "bad" : "good";
    const dtClass = s.dt_24h === null || s.dt_24h === undefined ? "" : Number(s.dt_24h) < 0 ? "bad" : "good";
    $("#drawer-metrics").innerHTML = `
      <div class="metric"><small>海平面气压 <i>${escapeHtml(s.pressure_basis || "")}</i></small><b>${num(s.pressure_hpa, 1)}<em>hPa</em></b></div>
      <div class="metric"><small>24h 变压</small><b class="${dpClass}">${signed(s.dp_24h)}<em>hPa</em></b></div>
      <div class="metric"><small>气温</small><b>${num(s.temp_c)}<em>°C</em></b></div>
      <div class="metric"><small>24h 变温</small><b class="${dtClass}">${signed(s.dt_24h)}<em>°C</em></b></div>
      <div class="metric"><small>风 / 阵风</small><b>${num(s.wind_ms)}<em>m/s</em></b><small>${escapeHtml(s.wind_dir_text || "—")} · ${num(s.gust_ms)} m/s</small></div>
      <div class="metric"><small>24h 降水</small><b>${num(s.precip_24h_mm, 0)}<em>mm</em></b></div>
      <div class="metric"><small>相对湿度</small><b>${num(s.rh_pct, 0)}<em>%</em></b></div>
      <div class="metric"><small>海温</small><b>${num(s.sea_temp_c)}<em>°C</em></b></div>
      <div class="metric"><small>观测时间</small><b style="font-size:11px">${timeShort(s.obs_time)}</b><small>${ageText(s.age_min)}</small></div>
    `;

    const series = data.series || {};
    drawChart("pressure", "chart-pressure", series.pressure, {
      color: "#1e7bb8",
      unit: "hPa",
      name: "气压",
      area: true,
    });
    drawDualChart("temp", "chart-temp", series.temp, series.rh);
    drawWindChart("wind", "chart-wind", series.wind, series.gust);
    drawRainChart("rain", "chart-rain", series.rain24h, series.rain3h, series.rain1h);
    $("#drawer-foot").innerHTML = `观测 ${timeFull(s.obs_time)} · 数据延迟 ${ageText(s.age_min)}<br>
      24h 变化基于同一序列；缺测显示 “—”，不做模式补值。`;
    setTimeout(() => Object.values(state.charts).forEach((c) => c && c.resize()), 80);
  }

  function chartBase(series, color, name, unit) {
    return {
      animation: false,
      grid: { left: 39, right: 10, top: 13, bottom: 22 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#ffffff",
        borderColor: "#d9e2ec",
        textStyle: { color: "#102a43", fontSize: 10 },
        formatter: (params) => {
          if (!params || !params.length) return "";
          const p = params[0];
          const d = new Date(p.value[0]);
          return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}Z<br/>${name} <b>${num(p.value[1], 1)}</b> ${unit}`;
        },
      },
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: "#e2e8f0" } },
        axisTick: { show: false },
        axisLabel: { color: "#627d98", fontSize: 8, formatter: (v) => `${pad(new Date(v).getUTCHours())}Z` },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { color: "#627d98", fontSize: 8 },
        splitLine: { lineStyle: { color: "#edf2f7" } },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          name,
          type: "line",
          showSymbol: false,
          smooth: 0.18,
          lineStyle: { color, width: 1.5 },
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: `${color}38` },
                { offset: 1, color: `${color}00` },
              ],
            },
          },
          data: series,
        },
      ],
    };
  }

  function drawChart(key, elId, series, opts) {
    const el = $(`#${elId}`);
    if (!el) return;
    if (state.charts[key]) {
      state.charts[key].dispose();
      delete state.charts[key];
    }
    if (!series || !series.length) {
      el.innerHTML = '<div class="empty-chart">暂无 72h 序列</div>';
      return;
    }
    el.innerHTML = "";
    const chart = echarts.init(el, null, { renderer: "canvas" });
    state.charts[key] = chart;
    const option = chartBase(series, opts.color, opts.name, opts.unit);
    chart.setOption(option);
  }

  function drawDualChart(key, elId, temp, rh) {
    const el = $(`#${elId}`);
    if (!el) return;
    if (state.charts[key]) {
      state.charts[key].dispose();
      delete state.charts[key];
    }
    if ((!temp || !temp.length) && (!rh || !rh.length)) {
      el.innerHTML = '<div class="empty-chart">暂无气温 / 湿度序列</div>';
      return;
    }
    el.innerHTML = "";
    const chart = echarts.init(el, null, { renderer: "canvas" });
    state.charts[key] = chart;
    chart.setOption({
      animation: false,
      grid: { left: 36, right: 36, top: 13, bottom: 22 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#ffffff",
        borderColor: "#d9e2ec",
        textStyle: { color: "#102a43", fontSize: 10 },
      },
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: "#e2e8f0" } },
        axisLabel: { color: "#627d98", fontSize: 8, formatter: (v) => `${pad(new Date(v).getUTCHours())}Z` },
        axisTick: { show: false },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: "value", scale: true, name: "°C", nameTextStyle: { color: "#627d98", fontSize: 8 },
          axisLabel: { color: "#627d98", fontSize: 8 },
          splitLine: { lineStyle: { color: "#edf2f7" } },
          axisLine: { show: false }, axisTick: { show: false },
        },
        {
          type: "value", scale: true, min: 0, max: 100, name: "%", nameTextStyle: { color: "#627d98", fontSize: 8 },
          axisLabel: { color: "#627d98", fontSize: 8 },
          splitLine: { show: false }, axisLine: { show: false }, axisTick: { show: false },
        },
      ],
      series: [
        { name: "气温", type: "line", showSymbol: false, smooth: 0.18, lineStyle: { color: "#b7791f", width: 1.4 }, data: temp || [] },
        { name: "湿度", type: "line", yAxisIndex: 1, showSymbol: false, smooth: 0.18, lineStyle: { color: "#1e7bb8", width: 1, opacity: 0.8 }, data: rh || [] },
      ],
    });
  }

  function drawWindChart(key, elId, wind, gust) {
    const el = $(`#${elId}`);
    if (!el) return;
    if (state.charts[key]) {
      state.charts[key].dispose();
      delete state.charts[key];
    }
    if ((!wind || !wind.length) && (!gust || !gust.length)) {
      el.innerHTML = '<div class="empty-chart">暂无风序列</div>';
      return;
    }
    el.innerHTML = "";
    const chart = echarts.init(el, null, { renderer: "canvas" });
    state.charts[key] = chart;
    chart.setOption({
      animation: false,
      grid: { left: 36, right: 10, top: 13, bottom: 22 },
      tooltip: { trigger: "axis", backgroundColor: "#ffffff", borderColor: "#d9e2ec", textStyle: { color: "#102a43", fontSize: 10 } },
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: "#e2e8f0" } },
        axisLabel: { color: "#627d98", fontSize: 8, formatter: (v) => `${pad(new Date(v).getUTCHours())}Z` },
        axisTick: { show: false }, splitLine: { show: false },
      },
      yAxis: {
        type: "value", scale: true, name: "m/s", nameTextStyle: { color: "#627d98", fontSize: 8 },
        axisLabel: { color: "#627d98", fontSize: 8 },
        splitLine: { lineStyle: { color: "#edf2f7" } },
        axisLine: { show: false }, axisTick: { show: false },
      },
      series: [
        { name: "平均风", type: "line", showSymbol: false, smooth: 0.18, lineStyle: { color: "#1e7bb8", width: 1.4 }, areaStyle: { color: "rgba(56,232,198,.08)" }, data: wind || [] },
        { name: "阵风", type: "line", showSymbol: false, smooth: 0.18, lineStyle: { color: "#c53030", width: 1, type: "dashed" }, data: gust || [] },
      ],
    });
  }

  function drawRainChart(key, elId, rain24, rain3, rain1) {
    const el = $(`#${elId}`);
    if (!el) return;
    if (state.charts[key]) {
      state.charts[key].dispose();
      delete state.charts[key];
    }
    const series = rain1 && rain1.length ? rain1 : rain3 && rain3.length ? rain3 : rain24 || [];
    const label = rain1 && rain1.length ? "1h" : rain3 && rain3.length ? "3h" : "24h";
    if (!series.length) {
      el.innerHTML = '<div class="empty-chart">暂无降水序列</div>';
      return;
    }
    el.innerHTML = "";
    const chart = echarts.init(el, null, { renderer: "canvas" });
    state.charts[key] = chart;
    chart.setOption({
      animation: false,
      grid: { left: 36, right: 10, top: 13, bottom: 22 },
      tooltip: { trigger: "axis", backgroundColor: "#ffffff", borderColor: "#d9e2ec", textStyle: { color: "#102a43", fontSize: 10 } },
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: "#e2e8f0" } },
        axisLabel: { color: "#627d98", fontSize: 8, formatter: (v) => `${pad(new Date(v).getUTCHours())}Z` },
        axisTick: { show: false }, splitLine: { show: false },
      },
      yAxis: {
        type: "value", name: "mm", nameTextStyle: { color: "#627d98", fontSize: 8 },
        axisLabel: { color: "#627d98", fontSize: 8 },
        splitLine: { lineStyle: { color: "#edf2f7" } },
        axisLine: { show: false }, axisTick: { show: false },
      },
      series: [
        { name: `${label} 降水`, type: "bar", barMaxWidth: 8, itemStyle: { color: "rgba(30,123,184,.65)", borderRadius: [2, 2, 0, 0] }, data: series },
      ],
    });
  }

  // ---------- quality ----------
  function sourceName(s) {
    return s.label || s.source;
  }

  function renderQualityMini(sources) {
    const list = [...(sources || [])].sort((a, b) => healthRank(a) - healthRank(b) || String(a.source).localeCompare(String(b.source)));
    const top = list.slice(0, 8);
    $("#quality-list").innerHTML = top
      .map((s) => {
        const cls = s.status === "ok" ? "ok" : s.status === "error" ? "error" : s.status === "running" ? "loading" : "";
        const label = s.status === "ok" ? `${s.item_count ?? "—"} 条` : s.status === "error" ? "失败" : s.status === "running" ? "更新中" : "待机";
        return `<div class="quality-item"><i class="status-dot ${cls}"></i><span>${escapeHtml(sourceName(s))}</span><span class="${s.status === "error" ? "err-text" : ""}">${label}</span></div>`;
      })
      .join("");
  }

  function updateHealth(sources) {
    const list = sources || [];
    const errors = list.filter((s) => s.status === "error").length;
    const running = list.filter((s) => s.status === "running").length;
    const dot = $("#health-dot");
    dot.className = "status-dot " + (errors ? "error" : running ? "loading" : "ok");
    $("#health-text").textContent = errors ? `${errors} 个源异常` : running ? "更新中" : "数据正常";
  }

  async function loadQuality() {
    try {
      const data = await api("/api/status");
      const sources = data.sources || [];
      renderQualityMini(sources);
      updateHealth(sources);
      const counts = data.counts || {};
      $("#quality-summary").innerHTML = `
        <div class="summary-chip"><small>站点总数</small><b>${counts.stations ?? "—"}</b></div>
        <div class="summary-chip"><small>有实况站点</small><b>${counts.current ?? "—"}</b></div>
        <div class="summary-chip"><small>72h 观测</small><b>${counts.observations ?? "—"}</b></div>
        <div class="summary-chip"><small>活跃台风</small><b>${counts.storms ?? "—"}</b></div>
        <div class="summary-chip"><small>卫星帧</small><b>${counts.frames ?? "—"}</b></div>
      `;
      const typeText = Object.entries(data.by_type || {})
        .map(([k, v]) => `${TYPE_LABEL[k] || k} ${v}`)
        .join(" · ");
      $("#quality-table").innerHTML = sources
        .map((s) => {
          const cls = s.status === "ok" ? "ok" : s.status === "error" ? "error" : s.status === "running" ? "loading" : "";
          return `<tr>
            <td>${escapeHtml(sourceName(s))}<br><small style="color:var(--dim)">${escapeHtml(s.source)}</small></td>
            <td><i class="status-dot ${cls}"></i> ${s.status === "ok" ? "正常" : s.status === "error" ? "异常" : s.status === "running" ? "更新中" : "待机"}</td>
            <td>${s.last_success ? timeFull(s.last_success) : "—"}</td>
            <td>${s.latency_ms ? `${(s.latency_ms / 1000).toFixed(1)}s` : "—"}</td>
            <td>${s.item_count ?? "—"}</td>
            <td class="msg" title="${escapeHtml(s.message || "")}">${escapeHtml(s.message || "—")}</td>
          </tr>`;
        })
        .join("");
      $("#quality-foot").textContent = `${typeText || "暂无站点"} · 自动重试已开启 · 失败保留上次成功数据`;
    } catch (err) {
      console.error(err);
      $("#quality-table").innerHTML = `<tr><td colspan="6">质量接口不可用</td></tr>`;
    }
  }

  // ---------- mobile ----------
  function closeMobilePanels() {
    $("#left-panel").classList.remove("mobile-open");
    $("#right-panel").classList.remove("mobile-open");
    $$("#mobile-nav button").forEach((b) => b.classList.remove("active"));
  }

  function bindMobile() {
    $$("#mobile-nav button").forEach((btn) => {
      btn.addEventListener("click", () => {
        const panel = btn.dataset.panel;
        const target = panel === "rankings" ? $("#right-panel") : $("#left-panel");
        const other = panel === "rankings" ? $("#left-panel") : $("#right-panel");
        other.classList.remove("mobile-open");
        target.classList.toggle("mobile-open");
        $$("#mobile-nav button").forEach((b) => b.classList.toggle("active", b === btn && target.classList.contains("mobile-open")));
        if (panel === "layers") {
          setTimeout(() => target.querySelector(".layer-panel")?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
        }
      });
    });
  }

  function openQuality() {
    $("#quality-modal").classList.add("open");
    $("#quality-modal").setAttribute("aria-hidden", "false");
    loadQuality();
  }
  function closeQuality() {
    $("#quality-modal").classList.remove("open");
    $("#quality-modal").setAttribute("aria-hidden", "true");
  }

  // ---------- bindings ----------
  function bindUI() {
    $("#rank-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-metric]");
      if (btn) loadRankings(btn.dataset.metric);
    });
    $("#play-btn").addEventListener("click", togglePlay);
    $("#prev-frame").addEventListener("click", () => stepFrame(-1));
    $("#next-frame").addEventListener("click", () => stepFrame(1));
    $("#timeline-slider").addEventListener("input", (e) => {
      if (state.satellite.playing) togglePlay();
      renderSatelliteFrame(Number(e.target.value));
      updateTimelineUI();
    });
    $("#speed").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-speed]");
      if (!btn) return;
      state.satellite.speed = Number(btn.dataset.speed);
      $$("#speed button").forEach((b) => b.classList.toggle("active", b === btn));
      if (state.satellite.playing) {
        togglePlay();
        togglePlay();
      }
    });
    $("#channel-row").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-channel]");
      if (btn) setChannel(btn.dataset.channel);
    });
    $("#sat-opacity").addEventListener("input", (e) => {
      const v = Number(e.target.value);
      $("#sat-opacity-label").textContent = `${v}%`;
      if (state.satLayer) state.satLayer.setOpacity(v / 100);
    });
    $("#layer-sat").addEventListener("change", (e) => {
      if (!state.satLayer) return;
      if (e.target.checked) state.satLayer.addTo(state.map);
      else state.map.removeLayer(state.satLayer);
    });
    $("#layer-barbs").addEventListener("change", renderBarbs);
    $("#layer-stations").addEventListener("change", renderStations);
    $("#drawer-close").addEventListener("click", closeDrawer);
    $("#drawer-backdrop").addEventListener("click", closeDrawer);
    $("#fullscreen-btn").addEventListener("click", async () => {
      try {
        if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
        else await document.exitFullscreen();
      } catch {
        toast("当前浏览器不允许全屏");
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeDrawer();
        closeMobilePanels();
      }
    });
  }

  function initClock() {
    const tick = () => {
      const now = new Date();
      $("#utc-clock").textContent = `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}`;
      try {
        $("#local-clock").textContent = new Intl.DateTimeFormat("zh-CN", {
          timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
        }).format(now);
      } catch {
        $("#local-clock").textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
      }
    };
    tick();
    setInterval(tick, 1000);
  }

  async function boot() {
    initClock();
    initMap();
    bindUI();
    bindMobile();
    try {
      state.config = await api("/api/config");
      DOMAIN.west = state.config.domain.west;
      DOMAIN.south = state.config.domain.south;
      DOMAIN.east = state.config.domain.east;
      DOMAIN.north = state.config.domain.north;
    } catch (e) {
      console.warn(e);
    }
    await loadOverview();
    await Promise.all([loadStations(), loadRankings(state.rankMetric)]);
    state.map.fitBounds([[DOMAIN.south, DOMAIN.west], [DOMAIN.north, DOMAIN.east]], { padding: [18, 18] });
    // 进入页面时后端可能还在做首次回填；静默重试几次，用户不需要手动刷新。
    let warmup = 0;
    const warmupTimer = setInterval(async () => {
      warmup += 1;
      await loadOverview();
      await Promise.all([loadStations(), loadRankings(state.rankMetric)]);
      if (warmup >= 3) clearInterval(warmupTimer);
    }, 10000);
    setInterval(() => {
      loadOverview();
      loadStations();
    }, 60000);
    setInterval(() => {
      if (!state.satellite.playing) loadSatellite(state.satellite.channel, true);
    }, 90000);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
