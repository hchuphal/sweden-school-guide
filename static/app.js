let schools = [];
let metadata = null;
const YEAR_MODE = "current";
const SUPPORTED_CITY = "goteborg";

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
  const links = (school.sources || [])
    .map(src => `<a href="${escapeHtml(src.url)}" target="_blank" rel="noopener">${escapeHtml(src.label)}</a>`)
    .join(" · ");
  return links ? `<strong>Sources:</strong> ${links}` : `<strong>Sources:</strong> Not listed`;
}

function dataFreshness(school) {
  const fallback = school.isFallback ? `<span class="fallback-pill">${escapeHtml(school.fallbackLabel || "Fallback data")}</span>` : "";
  return `
    <div class="data-freshness" title="Ratings and admission data are year-specific.">
      <span>Data year: <strong>${escapeHtml(school.dataYear || "Unknown")}</strong></span>
      <span>Last verified: <strong>${escapeHtml(school.lastVerified || "Not verified")}</strong></span>
      <span>Score confidence: <strong>${escapeHtml(school.dataConfidenceLabel || "Unknown")}</strong> (${fmt(school.dataCompletenessPct, "%")} data)</span>
      ${fallback}
    </div>
  `;
}

function gradeBadgeClass(grades) {
  return grades === "F–9" ? "good" : "";
}

function confidenceClass(school) {
  const label = normalize(school.dataConfidenceLabel);
  if (label.includes("high")) return "good";
  if (label.includes("medium")) return "medium";
  return "low";
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
  if (sortMode === "confidence") {
    return copy.sort((a, b) => ((b.dataCompletenessPct || 0) - (a.dataCompletenessPct || 0)) || ((b.qualityScore || 0) - (a.qualityScore || 0)));
  }
  if (sortMode === "name") {
    return copy.sort((a, b) => a.name.localeCompare(b.name, "sv"));
  }
  return copy.sort((a, b) => ((b.qualityScore || 0) - (a.qualityScore || 0)) || ((b.dataCompletenessPct || 0) - (a.dataCompletenessPct || 0)) || ((b.admissionScore || 0) - (a.admissionScore || 0)));
}

function sortNote(sortMode) {
  const notes = {
    quality: "Quality-first sorting uses the computed score. Admission chance is deliberately excluded.",
    admission: "Admission sorting ranks realistic access first. This can push easier-but-weaker schools higher.",
    nearbyFit: "Nearby fit is a scenario score for the address entered in the nearby section. In the MVP sort menu, the sample scenario uses Långströmsgatan 6 for F0.",
    confidence: "Data confidence sorting shows schools with the most complete rating fields first. It does not mean the school is best.",
    name: "Alphabetical sorting is useful for quickly finding a known school."
  };
  return notes[sortMode] || notes.quality;
}

function methodSummary(school) {
  const missing = (school.missingQualityFields || []).length;
  const confidence = `${escapeHtml(school.dataConfidenceLabel || "Unknown")} · ${fmt(school.dataCompletenessPct, "%")} complete`;
  const rows = (school.qualityBreakdown || [])
    .map(item => {
      const valueText = item.value === null || item.value === undefined
        ? `missing → neutral ${fmt(item.usedValue, "/10")}`
        : fmt(item.value, "/10");
      const cls = item.status === "available" ? "available" : "missing";
      return `<div class="method-row ${cls}"><span>${escapeHtml(item.label)} <small>${fmt(item.weight, "%")}</small></span><strong>${escapeHtml(valueText)}</strong></div>`;
    })
    .join("");
  return `
    <details class="method-details">
      <summary>How computed quality score is calculated</summary>
      <p>Computed by backend from survey ratings, academic signal and data confidence. Missing values use neutral 6.5/10 and reduce the confidence score. Admission realism is separate.</p>
      <div class="method-meta"><span>${confidence}</span><span>${missing} missing field${missing === 1 ? "" : "s"}</span></div>
      ${rows}
    </details>
  `;
}

function metricCell(label, value, suffix = "/10") {
  return `<div class="metric-cell"><span>${escapeHtml(label)}</span><strong>${fmt(value, suffix)}</strong></div>`;
}

function insightBlock(title, sourceLabel, rows, note = "") {
  return `
    <details class="insight-details">
      <summary class="insight-summary">
        <span class="summary-main">${escapeHtml(title)}</span>
        <span class="summary-meta">${escapeHtml(sourceLabel)}</span>
      </summary>
      <div class="insight-block">
        <div class="metric-grid">${rows.join("")}</div>
        ${note ? `<p class="block-note">${escapeHtml(note)}</p>` : ""}
      </div>
    </details>
  `;
}

function surveyRatingsBlock(school) {
  return insightBlock(
    "Skolenkäten survey ratings",
    "Parent and pupil survey data",
    [
      metricCell("F0 satisfaction", school.f0Satisfaction),
      metricCell("Safety / trygghet", school.safety),
      metricCell("Study peace / studiero", school.studyPeace),
      metricCell("Support / stöd", school.support),
      metricCell("Student satisfaction", school.studentSatisfaction),
      metricCell("Parent satisfaction", school.parentSatisfaction),
    ],
    "Survey values are shown where published for the school/year. Missing values reduce data confidence."
  );
}

function academicBlock(school) {
  return insightBlock(
    "Academic results",
    "Separate from Skolenkäten",
    [
      metricCell("Academic score", school.academicScore, "/10"),
      metricCell("Quality contribution", school.qualityBreakdown?.find(x => x.key === "academicScore")?.contribution, " pts"),
    ],
    school.academicSignal || "Academic indicator not yet imported for this school."
  );
}

function admissionBlock(school) {
  return insightBlock(
    "Admission realism",
    "Separate from quality score",
    [
      metricCell("Admission realism", school.admissionScore, "/100"),
      metricCell("Data confidence", school.dataCompletenessPct, "%"),
    ],
    school.admissionNote || "Admission rules should be verified with the school or municipality."
  );
}

function updateCityNotice() {
  const select = $("citySelect");
  const helper = $("cityHelper");
  if (!select || !helper) return;
  if (select.value === SUPPORTED_CITY) {
    helper.textContent = "Current dataset: Göteborg. The city selector is ready for future expansion.";
    helper.classList.remove("warn");
  } else {
    const label = select.options[select.selectedIndex]?.textContent || "Selected city";
    helper.textContent = `${label} is not loaded yet. Showing Göteborg data for now.`;
    helper.classList.add("warn");
  }
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
        <span class="badge confidence ${confidenceClass(school)}">${escapeHtml(school.dataConfidenceLabel || "Unknown")} confidence</span>
      </div>
      ${dataFreshness(school)}
      <div class="metric-row"><span>Computed quality score</span><strong>${fmt(school.qualityScore)}/100</strong></div>
      ${surveyRatingsBlock(school)}
      ${academicBlock(school)}
      ${admissionBlock(school)}
      ${methodSummary(school)}
      <p class="decision-note">${escapeHtml(school.decisionNote || "")}</p>
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
          <div class="metric-row"><span>Computed quality</span><strong>${fmt(school.qualityScore)}/100</strong></div>
          <div class="metric-row"><span>Admission realism</span><strong>${fmt(school.admissionScore)}/100</strong></div>
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

function barRow(label, value, suffix = "", max = 100) {
  const safe = value ?? 0;
  const pct = Math.max(0, Math.min(100, (safe / max) * 100));
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
      <div class="metric-row"><span>Computed quality</span><strong>${fmt(school.qualityScore)}/100</strong></div>
      ${surveyRatingsBlock(school)}
      ${academicBlock(school)}
      ${admissionBlock(school)}
      ${methodSummary(school)}
      <p class="decision-note">${escapeHtml(school.decisionNote || "")}</p>
      <p class="sources">${sourceLinks(school)}</p>
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
  const bestConfidence = [...selected].sort((a, b) => (b.dataCompletenessPct || 0) - (a.dataCompletenessPct || 0))[0];

  $("compareOutput").innerHTML = `
    <div class="compare-grid">${selected.map(compareCard).join("")}</div>
    <div class="chart-panel">
      <p class="chart-title">Computed quality score</p>
      ${selected.map(s => barRow(s.name, s.qualityScore, "/100")).join("")}
    </div>
    <div class="chart-panel">
      <p class="chart-title">Admission realism</p>
      ${selected.map(s => barRow(s.name, s.admissionScore, "/100")).join("")}
    </div>
    <div class="chart-panel">
      <p class="chart-title">Data completeness</p>
      ${selected.map(s => barRow(s.name, s.dataCompletenessPct, "%")).join("")}
    </div>
    <div class="recommendation">
      <strong>Reading:</strong> Best quality signal here is <strong>${escapeHtml(bestQuality.name)}</strong>. Best admission-realism signal is <strong>${escapeHtml(bestAdmission.name)}</strong>. Best data completeness is <strong>${escapeHtml(bestConfidence.name)}</strong>. Quality and admission are separate scores.
    </div>
  `;
}

function renderMethodology() {
  if (!metadata?.qualityFormula) return;
  const formula = metadata.qualityFormula;
  const rows = (formula.weights || [])
    .map(item => `<div class="method-row available"><span>${escapeHtml(item.label)}</span><strong>${fmt(item.weight, "%")}</strong></div>`)
    .join("");
  $("methodologyPanel").innerHTML = `
    <h3>Quality score formula</h3>
    <p>The score is calculated by the backend from three separated source blocks in the UI: Skolenkäten survey ratings, academic results and data confidence. Admission realism is intentionally excluded so schools are not ranked higher just because they are easier to get into.</p>
    <div class="method-list">${rows}</div>
    <div class="method-row available"><span>Data confidence</span><strong>${fmt(formula.dataConfidenceWeight, "%")}</strong></div>
    <p class="method-note">Missing rating values use neutral ${fmt(formula.missingValueBaseline, "/10")} and reduce the confidence label. This prevents a school with missing data from looking either unfairly excellent or unfairly poor.</p>
  `;
}

function updateDataMode(payload) {
  const banner = $("fallbackBanner");
  const currentYear = payload.currentDataYear || metadata?.currentDataYear || metadata?.latestAvailableYear;
  const fallbackCount = payload.fallbackCount || 0;
  const available = metadata?.availableYears?.join(", ") || "none";

  $("dataModeTitle").textContent = currentYear ? `Using ${currentYear} data` : "No school data loaded";
  $("dataModeCopy").textContent = currentYear
    ? `Current imported year: ${currentYear}. Imported years: ${available}. When a newer official import is added, the app will use it automatically.`
    : "Import school-rating data to begin.";

  if (fallbackCount > 0) {
    banner.hidden = false;
    banner.innerHTML = `<strong>Some schools use earlier data:</strong> the current imported year is ${escapeHtml(currentYear)}, but ${fallbackCount} school${fallbackCount === 1 ? "" : "s"} do not yet have a ${escapeHtml(currentYear)} record, so the app uses their latest verified prior-year record.`;
  } else {
    banner.hidden = true;
  }
}

async function loadData() {
  const [metaResponse, schoolsResponse] = await Promise.all([
    fetch("/api/metadata"),
    fetch(`/api/schools?year=${encodeURIComponent(YEAR_MODE)}`)
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
    $("dataModeCopy").textContent = "The frontend needs the backend service to load school data.";
    $("schoolGrid").innerHTML = `<p class="empty">Could not load backend API: ${escapeHtml(err.message)}</p>`;
    return;
  }

  $("schoolNames").innerHTML = schools.map(s => `<option value="${escapeHtml(s.name)}"></option>`).join("");
  ["schoolSearch", "typeFilter", "gradeFilter", "sortFilter"].forEach(id => $(id).addEventListener("input", renderDirectory));
  $("citySelect")?.addEventListener("change", updateCityNotice);
  $("findNearbyBtn").addEventListener("click", renderNearby);
  $("compareBtn").addEventListener("click", renderCompare);

  updateCityNotice();
  renderMethodology();
  renderDirectory();
  renderNearby();
  renderCompare();
}

init();
