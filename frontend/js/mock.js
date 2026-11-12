// ЗАГЛУШКИ: данные и ответы в формате контракта v2.
// Включаются флагом CONFIG.USE_MOCK в js/config.js.
// Меняешь данные здесь — меняется всё, что видит фронт.
(function () {
  const ago = (min) => new Date(Date.now() - min * 60000).toISOString().replace(/\.\d+Z$/, 'Z');

  // ---------- Buoy ----------
  const BUOYS = [
    { buoy_id: 'b-001', name: 'Буй №1', geo: { lat: 44.45, lon: 47.2 } },
    { buoy_id: 'b-002', name: 'Буй №2', geo: { lat: 44.1, lon: 47.35 } },
    { buoy_id: 'b-003', name: 'Буй №3', geo: { lat: 43.75, lon: 47.5 } },
    { buoy_id: 'b-004', name: 'Буй №4', geo: { lat: 43.35, lon: 47.6 } },
    { buoy_id: 'b-005', name: 'Буй №5', geo: { lat: 43.0, lon: 47.65 } },
  ];

  // ---------- SoundEvent ----------
  const cls = (label, confidence, second) => ({
    label,
    confidence,
    candidates: [
      { label, confidence },
      { label: second, confidence: +(1 - confidence).toFixed(2) },
    ],
  });
  const snd = (id, buoy_id, min, label, conf, second) => ({
    event_id: id,
    buoy_id,
    source: 'buoy',
    timestamp: ago(min),
    classification: cls(label, conf, second),
    audio_url: '/audio/' + id + '.wav',
  });
  const SOUND_EVENTS = [
    snd('evt-0001', 'b-001', 4, 'gunshot', 0.91, 'anomaly'),
    snd('evt-0002', 'b-003', 17, 'boat', 0.88, 'anomaly'),
    snd('evt-0003', 'b-002', 42, 'seal', 0.94, 'anomaly'),
    snd('evt-0004', 'b-005', 75, 'anomaly', 0.61, 'boat'),
    snd('evt-0005', 'b-004', 130, 'seal', 0.83, 'boat'),
    snd('evt-0006', 'b-001', 260, 'boat', 0.79, 'gunshot'),
    snd('evt-0007', 'b-003', 410, 'seal', 0.9, 'anomaly'),
    snd('evt-0008', 'b-002', 700, 'gunshot', 0.72, 'boat'),
  ];

  // ---------- SalinityMeasurement (по возрастанию времени, шаг 30 мин) ----------
  const SALINITY = [];
  BUOYS.forEach((b, bi) => {
    for (let i = 47; i >= 0; i--) {
      let s = 12.5 + Math.sin((i + bi * 5) / 6) * 0.4;
      if (b.buoy_id === 'b-003' && i < 4) s -= 2.6; // сброс у буя №3
      SALINITY.push({
        measurement_id: 'sal-' + b.buoy_id.slice(2) + '-' + String(47 - i).padStart(2, '0'),
        buoy_id: b.buoy_id,
        timestamp: ago(i * 30),
        salinity: +s.toFixed(2),
      });
    }
  });

  // ---------- WaterEvent (неприменимые поля — null) ----------
  const WATER_EVENTS = [
    {
      event_id: 'wtr-0001',
      type: 'illegal_discharge',
      geo: { lat: 43.75, lon: 47.5 },
      timestamp: ago(25),
      details: { salinity: 9.9, salinity_delta: -2.6, level_delta_m: null, confidence: null },
      source: { buoy_id: 'b-003', image_id: null },
    },
    {
      event_id: 'wtr-0002',
      type: 'illegal_discharge',
      geo: { lat: 43.2, lon: 47.75 },
      timestamp: ago(190),
      details: { salinity: null, salinity_delta: null, level_delta_m: null, confidence: 0.91 },
      source: { buoy_id: null, image_id: 'img-0001' },
    },
    {
      event_id: 'wtr-0003',
      type: 'level_anomaly',
      geo: { lat: 44.3, lon: 47.45 },
      timestamp: ago(520),
      details: { salinity: null, salinity_delta: null, level_delta_m: 0.4, confidence: 0.84 },
      source: { buoy_id: null, image_id: 'img-0002' },
    },
  ];

  // ---------- Статика-заглушка: короткий сигнал и картинка ----------
  let beepUrl = null;
  function beep() {
    if (beepUrl) return beepUrl;
    const rate = 8000, n = rate / 2;
    const dv = new DataView(new ArrayBuffer(44 + n * 2));
    const str = (o, s) => [...s].forEach((c, i) => dv.setUint8(o + i, c.charCodeAt(0)));
    str(0, 'RIFF'); dv.setUint32(4, 36 + n * 2, true); str(8, 'WAVEfmt ');
    dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
    dv.setUint32(24, rate, true); dv.setUint32(28, rate * 2, true);
    dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
    str(36, 'data'); dv.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) dv.setInt16(44 + i * 2, Math.sin((i / rate) * 2 * Math.PI * 440) * 9000, true);
    beepUrl = URL.createObjectURL(new Blob([dv], { type: 'audio/wav' }));
    return beepUrl;
  }
  const picture = (id) =>
    'data:image/svg+xml;utf8,' +
    encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200">' +
        '<rect width="320" height="200" fill="#1d4e63"/>' +
        '<text x="160" y="96" fill="#fff" font-family="sans-serif" font-size="16" text-anchor="middle">Снимок-заглушка</text>' +
        '<text x="160" y="120" fill="#cfe3ea" font-family="sans-serif" font-size="13" text-anchor="middle">' + id + '</text></svg>'
    );

  // ---------- Ответы API ----------
  const ok = (data) => new Promise((res) => setTimeout(() => res(JSON.parse(JSON.stringify(data))), 150));
  const notFound = () => Promise.reject(new Error('not_found'));
  const page = (list, p, defLimit) => {
    const offset = +p.offset || 0;
    return list.slice(offset, offset + (+p.limit || defLimit));
  };
  const inRange = (p) => (x) => (!p.from || x.timestamp >= p.from) && (!p.to || x.timestamp <= p.to);
  let userSeq = 0;

  window.MockApi = {
    getBuoys: () => ok({ buoys: BUOYS }),
    getBuoy: (id) => {
      const b = BUOYS.find((x) => x.buoy_id === id);
      return b ? ok(b) : notFound();
    },
    getEvents: (p = {}) => {
      const list = SOUND_EVENTS.filter(inRange(p)).filter(
        (e) => (!p.buoy_id || e.buoy_id === p.buoy_id) && (!p.label || e.classification.label === p.label)
      );
      return ok({ total: list.length, events: page(list, p, 50) });
    },
    getSalinity: (p = {}) => {
      const list = SALINITY.filter(inRange(p)).filter((m) => !p.buoy_id || m.buoy_id === p.buoy_id);
      return ok({ measurements: list.slice(0, +p.limit || 100) });
    },
    getWaterEvents: (p = {}) => {
      const list = WATER_EVENTS.filter(inRange(p)).filter((e) => !p.type || e.type === p.type);
      return ok({ total: list.length, events: page(list, p, 50) });
    },
    classifyUpload: (file, buoyId) => {
      const id = 'evt-user-' + ++userSeq;
      const classification = cls('boat', 0.87, 'anomaly'); // ответ-заглушка
      const res = { event_id: id, buoy_id: buoyId || null, classification, audio_url: '/audio/' + id + '.wav' };
      SOUND_EVENTS.unshift({ ...res, source: 'user', timestamp: ago(0) });
      return ok(res);
    },
    getForecast: (p = {}) => {
      const days = +p.days || 7;
      const level = (s) => (s >= 0.5 ? 'high' : s >= 0.25 ? 'medium' : 'low');
      const forecasts = BUOYS.filter((b) => !p.buoy_id || b.buoy_id === p.buoy_id).map((b, bi) => {
        const threats = [0, 4, 1, 0, 3][bi % 5];
        return {
          buoy_id: b.buoy_id,
          seal_events_7d: [0, 2, 1, 0, 3][bi % 5],
          threat_events_7d: threats,
          days: Array.from({ length: days }, (_, i) => {
            const prob = +(0.35 + 0.08 * bi + 0.05 * Math.sin(i + bi)).toFixed(2);
            const score = +((prob * (1 + Math.min(threats, 5) / 5)) / 2).toFixed(2);
            return {
              date: new Date(Date.now() + i * 86400000).toISOString().slice(0, 10),
              seal_probability: prob, risk_score: score, risk_level: level(score),
              air_temp_c: 8 + i, wind_ms: 4 + (i % 3),
            };
          }),
        };
      });
      return ok({ generated_at: ago(0), weather_source: 'climatology', training_data: 'synthetic', forecasts });
    },
    audioUrl: () => beep(),
    imageUrl: (imageId) => picture(imageId),
  };
})();
