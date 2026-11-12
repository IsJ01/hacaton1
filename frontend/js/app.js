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

  const state = { buoys: [], sound: [], water: [], sig: {} };
  const buoyName = (id) => {
    const b = state.buoys.find((x) => x.buoy_id === id);
    return b ? b.name : id || '—';
  };
  const isCritical = (item) =>
    item.kind === 'sound'
      ? C.CRITICAL_SOUND_LABELS.includes(item.classification && item.classification.label)
      : C.CRITICAL_WATER_TYPES.includes(item.type);

  // ---------- вкладки ----------
  let map;
  function showTab(name) {
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
    ['map', 'all', 'crit', 'forecast'].forEach((n) => ($('#panel-' + n).hidden = n !== name));
    if (name === 'map') map.invalidateSize();
    if (name === 'forecast') loadForecast();
  }
  document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => showTab(t.dataset.tab)));

  // ---------- карта ----------
  map = L.map('map', { attributionControl: false }).setView(C.MAP_CENTER, C.MAP_ZOOM);
  L.tileLayer(C.TILE_URL, { attribution: C.TILE_ATTRIBUTION, maxZoom: 18 }).addTo(map);
  const buoyLayer = L.layerGroup().addTo(map);
  const waterLayer = L.layerGroup().addTo(map);
  const buoyMarkers = {};
  const icon = (cls, size) => L.divIcon({ className: '', html: '<div class="mk ' + cls + '" style="width:' + size + 'px;height:' + size + 'px"></div>', iconSize: [size, size], iconAnchor: [size / 2, size / 2] });

  function renderBuoyMarkers() {
    buoyLayer.clearLayers();
    Object.keys(buoyMarkers).forEach((k) => delete buoyMarkers[k]);
    state.buoys.forEach((b) => {
      const m = L.marker([b.geo.lat, b.geo.lon], { icon: icon('buoy-mk', 18) }).addTo(buoyLayer);
      m.bindTooltip(esc(b.name));
      m.bindPopup('Загрузка…', { minWidth: 300, maxWidth: 300 });
      m.on('popupopen', () => loadBuoyPopup(m, b.buoy_id));
      buoyMarkers[b.buoy_id] = m;
    });
  }

  async function loadBuoyPopup(marker, id) {
    try {
      const [b, sal] = await Promise.all([Api.getBuoy(id), Api.getSalinity({ buoy_id: id, limit: C.SALINITY_LIMIT })]);
      marker.setPopupContent(
        '<div class="popup"><h3>' + esc(b.name) + '</h3><div class="sub">' + esc(b.buoy_id) + ' · ' + coords(b.geo) + '</div>' +
          salinityChart(sal.measurements || []) + '</div>'
      );
    } catch (e) {
      marker.setPopupContent('<div class="popup err-text">Ошибка: ' + esc(e.message) + '</div>');
    }
  }

  // График солёности: простая линия на SVG
  function salinityChart(ms) {
    if (!ms.length) return '<div class="sub">Нет измерений солёности</div>';
    const W = 300, H = 130, L0 = 34, R = 8, T = 8, B = 18;
    const xs = ms.map((m) => new Date(m.timestamp).getTime());
    const ys = ms.map((m) => m.salinity);
    let y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    if (y1 - y0 < 0.5) { y0 -= 0.5; y1 += 0.5; }
    const x0 = xs[0], x1 = xs[xs.length - 1];
    const px = (x) => (x1 === x0 ? (L0 + W - R) / 2 : L0 + ((x - x0) / (x1 - x0)) * (W - L0 - R));
    const py = (y) => T + (1 - (y - y0) / (y1 - y0)) * (H - T - B);
    const pts = ms.map((m, i) => px(xs[i]).toFixed(1) + ',' + py(ys[i]).toFixed(1)).join(' ');
    const last = ms[ms.length - 1];
    return (
      '<div class="sub">Солёность: <b>' + last.salinity.toFixed(2) + ' ‰</b> · ' + fmtTime(last.timestamp) + '</div>' +
      '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '">' +
      '<line x1="' + L0 + '" y1="' + T + '" x2="' + L0 + '" y2="' + (H - B) + '" stroke="#d9e1e6"/>' +
      '<line x1="' + L0 + '" y1="' + (H - B) + '" x2="' + (W - R) + '" y2="' + (H - B) + '" stroke="#d9e1e6"/>' +
      '<text x="' + (L0 - 4) + '" y="' + (T + 8) + '" text-anchor="end">' + y1.toFixed(1) + '</text>' +
      '<text x="' + (L0 - 4) + '" y="' + (H - B) + '" text-anchor="end">' + y0.toFixed(1) + '</text>' +
      '<text x="' + L0 + '" y="' + (H - 4) + '">' + fmtHM(ms[0].timestamp) + '</text>' +
      '<text x="' + (W - R) + '" y="' + (H - 4) + '" text-anchor="end">' + fmtHM(last.timestamp) + '</text>' +
      '<polyline points="' + pts + '" fill="none" stroke="#0b6e8a" stroke-width="2" stroke-linejoin="round"/>' +
      '<circle cx="' + px(x1).toFixed(1) + '" cy="' + py(last.salinity).toFixed(1) + '" r="3" fill="#0b6e8a"/>' +
      '</svg>'
    );
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

  // ---------- ленты событий ----------
  function eventNode(item) {
    const el = document.createElement('article');
    el.dataset.key = item.kind + ':' + item.event_id;
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
    return el;
  }

  // Обновляем список по ключам, не трогая уже показанные карточки (плеер не сбрасывается)
  function renderList(container, items) {
    const old = new Map(Array.from(container.children).map((n) => [n.dataset.key, n]));
    let ref = container.firstElementChild;
    items.forEach((item) => {
      const key = item.kind + ':' + item.event_id;
      if (ref && ref.dataset.key === key) { ref = ref.nextElementSibling; return; }
      container.insertBefore(old.get(key) || eventNode(item), ref);
    });
    while (ref) { const next = ref.nextElementSibling; ref.remove(); ref = next; }
  }

  function renderEvents() {
    const all = state.sound.map((e) => Object.assign({ kind: 'sound' }, e))
      .concat(state.water.map((e) => Object.assign({ kind: 'water' }, e)))
      .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    renderList($('#list-all'), all);
    renderList($('#list-crit'), all.filter(isCritical));
  }

  // ---------- правая панель: буи + запись звука ----------
  function renderBuoyList() {
    $('#buoys').innerHTML = state.buoys.map((b) =>
      '<li class="buoy" data-id="' + esc(b.buoy_id) + '">' +
        '<button class="buoy-main" title="Показать на карте"><b>' + esc(b.name) + '</b><span>' + esc(b.buoy_id) + ' · ' + coords(b.geo) + '</span></button>' +
        '<div class="buoy-actions">' +
          '<button class="btn rec">● Записать звук</button>' +
          '<label class="btn">Файл<input type="file" accept="audio/*" hidden></label>' +
        '</div><div class="buoy-result"></div></li>'
    ).join('');
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

  // ---------- прогноз присутствия тюленя ----------
  async function loadForecast() {
    const box = $('#forecast');
    if (!box.innerHTML) box.innerHTML = '<p class="sub">Загрузка прогноза…</p>';
    try {
      renderForecast(await Api.getForecast({ days: C.FORECAST_DAYS }));
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
      '. Он растёт с вероятностью тюленя и с числом лодок и выстрелов у буя.</p>' +
      '<p class="sub">Погода: ' + weather + '. ' +
      (data.training_data === 'synthetic' ? 'Модель обучена на синтетических данных. ' : '') +
      'Обновлено: ' + fmtTime(data.generated_at) + '</p>';
  }
  setInterval(() => { if (!$('#panel-forecast').hidden) loadForecast(); }, C.FORECAST_REFRESH_MS);

  // ---------- загрузка данных ----------
  function setStatus(ok) {
    const el = $('#status');
    el.className = 'status ' + (ok ? 'ok' : 'err');
    el.textContent = C.USE_MOCK ? 'заглушки (USE_MOCK)' : ok ? 'API: ' + C.API_BASE : 'нет связи: ' + C.API_BASE;
  }

  function changed(key, data) {
    const sig = JSON.stringify(data);
    if (state.sig[key] === sig) return false;
    state.sig[key] = sig;
    state[key] = data;
    return true;
  }

  let busy = false;
  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      const [b, s, w] = await Promise.all([
        Api.getBuoys(),
        Api.getEvents({ limit: C.EVENTS_LIMIT }),
        Api.getWaterEvents({ limit: C.EVENTS_LIMIT }),
      ]);
      setStatus(true);
      const cb = changed('buoys', b.buoys || []);
      const cs = changed('sound', s.events || []);
      const cw = changed('water', w.events || []);
      if (cb) { if (!rec) renderBuoyList(); renderBuoyMarkers(); }
      if (cb || cw) renderWaterMarkers();
      if (cb) { $('#list-all').replaceChildren(); $('#list-crit').replaceChildren(); }
      if (cb || cs || cw) renderEvents();
    } catch (e) {
      setStatus(false);
    } finally {
      busy = false;
    }
  }

  refresh();
  setInterval(refresh, C.POLL_INTERVAL_MS);
})();
