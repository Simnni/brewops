// BrewOps frontend — vanilla JS, no dependencies, no build step.

async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

// ---- dashboard ----

let loadSeq = 0;

function renderDrinkBars(perDrink) {
  const container = document.getElementById("drink-bars");
  container.innerHTML = "";
  const max = Math.max(1, ...perDrink.map((d) => d.count));
  for (const drink of perDrink) {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <span class="bar-label">${drink.label}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${(drink.count / max) * 100}%"></span></span>
      <span class="bar-count">${drink.count}</span>`;
    container.appendChild(row);
  }
}

function renderTimeline(perDay) {
  const svg = document.getElementById("timeline");
  const emptyNote = document.getElementById("timeline-empty");
  svg.innerHTML = "";
  
  if (perDay.length === 0) {
    emptyNote.removeAttribute("hidden");
    return;
  }
  
  emptyNote.setAttribute("hidden", "");
  
  const width = 600;
  const height = 130;
  const max = Math.max(1, ...perDay.map((d) => d.count));
  const barWidth = width / perDay.length;
  
  perDay.forEach((day, i) => {
    const barHeight = (day.count / max) * (height - 10);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", i * barWidth);
    rect.setAttribute("y", height - barHeight);
    rect.setAttribute("width", Math.max(0.5, barWidth - 0.6));
    rect.setAttribute("height", barHeight);
    rect.setAttribute("class", "timeline-bar");
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${day.day}: ${day.count} brews`;
    rect.appendChild(title);
    svg.appendChild(rect);
  });
}

function renderMachineCards(healths) {
  const container = document.getElementById("machine-cards");
  container.innerHTML = "";
  for (const m of healths) {
    const card = document.createElement("div");
    card.className = "card";
    const maintenance = m.last_maintenance
      ? `${m.last_maintenance.type} on ${m.last_maintenance.timestamp.slice(0, 10)}`
      : "none on record";
    const errors = m.recent_errors.length
      ? `<p class="errors">Recent errors: ${m.recent_errors
          .map((e) => `${e.error_code || "?"} (${e.timestamp.slice(0, 10)})`)
          .join(", ")}</p>`
      : "";
    const specialty = m.specialty_drink
      ? `<p>Specialty: ${m.specialty_drink.label}${m.specialty_drink.is_tied ? "*" : ""} (${m.specialty_drink.count} brews)</p>`
      : "";
    card.innerHTML = `
      <h3>${m.name}</h3>
      <p class="badge">${m.has_telemetry ? "telemetry" : "manual log"}</p>
      <p>${m.brew_count} brews · last ${m.last_brew ? m.last_brew.slice(0, 16) : "never"}</p>
      ${specialty}
      <p>Last maintenance: ${maintenance}</p>
      ${errors}`;
    container.appendChild(card);
  }
}

async function loadDashboard() {
  const f = getFilter();
  const q = filterQuery(f);
  
  // Validate date range
  if (f.start && f.end && f.start > f.end) {
    document.getElementById("filter-message").textContent = '"From" must not be after "To".';
    document.getElementById("filter-message").className = "message error";
    document.getElementById("filter-start").max = f.end;
    document.getElementById("filter-end").min = f.start;
    return;
  }
  
  // Clear validation message on valid range
  document.getElementById("filter-message").textContent = "";
  document.getElementById("filter-message").className = "message";
  document.getElementById("filter-start").max = f.end || "";
  document.getElementById("filter-end").min = f.start || "";
  
  // Stale response guard
  const mySeq = ++loadSeq;
  
  try {
    const stats = await fetchJSON("/api/stats" + q);
    if (mySeq !== loadSeq) return;
    
    document.getElementById("total-brews").textContent = stats.total_brews;
    
    // Update labels based on filter state
    const hasFilter = f.start || f.end;
    if (hasFilter) {
      document.getElementById("total-label").textContent = "brews in range";
      document.getElementById("today-label").textContent = "brews on last active day in range";
    } else {
      document.getElementById("total-label").textContent = "brews total";
      document.getElementById("today-label").textContent = "brews on last active day";
    }
    
    const lastDay = stats.per_day[stats.per_day.length - 1];
    document.getElementById("brews-today").textContent = lastDay ? lastDay.count : 0;
    
    renderDrinkBars(stats.per_drink);
    renderTimeline(fillDays(stats.per_day, f.start, f.end));
    
    if (mySeq !== loadSeq) return;
    
    const machines = await fetchJSON("/api/machines");
    if (mySeq !== loadSeq) return;
    
    document.getElementById("machine-count").textContent = machines.length;
    
    const healths = await Promise.all(machines.map((m) => fetchJSON(`/api/machines/${m.id}` + q)));
    if (mySeq !== loadSeq) return;
    
    renderMachineCards(healths);
  } catch (error) {
    document.getElementById("total-brews").textContent = "!";
    console.error("Dashboard failed to load:", error);
  }
}

// ---- forms ----

function localNow() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16); // datetime-local format
}

// ---- date filter ----

function getFilter() {
  return {
    start: document.getElementById("filter-start").value || null,
    end: document.getElementById("filter-end").value || null,
  };
}

function filterQuery(f) {
  const params = new URLSearchParams();
  if (f.start) params.append("start", f.start);
  if (f.end) params.append("end", f.end);
  const query = params.toString();
  return query ? "?" + query : "";
}

function localDateString(d) {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function fillDays(perDay, start, end) {
  if (perDay.length === 0) return [];
  
  const from = start || perDay[0]?.day;
  const to = end || perDay[perDay.length - 1]?.day;
  
  if (!from || !to) return [];
  
  const map = new Map(perDay.map((d) => [d.day, d.count]));
  const result = [];
  
  const fromDate = new Date(from + "T00:00:00");
  const toDate = new Date(to + "T00:00:00");
  
  // Safety cap: if span exceeds ~1000 days, return sparse data
  const daySpan = Math.floor((toDate - fromDate) / (24 * 60 * 60 * 1000)) + 1;
  if (daySpan > 1000) return perDay;
  
  const current = new Date(fromDate);
  while (current <= toDate) {
    const day = localDateString(current);
    result.push({
      day,
      count: map.get(day) || 0,
    });
    current.setDate(current.getDate() + 1);
  }
  
  return result;
}

function fillSelect(select, items, valueKey, labelKey) {
  select.innerHTML = "";
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item[valueKey];
    option.textContent = item[labelKey];
    select.appendChild(option);
  }
}

function setupFilter() {
  const startInput = document.getElementById("filter-start");
  const endInput = document.getElementById("filter-end");
  const presets = {
    "filter-7d": () => {
      const today = new Date();
      const end = localDateString(today);
      const start = new Date(today);
      start.setDate(start.getDate() - 6);
      startInput.value = localDateString(start);
      endInput.value = end;
      updatePresetButtons("filter-7d");
      loadDashboard();
    },
    "filter-30d": () => {
      const today = new Date();
      const end = localDateString(today);
      const start = new Date(today);
      start.setDate(start.getDate() - 29);
      startInput.value = localDateString(start);
      endInput.value = end;
      updatePresetButtons("filter-30d");
      loadDashboard();
    },
    "filter-all": () => {
      startInput.value = "";
      endInput.value = "";
      updatePresetButtons("filter-all");
      loadDashboard();
    },
  };
  
  for (const [id, handler] of Object.entries(presets)) {
    document.getElementById(id).addEventListener("click", handler);
  }
  
  startInput.addEventListener("change", () => {
    updatePresetButtons(null);
    loadDashboard();
  });
  
  endInput.addEventListener("change", () => {
    updatePresetButtons(null);
    loadDashboard();
  });
}

function updatePresetButtons(activeId) {
  for (const id of ["filter-7d", "filter-30d", "filter-all"]) {
    const btn = document.getElementById(id);
    if (id === activeId) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  }
}

async function setupForms() {
  const machines = await fetchJSON("/api/machines");
  const drinks = await fetchJSON("/api/drink-types");
  fillSelect(document.getElementById("brew-machine"), machines, "id", "name");
  fillSelect(document.getElementById("brew-drink"), drinks, "name", "label");
  fillSelect(document.getElementById("maintenance-machine"), machines, "id", "name");
  document.getElementById("brew-timestamp").value = localNow();
  document.getElementById("maintenance-timestamp").value = localNow();

  document.getElementById("brew-form").addEventListener("submit", (event) =>
    submitForm(event, "/api/brews", "brew-message", () => ({
      machine_id: Number(document.getElementById("brew-machine").value),
      drink_type: document.getElementById("brew-drink").value,
      timestamp: document.getElementById("brew-timestamp").value,
    }))
  );

  document.getElementById("maintenance-form").addEventListener("submit", (event) =>
    submitForm(event, "/api/maintenance", "maintenance-message", () => ({
      machine_id: Number(document.getElementById("maintenance-machine").value),
      type: document.getElementById("maintenance-type").value,
      timestamp: document.getElementById("maintenance-timestamp").value,
      note: document.getElementById("maintenance-note").value || null,
    }))
  );
}

async function submitForm(event, url, messageId, buildPayload) {
  event.preventDefault();
  const message = document.getElementById(messageId);
  message.textContent = "";
  message.className = "message";
  try {
    await fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    message.textContent = "Logged.";
    message.classList.add("ok");
    await loadDashboard();
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
}

loadDashboard().catch((error) => {
  document.getElementById("total-brews").textContent = "!";
  console.error("Dashboard failed to load:", error);
});
setupFilter();
setupForms().catch((error) => console.error("Form setup failed:", error));
