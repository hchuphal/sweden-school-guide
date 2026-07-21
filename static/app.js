let schools = [];
let metadata = null;
const TARGET_YEAR = 2027;

const knownAddresses = [
  { label: "Långströmsgatan 6, Göteborg", lat: 57.7135, lng: 11.8998, aliases: ["långströmsgatan", "langstromsgatan", "hakefjordsgatan", "jättesten"] },
  { label: "Lindholmen, Göteborg", lat: 57.7086, lng: 11.9400, aliases: ["lindholmen", "ceresgatan"] },
  { label: "Eriksberg, Göteborg", lat: 57.7019, lng: 11.9145, aliases: ["eriksberg", "astris"] },
  { label: "Kvillebäcken, Göteborg", lat: 57.7250, lng: 11.9480, aliases: ["kville", "kvillebäcken", "kvillebacken"] },
  { label: "Hisings Backa / St Jörgen, Göteborg", lat: 57.7429, lng: 11.9732, aliases: ["st jörgen", "st jorgen", "sankt jörgen", "hisings backa"] }
];

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function fmt(value, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  return `${value}${suffix}`;
}

function normalize(text) {
  return (text || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[–—]/g, "-");
}

function distanceKm(a, b) {
  const R = 6371;
  const dLat = (b.lat - a.lat) * Math.PI / 180;
  const dLng = (b.lng - a.lng) * Math.PI / 180;
  const lat1 = a.lat * Math.PI / 180;
  const lat2 = b.lat * Math.PI / 180;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function getPointFromAddress(input) {
  const q = normalize(input);
  const match = knownAddresses.find(a => a.aliases.some(alias => q.includes(normalize(alias))));
  return match || knownAddresses[0];
}

function sourceLinks(school) {
  return (school.sources || [])
    .map(src => `<a href="${escapeHtml(src.url)}" target="_blank" rel="noopener">${escapeHtml(src.label)}</a>`)
    .join(" · ");
}

function dataFreshness(school) {
  const fallback = school.isFallback ? `<span class="fallback-pill">${escapeHtml(school.fallbackLabel || "Fallback data")}</span>` : "";
  return `
    <div class="data-freshness" title="Ratings and admission data are year-specific.">
      <span>Data year: <strong>${escapeHtml(school.dataYear || "Unknown")}</strong></span>
      <span>Last verified: <strong>${escapeHtml(school.lastVerified || "Not verified")}</strong></span>
      ${fallback}
    </div>
  `;
}

function gradeBadgeClass(grades) {
  return grades === "F–9" ? "good" : "";
}

function nearbyFitScore(school) {
  const home = knownAddresses[0];
  const distance = distanceKm(home, school);
  const continuityBonus = school.grades === "F–9" ? 8 : 0;
  const socialContinuityBonus = school.name === "Jättestensskolan" ? 18 : 0;
  const preferredAreaBonus = ["Herrgårdsskolan", "Taubeskolan", "Lerlyckeskolan", "Innovitaskolan St Jörgen"].includes(school.name) ? 8 : 0;
  const privateQueuePenalty = school.name === "Fridaskolan Kvillebäcken" ? 35 : 0;
  return (school.qualityScore || 0) * 0.52 + (school.admissionScore || 0) * 0.22 + continuityBonus + socialContinuityBonus + preferredAreaBonus - distance * 3 - privateQueuePenalty;
}

function sortSchools(list, sortMode) {
  const copy = [...list];
  if (sortMode === "admission") {
    return copy.sort((a, b) => ((b.admissionScore || 0) - (a.admissionScore || 0)) || ((b.qualityScore || 0) - (a.qualityScore || 0)));
  }
  if (sortMode === "nearbyFit") {
    return copy.sort((a, b) => nearbyFitScore(b) - nearbyFitScore(a));
  }
  if (sortMode === "name") {
    return copy.sort((a, b) => a.name.localeCompare(b.name, "sv"));
  }
  return copy.sort((a, b) => ((b.qualityScore || 0) - (a.qualityScore || 0)) || ((b.admissionScore || 0) - (a.admissionScore || 0)));
}

function sortNote(sortMode) {
  const notes = {
    quality: "Quality-first sorting ranks by the app’s rating score, not by how easy the school is to get into.",
    admission: "Admission sorting ranks realistic access first. This can push easier-but-weaker schools higher.",
    nearbyFit: "Nearby fit is a scenario score for Långströmsgatan 6 and F0, combining distance, continuity, area fit, quality and admission realism.",
    name: "Alphabetical sorting is useful for quickly finding a known school."
  };
  return notes[sortMode] || notes.quality;
}

function schoolCard(school) {
  const typeClass = school.type === "Fristående" ? "warn" : "";
  return `
    <article class="school-card">
      <div class="card-topline">
        <h3>${escapeHtml(school.name)}</h3>
        <span class="score-chip">${fmt(school.qualityScore)}/100</span>
      </div>
      <p class="card-meta">${escapeHtml(school.area)} · ${escapeHtml(school.address)}</p>
      <div class="badges">
        <span class="badge ${typeClass}">${escapeHtml(school.type)}</span>
        <span class="badge ${gradeBadgeClass(school.grades)}">${escapeHtml(school.grades)}</span>
        <span class="badge">${escapeHtml(school.profile)}</span>
      </div>
      ${dataFreshness(school)}
      <div class="metric-row"><span>Quality / rating score</span><strong>${fmt(school.qualityScore)}/100</strong></div>
      <div class="metric-row"><span>Admission realism</span><strong>${fmt(school.admissionScore)}/100</strong></div>
      <div class="metric-row"><span>F0 satisfaction</span><strong>${fmt(school.f0Satisfaction)}/10</strong></div>
      <div class="metric-row"><span>Safety / trygghet</span><strong>${fmt(school.safety)}/10</strong></div>
      <p class="decision-note">${escapeHtml(school.decisionNote || "")}</p>
      <p class="admission-note">${escapeHtml(school.admissionNote || "")}</p>
      <p class="sources">${sourceLinks(school)}</p>
    </article>
  `;
}

function filteredSchools() {
  const q = normalize($("schoolSearch").value);
  const type = $("typeFilter").value;
  const grade = $("gradeFilter").value;
  const filtered = schools.filter(school => {
    const haystack = normalize([school.name, school.area, school.address, school.profile, school.type, school.grades].join(" "));
    const matchesSearch = !q || haystack.includes(q);
    const matchesType = type === "all" || school.type === type;
    const matchesGrade = grade === "all" || school.grades === grade;
    return matchesSearch && matchesType && matchesGrade;
  });
  return sortSchools(filtered, $("sortFilter").value);
}

function renderDirectory() {
  const mode = $("sortFilter").value;
  $("sortNote").textContent = sortNote(mode);
  const list = filteredSchools();
  $("schoolGrid").innerHTML = list.map(schoolCard).join("") || `<p class="empty">No schools match the current filters.</p>`;
}

function renderNearby() {
  const origin = getPointFromAddress($("addressInput").value);
  const nearby = schools
    .map(school => ({ ...school, distance: distanceKm(origin, school), fit: nearbyFitScore(school) }))
    .sort((a, b) => b.fit - a.fit)
    .slice(0, 6);
  $("nearbyResults").innerHTML = `
    <p class="nearby-context">Showing scenario results for <strong>${escapeHtml(origin.label)}</strong>. Distances are straight-line estimates in this MVP.</p>
    ${nearby.map((school, index) => `
      <article class="nearby-card">
        <div>
          <p class="eyebrow">Option ${index + 1}</p>
          <h3>${escapeHtml(school.name)}</h3>
          <p class="card-meta">${escapeHtml(school.type)} · ${escapeHtml(school.grades)} · ${escapeHtml(school.area)}</p>
          ${dataFreshness(school)}
          <p class="decision-note">${escapeHtml(school.decisionNote || "")}</p>
        </div>
        <div class="distance">
          <strong>${school.distance.toFixed(1)} km</strong>
          <span>fit score ${Math.round(school.fit)}</span>
        </div>
      </article>
    `).join("")}
  `;
}

function findSchoolByInput(value) {
  const q = normalize(value);
  return schools.find(s => normalize(s.name) === q) || schools.find(s => normalize(s.name).includes(q) || q.includes(normalize(s.name)));
}

function barRow(label, value, suffix = "") {
  const safe = value ?? 0;
  const pct = Math.max(0, Math.min(100, safe));
  return `
    <div class="bar-row">
      <span>${escapeHtml(label)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
      <strong>${fmt(value, suffix)}</strong>
    </div>
  `;
}

function compareCard(school) {
  return `
    <article class="compare-card">
      <h3>${escapeHtml(school.name)}</h3>
      <p class="card-meta">${escapeHtml(school.type)} · ${escapeHtml(school.grades)} · ${escapeHtml(school.area)}</p>
      ${dataFreshness(school)}
      <div class="metric-row"><span>Quality</span><strong>${fmt(school.qualityScore)}/100</strong></div>
      <div class="metric-row"><span>Admission</span><strong>${fmt(school.admissionScore)}/100</strong></div>
      <div class="metric-row"><span>F0 satisfaction</span><strong>${fmt(school.f0Satisfaction)}/10</strong></div>
      <div class="metric-row"><span>Parent satisfaction</span><strong>${fmt(school.parentSatisfaction)}/10</strong></div>
      <p class="decision-note">${escapeHtml(school.decisionNote || "")}</p>
    </article>
  `;
}

function renderCompare() {
  const selected = [...document.querySelectorAll(".compareSchool")]
    .map(input => findSchoolByInput(input.value))
    .filter(Boolean)
    .filter((school, index, arr) => arr.findIndex(s => s.slug === school.slug) === index)
    .slice(0, 3);

  if (!selected.length) {
    $("compareOutput").innerHTML = `<p class="empty">Enter one to three school names.</p>`;
    return;
  }

  const bestQuality = [...selected].sort((a, b) => (b.qualityScore || 0) - (a.qualityScore || 0))[0];
  const bestAdmission = [...selected].sort((a, b) => (b.admissionScore || 0) - (a.admissionScore || 0))[0];

  $("compareOutput").innerHTML = `
    <div class="compare-grid">${selected.map(compareCard).join("")}</div>
    <div class="chart-panel">
      <p class="chart-title">Quality score</p>
      ${selected.map(s => barRow(s.name, s.qualityScore, "/100")).join("")}
    </div>
    <div class="chart-panel">
      <p class="chart-title">Admission realism</p>
      ${selected.map(s => barRow(s.name, s.admissionScore, "/100")).join("")}
    </div>
    <div class="recommendation">
      <strong>Reading:</strong> Best quality signal here is <strong>${escapeHtml(bestQuality.name)}</strong>. Best admission-realism signal is <strong>${escapeHtml(bestAdmission.name)}</strong>. Use the cards to separate “best school” from “most realistic school”.
    </div>
  `;
}

function updateDataMode(payload) {
  const banner = $("fallbackBanner");
  const latest = metadata?.latestAvailableYear;
  const requested = payload.requestedYear;
  const fallbackCount = payload.fallbackCount || 0;
  const available = metadata?.availableYears?.join(", ") || "none";

  if (latest && latest >= TARGET_YEAR) {
    $("dataModeTitle").textContent = `Using ${latest} data where available`;
    $("dataModeCopy").textContent = `Imported years: ${available}. Schools without the target year still fall back individually.`;
  } else {
    $("dataModeTitle").textContent = `Using ${latest || "baseline"} fallback data`;
    $("dataModeCopy").textContent = `Target year is ${TARGET_YEAR}, but ${TARGET_YEAR} data has not been imported yet.`;
  }

  if (fallbackCount > 0) {
    banner.hidden = false;
    banner.innerHTML = `<strong>Year fallback active:</strong> requested ${escapeHtml(requested)}, but ${fallbackCount} school${fallbackCount === 1 ? "" : "s"} are using the latest verified prior year. Import official ${TARGET_YEAR} data to update those cards automatically.`;
  } else {
    banner.hidden = true;
  }
}

async function loadData() {
  const [metaResponse, schoolsResponse] = await Promise.all([
    fetch("/api/metadata"),
    fetch(`/api/schools?year=${TARGET_YEAR}`)
  ]);
  if (!metaResponse.ok) throw new Error("Could not load API metadata");
  if (!schoolsResponse.ok) throw new Error("Could not load school data");
  metadata = await metaResponse.json();
  const payload = await schoolsResponse.json();
  schools = payload.schools || [];
  updateDataMode(payload);
}

async function init() {
  try {
    await loadData();
  } catch (err) {
    console.error(err);
    $("dataModeTitle").textContent = "API not available";
    $("dataModeCopy").textContent = "The frontend needs the v0.4 backend service to load school data.";
    $("schoolGrid").innerHTML = `<p class="empty">Could not load backend API: ${escapeHtml(err.message)}</p>`;
    return;
  }

  $("schoolNames").innerHTML = schools.map(s => `<option value="${escapeHtml(s.name)}"></option>`).join("");
  ["schoolSearch", "typeFilter", "gradeFilter", "sortFilter"].forEach(id => $(id).addEventListener("input", renderDirectory));
  $("findNearbyBtn").addEventListener("click", renderNearby);
  $("compareBtn").addEventListener("click", renderCompare);

  renderDirectory();
  renderNearby();
  renderCompare();
}

init();
