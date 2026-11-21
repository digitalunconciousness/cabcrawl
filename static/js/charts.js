/* Chart.js (3.9.1, static/js/chart.min.js) on the design system.
 *
 * Load after chart.min.js, at the end of <body>. Colours and the font come from the CSS
 * tokens (static/css/tokens.css), so a chart can't drift from the page around it. Data
 * reaches the browser as a JSON island, <script type="application/json" id="…">, never as
 * template-built JavaScript; ArcadeCharts.read(id) parses one. Simple charts are declared
 * on the canvas (see simple() below); anything richer has its own file, like rails.js.
 */
(function () {
  "use strict";
  if (!window.Chart) return;

  var css = getComputedStyle(document.documentElement);
  function token(name) { return css.getPropertyValue(name).trim(); }

  var theme = {
    text: token("--text"),
    dim: token("--text-dim"),
    surface: token("--surface-raised"),
    border: token("--border-control"),
    grid: token("--chart-grid"),
    accent: token("--accent"),
    ok: token("--ok"),
    warn: token("--warn"),
    fault: token("--fault"),
    info: token("--info"),
    muted: token("--muted")
  };

  // "#5ef2b0" and 0.1 -> "rgba(94, 242, 176, 0.1)", for fills under a line.
  function alpha(hex, a) {
    var h = hex.replace("#", "");
    if (h.length === 3) h = h.replace(/./g, "$&$&");
    var n = parseInt(h, 16);
    return "rgba(" + (n >> 16) + ", " + ((n >> 8) & 255) + ", " + (n & 255) + ", " + a + ")";
  }

  var d = Chart.defaults;
  d.color = theme.dim;
  d.borderColor = theme.grid;
  d.font.family = token("--font-body");
  d.font.size = 13;
  d.maintainAspectRatio = false;
  d.plugins.legend.labels.boxWidth = 12;
  d.plugins.legend.labels.color = theme.text;
  d.plugins.tooltip.backgroundColor = theme.surface;
  d.plugins.tooltip.borderColor = theme.border;
  d.plugins.tooltip.borderWidth = 1;
  d.plugins.tooltip.titleColor = theme.text;
  d.plugins.tooltip.bodyColor = theme.text;
  d.plugins.tooltip.footerColor = theme.dim;
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    d.animation = false;
  }

  function read(id) {
    var el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }

  function money(v) {
    return "$" + Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  // Slices in order: a tone name in the data wins (a status keeps its colour from the chips).
  var SLICES = ["info", "accent", "ok", "warn", "muted", "fault"];

  /* The simple charts, declared in markup:
   *
   *   <canvas data-chart="line|bar|doughnut" data-source="island-id" [data-money]
   *           [data-horizontal] aria-label="…" role="img"></canvas>
   *
   * The island is {"labels": [...], "values": [...], "tones": [...]?}. A series with no values
   * draws nothing; the page says so in words instead.
   */
  function simple(canvas) {
    var d = read(canvas.getAttribute("data-source"));
    if (!d || !d.values || !d.values.length) return;
    var kind = canvas.getAttribute("data-chart");
    var isMoney = canvas.hasAttribute("data-money");
    var fmt = isMoney ? money : function (v) { return Number(v).toLocaleString(); };
    var horizontal = canvas.hasAttribute("data-horizontal");
    var set = { label: canvas.getAttribute("data-label") || "", data: d.values };
    function value(c) {
      if (kind === "doughnut") return c.parsed;
      return horizontal ? c.parsed.x : c.parsed.y;
    }
    var options = { plugins: { legend: { display: false }, tooltip: { callbacks: {
      label: function (c) { return " " + (kind === "doughnut" ? c.label + ": " : "") + fmt(value(c)); }
    } } } };

    if (kind === "line") {
      set.borderColor = theme.info;
      set.backgroundColor = alpha(theme.info, 0.1);
      set.fill = true;
      set.tension = 0;
      set.pointRadius = d.values.length > 40 ? 0 : 3;
      options.scales = { y: { beginAtZero: true, ticks: { callback: fmt } },
                         x: { ticks: { maxRotation: 0, autoSkip: true } } };
    } else if (kind === "bar") {
      set.backgroundColor = alpha(theme.accent, 0.75);
      set.borderColor = theme.accent;
      set.borderWidth = 1;
      set.maxBarThickness = 28;
      if (horizontal) {
        options.indexAxis = "y";
        options.scales = { x: { beginAtZero: true, ticks: { callback: fmt } } };
      } else {
        options.scales = { y: { beginAtZero: true, ticks: { callback: fmt } },
                           x: { ticks: { maxRotation: 45, autoSkip: true } } };
      }
    } else if (kind === "doughnut") {
      var colours = d.labels.map(function (_, i) {
        return theme[(d.tones && d.tones[i]) || SLICES[i % SLICES.length]];
      });
      set.backgroundColor = colours.map(function (c) { return alpha(c, 0.8); });
      set.borderColor = token("--surface");
      set.borderWidth = 2;
      options.plugins.legend = { display: true, position: "bottom" };
      options.cutout = "55%";
    } else {
      return;
    }
    new Chart(canvas, { type: kind, data: { labels: d.labels, datasets: [set] }, options: options });
  }

  window.ArcadeCharts = { theme: theme, alpha: alpha, read: read, money: money };

  // Scripts load at the end of <body>, so the canvases are already there.
  Array.prototype.forEach.call(document.querySelectorAll("canvas[data-chart]"), simple);
})();
