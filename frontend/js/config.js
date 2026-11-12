// Все настройки и переключатели фронта — в одном месте.
window.CONFIG = {
  // ---------- ЗАГЛУШКИ ----------
  // true  — фронт работает без бэкенда, на данных из js/mock.js
  // false — фронт ходит в настоящий API по адресу API_BASE
  USE_MOCK: false,

  // Адрес бэкенда (контракт v2). hostname берётся из адресной строки,
  // чтобы с телефона в той же сети работало без правок.
  API_BASE: 'http://' + (location.hostname || 'localhost') + ':8001',

  // ---------- ОБНОВЛЕНИЕ ----------
  POLL_INTERVAL_MS: 5000, // как часто перезапрашивать данные
  EVENTS_LIMIT: 50,       // limit для /events и /water-events
  SALINITY_LIMIT: 100,    // limit для /salinity

  // ---------- КРИТИЧЕСКИЕ СОБЫТИЯ ----------
  // Что попадает во вкладку «Критические».
  CRITICAL_SOUND_LABELS: ['gunshot', 'boat'],
  CRITICAL_WATER_TYPES: ['illegal_discharge', 'level_anomaly'],

  // ---------- ЗАПИСЬ ЗВУКА ----------
  RECORD_MAX_SEC: 30,    // автостоп записи
  CONVERT_TO_WAV: true,  // перекодировать запись/файл в WAV перед отправкой

  // ---------- КАРТА ----------
  MAP_CENTER: [43.75, 47.6],
  MAP_ZOOM: 8,
  TILE_URL: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  TILE_ATTRIBUTION: '&copy; OpenStreetMap',

  // ---------- ПОДПИСИ ----------
  SOUND_LABELS: { seal: 'Тюлень', boat: 'Лодка', gunshot: 'Выстрел', anomaly: 'Аномалия' },
  WATER_TYPES: { illegal_discharge: 'Незаконный сброс', level_anomaly: 'Аномалия уровня' },
};
