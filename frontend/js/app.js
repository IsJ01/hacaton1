(function () {
  const C = window.CONFIG;
  const Api = window.Api;

  // ---------- утилиты ----------
  const $ = (s, root) => (root || document).querySelector(s);
  const esc = (v) =>
    String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtTime = (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? esc(iso) : d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };
  const fmtHM = (iso) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const pct = (x) => (typeof x === 'number' ? Math.round(x * 100) + '%' : '—');
  const coords = (g) => (g ? g.lat.toFixed(4) + ', ' + g.lon.toFixed(4) : '—');
  const soundName = (l) => C.SOUND_LABELS[l] || l;
  const waterName = (t) => C.WATER_TYPES[t] || t;
  const ms = (iso) => new Date(iso).getTime();
  const MIN = 60000;
  function ago(t) {
    const s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 60) return s + ' с назад';
    if (s < 3600) return Math.round(s / 60) + ' мин назад';
    if (s < 86400) return Math.round(s / 3600) + ' ч назад';
    return Math.round(s / 86400) + ' дн назад';
  }
  function distKm(a, b) {
    const r = Math.PI / 180, dLa = (b.lat - a.lat) * r, dLo = (b.lon - a.lon) * r;
    const h = Math.sin(dLa / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLo / 2) ** 2;
    return 6371 * 2 * Math.asin(Math.sqrt(h));
  }
  function inPolygon(g, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [yi, xi] = poly[i], [yj, xj] = poly[j];
      if ((yi > g.lat) !== (yj > g.lat) && g.lon < ((xj - xi) * (g.lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  const store = {
    get: (k, def) => { try { const v = localStorage.getItem(k); return v === null ? def : JSON.parse(v); } catch (e) { return def; } },
    set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* приватный режим */ } },
  };

  const state = {
    buoys: [], sound: [], water: [], salinity: {}, forecast: null, sig: {}, status: {}, seals: null,
    incidents: [], buoyStatus: {}, zoneStatus: {}, lastOk: 0, online: null, ready: false,
    decisions: store.get('eco.decisions', {}), // id инцидента → confirmed | false | patrol
  };
  const buoyById = (id) => state.buoys.find((x) => x.buoy_id === id);
  const buoyName = (id) => { const b = buoyById(id); return b ? b.name : id || '—'; };
  const zoneOf = (g) => (g ? C.ZONES.find((z) => inPolygon(g, z.polygon)) || null : null);
  const itemKey = (item) => item.kind + ':' + item.event_id;
  const itemBuoy = (item) => (item.kind === 'sound' ? item.buoy_id : item.source && item.source.buoy_id);
  const itemGeo = (item) => (item.kind === 'water' ? item.geo : (buoyById(item.buoy_id) || {}).geo || null);
  const itemLabel = (item) => (item.kind === 'sound' ? item.classification && item.classification.label : item.type);
  const itemName = (item) => (item.kind === 'sound' ? soundName(itemLabel(item)) : waterName(item.type));
  const itemConf = (item) => (item.kind === 'sound' ? (item.classification || {}).confidence : (item.details || {}).confidence);
  const isCritical = (item) =>
    item.kind === 'sound' ? C.CRITICAL_SOUND_LABELS.includes(itemLabel(item)) : C.CRITICAL_WATER_TYPES.includes(item.type);
  const allItems = () =>
    state.sound.map((e) => Object.assign({ kind: 'sound' }, e))
      .concat(state.water.map((e) => Object.assign({ kind: 'water' }, e)))
      .sort((a, b) => ms(b.timestamp) - ms(a.timestamp));

  // ---------- слияние источников: критические события → инциденты ----------
  // Сигналы, близкие по времени и месту, — один инцидент. Слышали ≥2 буя или
  // сработали и буй, и спутник — инцидент подтверждён независимыми источниками.
  function buildIncidents() {
    const crit = allItems().filter(isCritical).reverse(); // от старых к новым
    const list = [];
    crit.forEach((item) => {
      const t = ms(item.timestamp), g = itemGeo(item);
      let inc = null;
      for (let i = list.length - 1; i >= 0; i--) {
        const c = list[i];
        if (t - c.end > C.INCIDENT_WINDOW_MIN * MIN) break;
        if (!g || !c.geo || distKm(g, c.geo) <= C.INCIDENT_RADIUS_KM) { inc = c; break; }
      }
      if (!inc) { inc = { id: 'inc-' + item.event_id, items: [], start: t, end: t, geo: null }; list.push(inc); }
      inc.items.push(item);
      inc.end = t;
      if (g) inc.geo = g;
    });
    list.forEach((inc) => {
      const labels = new Set(inc.items.map(itemLabel));
      const buoys = new Set(inc.items.map(itemBuoy).filter(Boolean));
      const hasImage = inc.items.some((i) => i.kind === 'water' && i.source && i.source.image_id);
      const hasBuoyWater = inc.items.some((i) => i.kind === 'water' && i.source && i.source.buoy_id);
      inc.labels = labels;
      inc.buoys = Array.from(buoys);
      inc.zones = Array.from(new Set(inc.items.map((i) => zoneOf(itemGeo(i))).filter(Boolean)));
      inc.zone = zoneOf(inc.geo) || inc.zones[0] || null;
      inc.severity = Array.from(labels).some((l) => !C.WARN_SOUND_LABELS.includes(l)) ? 'alarm' : 'warn';
      const names = [];
      ['boat', 'gunshot', 'illegal_discharge', 'level_anomaly'].forEach((l) => {
        if (labels.has(l)) names.push(l in C.SOUND_LABELS ? soundName(l) : waterName(l));
      });
      inc.title = (labels.has('boat') && labels.has('gunshot') ? 'Браконьерство: ' : '') + names.join(' + ').toLowerCase();
      inc.title = inc.title.charAt(0).toUpperCase() + inc.title.slice(1);
      inc.confirmed = buoys.size >= 2 ? 'Подтверждено ' + buoys.size + ' буями'
        : hasImage && hasBuoyWater ? 'Подтверждено буем и спутником' : '';
      inc.conf = Math.max.apply(null, inc.items.map((i) => itemConf(i) || 0));
      inc.decision = state.decisions[inc.id] || null;
      inc.active = Date.now() - inc.end <= C.ACTIVE_ALERT_MIN * MIN && inc.decision !== 'false';
    });
    state.incidents = list.reverse(); // новые сверху
  }

  // ---------- статусы буёв и зон ----------
  const todayRisk = (buoyId) => {
    const f = state.forecast && (state.forecast.forecasts || []).find((x) => x.buoy_id === buoyId);
    return f && f.days[0] ? f.days[0] : null;
  };
  // Статус буя за сутки считает бэкенд (GET /api/v1/buoy-status):
  // normal — синий, anomaly — жёлтый (аномальный звук, скачок солёности), critical — красный (лодка, выстрел, сброс).
  // Зона ООПТ берёт худший статус своих буёв.
  const LEVEL_RANK = { none: 0, off: 1, normal: 2, anomaly: 3, critical: 4 };
  function computeStatus() {
    state.buoyStatus = {};
    state.buoys.forEach((b) => {
      const st = state.status[b.buoy_id];
      state.buoyStatus[b.buoy_id] = st
        ? { level: st.status, reason: st.reason, last: st.last_signal ? ms(st.last_signal) : null, online: st.online, data: st }
        : { level: 'off', reason: 'Нет данных о буе', last: null, online: false, data: null };
    });
    state.zoneStatus = {};
    C.ZONES.forEach((z) => {
      const buoys = state.buoys.filter((b) => inPolygon(b.geo, z.polygon));
      let level = 'none';
      buoys.forEach((b) => { const l = state.buoyStatus[b.buoy_id].level; if (LEVEL_RANK[l] > LEVEL_RANK[level]) level = l; });
      const bad = buoys.filter((b) => ['anomaly', 'critical'].includes(state.buoyStatus[b.buoy_id].level));
      const reason = !buoys.length ? 'Нет датчиков в зоне'
        : bad.length ? bad.map((b) => b.name + ': ' + state.buoyStatus[b.buoy_id].reason).join('; ') : 'За сутки без происшествий';
      state.zoneStatus[z.id] = { level, reason, buoys };
    });
  }
  const LEVEL_TEXT = { normal: 'норма', anomaly: 'аномалия', critical: 'критично', off: 'нет данных', none: 'нет датчиков' };

  // ---------- вкладки ----------
  let map;
  const TABS = ['map', 'crit', 'all', 'mortality', 'forecast', 'about'];
  function showTab(name) {
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
    TABS.forEach((n) => ($('#panel-' + n).hidden = n !== name));
    if (name === 'map') map.invalidateSize();
    if (name === 'forecast') loadForecast();
    if (name === 'about') loadAbout();
    if (name === 'mortality') renderMortality();
  }
  document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => showTab(t.dataset.tab)));

  // ---------- карта ----------
  map = L.map('map', { zoomControl: true }).setView(C.MAP_CENTER, C.MAP_ZOOM);
  map.attributionControl.setPrefix(false);
  L.tileLayer(C.TILE_URL, { attribution: C.TILE_ATTRIBUTION, maxZoom: C.TILE_MAX_ZOOM || 18 }).addTo(map);
  const zoneLayer = L.layerGroup().addTo(map);
  const forecastLayer = L.layerGroup();
  const sealLayer = L.layerGroup().addTo(map);
  const carcassLayer = L.layerGroup().addTo(map);
  const driftLayer = L.layerGroup().addTo(map);
  const listenLayer = L.layerGroup().addTo(map);
  const buoyLayer = L.layerGroup().addTo(map);
  const waterLayer = L.layerGroup().addTo(map);
  const alertLayer = L.layerGroup().addTo(map);
  const buoyMarkers = {}, listenCircles = {};
  const icon = (cls, size) => L.divIcon({ className: '', html: '<div class="mk ' + cls + '" style="width:' + size + 'px;height:' + size + 'px"></div>', iconSize: [size, size], iconAnchor: [size / 2, size / 2] });

  C.PLACES.forEach((p) =>
    L.marker([p.lat, p.lon], { interactive: false, icon: L.divIcon({ className: '', iconSize: [0, 0], html: '<div class="place' + (p.sea ? ' sea' : '') + '">' + esc(p.name) + '</div>' }) }).addTo(map)
  );

  const zonePolys = {}, zoneLabels = {};
  const zoneLabelIcon = (z, level) => L.divIcon({
    className: '', iconSize: [0, 0],
    html: '<div class="zone-label"><b>' + esc(z.name) + '</b> <span class="zl-' + level + '">● ' + esc(LEVEL_TEXT[level]) + '</span><br>' + esc(z.note) + '</div>',
  });
  C.ZONES.forEach((z) => {
    zonePolys[z.id] = L.polygon(z.polygon, { className: 'zone', weight: 2 }).addTo(zoneLayer);
    zoneLabels[z.id] = L.marker(z.label || z.polygon[0], { icon: zoneLabelIcon(z, 'none'), interactive: false }).addTo(zoneLayer);
  });

  function renderZones() {
    C.ZONES.forEach((z) => {
      const s = state.zoneStatus[z.id] || { level: 'none', reason: '' };
      const p = zonePolys[z.id];
      const el = p.getElement();
      if (el) el.setAttribute('class', 'leaflet-interactive zone z-' + s.level);
      zoneLabels[z.id].setIcon(zoneLabelIcon(z, s.level));
      p.unbindPopup().bindPopup('<div class="popup"><h3>' + esc(z.name) + '</h3><div class="sub">' + esc(z.note) + ' · границы схематичные</div>' +
        '<p><b class="zl-' + s.level + '">' + esc(LEVEL_TEXT[s.level]) + '</b> — ' + esc(s.reason) + '</p>' +
        '<div class="sub">Буи в зоне: ' + (s.buoys && s.buoys.length ? s.buoys.map((b) => esc(b.name)).join(', ') : 'нет') + '</div></div>');
    });
  }

  function renderBuoyMarkers() {
    buoyLayer.clearLayers();
    listenLayer.clearLayers();
    Object.keys(buoyMarkers).forEach((k) => delete buoyMarkers[k]);
    state.buoys.forEach((b) => {
      listenCircles[b.buoy_id] = L.circle([b.geo.lat, b.geo.lon], { radius: C.LISTEN_RADIUS_KM * 1000, className: 'listen', interactive: false }).addTo(listenLayer);
      const m = L.marker([b.geo.lat, b.geo.lon], { icon: icon('buoy-mk', 18) }).addTo(buoyLayer);
      m.bindTooltip(esc(b.name));
      m.bindPopup('Загрузка…', { minWidth: 300, maxWidth: 300 });
      m.on('popupopen', () => loadBuoyPopup(m, b.buoy_id));
      buoyMarkers[b.buoy_id] = m;
    });
  }
  function paintBuoyMarkers() {
    Object.entries(buoyMarkers).forEach(([id, m]) => {
      const s = state.buoyStatus[id] || { level: 'off' };
      m.setIcon(icon('buoy-mk s-' + s.level + (s.online ? '' : ' offline'), 18));
      m.setTooltipContent(esc(buoyName(id)) + ' — ' + esc(LEVEL_TEXT[s.level]) + (s.online ? '' : ' · нет связи'));
      // круг слышимости: радиус и цвет — от бэкенда
      const c = listenCircles[id], r = s.data && s.data.listen_radius_km;
      if (c) {
        if (r) c.setRadius(r * 1000);
        const el = c.getElement();
        if (el) el.setAttribute('class', 'listen l-' + s.level);
      }
    });
  }

  async function loadBuoyPopup(marker, id) {
    try {
      const [b, sal] = await Promise.all([Api.getBuoy(id), Api.getSalinity({ buoy_id: id, limit: C.SALINITY_LIMIT })]);
      const s = state.buoyStatus[id] || {};
      marker.setPopupContent(
        '<div class="popup"><h3>' + esc(b.name) + ' <span class="zl-' + esc(s.level) + '">● ' + esc(LEVEL_TEXT[s.level] || '') + '</span></h3>' +
          '<div class="sub">' + esc(b.buoy_id) + ' · ' + coords(b.geo) + (zoneOf(b.geo) ? ' · ' + esc(zoneOf(b.geo).name) : '') + '</div>' +
          '<div class="sub">За сутки: ' + esc(s.reason || '—') + '</div>' +
          salinityChart(sortAsc(sal.measurements || [])) + sensorsHtml(id) + '</div>'
      );
    } catch (e) {
      marker.setPopupContent('<div class="popup err-text">Ошибка: ' + esc(e.message) + '</div>');
    }
  }
  const sortAsc = (list) => list.slice().sort((a, b) => ms(a.timestamp) - ms(b.timestamp));

  // График солёности: простая линия на SVG
  function salinityChart(ms_) {
    if (!ms_.length) return '<div class="sub">Нет измерений солёности</div>';
    const W = 300, H = 130, L0 = 34, R = 8, T = 8, B = 18;
    const xs = ms_.map((m) => ms(m.timestamp));
    const ys = ms_.map((m) => m.salinity);
    let y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    if (y1 - y0 < 0.5) { y0 -= 0.5; y1 += 0.5; }
    const x0 = xs[0], x1 = xs[xs.length - 1];
    const px = (x) => (x1 === x0 ? (L0 + W - R) / 2 : L0 + ((x - x0) / (x1 - x0)) * (W - L0 - R));
    const py = (y) => T + (1 - (y - y0) / (y1 - y0)) * (H - T - B);
    const pts = ms_.map((m, i) => px(xs[i]).toFixed(1) + ',' + py(ys[i]).toFixed(1)).join(' ');
    const last = ms_[ms_.length - 1];
    return (
      '<div class="sub">Солёность: <b>' + last.salinity.toFixed(2) + ' ‰</b> · ' + fmtTime(last.timestamp) + '</div>' +
      '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '">' +
      '<line x1="' + L0 + '" y1="' + T + '" x2="' + L0 + '" y2="' + (H - B) + '" stroke="#d9e1e6"/>' +
      '<line x1="' + L0 + '" y1="' + (H - B) + '" x2="' + (W - R) + '" y2="' + (H - B) + '" stroke="#d9e1e6"/>' +
      '<text x="' + (L0 - 4) + '" y="' + (T + 8) + '" text-anchor="end">' + y1.toFixed(1) + '</text>' +
      '<text x="' + (L0 - 4) + '" y="' + (H - B) + '" text-anchor="end">' + y0.toFixed(1) + '</text>' +
      '<text x="' + L0 + '" y="' + (H - 4) + '">' + fmtHM(ms_[0].timestamp) + '</text>' +
      '<text x="' + (W - R) + '" y="' + (H - 4) + '" text-anchor="end">' + fmtHM(last.timestamp) + '</text>' +
      '<polyline points="' + pts + '" fill="none" stroke="#0b6e8a" stroke-width="2" stroke-linejoin="round"/>' +
      '<circle cx="' + px(x1).toFixed(1) + '" cy="' + py(last.salinity).toFixed(1) + '" r="3" fill="#0b6e8a"/>' +
      '</svg>'
    );
  }

  // Датчики буя: солёность, температура воды, уровень, радиус слышимости — из GET /api/v1/buoy-status
  function sensorsHtml(id) {
    const d = (state.buoyStatus[id] || {}).data || {};
    const num = (v, digits, unit) => (typeof v === 'number' ? v.toFixed(digits) + ' ' + unit : '—');
    return '<dl class="sensors">' +
      '<div><dt>Солёность</dt><dd>' + num(d.salinity, 2, '‰') + '</dd></div>' +
      '<div><dt>Вода</dt><dd>' + num(d.water_temp_c, 1, '°C') + '</dd></div>' +
      '<div><dt>Уровень</dt><dd>' + num(d.level_m, 2, 'м') + '</dd></div>' +
      '<div><dt>Метан</dt><dd>' + num(d.methane_nmol, 0, 'нмоль/л') + '</dd></div>' +
      '<div><dt>Слышит</dt><dd>' + num(d.listen_radius_km, 0, 'км') + '</dd></div>' +
      '</dl>' + (d.telemetry_at ? '<div class="demo-note">Телеметрия: ' + fmtTime(d.telemetry_at) + '</div>' : '');
  }

  function renderWaterMarkers() {
    waterLayer.clearLayers();
    state.water.forEach((e) => {
      if (!e.geo) return;
      L.marker([e.geo.lat, e.geo.lon], { icon: icon('water-mk ' + esc(e.type), 16) })
        .addTo(waterLayer)
        .bindTooltip(esc(waterName(e.type)))
        .bindPopup(
          '<div class="popup"><h3>' + esc(waterName(e.type)) + '</h3><div class="sub">' + fmtTime(e.timestamp) + ' · ' + coords(e.geo) + '</div>' +
            waterDetails(e) + waterImage(e) + '</div>',
          { minWidth: 240, maxWidth: 300 }
        );
    });
  }

  // Пульсирующие метки активных инцидентов
  function renderAlerts() {
    alertLayer.clearLayers();
    state.incidents.filter((i) => i.active && i.geo).forEach((inc) => {
      L.marker([inc.geo.lat, inc.geo.lon], {
        icon: L.divIcon({ className: '', html: '<div class="pulse ' + inc.severity + '"></div>', iconSize: [44, 44], iconAnchor: [22, 22] }),
        zIndexOffset: 1000,
      }).addTo(alertLayer)
        .bindTooltip(esc(inc.title) + ' · ' + fmtHM(new Date(inc.end).toISOString()))
        .on('click', () => openIncident(inc.id));
    });
  }

  // Слой тюленей: где слышны, оценка численности, куда смещается группа (считает бэкенд, GET /api/v1/seals)
  function renderSeals() {
    sealLayer.clearLayers();
    const d = state.seals;
    const box = $('#seal-summary');
    if (!d) { if (box) box.textContent = 'нет данных'; return; }
    (d.buoys || []).forEach((p) => {
      const b = buoyById(p.buoy_id);
      if (!b) return;
      L.circleMarker([b.geo.lat, b.geo.lon], { radius: 6 + Math.min(16, p.detections * 1.5), className: 'seal-dots', interactive: true })
        .bindTooltip(esc(b.name) + ': тюлень слышен ' + p.detections + ' раз · последний ' + ago(ms(p.last_seen)))
        .addTo(sealLayer);
    });
    (d.groups || []).forEach((g) => {
      const pts = g.track.map((t) => [t.lat, t.lon]);
      if (pts.length >= 2) {
        L.polyline(pts, { className: 'seal-track', interactive: false }).addTo(sealLayer);
        pts.forEach((pt, i) => L.circleMarker(pt, { radius: 3, className: 'seal-step', interactive: false }).addTo(sealLayer));
      }
      const drift = g.drift ? 'дрейф на ' + g.drift.direction + ' · ' + g.drift.distance_km + ' км за ' + d.window_h + ' ч' : 'на месте';
      const arrow = g.drift ? '<i class="seal-arrow" style="transform:rotate(' + (g.drift.bearing_deg - 90) + 'deg)">➜</i>' : '';
      L.marker([g.center.lat, g.center.lon], {
        icon: L.divIcon({ className: '', iconSize: [0, 0], html: '<div class="seal-mk">' + arrow + '<b>Тюлени ≥' + g.estimate_min + '</b><span>' + esc(drift) + '</span></div>' }),
        zIndexOffset: 500,
      }).addTo(sealLayer)
        .bindPopup('<div class="popup"><h3>Группа тюленей</h3>' +
          '<dl class="kv"><dt>Вокализаций</dt><dd>' + g.detections + ' за ' + d.window_h + ' ч</dd>' +
          '<dt>Оценка численности</dt><dd>не меньше ' + g.estimate_min + '</dd>' +
          '<dt>Слышат буи</dt><dd>' + esc(g.buoys.map(buoyName).join(', ')) + '</dd>' +
          '<dt>Дрейф</dt><dd>' + (g.drift ? esc(g.drift.direction) + ' (' + g.drift.bearing_deg + '°), ' + g.drift.distance_km + ' км, ~' + g.drift.speed_kmh + ' км/ч' : 'не заметен') + '</dd>' +
          '<dt>Последний раз</dt><dd>' + fmtTime(g.last_seen) + '</dd></dl>' +
          '<div class="sub">' + esc(d.method) + '</div></div>', { maxWidth: 320 });
    });
    if (box) {
      const g = (d.groups || [])[0];
      box.innerHTML = d.total_detections
        ? d.total_detections + ' вокализаций за ' + d.window_h + ' ч · ≥' + d.estimate_min + ' особ.' + (g && g.drift ? ' · дрейф на ' + esc(g.drift.direction) : '')
        : 'за ' + d.window_h + ' ч не слышно';
    }
  }

  // Слой прогноза: приоритет патруля по буям на выбранный день
  let fcDay = 0;
  function renderForecastLayer() {
    forecastLayer.clearLayers();
    const list = (state.forecast && state.forecast.forecasts) || [];
    list.forEach((f) => {
      const b = buoyById(f.buoy_id), d = f.days[fcDay];
      if (!b || !d) return;
      L.circle([b.geo.lat, b.geo.lon], { radius: 9000 + 14000 * d.risk_score, className: 'fc-zone r-' + d.risk_level, interactive: true })
        .bindTooltip(esc(b.name) + ': тюлень ' + pct(d.seal_probability) + ', приоритет ' + esc(C.RISK_LEVELS[d.risk_level] || d.risk_level))
        .addTo(forecastLayer);
    });
    const label = $('#fc-day-label');
    if (label && list[0] && list[0].days[fcDay]) {
      label.textContent = new Date(list[0].days[fcDay].date + 'T00:00:00').toLocaleDateString('ru-RU', { weekday: 'short', day: '2-digit', month: '2-digit' });
    }
  }

  const LayersCtl = L.Control.extend({
    options: { position: 'topright' },
    onAdd() {
      const div = L.DomUtil.create('div', 'map-ctl');
      div.innerHTML =
        '<label><input type="checkbox" id="seal-toggle" checked> Тюлени: где и куда дрейфуют</label>' +
        '<div class="sub" id="seal-summary">загрузка…</div>' +
        '<label><input type="checkbox" id="fc-toggle"> Прогноз: приоритет патруля</label>' +
        '<div class="fc-slider" hidden><input type="range" id="fc-day" min="0" max="' + (C.FORECAST_DAYS - 1) + '" value="0"> <span id="fc-day-label"></span>' +
        '<div class="sub">Модель на допущениях (синтетика)</div></div>';
      L.DomEvent.disableClickPropagation(div);
      L.DomEvent.disableScrollPropagation(div);
      return div;
    },
  });
  new LayersCtl().addTo(map);
  $('#fc-toggle').addEventListener('change', (e) => {
    $('.fc-slider').hidden = !e.target.checked;
    if (e.target.checked) { forecastLayer.addTo(map); if (!state.forecast) loadForecastData(); else renderForecastLayer(); }
    else map.removeLayer(forecastLayer);
  });
  $('#seal-toggle').addEventListener('change', (e) => (e.target.checked ? sealLayer.addTo(map) : map.removeLayer(sealLayer)));
  $('#fc-day').addEventListener('input', (e) => { fcDay = +e.target.value; renderForecastLayer(); });

  const Legend = L.Control.extend({
    options: { position: 'bottomleft' },
    onAdd() {
      const div = L.DomUtil.create('div', 'map-ctl legend');
      div.innerHTML =
        '<b>Буй за сутки</b>' +
        ['normal', 'anomaly', 'critical'].map((l) => '<div><i class="lg s-' + l + '"></i>' + LEVEL_TEXT[l] + '</div>').join('') +
        '<div><i class="lg s-off"></i>нет связи</div>' +
        '<div><i class="lg zone-key"></i>ООПТ (схематично)</div>' +
        '<div><i class="lg listen-key"></i>радиус слышимости буя</div>' +
        '<div><i class="lg seal-key"></i>тюлени и их дрейф</div>' +
        '<div><i class="lg water-key"></i>событие воды</div>';
      return div;
    },
  });
  new Legend().addTo(map);

  // details и source: показываем только поля, которые не null
  function waterDetails(e) {
    const d = e.details || {}, s = e.source || {}, rows = [];
    const add = (k, v) => rows.push('<dt>' + k + '</dt><dd>' + v + '</dd>');
    if (d.salinity != null) add('Солёность', d.salinity + ' ‰');
    if (d.salinity_delta != null) add('Отклонение солёности', (d.salinity_delta > 0 ? '+' : '') + d.salinity_delta + ' ‰');
    if (d.level_delta_m != null) add('Изменение уровня', (d.level_delta_m > 0 ? '+' : '') + d.level_delta_m + ' м');
    if (d.confidence != null) add('Уверенность', pct(d.confidence));
    if (s.buoy_id != null) add('Буй', esc(buoyName(s.buoy_id)));
    if (s.image_id != null) add('Снимок', esc(s.image_id));
    return rows.length ? '<dl class="kv">' + rows.join('') + '</dl>' : '';
  }
  const waterImage = (e) =>
    e.source && e.source.image_id ? '<img src="' + esc(Api.imageUrl(e.source.image_id)) + '" alt="Спутниковый снимок" loading="lazy">' : '';

  // ---------- лента всех событий ----------
  function eventNode(item) {
    const el = document.createElement('article');
    el.dataset.key = itemKey(item);
    el.className = 'ev' + (isCritical(item) ? ' crit' : '');
    if (item.kind === 'sound') {
      const c = item.classification || {};
      const cands = (c.candidates || []).map((x) => esc(soundName(x.label)) + ' ' + pct(x.confidence)).join(' · ');
      el.innerHTML =
        '<div class="ev-head"><span class="badge ' + esc(c.label) + '">' + esc(soundName(c.label)) + '</span>' +
        '<span class="conf">' + pct(c.confidence) + '</span><time>' + fmtTime(item.timestamp) + '</time></div>' +
        '<div class="ev-meta">Звук · ' + esc(buoyName(item.buoy_id)) + ' · источник: ' + esc(item.source) + ' · ' + esc(item.event_id) + '</div>' +
        (cands ? '<div class="cands">Варианты: ' + cands + '</div>' : '') +
        '<audio controls preload="none" src="' + esc(Api.audioUrl(item)) + '"></audio>';
    } else {
      el.innerHTML =
        '<div class="ev-head"><span class="badge ' + esc(item.type) + '">' + esc(waterName(item.type)) + '</span>' +
        '<time>' + fmtTime(item.timestamp) + '</time></div>' +
        '<div class="ev-meta">Вода · ' + coords(item.geo) + ' · ' + esc(item.event_id) + '</div>' +
        '<div class="ev-body">' + waterImage(item) + waterDetails(item) + '</div>';
    }
    el.title = 'Подробнее: какие соседние буи это слышали';
    el.addEventListener('click', (ev) => {
      if (ev.target.closest('audio')) return;
      const it = allItems().find((x) => itemKey(x) === el.dataset.key);
      if (it) openEvent(it);
    });
    return el;
  }

  // Обновляем список по ключам, не трогая уже показанные карточки (плеер не сбрасывается)
  function renderList(container, items, keyOf, build) {
    const old = new Map(Array.from(container.children).map((n) => [n.dataset.key, n]));
    let ref = container.firstElementChild;
    items.forEach((item) => {
      const key = keyOf(item);
      if (ref && ref.dataset.key === key) { ref = ref.nextElementSibling; return; }
      container.insertBefore(old.get(key) || build(item), ref);
    });
    while (ref) { const next = ref.nextElementSibling; ref.remove(); ref = next; }
  }

  // ---------- инциденты ----------
  const DECISIONS = { confirmed: 'Подтверждено инспектором', false: 'Ложная тревога', patrol: 'Патруль отправлен' };
  const incSig = (inc) => [inc.id, inc.items.length, inc.decision, inc.active, inc.zone && inc.zone.id].join('|');

  function incidentNode(inc) {
    const el = document.createElement('article');
    el.dataset.key = incSig(inc);
    el.className = 'inc ' + inc.severity + (inc.active ? ' active' : '') + (inc.decision === 'false' ? ' dismissed' : '');
    el.innerHTML =
      '<div class="ev-head"><span class="sev ' + inc.severity + '">' + (inc.severity === 'alarm' ? 'Тревога' : 'Внимание') + '</span>' +
      '<b class="inc-title">' + esc(inc.title) + '</b>' +
      (inc.confirmed ? '<span class="confirmed">✓ ' + esc(inc.confirmed) + '</span>' : '') +
      '<time>' + fmtTime(new Date(inc.end).toISOString()) + '</time></div>' +
      '<div class="ev-meta">' + (inc.zone ? 'ООПТ «' + esc(inc.zone.name) + '» · ' : 'Вне ООПТ · ') +
      (inc.buoys.length ? esc(inc.buoys.map(buoyName).join(', ')) + ' · ' : '') +
      'сигналов: ' + inc.items.length + ' · уверенность до ' + pct(inc.conf) +
      (inc.decision ? ' · <b>' + esc(DECISIONS[inc.decision]) + '</b>' : inc.active ? ' · <b class="todo">не разобрано</b>' : '') + '</div>' +
      '<div class="inc-chips">' + inc.items.slice().reverse().map((i) =>
        '<span class="badge ' + esc(itemLabel(i)) + '">' + esc(itemName(i)) + ' ' + pct(itemConf(i)) + '</span>').join(' ') + '</div>';
    el.addEventListener('click', () => openIncident(inc.id));
    return el;
  }

  function renderIncidents() {
    renderList($('#list-crit'), state.incidents, incSig, incidentNode);
    const todo = state.incidents.filter((i) => i.active && !i.decision).length;
    const cnt = $('#crit-count');
    cnt.hidden = !todo;
    cnt.textContent = todo;
  }

  let openId = null;
  function openIncident(id) {
    const inc = state.incidents.find((i) => i.id === id);
    if (!inc) return;
    openId = id;
    const risk = inc.buoys.map((b) => [b, todayRisk(b)]).filter((x) => x[1]);
    $('#modal-body').innerHTML =
      '<div class="inc-head"><span class="sev ' + inc.severity + '">' + (inc.severity === 'alarm' ? 'Тревога' : 'Внимание') + '</span>' +
      (inc.confirmed ? '<span class="confirmed">✓ ' + esc(inc.confirmed) + '</span>' : '') + '</div>' +
      '<h2>' + esc(inc.title) + '</h2>' +
      '<dl class="kv">' +
      '<dt>Время</dt><dd>' + fmtTime(new Date(inc.start).toISOString()) + (inc.end !== inc.start ? ' — ' + fmtHM(new Date(inc.end).toISOString()) : '') + '</dd>' +
      '<dt>Место</dt><dd>' + (inc.zone ? 'ООПТ «' + esc(inc.zone.name) + '» (' + esc(inc.zone.note) + ')' : 'вне ООПТ') + ' · ' + coords(inc.geo) + '</dd>' +
      '<dt>Буи</dt><dd>' + (inc.buoys.length ? esc(inc.buoys.map(buoyName).join(', ')) : '—') + '</dd>' +
      (risk.length ? '<dt>Прогноз на сегодня</dt><dd>' + risk.map(([b, d]) =>
        esc(buoyName(b)) + ': тюлень ' + pct(d.seal_probability) + ', приоритет ' + esc(C.RISK_LEVELS[d.risk_level] || d.risk_level)).join('; ') + '</dd>' : '') +
      '</dl>' +
      '<h3>Сигналы (' + inc.items.length + ')</h3>' +
      '<div class="signals">' + inc.items.map(signalHtml).join('') + '</div>' +
      '<div class="decide">' +
      '<button class="btn primary" data-d="patrol">Отправить патруль</button>' +
      '<button class="btn" data-d="confirmed">Подтвердить</button>' +
      '<button class="btn danger" data-d="false">Ложная тревога</button>' +
      '<button class="btn" id="act-btn">Скачать акт (PDF)</button></div>' +
      '<div class="sub decide-note">' + (inc.decision ? 'Решение: <b>' + esc(DECISIONS[inc.decision]) + '</b>. ' : '') +
      'Отметки «ложная тревога» — новые метки для дообучения модели (в прототипе хранятся в этом браузере).</div>';
    $('#modal').hidden = false;
    document.querySelectorAll('#modal-body [data-d]').forEach((b) => b.classList.toggle('chosen', b.dataset.d === inc.decision));
    fillModal();
  }

  // Спектрограммы и «кто ещё слышал» в открытом окне
  function fillModal() {
    document.querySelectorAll('#modal-body canvas[data-src]').forEach((cv) => {
      Spectrogram.draw(cv, cv.dataset.src)
        .then((r) => { cv.nextElementSibling.textContent = 'Спектрограмма · ' + r.seconds.toFixed(1) + ' с · 0–' + Math.round(r.maxHz / 1000) + ' кГц'; })
        .catch(() => { cv.nextElementSibling.textContent = 'Спектрограмма недоступна'; cv.remove(); });
    });
    document.querySelectorAll('#modal-body .nbrs[data-id]').forEach((box) => {
      Api.getNeighbors(box.dataset.kind, box.dataset.id)
        .then((n) => (box.innerHTML = neighborsHtml(n, box.classList.contains('full'))))
        .catch((e) => (box.innerHTML = '<span class="sub">Соседи недоступны: ' + esc(e.message) + '</span>'));
    });
  }

  const VERDICT = { confirmed: '✓ подтверждено', unconfirmed: '⚠ не подтверждено', isolated: '— проверить нечем' };
  function neighborsHtml(n, full) {
    const head = '<div class="verdict v-' + esc(n.verdict) + '"><b>' + esc(VERDICT[n.verdict] || n.verdict) + '</b> · ' + esc(n.message) + '</div>';
    const list = n.neighbors || [];
    if (!full) {
      return head + '<div class="nb-chips">' + list.filter((x) => x.in_range).map((x) =>
        '<span class="nb ' + (x.heard ? 'yes' : 'no') + '">' + (x.heard ? '✓ ' : '✗ ') + esc(x.name) +
        (x.heard && x.delta_sec != null ? ' (' + (x.delta_sec > 0 ? '+' : '') + x.delta_sec + ' с)' : '') + '</span>').join('') + '</div>';
    }
    const isWater = n.label in C.WATER_TYPES;
    return head + '<table class="nb-table"><thead><tr><th>Буй</th><th>Расстояние</th><th>Мог ' + (isWater ? 'зафиксировать' : 'услышать') + '</th><th>' +
      (isWater ? 'Солёность рядом по времени' : 'Слышал то же ±' + n.window_min + ' мин') + '</th></tr></thead><tbody>' +
      list.map((x) => '<tr class="' + (x.in_range ? '' : 'far') + '"><td>' + esc(x.name) + '</td><td>' + x.distance_km + ' км</td>' +
        '<td>' + (x.in_range ? 'да' : 'нет, далеко') + '</td><td class="' + (x.heard ? 'yes' : x.in_range ? 'no' : '') + '">' +
        (isWater
          ? (x.salinity != null ? x.salinity + ' ‰ (' + (x.salinity_delta > 0 ? '+' : '') + x.salinity_delta + ')' : 'нет данных') + (x.heard ? ' ✓' : '')
          : x.heard ? '✓ ' + pct(x.confidence) + ', ' + (x.delta_sec > 0 ? '+' : '') + x.delta_sec + ' с' : x.in_range ? '✗ не слышал' : '—') +
        '</td></tr>').join('') + '</tbody></table>';
  }

  // Окно одного события: что услышала модель и какие соседи это подтверждают
  function openEvent(item) {
    openId = null;
    const inc = state.incidents.find((i) => i.items.some((x) => itemKey(x) === itemKey(item)));
    const zone = zoneOf(itemGeo(item));
    $('#modal-body').innerHTML =
      '<h2>' + esc(itemName(item)) + (itemConf(item) != null ? ' · ' + pct(itemConf(item)) : '') + '</h2>' +
      '<div class="sub">' + fmtTime(item.timestamp) + ' · ' + (itemBuoy(item) ? esc(buoyName(itemBuoy(item))) + ' · ' : '') +
      (zone ? 'ООПТ «' + esc(zone.name) + '»' : 'вне ООПТ') + ' · ' + esc(item.event_id) + '</div>' +
      signalHtml(item, false) +
      '<h3>Кто ещё ' + (item.kind === 'sound' ? 'слышал' : 'фиксировал') + ' это</h3>' +
      '<div class="nbrs full" data-kind="' + item.kind + '" data-id="' + esc(item.event_id) + '">Проверяем соседние буи…</div>' +
      '<div class="ctx-head"><h3>Что происходило рядом</h3><div class="ctx-range">' +
      C.CONTEXT_MINUTES.map((m) => '<button class="ctx-btn' + (m === ctxMinutes ? ' on' : '') + '" data-ctx="' + m + '">±' + m + ' мин</button>').join('') +
      '</div></div><div class="ctx" id="ctx">Загружаем события вокруг…</div>' +
      (inc ? '<div class="decide"><button class="btn primary" data-inc="' + esc(inc.id) + '">Открыть инцидент: ' + esc(inc.title) + '</button></div>' : '');
    $('#modal').hidden = false;
    ctxItem = item;
    fillModal();
    loadContext();
  }

  // Общая картина: все события на всех буях в ± N минут вокруг выбранного (GET …/context)
  let ctxMinutes = C.CONTEXT_MINUTES[1], ctxItem = null;
  async function loadContext() {
    const box = $('#ctx');
    if (!box || !ctxItem) return;
    try {
      renderContext(box, await Api.getContext(ctxItem.kind, ctxItem.event_id, ctxMinutes));
    } catch (e) {
      box.innerHTML = '<span class="err-text">Не удалось загрузить: ' + esc(e.message) + '</span>';
    }
  }

  function renderContext(box, d) {
    const W = d.window_min * 60;
    const name = (it) => (it.kind === 'sound' ? soundName(it.label) : waterName(it.label));
    const dt = (sec) => (sec === 0 ? '0 с' : (sec > 0 ? '+' : '−') + (Math.abs(sec) >= 60 ? Math.floor(Math.abs(sec) / 60) + ' мин ' + (Math.abs(sec) % 60) + ' с' : Math.abs(sec) + ' с'));
    // лента времени: точки событий по горизонтали от −N до +N минут, по строке на буй
    const rows = Array.from(new Set(d.items.map((i) => i.buoy_id || 'спутник'))).sort();
    const rowH = 22, H = rows.length * rowH + 22;
    const x = (sec) => (20 + ((sec + W) / (2 * W)) * 76).toFixed(2) + '%'; // слева 20% — подписи буёв
    const lane = (i) => rows.indexOf(i.buoy_id || 'спутник');
    const timeline =
      '<div class="tl" style="height:' + H + 'px">' +
      rows.map((r, k) => '<div class="tl-row" style="top:' + (k * rowH) + 'px"><span>' + esc(r === 'спутник' ? r : buoyName(r)) + '</span></div>').join('') +
      '<div class="tl-now" style="left:' + x(0) + '"></div>' +
      d.items.map((i) => '<button class="tl-dot ' + esc(i.label) + (i.is_origin ? ' origin' : '') + '" style="left:' + x(i.delta_sec) + ';top:' + (lane(i) * rowH + 4) + 'px" title="' +
        esc(name(i) + ' · ' + (i.buoy_id ? buoyName(i.buoy_id) : 'спутник') + ' · ' + dt(i.delta_sec)) + '" data-ev-kind="' + i.kind + '" data-ev-id="' + esc(i.event_id) + '"></button>').join('') +
      '<div class="tl-axis" style="top:' + (rows.length * rowH + 2) + 'px"><span style="left:' + x(-W) + '">−' + d.window_min + ' мин</span><span style="left:' + x(0) + '">это событие</span><span style="left:' + x(W) + '">+' + d.window_min + ' мин</span></div>' +
      '</div>';
    const summary = d.total
      ? 'Ещё ' + d.total + ' событ. на ' + d.buoys.length + ' буях: ' + Object.entries(d.counts).map(([l, n]) => esc(l in C.SOUND_LABELS ? soundName(l) : waterName(l)) + ' ×' + n).join(', ')
      : 'В этом окне больше ничего не происходило — событие одиночное.';
    const list = d.items.map((i) =>
      '<tr class="' + (i.is_origin ? 'origin' : '') + '" data-ev-kind="' + i.kind + '" data-ev-id="' + esc(i.event_id) + '">' +
      '<td>' + dt(i.delta_sec) + '</td><td><span class="badge sm ' + esc(i.label) + '">' + esc(name(i)) + '</span> ' + (i.confidence != null ? pct(i.confidence) : '') + '</td>' +
      '<td>' + esc(i.buoy_id ? buoyName(i.buoy_id) : i.source || '—') + (i.same_buoy && !i.is_origin ? ' <span class="sub">тот же буй</span>' : '') + '</td>' +
      '<td>' + (i.is_origin ? '—' : i.distance_km != null ? i.distance_km + ' км' : '—') + '</td></tr>').join('');
    box.innerHTML = '<div class="ctx-sum">' + summary + '</div>' + timeline +
      '<table class="nb-table ctx-table"><thead><tr><th>Время</th><th>Что</th><th>Где</th><th>Расстояние</th></tr></thead><tbody>' + list + '</tbody></table>';
  }

  async function openEventById(kind, id) {
    try {
      const ev = kind === 'water' ? await Api.getWaterEvent(id) : await Api.getEvent(id);
      openEvent(Object.assign({ kind }, ev));
    } catch (e) {
      toast('info', 'Событие не открылось', e.message);
    }
  }

  function signalHtml(i, withNeighbors) {
    const nb = withNeighbors === false ? '' : '<div class="nbrs" data-kind="' + i.kind + '" data-id="' + esc(i.event_id) + '"></div>';
    if (i.kind === 'sound') {
      const c = i.classification || {};
      const cands = (c.candidates || []).map((x) => esc(soundName(x.label)) + ' ' + pct(x.confidence)).join(' · ');
      return '<div class="signal"><div class="ev-head"><span class="badge ' + esc(c.label) + '">' + esc(soundName(c.label)) + '</span>' +
        '<span class="conf">' + pct(c.confidence) + '</span><span class="sub">' + esc(buoyName(i.buoy_id)) + '</span><time>' + fmtTime(i.timestamp) + '</time>' +
        (withNeighbors === false ? '' : '<button class="link" data-ev-kind="sound" data-ev-id="' + esc(i.event_id) + '">что было рядом →</button>') + '</div>' +
        (cands ? '<div class="cands">Модель: ' + cands + '</div>' : '') +
        '<canvas class="spec" width="560" height="110" data-src="' + esc(Api.audioUrl(i)) + '"></canvas><div class="sub spec-cap">Спектрограмма…</div>' +
        '<audio controls preload="none" src="' + esc(Api.audioUrl(i)) + '"></audio>' + nb + '</div>';
    }
    const src = i.source && i.source.image_id ? 'спутник' : i.type === 'level_anomaly' ? 'датчик уровня' : 'датчик солёности';
    return '<div class="signal"><div class="ev-head"><span class="badge ' + esc(i.type) + '">' + esc(waterName(i.type)) + '</span>' +
      '<span class="sub">' + src + '</span><time>' + fmtTime(i.timestamp) + '</time></div>' +
      '<div class="ev-body">' + waterImage(i) + waterDetails(i) + '</div>' + nb + '</div>';
  }

  function closeModal() { $('#modal').hidden = true; openId = null; $('#modal-body').innerHTML = ''; }
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#modal').hidden) closeModal(); });
  $('#modal-body').addEventListener('click', (e) => {
    const b = e.target.closest('[data-d]');
    if (b && openId) {
      const d = b.dataset.d;
      if (state.decisions[openId] === d) delete state.decisions[openId]; else state.decisions[openId] = d;
      store.set('eco.decisions', state.decisions);
      const id = openId;
      recompute();
      if (d === 'patrol' && state.decisions[id] === 'patrol') toast('info', 'Патруль отправлен', 'Координаты переданы группе: ' + coords((state.incidents.find((i) => i.id === id) || {}).geo));
      openIncident(id);
    }
    if (e.target.id === 'act-btn' && openId) printAct(openId);
    const incBtn = e.target.closest('[data-inc]');
    if (incBtn) openIncident(incBtn.dataset.inc);
    const ctxBtn = e.target.closest('[data-ctx]');
    if (ctxBtn) {
      ctxMinutes = +ctxBtn.dataset.ctx;
      document.querySelectorAll('.ctx-btn').forEach((b) => b.classList.toggle('on', b === ctxBtn));
      loadContext();
    }
    const evLink = e.target.closest('[data-ev-id]');
    if (evLink && !evLink.classList.contains('origin')) openEventById(evLink.dataset.evKind, evLink.dataset.evId);
  });

  // Акт фиксации: печатная страница → «Сохранить как PDF»
  function printAct(id) {
    const inc = state.incidents.find((i) => i.id === id);
    const w = window.open('', '_blank');
    if (!inc || !w) return;
    const specs = Array.from(document.querySelectorAll('#modal-body canvas.spec')).map((cv) => cv.toDataURL('image/png'));
    let si = 0;
    const rows = inc.items.map((i) => {
      const spec = i.kind === 'sound' && specs[si] ? '<img class="spec" src="' + specs[si++] + '">' : '';
      const img = i.kind === 'water' && i.source && i.source.image_id ? '<img class="sat" src="' + esc(Api.imageUrl(i.source.image_id)) + '">' : '';
      return '<tr><td>' + fmtTime(i.timestamp) + '</td><td>' + esc(itemName(i)) + '</td><td>' + pct(itemConf(i)) + '</td>' +
        '<td>' + esc(itemBuoy(i) ? buoyName(itemBuoy(i)) : 'спутник') + '<br>' + coords(itemGeo(i)) + '</td>' +
        '<td>' + esc(i.event_id) + (i.kind === 'sound' ? '<br><small>' + esc(Api.audioUrl(i)) + '</small>' : '') + spec + img + '</td></tr>';
    }).join('');
    w.document.write('<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Акт ' + esc(inc.id) + '</title><style>' +
      'body{font:13px/1.45 system-ui,sans-serif;color:#111;margin:24px}h1{font-size:18px;margin:0 0 4px}table{border-collapse:collapse;width:100%;margin-top:12px}' +
      'td,th{border:1px solid #bbb;padding:6px;vertical-align:top;text-align:left}img.spec{display:block;width:100%;max-width:420px;margin-top:6px}' +
      'img.sat{display:block;max-width:260px;margin-top:6px}.muted{color:#555}.sign{margin-top:40px;display:flex;gap:60px}</style></head><body>' +
      '<h1>Акт фиксации нарушения № ' + esc(inc.id) + '</h1>' +
      '<div class="muted">Сформирован автоматически системой «Эко-Щит Каспия» · ' + fmtTime(new Date().toISOString()) + '</div>' +
      '<p><b>' + esc(inc.title) + '</b>' + (inc.confirmed ? ' — ' + esc(inc.confirmed) : '') + '<br>' +
      'Место: ' + (inc.zone ? 'ООПТ «' + esc(inc.zone.name) + '»' : 'вне ООПТ') + ', ' + coords(inc.geo) + '<br>' +
      'Период: ' + fmtTime(new Date(inc.start).toISOString()) + ' — ' + fmtTime(new Date(inc.end).toISOString()) + '<br>' +
      'Решение инспектора: ' + esc(DECISIONS[inc.decision] || 'не принято') + '</p>' +
      '<table><tr><th>Время</th><th>Сигнал</th><th>Уверенность</th><th>Источник, координаты</th><th>Доказательства</th></tr>' + rows + '</table>' +
      '<div class="sign"><div>Инспектор ____________________</div><div>Дата ____________</div></div>' +
      '<script>window.onload=function(){setTimeout(function(){window.print()},300)}<\/script></body></html>');
    w.document.close();
  }

  // ---------- алерты ----------
  const seen = {}; // id инцидента → число сигналов, которое уже показали
  function notifyNew() {
    state.incidents.forEach((inc) => {
      const n = inc.items.length;
      if (state.ready && inc.active && !inc.decision && (seen[inc.id] || 0) < n) {
        const last = inc.items[n - 1];
        toast(inc.severity, inc.title, (inc.zone ? 'ООПТ «' + inc.zone.name + '» · ' : '') +
          (itemBuoy(last) ? buoyName(itemBuoy(last)) + ' · ' : '') + itemName(last) + ' ' + pct(itemConf(last)) +
          (inc.confirmed ? ' · ✓ ' + inc.confirmed : ''), inc.id);
      }
      seen[inc.id] = n;
    });
  }

  function toast(kind, title, text, incId) {
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.innerHTML = '<b>' + esc(title) + '</b><div>' + esc(text) + '</div>' + (incId ? '<button class="btn">Открыть</button>' : '');
    if (incId) el.querySelector('button').addEventListener('click', () => { el.remove(); openIncident(incId); });
    el.addEventListener('dblclick', () => el.remove());
    $('#toasts').prepend(el);
    while ($('#toasts').children.length > 4) $('#toasts').lastElementChild.remove();
    setTimeout(() => el.classList.add('hide'), kind === 'info' ? 5000 : 12000);
    setTimeout(() => el.remove(), kind === 'info' ? 5600 : 12600);
    if (kind !== 'info') beep(kind === 'alarm' ? 2 : 1);
  }

  let audioCtx = null;
  function beep(times) {
    if (!C.ALERT_SOUND) return;
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      for (let k = 0; k < times; k++) {
        const o = audioCtx.createOscillator(), g = audioCtx.createGain(), t = audioCtx.currentTime + k * 0.22;
        o.frequency.value = 880; g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.15, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
        o.connect(g).connect(audioCtx.destination); o.start(t); o.stop(t + 0.2);
      }
    } catch (e) { /* звук недоступен — не страшно */ }
  }

  // ---------- шапка ----------
  function renderKpis() {
    const day = Date.now() - 24 * 60 * MIN;
    const inc24 = state.incidents.filter((i) => i.end >= day && i.decision !== 'false');
    const online = state.buoys.filter((b) => (state.buoyStatus[b.buoy_id] || {}).online).length;
    const todo = state.incidents.filter((i) => i.active && !i.decision).length;
    const falseCnt = Object.values(state.decisions).filter((d) => d === 'false').length;
    const kpi = (v, l, cls) => '<div class="kpi ' + (cls || '') + '"><b>' + v + '</b><span>' + l + '</span></div>';
    $('#kpis').innerHTML =
      kpi(online + '/' + state.buoys.length, 'буёв на связи', online < state.buoys.length ? 'warn' : '') +
      kpi(inc24.length, 'тревог за 24 ч') +
      kpi(todo, 'не разобрано', todo ? 'alarm' : '') +
      kpi(falseCnt, 'ложных отмечено');
  }

  function renderStatus() {
    const el = $('#status');
    el.title = C.API_BASE;
    if (C.USE_MOCK) { el.className = 'status ok'; $('span', el).textContent = 'демо-данные'; return; }
    if (state.online === false) { el.className = 'status err'; $('span', el).textContent = 'нет связи с сервером'; return; }
    if (!state.lastOk) return;
    el.className = 'status ok';
    $('span', el).textContent = 'онлайн · обновлено ' + ago(state.lastOk);
  }
  setInterval(renderStatus, 1000);

  // ---------- правая панель: буи ----------
  function renderBuoyList() {
    $('#buoys').innerHTML = state.buoys.map((b) =>
      '<li class="buoy" data-id="' + esc(b.buoy_id) + '">' +
        '<button class="buoy-main" title="Показать на карте"><i class="sdot"></i><b>' + esc(b.name) + '</b>' +
        '<span class="buoy-zone">' + esc((zoneOf(b.geo) || { name: 'вне ООПТ' }).name) + '</span></button>' +
        '<div class="buoy-state"></div>' +
        '<div class="buoy-actions">' +
          '<button class="btn rec">● Записать звук</button>' +
          '<label class="btn">Файл<input type="file" accept="audio/*" hidden></label>' +
        '</div><div class="buoy-result"></div></li>'
    ).join('');
  }

  // Мини-график солёности за сутки
  function sparkline(list) {
    if (list.length < 2) return '';
    const W = 120, H = 26, ys = list.map((m) => m.salinity);
    const y0 = Math.min.apply(null, ys) - 0.1, y1 = Math.max.apply(null, ys) + 0.1;
    const pts = ys.map((y, i) => ((i / (ys.length - 1)) * W).toFixed(1) + ',' + (H - ((y - y0) / (y1 - y0)) * H).toFixed(1)).join(' ');
    return '<svg class="spark" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none"><polyline points="' + pts + '"/></svg>';
  }

  function paintBuoyList() {
    document.querySelectorAll('#buoys .buoy').forEach((li) => {
      const id = li.dataset.id, s = state.buoyStatus[id] || { level: 'off' };
      li.className = 'buoy s-' + s.level + (s.online ? '' : ' offline');
      const last = state.sound.find((e) => e.buoy_id === id);
      const sal = state.salinity[id] || [];
      const d = s.data || {};
      $('.buoy-state', li).innerHTML =
        '<div class="bs-line"><span class="zl-' + s.level + '">' + esc(LEVEL_TEXT[s.level]) + '</span> за сутки · ' + esc(s.reason) + '</div>' +
        '<div class="bs-line sub">' + (s.online ? 'Сигнал: ' + (s.last ? ago(s.last) : '—') : '<b class="err-text">нет связи</b>' + (s.last ? ' · последний сигнал ' + ago(s.last) : '')) +
        (last ? ' · слышал: <span class="badge sm ' + esc(last.classification.label) + '">' + esc(soundName(last.classification.label)) + '</span>' : '') + '</div>' +
        (sal.length ? '<div class="bs-line sub sal">Солёность ' + sal[sal.length - 1].salinity.toFixed(2) + ' ‰ ' + sparkline(sal) + '</div>' : '') +
        '<div class="bs-line sub">' + [typeof d.water_temp_c === 'number' ? 'вода ' + d.water_temp_c.toFixed(1) + ' °C' : '',
          typeof d.level_m === 'number' ? 'уровень ' + d.level_m.toFixed(2) + ' м' : '',
          typeof d.methane_nmol === 'number' ? 'метан ' + d.methane_nmol.toFixed(0) + ' нмоль/л' : '',
          d.listen_radius_km ? 'слышит ' + d.listen_radius_km + ' км' : ''].filter(Boolean).join(' · ') + '</div>';
    });
  }

  $('#buoys').addEventListener('click', (ev) => {
    const li = ev.target.closest('.buoy');
    if (!li) return;
    if (ev.target.closest('.buoy-main')) focusBuoy(li.dataset.id);
    else if (ev.target.closest('.rec')) toggleRecord(li);
  });
  $('#buoys').addEventListener('change', (ev) => {
    const li = ev.target.closest('.buoy');
    const file = ev.target.files && ev.target.files[0];
    if (li && file) upload(li, file, file.name);
    ev.target.value = '';
  });

  function focusBuoy(id) {
    const m = buoyMarkers[id];
    if (!m) return;
    showTab('map');
    map.setView(m.getLatLng(), Math.max(map.getZoom(), 9));
    m.openPopup();
  }

  const setResult = (li, html) => ($('.buoy-result', li).innerHTML = html);
  const errHtml = (msg) => '<span class="err-text">' + esc(msg) + '</span>';

  let rec = null; // активная запись: { li, recorder }
  async function toggleRecord(li) {
    if (rec) { if (rec.li === li) rec.recorder.stop(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      setResult(li, errHtml('Микрофон в браузере доступен только на localhost или https. Используй кнопку «Файл».'));
      return;
    }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (e) { setResult(li, errHtml('Нет доступа к микрофону')); return; }

    const btn = $('.rec', li), chunks = [], started = Date.now();
    const recorder = new MediaRecorder(stream);
    const tick = () => {
      const s = Math.floor((Date.now() - started) / 1000);
      btn.textContent = '■ Стоп 0:' + String(s).padStart(2, '0');
      if (s >= C.RECORD_MAX_SEC && recorder.state === 'recording') recorder.stop();
    };
    const timer = setInterval(tick, 250);
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      clearInterval(timer);
      stream.getTracks().forEach((t) => t.stop());
      btn.classList.remove('on');
      btn.textContent = '● Записать звук';
      rec = null;
      upload(li, new Blob(chunks, { type: recorder.mimeType }), 'recording');
    };
    rec = { li, recorder };
    btn.classList.add('on');
    setResult(li, '');
    tick();
    recorder.start();
  }

  // Любой аудио-блоб → WAV (моно, 16 бит)
  async function toWav(blob) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    let buf;
    try { buf = await ctx.decodeAudioData(await blob.arrayBuffer()); } finally { ctx.close(); }
    const data = buf.getChannelData(0), n = data.length, rate = buf.sampleRate;
    const dv = new DataView(new ArrayBuffer(44 + n * 2));
    const str = (o, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
    str(0, 'RIFF'); dv.setUint32(4, 36 + n * 2, true); str(8, 'WAVEfmt ');
    dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
    dv.setUint32(24, rate, true); dv.setUint32(28, rate * 2, true);
    dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
    str(36, 'data'); dv.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) {
      const s = Math.max(-1, Math.min(1, data[i]));
      dv.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([dv], { type: 'audio/wav' });
  }

  // POST /api/v1/classify/upload и показ результата в карточке буя
  async function upload(li, blob, name) {
    setResult(li, 'Отправка…');
    try {
      let file = null;
      if (C.CONVERT_TO_WAV) {
        try { file = new File([await toWav(blob)], name.replace(/\.[^.]+$/, '') + '.wav', { type: 'audio/wav' }); }
        catch (e) { /* не удалось перекодировать — шлём как есть */ }
      }
      if (!file) file = blob instanceof File ? blob : new File([blob], name, { type: blob.type });
      const r = await Api.classifyUpload(file, li.dataset.id);
      const c = r.classification || {};
      const cands = (c.candidates || []).map((x) => esc(soundName(x.label)) + ' ' + pct(x.confidence)).join(' · ');
      setResult(li,
        '<span class="badge ' + esc(c.label) + '">' + esc(soundName(c.label)) + '</span> <span class="conf">' + pct(c.confidence) + '</span>' +
        (cands ? '<div class="cands">Варианты: ' + cands + '</div>' : '') +
        (r.audio_url ? '<audio controls preload="none" src="' + esc(Api.audioUrl(r)) + '"></audio>' : '')
      );
      refresh();
    } catch (e) {
      setResult(li, errHtml('Ошибка: ' + e.message));
    }
  }

  // ---------- режим демо ----------
  function setDemo(on) {
    document.body.classList.toggle('demo', on);
    $('#demo-mode').checked = on;
    $('#demo-run').hidden = !on;
    store.set('eco.demo', on);
  }
  $('#demo-mode').addEventListener('change', (e) => setDemo(e.target.checked));
  setDemo(store.get('eco.demo', false));

  let fastUntil = 0;
  $('#demo-run').addEventListener('click', async () => {
    const btn = $('#demo-run');
    btn.disabled = true;
    try {
      const r = await Api.runScenario();
      toast('info', r.started ? 'Сценарий запущен' : 'Сценарий уже идёт', 'Эмулятор буя шлёт данные на бэкенд, около ' + r.duration_sec + ' с: лодка → второй буй → выстрел → сброс → спутник');
      fastUntil = Date.now() + (r.duration_sec + 8) * 1000;
      showTab('map');
      setTimeout(() => (btn.disabled = false), (r.duration_sec + 2) * 1000);
    } catch (e) {
      toast('info', 'Сценарий не запустился', e.message);
      btn.disabled = false;
    }
  });

  // ---------- прогноз присутствия тюленя ----------
  async function loadForecastData() {
    try {
      const data = await Api.getForecast({ days: C.FORECAST_DAYS });
      state.forecast = data;
      recompute();
      renderForecastLayer();
      return data;
    } catch (e) {
      return null;
    }
  }

  async function loadForecast() {
    const box = $('#forecast');
    if (!box.innerHTML) box.innerHTML = '<p class="sub">Загрузка прогноза…</p>';
    try {
      const data = await Api.getForecast({ days: C.FORECAST_DAYS });
      state.forecast = data;
      renderForecast(data);
    } catch (e) {
      box.innerHTML = '<p class="err-text">Прогноз недоступен: ' + esc(e.message) + '</p>';
    }
  }

  function renderForecast(data) {
    const list = data.forecasts || [];
    if (!list.length) { $('#forecast').innerHTML = '<p class="sub">Нет данных прогноза</p>'; return; }
    const day = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString('ru-RU', { weekday: 'short', day: '2-digit', month: '2-digit' });
    const head = list[0].days.map((d) => '<th>' + esc(day(d.date)) + '</th>').join('');
    const rows = list.map((f) =>
      '<tr><th class="fc-buoy">' + esc(buoyName(f.buoy_id)) + '</th>' +
      f.days.map((d) =>
        '<td class="risk ' + esc(d.risk_level) + '" title="Риск: ' + esc(C.RISK_LEVELS[d.risk_level] || d.risk_level) +
        ' (' + pct(d.risk_score) + ') · воздух ' + esc(d.air_temp_c) + ' °C · ветер ' + esc(d.wind_ms) + ' м/с">' +
        pct(d.seal_probability) + '</td>'
      ).join('') +
      '<td>' + esc(f.seal_events_7d) + '</td><td>' + esc(f.threat_events_7d) + '</td></tr>'
    ).join('');
    const weather = data.weather_source === 'open-meteo' ? 'прогноз Open-Meteo' : 'климатическая норма (нет связи с Open-Meteo)';
    $('#forecast').innerHTML =
      '<h2>Вероятность присутствия каспийского тюленя</h2>' +
      '<table class="fc"><thead><tr><th></th>' + head + '<th>Тюлень<br>за 7 дн.</th><th>Лодки и выстрелы<br>за 7 дн.</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table>' +
      '<p class="sub">Цвет — приоритет патрулирования: ' +
      ['low', 'medium', 'high'].map((l) => '<span class="risk-key risk ' + l + '">' + esc(C.RISK_LEVELS[l]) + '</span>').join(' ') +
      '. Он растёт с вероятностью тюленя и с числом лодок и выстрелов у буя. На карте — слой «Прогноз: приоритет патруля».</p>' +
      '<p class="sub">Погода: ' + weather + '. ' +
      (data.training_data === 'synthetic' ? 'Модель обучена на синтетических данных — прогноз на допущениях. ' : '') +
      'Обновлено: ' + fmtTime(data.generated_at) + '</p>';
  }
  setInterval(() => {
    if (!$('#panel-forecast').hidden) loadForecast();
    else loadForecastData();
  }, C.FORECAST_REFRESH_MS);

  // ---------- о системе ----------
  async function loadAbout() {
    const box = $('#about');
    let info = null;
    try { info = await Api.getModelInfo(); } catch (e) { /* покажем без метрик */ }
    const s = (info && info.sound) || {}, f = (info && info.forecast) || {};
    const num = (x, d) => (typeof x === 'number' ? x.toFixed(d === undefined ? 2 : d) : '—');
    const marks = Object.values(state.decisions);
    box.innerHTML =
      '<div class="hero"><canvas id="hero-dots"></canvas><div class="hero-text">' +
      '<h2>Каждый звук моря —<br>под наблюдением</h2>' +
      '<p>Буи слушают воду, модель узнаёт лодку, выстрел и тюленя, а система сводит сигналы с разных датчиков в один инцидент для инспектора.</p>' +
      '</div></div>' +
      '<h2>Как это работает</h2>' +
      '<div class="pipe">' +
      step('Буй', 'гидрофон, датчик солёности, GPS, солнечная панель') +
      step('Вычислитель на буе', 'мини-компьютер или Android-телефон: фильтр, отбор звуков') +
      step('Связь', 'LTE или спутник; передаются события, а не поток') +
      step('Сервер', 'модель звука, правила для воды и снимков, прогноз, слияние в инциденты') +
      step('Инспектор', 'алерт, карточка с доказательствами, решение, акт') +
      '</div>' +
      '<p class="sub">Прототип буя — Android-телефон с микрофоном: он записывает звук и отправляет его в <code>/api/v1/classify/upload</code>. В демо поток событий даёт эмулятор буя.</p>' +
      '<div class="about-grid">' +
      card('Модель звука', 'Градиентный бустинг по 13 MFCC после подводного фильтра. Классы: тюлень, лодка, выстрел, аномалия, фон. Уверенность ниже ' + pct(s.anomaly_threshold) + ' → «аномалия».',
        [['Accuracy', pct(s.accuracy)], ['F1 (macro)', num(s.f1_macro)], ['Precision / Recall', num(s.precision_macro) + ' / ' + num(s.recall_macro)],
         ['Кросс-валидация F1', num(s.cv_f1_mean) + ' ± ' + num(s.cv_f1_std)], ['Тестовых записей', s.n_samples || '—']]) +
      card('Прогноз тюленя', 'Градиентный бустинг: день года, широта, температура воздуха, ветер, детекции тюленя и угроз за 7 дней. Погода — Open-Meteo.',
        [['ROC AUC', num(f.roc_auc, 3)], ['Brier', num(f.brier, 3)], ['Обучающих примеров', f.samples || '—'], ['Данные', f.data === 'synthetic' ? 'синтетика' : f.data || '—']]) +
      card('Слияние источников', 'Сигналы ближе ' + C.INCIDENT_WINDOW_MIN + ' мин и ' + C.INCIDENT_RADIUS_KM + ' км — один инцидент. Слышали ≥2 буя или совпали буй и спутник — «подтверждено». Так меньше ложных тревог.',
        [['Отмечено ложных', marks.filter((m) => m === 'false').length], ['Подтверждено', marks.filter((m) => m === 'confirmed').length], ['Патрулей', marks.filter((m) => m === 'patrol').length]]) +
      '</div>' +
      '<h2>Ограничения (честно)</h2><ul class="limits">' +
      '<li>Модели обучены на синтетических данных; размеченных записей по Каспию в открытом доступе нет. Следующий шаг — записи с буя и отметки инспекторов.</li>' +
      '<li>Метрики звука посчитаны на ' + (s.n_samples || 'небольшой') + ' тестовых записях того же генератора — на реальном звуке они будут ниже.</li>' +
      '<li>Снимки анализируются правилом по цвету, не обученной моделью.</li>' +
      '<li>Границы ООПТ на карте схематичные; радиус слышимости буя — допущение (задаётся в buoys.json).</li>' +
      '</ul>';
    heroDots();
  }
  // Поле цветных точек-волн в тёмном блоке; останавливается, когда вкладка скрыта
  function heroDots() {
    const cv = $('#hero-dots');
    if (!cv) return;
    const g = cv.getContext('2d'), dpr = window.devicePixelRatio || 1;
    const t0 = performance.now();
    function frame(now) {
      if (!cv.isConnected || $('#panel-about').hidden) return;
      const w = cv.clientWidth, h = cv.clientHeight;
      if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, h);
      const t = (now - t0) / 1000, step = 14;
      for (let x = 0; x < w; x += step) {
        for (let y = 0; y < h; y += step) {
          const k = x / w;
          const wave = Math.sin(x * 0.012 + t * 0.8) * 0.5 + Math.sin(y * 0.02 - t * 0.6 + k * 4) * 0.5;
          const a = Math.max(0, wave) * (0.25 + 0.75 * k);
          if (a < 0.05) continue;
          const r = Math.round(66 + 120 * k), gr = Math.round(133 - 40 * k + 60 * Math.sin(t + y * 0.01)), b = 244 - Math.round(20 * k);
          g.fillStyle = 'rgba(' + r + ',' + gr + ',' + b + ',' + a.toFixed(2) + ')';
          g.beginPath();
          g.arc(x + Math.sin(t + y * 0.05) * 2, y, 1.1 + a * 1.4, 0, Math.PI * 2);
          g.fill();
        }
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }
  const step = (t, d) => '<div class="pipe-step"><b>' + esc(t) + '</b><span>' + esc(d) + '</span></div>';
  const card = (t, d, rows) => '<div class="about-card"><h3>' + esc(t) + '</h3><p>' + esc(d) + '</p><dl class="kv">' +
    rows.map(([k, v]) => '<dt>' + esc(k) + '</dt><dd>' + esc(v) + '</dd>').join('') + '</dl></div>';

  // ---------- гибель тюленей: находки туш и расследование ----------
  // Туши находят через дни после гибели — причину уже не установить. Бэкенд восстанавливает обратный дрейф
  // до вероятного места гибели и сопоставляет его с данными буёв, ветром и землетрясениями (GET …/investigation).
  const COND = { fresh: 'свежая (0,5–2 сут)', moderate: 'средней давности (2–5 сут)', decomposed: 'разложившаяся (5–12 сут)' };
  let invId = null, inv = null, pickMode = false;
  state.carcasses = [];

  const carcassIcon = (c, on) => L.divIcon({ className: '', iconSize: [0, 0],
    html: '<div class="carc-mk' + (on ? ' on' : '') + '"><b>' + c.count + '</b></div>' });
  function renderCarcasses() {
    carcassLayer.clearLayers();
    state.carcasses.forEach((c) => {
      L.marker([c.geo.lat, c.geo.lon], { icon: carcassIcon(c, c.carcass_id === invId), zIndexOffset: 800 })
        .bindTooltip('Найдено погибших тюленей: ' + c.count + ' · ' + fmtTime(c.found_at) + (c.note ? ' · ' + esc(c.note) : ''))
        .on('click', () => { showTab('mortality'); openInvestigation(c.carcass_id); })
        .addTo(carcassLayer);
    });
  }

  function renderMortality() {
    const box = $('#mortality');
    const total = state.carcasses.reduce((a, c) => a + c.count, 0);
    box.innerHTML =
      '<div class="mort-intro"><h2>Гибель тюленей</h2><p>Каждую осень на побережье Дагестана находят сотни погибших каспийских тюленей, ' +
      'а причину установить не удаётся: туши выносит на берег через дни, ткани разложены. Версии спорят — выброс газа со дна, ' +
      'сети браконьеров, загрязнение. Система восстанавливает, <b>где и когда</b> животное могло погибнуть, и поднимает всё, что в это время фиксировали буи.</p></div>' +
      '<div class="mort-grid"><div class="mort-list">' +
      '<div class="mort-head"><b>Находки · ' + total + ' туш</b><button class="btn primary sm" id="carc-add">+ Находка</button></div>' +
      '<form class="carc-form" id="carc-form" hidden>' +
      '<div class="row"><label>Широта<input name="lat" type="number" step="0.0001" value="43.30" required></label>' +
      '<label>Долгота<input name="lon" type="number" step="0.0001" value="47.57" required></label></div>' +
      '<button type="button" class="btn sm" id="carc-pick">Указать на карте</button>' +
      '<div class="row"><label>Сколько туш<input name="count" type="number" min="1" value="1" required></label>' +
      '<label>Состояние<select name="condition">' + Object.entries(COND).map(([k, v]) => '<option value="' + k + '">' + v + '</option>').join('') + '</select></label></div>' +
      '<label>Заметка<input name="note" maxlength="200" placeholder="например: рядом обрывки сети"></label>' +
      '<div class="row"><button class="btn primary" type="submit">Сохранить и расследовать</button><button class="btn" type="button" id="carc-cancel">Отмена</button></div></form>' +
      (state.carcasses.length ? state.carcasses.map((c) =>
        '<button class="carc' + (c.carcass_id === invId ? ' on' : '') + '" data-carc="' + esc(c.carcass_id) + '"><b>' + c.count + ' туш</b>' +
        '<span>' + fmtTime(c.found_at) + ' · ' + esc(COND[c.condition] || c.condition) + '</span>' + (c.note ? '<span class="sub">' + esc(c.note) + '</span>' : '') + '</button>').join('')
        : '<p class="sub">Находок пока нет. Эмулятор: /scenario/mortality, или добавьте вручную.</p>') +
      '</div><div class="mort-report" id="mort-report">' + (inv ? reportHtml(inv) : '<p class="sub">Выберите находку слева — система восстановит, где животные могли погибнуть.</p>') + '</div></div>';
  }

  async function openInvestigation(id) {
    invId = id;
    renderCarcasses();
    renderMortality();
    $('#mort-report').innerHTML = '<p class="sub">Восстанавливаем дрейф и поднимаем данные буёв…</p>';
    try {
      inv = await Api.getInvestigation(id);
      if (invId !== id) return;
      $('#mort-report').innerHTML = reportHtml(inv);
      drawDrift(inv);
    } catch (e) {
      $('#mort-report').innerHTML = '<p class="err-text">Не удалось: ' + esc(e.message) + '</p>';
    }
  }

  const HYP_COLOR = { nets: '#d93025', gas: '#7c4dff', pollution: '#b06000' };
  function reportHtml(d) {
    const e = d.evidence, w = d.death_window;
    const fmtD = (iso) => new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    return '<div class="verdict-card"><span class="sub">Вывод системы</span><h3>' + esc(d.verdict) + '</h3>' +
      '<p>' + d.carcass.count + ' туш · ' + esc(d.condition_ru) + ' · гибель вероятно между <b>' + fmtD(w.from) + '</b> и <b>' + fmtD(w.to) + '</b></p>' +
      '<button class="btn sm" id="drift-show">Показать дрейф на карте</button></div>' +
      '<h3>Версии</h3>' + d.hypotheses.map((h) =>
        '<div class="hyp"><div class="hyp-head"><b>' + esc(h.title) + '</b><span>' + Math.round(h.score * 100) + '%</span></div>' +
        '<div class="hyp-bar"><i style="width:' + Math.max(2, h.score * 100) + '%;background:' + (HYP_COLOR[h.id] || '#5f6368') + '"></i></div>' +
        (h.evidence.length ? '<ul>' + h.evidence.map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul>' : '<div class="sub">улик нет</div>') +
        '<div class="sub">Проверить: ' + esc(h.check) + '</div></div>').join('') +
      '<h3>Данные, на которые опирается вывод</h3>' +
      '<dl class="kv">' +
      '<dt>Буи рядом с коридором</dt><dd>' + (d.buoys_in_range.length ? d.buoys_in_range.map((b) => esc(b.name) + ' (' + b.distance_km + ' км)').join(', ') : 'нет') + '</dd>' +
      '<dt>Покрытие данными</dt><dd>' + Math.round(d.coverage * 100) + '% часов окна гибели</dd>' +
      '<dt>Метан, максимум</dt><dd>' + (e.methane_max ? e.methane_max.value + ' нмоль/л · ' + esc(buoyName(e.methane_max.buoy_id)) : '—') + '</dd>' +
      '<dt>Штиль</dt><dd>' + e.calm_hours + ' ч подряд</dd>' +
      '<dt>Землетрясения (' + esc(e.earthquake_source) + ')</dt><dd>' + (e.earthquakes.length ? e.earthquakes.map((q) => 'M' + q.mag + ' · ' + q.distance_km + ' км · ' + fmtD(q.time)).join('<br>') : 'нет M≥2.5 в 200 км') + '</dd>' +
      '<dt>Ветер</dt><dd>' + (d.drift.wind_source === 'open-meteo' ? 'Open-Meteo, реальные данные' : 'климатическая норма') + '</dd>' +
      '<dt>Модель дрейфа</dt><dd>' + esc(d.drift.assumptions) + '</dd></dl>' +
      (e.events.length ? '<h3>События буёв в окне</h3><div class="ev-chips">' + e.events.map((x) =>
        '<button class="badge ' + esc(x.label) + '" data-ev-kind="' + x.kind + '" data-ev-id="' + esc(x.event_id) + '">' +
        esc(x.kind === 'sound' ? soundName(x.label) : waterName(x.label)) + ' · ' + esc(buoyName(x.buoy_id)) + ' · ' + fmtD(x.timestamp) + '</button>').join('') + '</div>' : '') +
      '<p class="sub mort-note">' + esc(d.note) + '</p>';
  }

  function drawDrift(d) {
    driftLayer.clearLayers();
    const pts = d.drift.points.map((p) => [p.lat, p.lon]);
    L.polyline(pts, { className: 'drift-line', interactive: false }).addTo(driftLayer);
    d.drift.zone.forEach((p, i) => {
      if (i % 8) return; // каждый восьмой час — иначе круги сливаются
      L.circle([p.lat, p.lon], { radius: p.spread_km * 1000, className: 'drift-zone', interactive: false }).addTo(driftLayer);
    });
    const mid = d.drift.zone[Math.floor(d.drift.zone.length / 2)];
    if (mid) {
      L.marker([mid.lat, mid.lon], { icon: L.divIcon({ className: '', iconSize: [0, 0], html: '<div class="drift-label">вероятное место гибели</div>' }), interactive: false }).addTo(driftLayer);
    }
    d.evidence.earthquakes.forEach((q) => {
      L.circleMarker([q.geo.lat, q.geo.lon], { radius: 4 + (q.mag || 0) * 2, className: 'quake' })
        .bindTooltip('Землетрясение M' + q.mag + ' · ' + esc(q.region) + ' · ' + fmtTime(q.time)).addTo(driftLayer);
    });
  }

  $('#mortality').addEventListener('click', (e) => {
    const c = e.target.closest('[data-carc]');
    if (c) openInvestigation(c.dataset.carc);
    if (e.target.id === 'carc-add') $('#carc-form').hidden = false;
    if (e.target.id === 'carc-cancel') $('#carc-form').hidden = true;
    if (e.target.id === 'carc-pick') { pickMode = true; showTab('map'); toast('info', 'Укажите место находки', 'Кликните по карте у берега'); }
    if (e.target.id === 'drift-show' && inv) { showTab('map'); drawDrift(inv); map.fitBounds(L.latLngBounds(inv.drift.points.map((p) => [p.lat, p.lon])).pad(0.4)); }
    const ev = e.target.closest('[data-ev-id]');
    if (ev) openEventById(ev.dataset.evKind, ev.dataset.evId);
  });
  $('#mortality').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const c = await Api.postCarcass({ geo: { lat: +f.get('lat'), lon: +f.get('lon') }, count: +f.get('count'),
        condition: f.get('condition'), note: f.get('note') || null });
      state.sig.carcasses = null;
      await refresh();
      openInvestigation(c.carcass_id);
    } catch (err) {
      toast('info', 'Находка не сохранилась', err.message);
    }
  });
  map.on('click', (e) => {
    if (!pickMode) return;
    pickMode = false;
    showTab('mortality');
    $('#carc-form').hidden = false;
    $('#carc-form').lat.value = e.latlng.lat.toFixed(4);
    $('#carc-form').lon.value = e.latlng.lng.toFixed(4);
  });

  // ---------- загрузка данных ----------
  function changed(key, data) {
    const sig = JSON.stringify(data);
    if (state.sig[key] === sig) return false;
    state.sig[key] = sig;
    state[key] = data;
    return true;
  }

  // Всё, что зависит от событий, решений и прогноза
  function recompute() {
    buildIncidents();
    computeStatus();
    renderZones();
    paintBuoyMarkers();
    renderAlerts();
    renderIncidents();
    paintBuoyList();
    renderKpis();
    notifyNew();
  }

  let busy = false;
  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      const [b, s, w, st, seals] = await Promise.all([
        Api.getBuoys(),
        Api.getEvents({ limit: C.EVENTS_LIMIT }),
        Api.getWaterEvents({ limit: C.EVENTS_LIMIT }),
        Api.getBuoyStatus().catch(() => ({ buoys: [] })),
        Api.getSeals({ hours: C.SEAL_WINDOW_H }).catch(() => null),
      ]);
      const carc = await Api.getCarcasses().catch(() => null);
      const sal = await Promise.all((b.buoys || []).map((x) => Api.getSalinity({ buoy_id: x.buoy_id, limit: 48 }).catch(() => ({ measurements: [] }))));
      const salinity = {};
      (b.buoys || []).forEach((x, i) => (salinity[x.buoy_id] = sortAsc(sal[i].measurements || [])));
      state.online = true;
      state.lastOk = Date.now();
      const cb = changed('buoys', b.buoys || []);
      const cs = changed('sound', s.events || []);
      const cw = changed('water', w.events || []);
      changed('salinity', salinity);
      state.status = {};
      (st.buoys || []).forEach((x) => (state.status[x.buoy_id] = x));
      if (changed('seals', seals)) renderSeals();
      if (changed('carcasses', (carc && carc.carcasses) || [])) { renderCarcasses(); if (!$('#panel-mortality').hidden) renderMortality(); }
      if (cb) { if (!rec) renderBuoyList(); renderBuoyMarkers(); }
      if (cb || cw) renderWaterMarkers();
      if (cb) $('#list-all').replaceChildren();
      if (cb || cs || cw) renderList($('#list-all'), allItems(), itemKey, eventNode);
      recompute(); // статусы зависят и от времени — пересчитываем каждый раз
      state.ready = true;
    } catch (e) {
      state.online = false;
    } finally {
      busy = false;
      renderStatus();
    }
  }

  function loop() {
    refresh().finally(() => setTimeout(loop, Date.now() < fastUntil ? 1500 : C.POLL_INTERVAL_MS));
  }
  loop();
  loadForecastData();
})();
