// The dashboard: the six analytics metrics as charts.
//
// Chart.js is not imported: its UMD build is loaded by dashboard.html as a classic
// deferred script, which runs before this module and defines window.Chart. It
// draws on a <canvas> and only sets styles through the CSSOM (element.style),
// which the Content-Security-Policy allows without 'unsafe-inline'.

import { ApiError, api } from "../api.js";
import { addOptions, el } from "../dom.js";
import { PRIORITY, STATUS } from "../labels.js";
import { startPage } from "../layout.js";

const { Chart } = window;

// Kept equal to the Django URLs by apps/web/tests/test_pages.py.
const ORGANIZATIONS = "/api/organizations/";
const METRICS = {
  "sla-compliance": "/api/analytics/sla-compliance/",
  "response-times": "/api/analytics/response-times/",
  "daily-backlog": "/api/analytics/daily-backlog/",
  "open-ticket-aging": "/api/analytics/open-ticket-aging/",
  "category-trends": "/api/analytics/category-trends/",
  "agent-workload": "/api/analytics/agent-workload/",
};
// A snapshot of the current state: it rejects a period with a 400.
const SNAPSHOTS = new Set(["open-ticket-aging"]);
const STAFF_ONLY = new Set(["agent-workload"]);
const OPEN_STATUSES = ["new", "open", "pending"];
const PRESET_MONTHS = { "3m": 3, "6m": 6, "12m": 12 };

const user = await startPage();
const staff = user.role !== "client";
const form = document.getElementById("filters");
const periodSummary = document.getElementById("period-summary");
const periodError = document.getElementById("period-error");

// Colours from the CSS variables of base.css, so the charts and the badges share
// one palette.
const css = getComputedStyle(document.documentElement);
const token = (name) => css.getPropertyValue(`--${name}`).trim();
const SERIES = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => token(`chart-${n}`));
const STATUS_COLOR = Object.fromEntries(OPEN_STATUSES.map((s) => [s, token(`chart-${s}`)]));
// "#1d4ed8" + "59": the same colour, about a third opaque, for filled areas.
const translucent = (color) => `${color}59`;

Chart.defaults.color = token("color-muted");
Chart.defaults.borderColor = token("color-border");
Chart.defaults.font.family = css.fontFamily;
// The height comes from the container (.chart-box), not from the width.
Chart.defaults.maintainAspectRatio = false;

const NUMBER = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
// The API's months and days are calendar dates ("2026-03-01"), not instants: they
// are read and formatted in UTC, so no time zone moves them to the day before.
const MONTH = new Intl.DateTimeFormat("es-ES", {
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});
const DAY = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short", timeZone: "UTC" });
const LONG_DAY = new Intl.DateTimeFormat("es-ES", { dateStyle: "medium", timeZone: "UTC" });
const TIME = new Intl.DateTimeFormat("es-ES", { timeStyle: "short" });
const calendarDate = (iso) => new Date(`${iso}T00:00:00Z`);

function percent(value) {
  return value === null ? "sin datos" : `${NUMBER.format(value)} %`;
}

// Business minutes, as people read them: "45 min", "3,5 h".
function duration(minutes) {
  if (minutes === null) {
    return "sin datos";
  }
  return minutes < 60 ? `${Math.round(minutes)} min` : `${NUMBER.format(minutes / 60)} h`;
}

// One entry per chart section of the page: its elements, its Chart and the rows
// of its last answer (the selects inside a card redraw from them, without asking
// the API again).
const cards = {};
for (const section of document.querySelectorAll("[data-chart]")) {
  cards[section.dataset.chart] = {
    section,
    box: section.querySelector(".chart-box"),
    canvas: section.querySelector("canvas"),
    message: section.querySelector("[data-message]"),
    summary: section.querySelector("[data-summary]"),
    defaultSummary: section.querySelector("[data-summary]")?.textContent ?? "",
    chart: null,
    response: null,
  };
}
const shown = Object.keys(METRICS).filter((name) => staff || !STAFF_ONLY.has(name));

// --- Filters ------------------------------------------------------------------

for (const select of document.querySelectorAll("[data-priority]")) {
  addOptions(select, PRIORITY);
}

if (staff) {
  // Former clients apart: their past metrics remain, but they are not current.
  const organizations = await api(ORGANIZATIONS);
  const former = el("optgroup", { label: "Antiguos clientes" });
  for (const organization of organizations) {
    const option = el("option", { value: String(organization.id), text: organization.name });
    (organization.is_active ? form.elements.organization : former).append(option);
  }
  if (former.children.length) {
    form.elements.organization.append(former);
  }
}

// The filters live in the URL, as in the ticket list: reloading or sharing the
// link keeps them. A value that is not an option is left out by the select.
const fromUrl = new URLSearchParams(location.search);
for (const name of ["period", "start", "end", "organization"]) {
  if (fromUrl.has(name)) {
    form.elements[name].value = fromUrl.get(name);
  }
}
if (!form.elements.period.value) {
  form.elements.period.value = "12m";
}

function isoDate(date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

// The period as the API takes it. The presets count whole months back from the
// browser's today and leave out the end (the API's today, in the provider's time
// zone): the two only differ for a few hours around midnight.
function periodParams() {
  const params = new URLSearchParams();
  const preset = form.elements.period.value;
  const today = new Date();
  if (preset === "custom") {
    for (const name of ["start", "end"]) {
      if (form.elements[name].value) {
        params.set(name, form.elements[name].value);
      }
    }
  } else if (preset === "year") {
    params.set("start", `${today.getFullYear()}-01-01`);
  } else {
    const months = PRESET_MONTHS[preset];
    params.set("start", isoDate(new Date(today.getFullYear(), today.getMonth() - (months - 1), 1)));
  }
  return params;
}

function showCustomDates() {
  const custom = form.elements.period.value === "custom";
  for (const label of form.querySelectorAll("[data-custom]")) {
    label.hidden = !custom;
  }
}

// --- Charts -------------------------------------------------------------------

function showMessage(card, text, { error = false } = {}) {
  if (card.summary) {
    card.summary.textContent = card.defaultSummary;
  }
  card.chart?.destroy();
  card.chart = null;
  card.box.hidden = true;
  card.message.textContent = text;
  card.message.classList.toggle("error", error);
  card.message.hidden = false;
}

function draw(card, config) {
  card.message.hidden = true;
  card.box.hidden = false;
  card.chart?.destroy();
  card.chart = new Chart(card.canvas, config);
}

function monthLabels(rows) {
  return rows.map((row) => MONTH.format(calendarDate(row.month)));
}

function slaCompliance(card) {
  const priority = card.section.querySelector("[data-priority]").value;
  const rows = card.response.results.filter((row) => row.priority === priority);
  if (rows.every((row) => row.first_responses === 0 && row.resolutions === 0)) {
    return showMessage(card, "No hay respuestas ni resoluciones en este periodo.");
  }
  // A month without outcomes has a null rate: a gap in the line, not a 0 %.
  const series = (label, prefix, color) => ({
    label,
    data: rows.map((row) => row[`${prefix}_rate`]),
    // Not a Chart.js option: read back by the tooltip below.
    counts: rows.map((row) => [row[`${prefix}s_met`], row[`${prefix}s`]]),
    borderColor: color,
    backgroundColor: color,
  });
  draw(card, {
    type: "line",
    data: {
      labels: monthLabels(rows),
      datasets: [
        series("Primera respuesta", "first_response", SERIES[0]),
        series("Resolución", "resolution", SERIES[1]),
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: { y: { min: 0, max: 100, ticks: { callback: (value) => `${value} %` } } },
      plugins: {
        tooltip: {
          callbacks: {
            label: (item) => {
              const [met, total] = item.dataset.counts[item.dataIndex];
              return `${item.dataset.label}: ${percent(item.raw)} (${met} de ${total})`;
            },
          },
        },
      },
    },
  });
}

function responseTimes(card) {
  const priority = card.section.querySelector("[data-priority]").value;
  const measure = card.section.querySelector("[data-measure]").value;
  const rows = card.response.results.filter((row) => row.priority === priority);
  if (rows.every((row) => row[`${measure}s`] === 0)) {
    return showMessage(card, "No hay tickets con esta medida en este periodo.");
  }
  const hours = (key) => rows.map((row) => (row[key] === null ? null : row[key] / 60));
  draw(card, {
    type: "bar",
    data: {
      labels: monthLabels(rows),
      datasets: [
        { label: "Mediana", data: hours(`${measure}_median`), backgroundColor: SERIES[0] },
        { label: "Percentil 90", data: hours(`${measure}_p90`), backgroundColor: SERIES[5] },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: { y: { beginAtZero: true, ticks: { callback: (value) => `${value} h` } } },
      plugins: {
        tooltip: {
          callbacks: {
            label: (item) => {
              const minutes = item.raw === null ? null : item.raw * 60;
              return `${item.dataset.label}: ${duration(minutes)}`;
            },
            // Wall-clock time is the secondary figure (business time is what the
            // SLA counts): in the tooltip only.
            footer: ([item]) => {
              const row = rows[item.dataIndex];
              return [
                `Tickets: ${row[`${measure}s`]}`,
                `Mediana en tiempo real: ${duration(row[`${measure}_median_wall`])}`,
              ];
            },
          },
        },
      },
    },
  });
}

function statusDatasets(rows, options = {}) {
  return OPEN_STATUSES.map((status) => ({
    label: STATUS[status],
    data: rows.map((row) => row[status]),
    borderColor: STATUS_COLOR[status],
    backgroundColor: options.fill ? translucent(STATUS_COLOR[status]) : STATUS_COLOR[status],
    ...options,
  }));
}

function dailyBacklog(card) {
  const rows = card.response.results;
  const last = rows.at(-1);
  if (!last) {
    return showMessage(card, "El periodo empieza después de hoy.");
  }
  card.summary.textContent =
    `${LONG_DAY.format(calendarDate(last.day))}: ${last.backlog} abiertos ` +
    `(${last.new} nuevos, ${last.open} abiertos, ${last.pending} en espera).`;
  // Stacked areas: each status filled down to the one below it.
  const datasets = statusDatasets(rows, { fill: true, pointRadius: 0, borderWidth: 1.5 });
  datasets.forEach((dataset, index) => {
    dataset.fill = index === 0 ? "origin" : "-1";
  });
  draw(card, {
    type: "line",
    data: { labels: rows.map((row) => DAY.format(calendarDate(row.day))), datasets },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 12 } },
        y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
      },
      plugins: {
        tooltip: {
          callbacks: {
            footer: ([item]) => {
              const row = rows[item.dataIndex];
              return [
                `Total: ${row.backlog}`,
                `Entradas: ${row.created} · Resueltos: ${row.resolved}`,
              ];
            },
          },
        },
      },
    },
  });
}

// The buckets come with their bounds in days, [min, max): the labels follow them.
function bucketLabel(row) {
  if (row.max_days === null) {
    return `Más de ${row.min_days} días`;
  }
  if (row.min_days === 0) {
    return `Menos de ${row.max_days} ${row.max_days === 1 ? "día" : "días"}`;
  }
  return `De ${row.min_days} a ${row.max_days} días`;
}

function openTicketAging(card) {
  const rows = card.response.results;
  const sum = (key) => rows.reduce((total, row) => total + row[key], 0);
  const at = TIME.format(new Date(card.response.as_of));
  if (sum("tickets") === 0) {
    return showMessage(card, `No hay tickets abiertos (a las ${at}).`);
  }
  card.summary.textContent =
    `A las ${at}: ${sum("tickets")} abiertos, ${sum("resolution_breached")} con la resolución ` +
    `vencida y ${sum("first_response_overdue")} sin primera respuesta en plazo.`;
  draw(card, {
    type: "bar",
    data: { labels: rows.map(bucketLabel), datasets: statusDatasets(rows) },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: { x: { stacked: true }, y: { stacked: true, ticks: { precision: 0 } } },
      plugins: {
        tooltip: {
          callbacks: {
            footer: ([item]) => {
              const row = rows[item.dataIndex];
              return [
                `Resolución vencida: ${row.resolution_breached}`,
                `Sin primera respuesta en plazo: ${row.first_response_overdue}`,
              ];
            },
          },
        },
      },
    },
  });
}

function categoryTrends(card) {
  const rows = card.response.results;
  if (rows.every((row) => row.tickets === 0)) {
    return showMessage(card, "No se han creado tickets en este periodo.");
  }
  // Rows come by month, then rank: pivot them to one series per category.
  const months = [...new Set(rows.map((row) => row.month))];
  const categories = new Map();
  for (const row of rows) {
    if (!categories.has(row.category_id)) {
      categories.set(row.category_id, { name: row.category, byMonth: new Map() });
    }
    categories.get(row.category_id).byMonth.set(row.month, row);
  }
  const datasets = [...categories.values()].map((category, index) => ({
    label: category.name,
    data: months.map((month) => category.byMonth.get(month)?.tickets ?? 0),
    rows: months.map((month) => category.byMonth.get(month) ?? null),
    backgroundColor: SERIES[index % SERIES.length],
  }));
  draw(card, {
    type: "bar",
    data: { labels: months.map((month) => MONTH.format(calendarDate(month))), datasets },
    options: {
      scales: { x: { stacked: true }, y: { stacked: true, ticks: { precision: 0 } } },
      plugins: {
        tooltip: {
          callbacks: {
            label: (item) => {
              const row = item.dataset.rows[item.dataIndex];
              const parts = [`${item.dataset.label}: ${item.raw}`];
              if (row?.share !== null && row?.share !== undefined) {
                parts.push(`${NUMBER.format(row.share)} % del mes`);
              }
              if (row?.change_pct !== null && row?.change_pct !== undefined) {
                const sign = row.change_pct > 0 ? "+" : "";
                parts.push(`${sign}${NUMBER.format(row.change_pct)} % sobre el mes anterior`);
              }
              return parts.join(" · ");
            },
          },
        },
      },
    },
  });
}

function agentWorkload(card) {
  const rows = card.response.results;
  if (rows.length === 0) {
    return showMessage(card, "No hay personal de soporte.");
  }
  const name = (row) => {
    if (row.agent_id === null) {
      return "Sin asignar";
    }
    return row.is_active ? row.full_name : `${row.full_name} (inactivo)`;
  };
  // One bar row per person: the height grows with the team. CSSOM, as Chart.js.
  card.box.style.height = `${rows.length * 44 + 60}px`;
  draw(card, {
    type: "bar",
    data: {
      labels: rows.map(name),
      datasets: [
        {
          label: "Abiertos ahora",
          data: rows.map((row) => row.open_tickets),
          backgroundColor: SERIES[0],
        },
        {
          label: "Resueltos en el periodo",
          data: rows.map((row) => row.resolved),
          backgroundColor: SERIES[2],
        },
      ],
    },
    options: {
      indexAxis: "y",
      interaction: { mode: "index", intersect: false },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
      plugins: {
        tooltip: {
          callbacks: {
            footer: ([item]) => {
              const row = rows[item.dataIndex];
              return [
                `En espera del cliente: ${row.pending}`,
                `Con la resolución vencida: ${row.open_breached}`,
                `Mediana de resolución: ${duration(row.resolution_median)}`,
                `Resueltos fuera de plazo: ${row.resolved_late}`,
              ];
            },
          },
        },
      },
    },
  });
}

const RENDER = {
  "sla-compliance": slaCompliance,
  "response-times": responseTimes,
  "daily-backlog": dailyBacklog,
  "open-ticket-aging": openTicketAging,
  "category-trends": categoryTrends,
  "agent-workload": agentWorkload,
};

// --- Loading ------------------------------------------------------------------

function describePeriod(period) {
  const from = LONG_DAY.format(calendarDate(period.start));
  const to = LONG_DAY.format(calendarDate(period.end));
  return `Del ${from} al ${to}`;
}

// Each load gets a number; answers for an older one are dropped, as in the ticket
// list: a slow answer to the previous filters must not overwrite the new ones.
let generation = 0;
// The period of the last answer, to start the custom dates from what is shown.
let lastPeriod = null;

async function load() {
  const period = periodParams();
  const organization = staff ? form.elements.organization.value : "";

  const pageParams = new URLSearchParams({ period: form.elements.period.value });
  if (form.elements.period.value === "custom") {
    for (const [name, value] of period) {
      pageParams.set(name, value);
    }
  }
  if (organization) {
    pageParams.set("organization", organization);
  }
  history.replaceState(null, "", `?${pageParams}`);

  generation += 1;
  const current = generation;
  periodError.hidden = true;

  // In parallel, and each on its own: a metric that fails leaves the others drawn.
  const results = await Promise.allSettled(
    shown.map((name) => {
      const params = SNAPSHOTS.has(name) ? new URLSearchParams() : new URLSearchParams(period);
      if (organization) {
        params.set("organization", organization);
      }
      return api(`${METRICS[name]}?${params}`);
    }),
  );
  if (current !== generation) {
    return;
  }

  let periodProblem = null;
  shown.forEach((name, index) => {
    const card = cards[name];
    const result = results[index];
    if (result.status === "fulfilled") {
      card.response = result.value;
      RENDER[name](card);
      lastPeriod = result.value.period ?? lastPeriod;
      return;
    }
    card.response = null;
    const error = result.reason;
    // Every metric validates the period the same way: one message for the form.
    const invalid = error instanceof ApiError && error.status === 400;
    const problem = invalid ? (error.body.start ?? error.body.end) : null;
    if (problem) {
      periodProblem = [problem].flat().join(" ");
      showMessage(card, "Corrige el periodo para ver este gráfico.", { error: true });
    } else {
      const text = "No se ha podido cargar este gráfico. Recarga la página para reintentarlo.";
      showMessage(card, text, { error: true });
    }
  });

  if (periodProblem) {
    periodError.textContent = periodProblem;
    periodError.hidden = false;
    periodSummary.textContent = "";
  } else if (lastPeriod) {
    periodSummary.textContent = describePeriod(lastPeriod);
  }
}

form.addEventListener("submit", (event) => {
  // Enter in a date field: load here instead of sending the form.
  event.preventDefault();
  load();
});
form.elements.period.addEventListener("change", () => {
  if (form.elements.period.value === "custom" && lastPeriod && !form.elements.start.value) {
    form.elements.start.value = lastPeriod.start;
    form.elements.end.value = lastPeriod.end;
  }
  showCustomDates();
});
form.addEventListener("change", load);

// The selects inside a card redraw it from the rows it already has.
for (const [name, card] of Object.entries(cards)) {
  card.section.addEventListener("change", () => {
    if (card.response) {
      RENDER[name](card);
    }
  });
}

showCustomDates();
await load();
