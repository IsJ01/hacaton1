// Спектрограмма звука события: считается в браузере из того же wav, что слушает инспектор.
// Spectrogram.draw(canvas, url) → Promise; по горизонтали время, по вертикали частота (0 – MAX_HZ).
(function () {
  const MAX_HZ = 5000; // выше под водой почти ничего нет — показываем только нижнюю полосу

  // Итеративное БПФ по основанию 2, на месте
  function fft(re, im) {
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const ang = (-2 * Math.PI) / len, wr = Math.cos(ang), wi = Math.sin(ang);
      for (let i = 0; i < n; i += len) {
        let cr = 1, ci = 0;
        for (let k = 0; k < len / 2; k++) {
          const a = i + k, b = a + len / 2;
          const tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
          re[b] = re[a] - tr; im[b] = im[a] - ti;
          re[a] += tr; im[a] += ti;
          [cr, ci] = [cr * wr - ci * wi, cr * wi + ci * wr];
        }
      }
    }
  }

  // Тёмно-синий → бирюзовый → жёлтый
  const STOPS = [[13, 27, 42], [11, 110, 138], [80, 180, 140], [250, 220, 90]];
  function color(t) {
    const x = Math.max(0, Math.min(1, t)) * (STOPS.length - 1);
    const i = Math.min(STOPS.length - 2, Math.floor(x)), f = x - i;
    return STOPS[i].map((c, k) => Math.round(c + (STOPS[i + 1][k] - c) * f));
  }

  async function draw(canvas, url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    let buf;
    try { buf = await ctx.decodeAudioData(await res.arrayBuffer()); } finally { ctx.close(); }
    const data = buf.getChannelData(0), sr = buf.sampleRate;
    const N = sr > 24000 ? 2048 : 512; // окно БПФ: на высокой частоте дискретизации — шире
    const W = canvas.width, H = canvas.height;
    const bins = Math.min(N / 2, Math.round((MAX_HZ / (sr / 2)) * (N / 2)));
    const hop = Math.max(1, Math.floor((data.length - N) / W));
    const win = Array.from({ length: N }, (_, i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (N - 1)));

    const cols = [];
    let max = -Infinity;
    for (let x = 0; x < W; x++) {
      const re = new Float64Array(N), im = new Float64Array(N), start = x * hop;
      for (let i = 0; i < N; i++) re[i] = (data[start + i] || 0) * win[i];
      fft(re, im);
      const col = new Float32Array(bins); // только полоса 0 – MAX_HZ
      for (let k = 0; k < bins; k++) {
        col[k] = 10 * Math.log10(re[k] * re[k] + im[k] * im[k] + 1e-12);
        if (col[k] > max) max = col[k];
      }
      cols.push(col);
    }

    const g = canvas.getContext('2d'), img = g.createImageData(W, H), range = 70; // показываем 70 дБ от пика
    for (let x = 0; x < W; x++) {
      for (let y = 0; y < H; y++) {
        const k = Math.floor(((H - 1 - y) / H) * bins);
        const [r, gr, b] = color((cols[x][k] - (max - range)) / range);
        const o = (y * W + x) * 4;
        img.data[o] = r; img.data[o + 1] = gr; img.data[o + 2] = b; img.data[o + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
    return { seconds: data.length / sr, maxHz: (bins / (N / 2)) * (sr / 2) };
  }

  window.Spectrogram = { draw };
})();
