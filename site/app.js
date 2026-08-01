// Chess Calendar — vanilla JS, no build step.
// Static build: fetches ./tournaments.json (committed by the GitHub Actions
// cron), renders into FullCalendar + a list view, supports instant client-side
// filtering across state/format/source/FIDE.

const INDIAN_STATES = [
  "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
  "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
  "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
  "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
  "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
  "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
  "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
];

const COMMON_AGE_CATEGORIES = [
  "U-07", "U-08", "U-09", "U-10", "U-11", "U-12", "U-13", "U-14",
  "U-15", "U-16", "U-17", "U-18", "U-19", "U-20", "U-25", "Senior",
];

const PREF_STATES_KEY = "chesscal.preferredStates";

function loadPreferredStates() {
  try {
    const raw = localStorage.getItem(PREF_STATES_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw));
  } catch {
    return new Set();
  }
}

function savePreferredStates(set) {
  try {
    localStorage.setItem(PREF_STATES_KEY, JSON.stringify([...set]));
  } catch {
    /* ignore quota / privacy-mode errors */
  }
}

const state = {
  all: [],
  filters: { format: "", source: "", fideOnly: false, age: "", openness: "" },
  preferredStates: loadPreferredStates(), // empty = all states
  collapse: true,
  view: "calendar",
  calendar: null,
  calendarSig: null, // signature of the event set currently drawn (see renderCalendar)
  map: null,
  markers: null,
  // The month all three views agree on (first-of-month Date; null = not resolved
  // yet). Paging the calendar writes it via the datesSet handler, the map's own
  // ‹/› buttons write it too, and the list filters by it — so switching views
  // keeps the month you were looking at instead of jumping back to today.
  month: null,
};

const $ = (id) => document.getElementById(id);

async function fetchAll() {
  // Static build: data is a JSON file refreshed by the GitHub Actions cron and
  // committed to the repo, not served by a live API. Same payload shape as the
  // old GET /api/tournaments ({count, tournaments, last_refresh}).
  //
  // No `?v=` cache-buster: GitHub Pages serves this with `max-age=600` plus an
  // ETag, so the browser revalidates within 10 minutes on its own and a fresh
  // commit lands without a hard reload. A per-hour query string only forced a
  // second download by missing the <link rel=preload> in index.html, which URL-
  // matches exactly.
  const r = await fetch("./tournaments.json");
  if (!r.ok) throw new Error("fetch failed");
  const j = await r.json();
  state.all = j.tournaments || [];
  $("count").textContent = `${state.all.length} upcoming tournaments`;
  if (j.last_refresh && j.last_refresh.finished_at) {
    $("last-refresh").textContent = `Last refresh: ${j.last_refresh.finished_at.replace("T", " ").slice(0, 16)} UTC`;
  } else {
    $("last-refresh").textContent = "No successful refresh yet";
  }
  populateSelects();
  render();
}

function populateSelects() {
  // States dropdown is the canonical 28 states + 8 UTs ONLY — never unioned
  // with raw scraped values, so foreign/garbage states can't leak in.
  buildStatesList(INDIAN_STATES);

  const sources = [...new Set(state.all.map((t) => t.source).filter(Boolean))].sort();

  const dataAges = state.all.flatMap((t) => t.age_categories || []).filter(Boolean);
  const ages = [...new Set([...COMMON_AGE_CATEGORIES, ...dataAges])].sort(ageSort);

  fillSelect($("f-source"), sources);
  fillSelect($("f-age"), ages);
}

function buildStatesList(states) {
  const box = $("f-states");
  box.innerHTML = states
    .map(
      (s) => `
      <label class="state-item">
        <input type="checkbox" value="${escapeHtml(s)}" ${state.preferredStates.has(s) ? "checked" : ""} />
        ${escapeHtml(s)}
      </label>`
    )
    .join("");
  box.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) state.preferredStates.add(cb.value);
      else state.preferredStates.delete(cb.value);
      savePreferredStates(state.preferredStates);
      updateStatesSummary();
      scheduleRender();
    });
  });
  updateStatesSummary();
}

function updateStatesSummary() {
  const n = state.preferredStates.size;
  $("states-summary").textContent =
    n === 0 ? "All states" : n === 1 ? [...state.preferredStates][0] : `${n} states selected`;
}

function ageSort(a, b) {
  // Sort U-NN numerically, push non-U entries to the end alphabetically.
  const ma = a.match(/^U-?(\d+)/i);
  const mb = b.match(/^U-?(\d+)/i);
  if (ma && mb) return parseInt(ma[1], 10) - parseInt(mb[1], 10);
  if (ma) return -1;
  if (mb) return 1;
  return a.localeCompare(b);
}

function fillSelect(el, values) {
  const cur = el.value;
  const first = el.querySelector("option");
  el.innerHTML = "";
  el.appendChild(first);
  for (const v of values) {
    const o = document.createElement("option");
    o.value = v; o.textContent = v;
    el.appendChild(o);
  }
  el.value = cur;
}

function applyFilters(rows) {
  const f = state.filters;
  const prefer = state.preferredStates;
  return rows.filter((t) => {
    if (prefer.size > 0 && !prefer.has(t.state)) return false;
    if (f.format && (t.format || "").toLowerCase() !== f.format.toLowerCase()) return false;
    if (f.source && t.source !== f.source) return false;
    if (f.fideOnly && !t.is_fide_rated) return false;
    if (f.age) {
      const cats = (t.age_categories || []).map((c) => c.toLowerCase());
      // "Open to all" tournaments without explicit age categories also match
      // any age filter — a 10-year-old can play in an Open.
      if (cats.length > 0 && !cats.includes(f.age.toLowerCase())) return false;
      if (cats.length === 0 && !t.open_to_all) return false;
    }
    if (f.openness === "open" && !t.open_to_all) return false;
    if (f.openness === "restricted" && t.open_to_all) return false;
    return true;
  });
}

function normalizeForGrouping(name) {
  // Strip parenthesized "(Under-12)" / "(U-12 Open)" / "(U-19Open)" suffixes,
  // strip trailing "Section A/B/C", strip "U-NN" / "Under NN" tokens, and
  // collapse whitespace. Used only as a fallback in sameEvent() for names that
  // reduce to nothing but boilerplate (too few tokens for the subset test).
  let s = name || "";
  s = s.replace(/\s*\((?:[^)]*\b(?:U-?\d+|Under[-\s]?\d+|Open|Girls?|Boys?)[^)]*)\)\s*/gi, " ");
  s = s.replace(/\s*[,-]\s*Section\s+[A-Z][^,]*$/i, "");
  s = s.replace(/\s*[,-]\s*(?:U-?\d+|Under[-\s]?\d+)(?:\s*(?:Open|Girls?|Boys?))?\s*$/gi, "");
  s = s.replace(/\b(?:U-?\d+|Under[-\s]?\d+)\b/gi, " ");
  s = s.replace(/\s+/g, " ").trim().toLowerCase();
  return s;
}

// Pure boilerplate shared across sources' naming conventions. Dropping these
// lets "1st SRM International Open FIDE Rated Rapid Chess Tournament" (aicf) and
// "1st SRM International Open Fide Rapid Rating Chess Tournament 2026"
// (chess-results) reduce to the same set of distinguishing tokens.
// NOTE: format words (rapid/blitz/classical) are deliberately NOT boilerplate —
// keeping them means a same-day Rapid and Blitz of one series stay separate.
const GROUPING_STOPWORDS = new Set([
  "fide", "aicf", "rated", "rating", "chess",
  "tournament", "tournaments", "championship", "championships",
  "the", "of", "for", "and", "a", "an",
  "all", "india", "allindia", "national", "international", "open",
]);

// sameEvent() compares every pair within a date bucket, so one render asks for
// the same name's tokens hundreds of times (18k calls for 523 rows). The token
// set depends only on the name, so cache it — grouping drops from ~50ms to ~5ms
// per render, and render() runs on every filter keystroke.
const tokenCache = new Map();

function nameTokens(name) {
  const hit = tokenCache.get(name);
  if (hit) return hit;
  const toks = computeNameTokens(name);
  tokenCache.set(name, toks);
  return toks;
}

function computeNameTokens(name) {
  // Order-independent set of the distinguishing words in a tournament name.
  // Two sources phrase the same event differently ("FIDE Rated Rapid" vs "Fide
  // Rapid Rating", trailing year present or not, different word order, and one
  // side often appends promo/contact junk like "TOTAL CASH PRIZE-Rs.300000/-"),
  // so comparing token SETS — rather than exact strings — collapses those
  // differences while a genuinely different event still contributes new words.
  let s = (name || "").toLowerCase();
  s = s.replace(/\([^)]*\)/g, " ");                     // sections / contacts / notes
  s = s.replace(/\b(?:19|20)\d{2}\b/g, " ");            // years ("2026")
  s = s.replace(/\b(?:u-?\d+|under[-\s]?\d+)\b/g, " "); // age tokens → shown as ×N chips
  s = s.replace(/\b\d{4,}\b/g, " ");                    // phone / pincode / fee / long ids
  const toks = (s.match(/[a-z0-9]+/g) || []).filter((t) => !GROUPING_STOPWORDS.has(t));
  return new Set(toks);
}

function sameEvent(a, b) {
  // True when a and b are the same real-world event. One name's distinguishing
  // tokens being a subset of the other's (with ≥2 tokens in common) means they
  // differ only by appended noise — promo text, a contact number, a venue
  // string — not by identity. The ≥2 floor stops a single shared generic word
  // ("academy", "state") from merging unrelated events.
  if ((a.format || null) && (b.format || null) && a.format !== b.format) return false;
  const ta = nameTokens(a.name);
  const tb = nameTokens(b.name);
  // All-boilerplate names ("National Under 17 Chess Championship") reduce to
  // fewer than two distinguishing tokens; the subset test isn't meaningful, so
  // require exact normalized-string equality. Identical duplicates still merge,
  // distinct ones (Open vs Girls, Senior vs Under) don't, and dozens of such
  // names never collapse onto one empty key.
  if (ta.size < 2 || tb.size < 2) {
    return normalizeForGrouping(a.name) === normalizeForGrouping(b.name);
  }
  const [small, big] = ta.size <= tb.size ? [ta, tb] : [tb, ta];
  for (const t of small) {
    if (!big.has(t)) return false; // small must be a subset of big
  }
  return true; // small ⊆ big and |small| ≥ 2
}

function extractAgeTag(name) {
  // Returns "U-12", "Open", "Girls", etc. — what to show as a chip.
  const m = name.match(/U-?(\d+)|Under[-\s]?(\d+)/i);
  if (m) return `U-${m[1] || m[2]}`;
  if (/\bopen\b/i.test(name)) return "Open";
  if (/\bgirls?\b/i.test(name)) return "Girls";
  if (/\bboys?\b/i.test(name)) return "Boys";
  const sec = name.match(/Section\s+([A-Z])/i);
  if (sec) return `Section ${sec[1]}`;
  return null;
}

function collapseGroups(rows) {
  if (!state.collapse) return rows;
  // Bucket by start_date first, then union same-event rows within each date.
  // Date is the one field every source agrees on; city is deliberately NOT part
  // of the key because sources disagree (NULL vs "Pune"; "Bhilwara" vs "Near
  // Roadways Bus Stand Bhilwara"). Within a date, sameEvent() decides identity
  // by token-subset so appended promo/contact junk on one source's name doesn't
  // keep a true duplicate apart.
  const byDate = new Map();
  for (const t of rows) {
    const d = t.start_date || "";
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d).push(t);
  }
  const groups = [];
  for (const bucket of byDate.values()) {
    // Union-find over the bucket: each row joins the first existing group it is
    // the same event as, else starts its own.
    const clusters = [];
    for (const t of bucket) {
      const hit = clusters.find((c) => c.some((m) => sameEvent(m, t)));
      if (hit) hit.push(t);
      else clusters.push([t]);
    }
    for (const c of clusters) groups.push(c);
  }
  const out = [];
  for (const variants of groups) {
    if (variants.length === 1) {
      out.push(variants[0]);
      continue;
    }
    // Merge: shortest name as display, union of age tags + categories.
    variants.sort((a, b) => a.name.length - b.name.length);
    const lead = variants[0];
    const tags = new Set();
    const categories = new Set();
    for (const v of variants) {
      const tag = extractAgeTag(v.name);
      if (tag) tags.add(tag);
      for (const c of v.age_categories || []) categories.add(c);
    }
    // The shortest-name variant wins the display name, but it isn't necessarily
    // the most complete record: the aicf variant often has the tersest name yet
    // a NULL city and no coordinates, while the chess-results variant carries the
    // city + lat/lng that the map needs. Backfill any field the lead is missing
    // from the other variants so merging never drops location or detail data.
    const merged = { ...lead };
    for (const v of variants) {
      for (const k in v) {
        if (k.startsWith("_")) continue;
        if (merged[k] == null || merged[k] === "") merged[k] = v[k];
      }
    }
    out.push({
      ...merged,
      name: lead.name,
      _grouped: true,
      _variants: variants,
      _variant_count: variants.length,
      _variant_tags: [...tags],
      age_categories: [...new Set([...(lead.age_categories || []), ...categories])],
    });
  }
  return out;
}

// Redrawing the month grid is ~250–550ms of unavoidable FullCalendar layout for
// this many events. Called straight from a click handler, that work runs before
// the browser paints, so the checkbox itself appears frozen until it finishes.
// Yielding one frame first lets the checkbox tick and the cursor update
// immediately, then does the heavy work — same total time, but the UI responds
// at once. Coalescing also means dragging over several checkboxes renders once.
let renderQueued = false;

function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    render();
  });
}

function render() {
  const filtered = collapseGroups(applyFilters(state.all));
  const banner = $("empty-banner");
  if (state.all.length === 0) {
    banner.classList.remove("hidden");
    banner.textContent = "No tournaments cached yet — click Refresh sources (this takes 1–5 minutes the first time).";
  } else if (filtered.length === 0) {
    banner.classList.remove("hidden");
    banner.textContent = "No tournaments match these filters.";
  } else {
    banner.classList.add("hidden");
  }
  if (state.view === "calendar") renderCalendar(filtered);
  else if (state.view === "map") renderMap(filtered);
  else renderList(filtered);
  // The list and map narrow to state.month, so they own their own "nothing in
  // this month" message; only hide the shared banner for them once it has been
  // set above.
  if (state.view === "list" && filtered.length > 0) {
    const rows = filtered.filter((t) => inMonth(t, monthKey(state.month)));
    if (rows.length === 0) {
      banner.classList.remove("hidden");
      banner.textContent = `No tournaments in ${monthLabel(state.month)}.`;
    }
  }
}

function calendarEvents(rows) {
  return rows.map((t) => ({
    title: t.name + (t._grouped ? `  ×${t._variant_count}` : ""),
    start: t.start_date,
    end: t.end_date ? addDay(t.end_date) : null,
    color: t.is_fide_rated ? "#2f5d3f" : "#7a7a7a",
    extendedProps: { _t: t },
  }));
}

function renderCalendar(rows) {
  const el = $("calendar");
  if (!state.calendar) {
    state.calendar = new FullCalendar.Calendar(el, {
      initialView: "dayGridMonth",
      initialDate: state.month || undefined,
      headerToolbar: { left: "prev,next today", center: "title", right: "dayGridMonth,listMonth" },
      height: "100%",
      // Busy days carry 100+ events. Without a cap the month grid stacks every
      // one in its cell — a ~4900px-tall body inside a ~685px viewport, which
      // spilled over the page. `true` fits each cell exactly and moves the rest
      // behind a "+N more" popover; a fixed number (4) overflows again on the
      // busiest rows, so let it measure.
      dayMaxEvents: true,
      // Stretch the week rows to fill the height in one pass. Without it,
      // dayMaxEvents:true measures and re-measures row heights: ~470ms per
      // re-render vs ~300ms with it, same final layout.
      expandRows: true,
      // Pass the events up front: constructing empty and then loading them
      // renders the whole grid twice (~370ms + ~700ms). One pass is ~830ms.
      events: calendarEvents(rows),
      // Paging the calendar is the source of truth for the month the other two
      // views show. Without this, prev/next moved the grid only and the map and
      // list stayed on the month the page opened at.
      datesSet: (info) => {
        // currentStart is the first of the displayed month (activeStart would be
        // the leading Sunday, which can belong to the previous month).
        const m = new Date(info.view.currentStart.getFullYear(), info.view.currentStart.getMonth(), 1);
        if (state.month && monthKey(state.month) === monthKey(m)) return;
        state.month = m;
        syncMonthLabel();
      },
      eventClick: (info) => {
        const t = info.event.extendedProps._t;
        if (t) showModal(t);
      },
    });
    state.calendarSig = rows.length + ":" + rows.map((t) => t.id).join(",");
    state.calendar.render();
    return;
  }
  // Follow a month picked in another view (the map's or list's ‹/› buttons).
  // Do this BEFORE reloading events: gotoDate() re-renders the grid, so reusing
  // that same render for the new events avoids doing the work twice.
  const needsGoto = state.month && monthKey(state.calendar.view.currentStart) !== monthKey(state.month);

  // Reloading events costs ~300ms of grid layout, so skip it when the event set
  // is identical to what's already drawn — which is the case for a plain month
  // page, where only the date range changed. Compare a cheap signature rather
  // than the objects themselves.
  const sig = rows.length + ":" + rows.map((t) => t.id).join(",");
  if (sig !== state.calendarSig) {
    state.calendarSig = sig;
    // setOption("events", …) replaces the source and re-renders once. The old
    // removeAllEventSources()+addEventSource() pair tore the grid down and
    // rebuilt it — ~580ms, which is what made every checkbox click lag.
    state.calendar.setOption("events", calendarEvents(rows));
  }
  if (needsGoto) state.calendar.gotoDate(state.month);
}

function addDay(iso) {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

function monthKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function eventMonths(t) {
  // The set of "YYYY-MM" months an event touches (a multi-day event spanning a
  // month boundary shows on both months' maps).
  const start = new Date((t.start_date || "") + "T00:00:00");
  if (isNaN(start)) return [];
  const end = t.end_date ? new Date(t.end_date + "T00:00:00") : start;
  const months = [];
  const cur = new Date(start.getFullYear(), start.getMonth(), 1);
  const last = new Date(end.getFullYear(), end.getMonth(), 1);
  while (cur <= last) {
    months.push(monthKey(cur));
    cur.setMonth(cur.getMonth() + 1);
  }
  return months;
}

function defaultMonth(rows) {
  // Current month if it has events, else the earliest month that does — so the
  // map never opens on an empty view.
  const now = new Date();
  const nowKey = monthKey(now);
  const keys = new Set(rows.flatMap(eventMonths));
  if (keys.has(nowKey) || keys.size === 0) return new Date(now.getFullYear(), now.getMonth(), 1);
  const earliest = [...keys].sort()[0];
  const [y, m] = earliest.split("-").map(Number);
  return new Date(y, m - 1, 1);
}

function monthLabel(d) {
  return d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function syncMonthLabel() {
  // The map toolbar and the list heading both show state.month; the calendar
  // draws its own title.
  if (state.month) $("map-month").textContent = monthLabel(state.month);
}

function shiftMonth(delta) {
  const base = state.month || defaultMonth(collapseGroups(applyFilters(state.all)));
  state.month = new Date(base.getFullYear(), base.getMonth() + delta, 1);
  syncMonthLabel();
  scheduleRender();
}

function inMonth(t, key) {
  return eventMonths(t).includes(key);
}

function renderMap(rows) {
  const el = $("map");
  // Leaflet is deferred (it's ~182KB and only this view needs it), so it may not
  // have executed yet if the user hits the Map tab immediately. Retry on the
  // next frame rather than throwing.
  if (typeof L === "undefined" || !L.markerClusterGroup) {
    $("map-month").textContent = "Loading map…";
    setTimeout(() => { if (state.view === "map") render(); }, 60);
    return;
  }
  if (!state.map) {
    state.map = L.map(el, { scrollWheelZoom: true }).setView([22.5, 80], 5);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 18,
    }).addTo(state.map);
    state.markers = L.markerClusterGroup({ maxClusterRadius: 45 });
    state.map.addLayer(state.markers);
  }
  if (!state.month) state.month = defaultMonth(rows);

  const wantKey = monthKey(state.month);
  syncMonthLabel();
  const monthRows = rows.filter((t) => inMonth(t, wantKey));

  state.markers.clearLayers();

  // Group by resolved location so several tournaments at the same place share
  // ONE pin (the "same address in two spots" bug came from jittering each event
  // individually). We spread only distinct locations that collided on the same
  // coordinate — e.g. many venues all falling back to a state centroid.
  const byLocation = new Map();
  for (const t of monthRows) {
    if (t.latitude == null || t.longitude == null) continue;
    // A precise venue string distinguishes real locations that share a city
    // centroid; without one, fall back to the coordinate itself.
    const locKey = (t.venue || t.city || "").trim().toLowerCase() || `${t.latitude},${t.longitude}`;
    if (!byLocation.has(locKey)) {
      byLocation.set(locKey, { lat: t.latitude, lng: t.longitude, precision: t.geo_precision, items: [] });
    }
    byLocation.get(locKey).items.push(t);
  }

  // Spread groups that share an identical coordinate so they don't stack.
  const coordSeen = new Map();
  let placed = 0;
  for (const group of byLocation.values()) {
    const coordKey = `${group.lat},${group.lng}`;
    const n = coordSeen.get(coordKey) || 0;
    coordSeen.set(coordKey, n + 1);
    const [lat, lng] = jitter(group.lat, group.lng, n);

    const fide = group.items.some((t) => t.is_fide_rated);
    const marker = L.circleMarker([lat, lng], {
      radius: group.items.length > 1 ? 9 : 7,
      color: fide ? "#2f5d3f" : "#7a7a7a",
      weight: 2,
      fillColor: fide ? "#2f5d3f" : "#9a9a9a",
      fillOpacity: 0.8,
    });
    marker.bindTooltip(locationTooltip(group), { direction: "top", offset: [0, -6] });
    marker.on("click", () => {
      if (group.items.length === 1) showModal(group.items[0]);
      else showLocationModal(group);
    });
    state.markers.addLayer(marker);
    placed++;
  }

  const banner = $("empty-banner");
  if (monthRows.length === 0) {
    banner.classList.remove("hidden");
    banner.textContent = `No tournaments in ${$("map-month").textContent}.`;
  } else if (placed === 0) {
    banner.classList.remove("hidden");
    banner.textContent = "None of these tournaments have a mappable location.";
  } else {
    banner.classList.add("hidden");
  }

  // The container may have been display:none when created; fix sizing.
  setTimeout(() => state.map.invalidateSize(), 0);
}

function jitter(lat, lng, n) {
  if (n === 0) return [lat, lng];
  // Deterministic small spiral offset (~1–3 km) per collision index.
  const angle = n * 2.399963; // golden angle in radians
  const radius = 0.02 * Math.sqrt(n);
  return [lat + radius * Math.cos(angle), lng + radius * Math.sin(angle)];
}

function locationTooltip(group) {
  const items = group.items;
  const where = [items[0].city, items[0].state].filter(Boolean).join(", ");
  const approx = group.precision === "state" ? " (approx.)" : "";
  if (items.length === 1) {
    const t = items[0];
    const suffix = t._grouped ? `  ×${t._variant_count}` : "";
    const bits = [
      `<strong>${escapeHtml(t.name + suffix)}</strong>`,
      formatRange(t.start_date, t.end_date),
      where ? escapeHtml(where) + approx : "",
      [t.format, t.is_fide_rated ? "FIDE" : "", t.is_aicf_rated ? "AICF" : ""]
        .filter(Boolean)
        .map(escapeHtml)
        .join(" · "),
    ].filter(Boolean);
    return `<div class="map-tip">${bits.join("<br>")}</div>`;
  }
  // Multiple tournaments at one location — summarise, list a few names.
  const names = items
    .slice(0, 4)
    .map((t) => `• ${escapeHtml(t.name)}`)
    .join("<br>");
  const more = items.length > 4 ? `<br>+${items.length - 4} more…` : "";
  const bits = [
    `<strong>${items.length} tournaments${where ? " · " + escapeHtml(where) + approx : ""}</strong>`,
    names + more,
  ];
  return `<div class="map-tip">${bits.join("<br>")}</div>`;
}

function showLocationModal(group) {
  const where = [group.items[0].city, group.items[0].state].filter(Boolean).join(", ");
  $("modal-body").innerHTML = `
    <h2>${group.items.length} tournaments${where ? ` <span class="badge">${escapeHtml(where)}</span>` : ""}</h2>
    <ul class="variants">
      ${group.items
        .map(
          (t) => `
        <li>
          <a href="#" data-id="${t.id}" class="loc-link">${escapeHtml(t.name)}</a>
          — ${formatRange(t.start_date, t.end_date)}
        </li>`
        )
        .join("")}
    </ul>
  `;
  $("modal-body")
    .querySelectorAll(".loc-link")
    .forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const t = group.items.find((x) => x.id === a.dataset.id);
        if (t) showModal(t);
      });
    });
  $("modal").classList.remove("hidden");
}

function renderList(allRows) {
  const el = $("list");
  // Show the month the other views are on, not the whole dataset — otherwise
  // paging the calendar and switching to the list looked like nothing happened.
  if (!state.month) state.month = defaultMonth(allRows);
  const wantKey = monthKey(state.month);
  const rows = allRows.filter((t) => inMonth(t, wantKey));
  $("list-month").textContent = monthLabel(state.month);
  $("list-count").textContent = rows.length === 1 ? "1 tournament" : `${rows.length} tournaments`;
  const body = $("list-body");
  if (rows.length === 0) {
    body.innerHTML = "";
    return;
  }
  body.innerHTML = rows.map((t) => `
    <div class="card" data-id="${t.id}">
      <div class="title">${escapeHtml(t.name)}${t._grouped ? ` <span class="badge">${t._variant_count} variants</span>` : ""}</div>
      <div class="meta-row">
        ${chip(t.format, "format")}
        ${t.is_fide_rated ? `<span class="chip fide">FIDE</span>` : ""}
        ${t.is_aicf_rated ? `<span class="chip aicf">AICF</span>` : ""}
        ${(t._variant_tags || []).map((tg) => `<span class="chip variant">${escapeHtml(tg)}</span>`).join("")}
        ${formatRange(t.start_date, t.end_date)}
        ${t.city || t.state ? ` · ${escapeHtml([t.city, t.state].filter(Boolean).join(", "))}` : ""}
        · <em>${escapeHtml(t.source)}</em>
      </div>
    </div>
  `).join("");
  body.querySelectorAll(".card").forEach((c) => {
    c.addEventListener("click", () => {
      const t = rows.find((x) => x.id === c.dataset.id);
      if (t) showModal(t);
    });
  });
}

function chip(value, cls) {
  if (!value) return "";
  return `<span class="chip ${cls}">${escapeHtml(value)}</span>`;
}

function formatRange(s, e) {
  if (!s) return "";
  if (!e || e === s) return s;
  return `${s} → ${e}`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function showModal(t) {
  const variantsHtml = t._grouped ? `
    <h3 style="margin-top:20px">${t._variant_count} variants</h3>
    <ul class="variants">
      ${t._variants.map((v) => `
        <li>
          ${escapeHtml(v.name)}
          ${v.source_url ? ` — <a href="${v.source_url}" target="_blank" rel="noopener">link</a>` : ""}
        </li>
      `).join("")}
    </ul>
  ` : "";
  $("modal-body").innerHTML = `
    <h2>${escapeHtml(t.name)}${t._grouped ? ` <span class="badge">${t._variant_count}</span>` : ""}</h2>
    <dl>
      <dt>Dates</dt><dd>${formatRange(t.start_date, t.end_date)}</dd>
      <dt>Location</dt><dd>${escapeHtml([t.city, t.state, t.country].filter(Boolean).join(", ")) || "—"}</dd>
      <dt>Venue</dt><dd>${escapeHtml(t.venue || "—")}</dd>
      <dt>Format</dt><dd>${escapeHtml(t.format || "—")}${t.time_control ? ` (${escapeHtml(t.time_control)})` : ""}</dd>
      <dt>Rated</dt><dd>${t.is_fide_rated ? "FIDE " : ""}${t.is_aicf_rated ? "AICF" : ""}${!t.is_fide_rated && !t.is_aicf_rated ? "Unrated/local" : ""}</dd>
      <dt>Age categories</dt><dd>${(t.age_categories || []).join(", ") || "—"}</dd>
      <dt>Entry fee</dt><dd>${t.entry_fee_inr != null ? "₹" + t.entry_fee_inr : "—"}</dd>
      <dt>Prize fund</dt><dd>${t.prize_fund_inr != null ? "₹" + t.prize_fund_inr : "—"}</dd>
      <dt>Registration deadline</dt><dd>${t.registration_deadline || "—"}</dd>
      <dt>Organizer</dt><dd>${escapeHtml(t.organizer || "—")}</dd>
      <dt>Contact</dt><dd>${escapeHtml(t.contact || "—")}</dd>
      <dt>Source</dt><dd>${t.source_url ? `<a href="${t.source_url}" target="_blank" rel="noopener">${escapeHtml(t.source)}</a>` : escapeHtml(t.source)}</dd>
      ${t.registration_url ? `<dt>Register</dt><dd><a href="${t.registration_url}" target="_blank" rel="noopener">Open form</a></dd>` : ""}
    </dl>
    ${variantsHtml}
  `;
  $("modal").classList.remove("hidden");
}

function bind() {
  // All filter inputs go through scheduleRender() so the control itself repaints
  // before the grid redraw blocks the main thread.
  $("f-format").addEventListener("change", (e) => { state.filters.format = e.target.value; scheduleRender(); });
  $("f-age").addEventListener("change", (e) => { state.filters.age = e.target.value; scheduleRender(); });
  $("f-open").addEventListener("change", (e) => { state.filters.openness = e.target.value; scheduleRender(); });
  $("f-source").addEventListener("change", (e) => { state.filters.source = e.target.value; scheduleRender(); });
  $("f-fide").addEventListener("change", (e) => { state.filters.fideOnly = e.target.checked; scheduleRender(); });
  $("f-collapse").addEventListener("change", (e) => { state.collapse = e.target.checked; scheduleRender(); });

  const gotoToday = () => {
    const now = new Date();
    state.month = new Date(now.getFullYear(), now.getMonth(), 1);
    syncMonthLabel();
    scheduleRender();
  };
  $("map-prev").addEventListener("click", () => shiftMonth(-1));
  $("map-next").addEventListener("click", () => shiftMonth(1));
  $("map-today").addEventListener("click", gotoToday);
  $("list-prev").addEventListener("click", () => shiftMonth(-1));
  $("list-next").addEventListener("click", () => shiftMonth(1));
  $("list-today").addEventListener("click", gotoToday);

  $("states-all").addEventListener("click", () => {
    state.preferredStates = new Set(INDIAN_STATES);
    savePreferredStates(state.preferredStates);
    buildStatesList(INDIAN_STATES);
    scheduleRender();
  });
  $("states-clear").addEventListener("click", () => {
    state.preferredStates = new Set();
    savePreferredStates(state.preferredStates);
    buildStatesList(INDIAN_STATES);
    scheduleRender();
  });

  document.querySelectorAll(".view-tabs button").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".view-tabs button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      state.view = b.dataset.view;
      $("calendar").classList.toggle("hidden", state.view !== "calendar");
      $("list").classList.toggle("hidden", state.view !== "list");
      $("map-view").classList.toggle("hidden", state.view !== "map");
      render();
      if (state.view === "calendar" && state.calendar) state.calendar.updateSize();
      if (state.view === "map" && state.map) state.map.invalidateSize();
    });
  });

  // Collapse the left pane in place: "«" (inside the pane) hides it, "☰"
  // (floating, only visible while collapsed) brings it back. Works on all
  // screen sizes; on mobile it starts collapsed so the view gets full width.
  $("sidebar-collapse").addEventListener("click", () => setSidebarCollapsed(true));
  $("sidebar-show").addEventListener("click", () => setSidebarCollapsed(false));

  // Static build: no server to trigger a scrape, so the button is hidden.
  // Data refreshes on the GitHub Actions cron (see .github/workflows/refresh.yml).
  const refreshBtn = $("refresh-btn");
  if (refreshBtn) refreshBtn.remove();
  $("modal-close").addEventListener("click", () => $("modal").classList.add("hidden"));
  $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("modal").classList.add("hidden"); });
}

function setSidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  // The content area changed width — let the active view re-fit it. Deferred so
  // it runs after layout reflows to the new grid columns.
  setTimeout(() => {
    if (state.view === "map" && state.map) state.map.invalidateSize();
    if (state.view === "calendar" && state.calendar) state.calendar.updateSize();
  }, 0);
}

bind();
// Start collapsed on narrow screens so the map/calendar/list get full width;
// the user opens the pane with the "☰" button when they want to filter.
if (window.matchMedia("(max-width: 768px)").matches) setSidebarCollapsed(true);
fetchAll().catch((e) => {
  $("count").textContent = "Failed to load tournaments.json.";
  console.error(e);
});
