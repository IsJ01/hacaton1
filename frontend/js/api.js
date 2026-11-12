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
