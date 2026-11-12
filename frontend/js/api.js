// Клиент API по контракту v2. Если CONFIG.USE_MOCK = true — вместо него работает js/mock.js.
(function () {
  const C = window.CONFIG;

  async function request(path, options) {
    const res = await fetch(C.API_BASE + path, options);
    let body = null;
    try { body = await res.json(); } catch (e) { /* пустое тело */ }
    if (!res.ok) throw new Error((body && (body.message || body.error)) || 'HTTP ' + res.status);
    return body;
  }

  function query(params) {
    const q = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') q.set(k, v);
    });
    const s = q.toString();
    return s ? '?' + s : '';
  }

  const RealApi = {
    // GET /api/v1/buoys → { buoys: [Buoy] }
    getBuoys: () => request('/api/v1/buoys'),
    // GET /api/v1/buoys/{buoy_id} → Buoy
    getBuoy: (id) => request('/api/v1/buoys/' + encodeURIComponent(id)),
    // GET /api/v1/events → { total, events: [SoundEvent] }; query: buoy_id, label, from, to, limit, offset
    getEvents: (params) => request('/api/v1/events' + query(params)),
    // GET /api/v1/salinity → { measurements: [SalinityMeasurement] }; query: buoy_id, from, to, limit
    getSalinity: (params) => request('/api/v1/salinity' + query(params)),
    // GET /api/v1/water-events → { total, events: [WaterEvent] }; query: type, from, to, limit, offset
    getWaterEvents: (params) => request('/api/v1/water-events' + query(params)),
    // GET /api/v1/forecast → { generated_at, weather_source, training_data, forecasts: [BuoyForecast] }; query: buoy_id, days
    getForecast: (params) => request('/api/v1/forecast' + query(params)),
    // GET /api/v1/buoy-status → { buoys: [{ status, reason, listen_radius_km, salinity, water_temp_c, level_m, online, … }] }
    getBuoyStatus: () => request('/api/v1/buoy-status'),
    // GET /api/v1/events/{id}/neighbors или /api/v1/water-events/{id}/neighbors → { verdict, message, neighbors }
    getNeighbors: (kind, id) => request((kind === 'water' ? '/api/v1/water-events/' : '/api/v1/events/') + encodeURIComponent(id) + '/neighbors'),
    // GET /api/v1/events/{id}, /api/v1/water-events/{id}
    getEvent: (id) => request('/api/v1/events/' + encodeURIComponent(id)),
    getWaterEvent: (id) => request('/api/v1/water-events/' + encodeURIComponent(id)),
    // GET …/{id}/context?minutes= → { total, counts, buoys, items: [{ kind, event_id, label, delta_sec, distance_km, … }] }
    getContext: (kind, id, minutes) =>
      request((kind === 'water' ? '/api/v1/water-events/' : '/api/v1/events/') + encodeURIComponent(id) + '/context' + query({ minutes })),
    // GET /api/v1/seals → { total_detections, estimate_min, buoys, groups: [{ center, drift, track, … }] }
    getSeals: (params) => request('/api/v1/seals' + query(params)),
    // GET /api/v1/model-info → { sound, forecast, library_size } (сверх контракта)
    getModelInfo: () => request('/api/v1/model-info'),
    // Эмулятор буя: POST {EMULATOR_URL}/scenario/poacher → он сам шлёт события на бэкенд, как настоящие буи
    runScenario: () =>
      fetch(C.EMULATOR_URL + '/scenario/poacher', { method: 'POST' })
        .catch(() => { throw new Error('эмулятор буя не отвечает (' + C.EMULATOR_URL + '): cd mock-backend && bash docker.sh'); })
        .then((r) => r.json()),
    // POST /api/v1/classify/upload — multipart: file, buoy_id?
    classifyUpload: (file, buoyId) => {
      const form = new FormData();
      form.append('file', file, file.name);
      if (buoyId) form.append('buoy_id', buoyId);
      return request('/api/v1/classify/upload', { method: 'POST', body: form });
    },
    // GET /audio/{event_id}.wav — берём готовый audio_url из объекта
    audioUrl: (obj) => C.API_BASE + obj.audio_url,
    // GET /images/{image_id}.jpg
    imageUrl: (imageId) => C.API_BASE + '/images/' + encodeURIComponent(imageId) + '.jpg',
  };

  window.Api = C.USE_MOCK ? window.MockApi : RealApi;
})();
