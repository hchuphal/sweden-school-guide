let schools = [];
let metadata = null;
let currentCityPayload = null;
const YEAR_MODE = "current";
const CITY_LABELS = {
  goteborg: "Göteborg",
  stockholm: "Stockholm",
  malmo: "Malmö",
  uppsala: "Uppsala",
};
const CITY_CENTERS = {
  goteborg: { label: "Göteborg", lat: 57.7089, lng: 11.9746 },
  stockholm: { label: "Stockholm", lat: 59.3293, lng: 18.0686 },
  malmo: { label: "Malmö", lat: 55.6050, lng: 13.0038 },
  uppsala: { label: "Uppsala", lat: 59.8586, lng: 17.6389 },
};
let lastNearbyOrigin = CITY_CENTERS.goteborg;
let cityReloadTimer = null;
let cityReloadAttempts = 0;
let activeCityKey = "goteborg";
let cityLoadEpoch = 0;
let cityFetchController = null;

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

function fmt100(value) {
  return value === null || value === undefined || Number.isNaN(value) ? "n/a" : `${value}/100`;
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

async function geocodeAddress(input, cityKey) {
  const query = String(input || "").trim();
  if (!query) throw new Error("Enter an address or postal code.");
  const response = await fetch(`/api/geocode?q=${encodeURIComponent(query)}&city=${encodeURIComponent(cityKey)}`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map(item => item.msg || String(item)).join("; ")
      : payload.detail;
    throw new Error(detail || `Address lookup failed (HTTP ${response.status}).`);
  }
  return payload;
}

function sourceLinks(school) {
  const links = (school.sources || [])
    .map(src => `<a href="${escapeHtml(src.url)}" target="_blank" rel="noopener">${escapeHtml(src.label)}</a>`)
    .join(" · ");
  return links ? `<strong>Sources:</strong> ${links}` : `<strong>Sources:</strong> Not listed`;
}

function dataFreshness(school) {
  if (!school.dataYear) {
    return `
      <div class="data-freshness" title="This school is listed from the official school-unit register, but detailed rating data is not yet imported.">
        <span>School record: <strong>${escapeHtml(school.registrySource || "Official registry")}</strong></span>
        <span>Rating year: <strong>not available</strong></span>
        <span>Score confidence: <strong>No rating data</strong></span>
      </div>
    `;
  }
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

function nearbyFitScore(school, origin = lastNearbyOrigin) {
  const distance = Number.isFinite(school.distanceKm) ? school.distanceKm : distanceKm(origin, school);
  const quality = school.qualityScore ?? 45;
  const admission = school.admissionScore ?? 45;
  const continuityBonus = school.grades === "F–9" ? 8 : school.grades === "F–6" ? 4 : 0;
  return quality * 0.58 + admission * 0.18 + continuityBonus - distance * 3.2;
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
    nearbyFit: "Nearby fit uses the last successfully searched address. Before a search, it uses the selected city centre.",
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
  const label = CITY_LABELS[select.value] || select.options[select.selectedIndex]?.textContent || "Selected city";
  helper.textContent = `Current dataset: ${label}.`;
  helper.classList.remove("warn");
}

function schoolCard(school) {
  const typeClass = school.type === "Fristående" ? "warn" : "";
  return `
    <article class="school-card">
      <div class="card-topline">
        <h3>${escapeHtml(school.name)}</h3>
        <span class="score-chip">${fmt100(school.qualityScore)}</span>
      </div>
      <p class="card-meta">${escapeHtml(school.area)} · ${escapeHtml(school.address)}</p>
      <div class="badges">
        <span class="badge ${typeClass}">${escapeHtml(school.type)}</span>
        <span class="badge ${gradeBadgeClass(school.grades)}">${escapeHtml(school.grades)}</span>
        <span class="badge">${escapeHtml(school.profile)}</span>
        <span class="badge confidence ${confidenceClass(school)}">${escapeHtml(school.dataConfidenceLabel || "Unknown")} confidence</span>
      </div>
      ${dataFreshness(school)}
      <div class="metric-row"><span>Computed quality score</span><strong>${fmt100(school.qualityScore)}</strong></div>
      ${surveyRatingsBlock(school)}
      ${academicBlock(school)}
      ${admissionBlock(school)}
      ${methodSummary(school)}
      <p class="decision-note">${escapeHtml(school.decisionNote || school.verificationNote || "Official school record; detailed ratings may not yet be imported.")}</p>
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
  const cityKey = activeCityKey || $("citySelect")?.value || "goteborg";
  const cityLabel = CITY_LABELS[cityKey] || "Selected city";
  $("directoryTitle").textContent = `${cityLabel} school directory`;
  const qualityCount = schools.filter(s => Number(s.dataCompletenessPct || 0) > 0).length;
  $("directoryMeta").textContent = schools.length
    ? `${schools.length} schools loaded · ${qualityCount} with published survey/academic metrics`
    : `No schools loaded yet for ${cityLabel}`;

  const surveySync = currentCityPayload?.surveySync || metadata?.surveySync || {};
  const status = $("directoryStatus");
  let surveyNotice = "";
  if (schools.length && qualityCount === 0 && surveySync.status === "running") {
    surveyNotice = `<strong>Adding national survey data:</strong> the app is matching the official 2026 and 2025 Skolenkäten files to ${escapeHtml(cityLabel)} schools. Cards refresh automatically.`;
  } else if (schools.length && qualityCount === 0 && surveySync.status === "failed") {
    surveyNotice = `<strong>Survey enrichment failed:</strong> ${escapeHtml(surveySync.error || "the official survey files could not be downloaded or matched")}. The school directory remains available, but quality scores stay n/a until a successful refresh.`;
  } else if (schools.length && qualityCount === 0 && ["complete", "partial"].includes(surveySync.status)) {
    surveyNotice = `<strong>No matched quality metrics yet:</strong> the directory records are loaded, but none of these schools matched a published 2025/2026 survey or academic record. Missing or privacy-suppressed values remain n/a.`;
  }
  if (status) {
    status.hidden = !surveyNotice;
    status.innerHTML = surveyNotice;
  }
  const cards = list.map(schoolCard).join("") || `<p class="empty">No schools match the current filters.</p>`;
  $("schoolGrid").innerHTML = cards;
}

async function renderNearby() {
  const input = $("addressInput").value;
  const selectedCity = $("citySelect")?.value || "goteborg";
  $("nearbyResults").innerHTML = `<p class="nearby-context">Looking up the address and nearby schools…</p>`;

  try {
    const response = await fetch(`/api/nearby?q=${encodeURIComponent(input)}&city=${encodeURIComponent(selectedCity)}&year=${encodeURIComponent(YEAR_MODE)}&limit=12&radius_km=30`);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(payload.detail)
        ? payload.detail.map(item => item.msg || String(item)).join("; ")
        : payload.detail;
      throw new Error(detail || `Nearby search failed (HTTP ${response.status}).`);
    }
    const result = payload.geocode || {};
    if (!result.found) {
      $("nearbyResults").innerHTML = `<div class="notice"><strong>Address not found:</strong> ${escapeHtml(result.message || "Try a full Swedish street address or postal code.")}${result.queryWarning ? `<br><span>${escapeHtml(result.queryWarning)}</span>` : ""}</div>`;
      return;
    }

    if (payload.autoSwitched && payload.matchedCityKey && CITY_LABELS[payload.matchedCityKey]) {
      $("citySelect").value = payload.matchedCityKey;
      await loadSchoolsForCity(payload.matchedCityKey);
      updateCityNotice();
    }

    lastNearbyOrigin = {
      label: result.displayName || input,
      lat: Number(result.lat),
      lng: Number(result.lng),
    };

    const nearby = payload.schools || [];
    if (!nearby.length) {
      $("nearbyResults").innerHTML = `
        <div class="notice">
          <strong>No nearby schools returned:</strong> ${escapeHtml(payload.message || "The address is outside the loaded regions or school coordinates are unavailable.")}
          <br><span>Matched address: ${escapeHtml(result.displayName || input)}</span>
          ${payload.trackedSchoolCount !== undefined ? `<br><span>Coordinate coverage: ${escapeHtml(payload.coordinateSchoolCount || 0)} of ${escapeHtml(payload.trackedSchoolCount || 0)} loaded schools.</span>` : ""}
        </div>`;
      return;
    }

    const queryWarning = result.queryWarning
      ? `<div class="notice nearby-warning"><strong>Address adjustment:</strong> ${escapeHtml(result.queryWarning)}</div>`
      : "";
    const postcodeWarning = result.postcodeWarning
      ? `<div class="notice nearby-warning"><strong>Location note:</strong> ${escapeHtml(result.postcodeWarning)}</div>`
      : "";
    const discoveryNote = payload.mapDiscovery?.status === "failed"
      ? `<div class="notice nearby-warning"><strong>Map discovery unavailable:</strong> ${escapeHtml(payload.mapDiscovery.error || "Only tracked schools could be returned for this search.")}</div>`
      : "";
    $("nearbyResults").innerHTML = `
      <p class="nearby-context">Matched <strong>${escapeHtml(result.displayName || input)}</strong>${result.postalCode ? ` · postal code ${escapeHtml(result.postalCode)}` : ""}${result.approximate ? " · approximate postcode point" : ""}. ${escapeHtml(payload.message || "")} Distances are straight-line estimates.</p>
      ${queryWarning}
      ${postcodeWarning}
      ${discoveryNote}
      ${nearby.map((school, index) => `
        <article class="nearby-card ${school.mapDiscovered ? "map-school-card" : ""}">
          <div>
            <p class="eyebrow">Option ${index + 1}</p>
            <h3>${escapeHtml(school.name)}</h3>
            <div class="badges nearby-badges">
              ${school.mapDiscovered ? `<span class="badge warn">Nearby map discovery</span>` : `<span class="badge good">Tracked school record</span>`}
              ${school.mapConfirmed ? `<span class="badge">Map location confirmed</span>` : ""}
            </div>
            <p class="card-meta">${escapeHtml(school.type || "Type not verified")} · ${escapeHtml(school.grades || "Grades not verified")} · ${escapeHtml(school.municipality || school.area || "Area not verified")}</p>
            ${school.address ? `<p class="card-meta">${escapeHtml(school.address)}</p>` : ""}
            ${dataFreshness(school)}
            <div class="metric-row"><span>Computed quality</span><strong>${fmt100(school.qualityScore)}</strong></div>
            <div class="metric-row"><span>Admission realism</span><strong>${fmt100(school.admissionScore)}</strong></div>
            <p class="decision-note">${escapeHtml(school.decisionNote || school.verificationNote || "School record found near the address; detailed ratings may not yet be matched.")}</p>
            <p class="sources">${sourceLinks(school)}</p>
          </div>
          <div class="distance">
            <strong>${Number(school.distanceKm).toFixed(1)} km</strong>
            <span>straight-line</span>
          </div>
        </article>
      `).join("")}
    `;
    renderDirectory();
  } catch (err) {
    $("nearbyResults").innerHTML = `<div class="notice"><strong>Address lookup failed:</strong> ${escapeHtml(err.message || "Please try again.")}</div>`;
  }
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
      <div class="metric-row"><span>Computed quality</span><strong>${fmt100(school.qualityScore)}</strong></div>
      ${surveyRatingsBlock(school)}
      ${academicBlock(school)}
      ${admissionBlock(school)}
      ${methodSummary(school)}
      <p class="decision-note">${escapeHtml(school.decisionNote || school.verificationNote || "Official school record; detailed ratings may not yet be imported.")}</p>
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

function updateCompareDefaults() {
  const inputs = [...document.querySelectorAll(".compareSchool")];
  const availableNames = new Set(schools.map(s => normalize(s.name)));
  inputs.forEach((input, index) => {
    if (!availableNames.has(normalize(input.value))) {
      input.value = schools[index]?.name || "";
    }
  });
}

function scheduleCityReload(cityKey, payload, epoch = cityLoadEpoch) {
  if (cityReloadTimer) clearTimeout(cityReloadTimer);
  if (cityKey !== activeCityKey || $("citySelect")?.value !== cityKey || epoch !== cityLoadEpoch) {
    return;
  }
  const registryStatus = payload?.registrySync?.status;
  const surveyStatus = payload?.surveySync?.status;
  const needsRegistry = !schools.length && (registryStatus === "running" || payload?.syncTriggered);
  const needsSurvey = schools.length > 0 && Number(payload?.qualityMetricCount || 0) === 0 &&
    (surveyStatus === "running" || payload?.surveySyncTriggered);
  if ((!needsRegistry && !needsSurvey) || cityReloadAttempts >= 40) {
    cityReloadAttempts = 0;
    return;
  }
  cityReloadAttempts += 1;
  cityReloadTimer = setTimeout(async () => {
    if (cityKey !== activeCityKey || $("citySelect")?.value !== cityKey || epoch !== cityLoadEpoch) {
      return;
    }
    try {
      await loadSchoolsForCity(cityKey, true, epoch);
    } catch (err) {
      if (err?.name !== "AbortError") console.error(err);
    }
  }, 3000);
}

async function loadSchoolsForCity(cityKey, isPoll = false, expectedEpoch = null) {
  const selectedCity = $("citySelect")?.value || cityKey;
  if (isPoll && (selectedCity !== cityKey || activeCityKey !== cityKey || expectedEpoch !== cityLoadEpoch)) {
    return null;
  }

  let requestEpoch;
  if (!isPoll) {
    activeCityKey = cityKey;
    cityLoadEpoch += 1;
    requestEpoch = cityLoadEpoch;
    cityReloadAttempts = 0;
    if (cityReloadTimer) clearTimeout(cityReloadTimer);
    if (cityFetchController) cityFetchController.abort();
    currentCityPayload = null;
    schools = [];
    const status = $("directoryStatus");
    if (status) status.hidden = true;
    $("schoolGrid").innerHTML = `<p class="empty">Loading ${escapeHtml(CITY_LABELS[cityKey] || cityKey)} schools…</p>`;
    $("directoryTitle").textContent = `${CITY_LABELS[cityKey] || "Selected city"} school directory`;
    $("directoryMeta").textContent = "Loading official school records…";
  } else {
    requestEpoch = expectedEpoch ?? cityLoadEpoch;
  }

  const controller = new AbortController();
  cityFetchController = controller;
  const schoolsResponse = await fetch(
    `/api/schools?year=${encodeURIComponent(YEAR_MODE)}&city=${encodeURIComponent(cityKey)}&_=${Date.now()}`,
    { cache: "no-store", signal: controller.signal }
  );
  if (!schoolsResponse.ok) throw new Error("Could not load school data");
  const payload = await schoolsResponse.json();

  // A previous city request may finish after the user has switched cities. Never
  // let that stale response replace the currently selected directory.
  if (requestEpoch !== cityLoadEpoch || cityKey !== activeCityKey || $("citySelect")?.value !== cityKey) {
    return null;
  }
  if (payload.city !== cityKey) {
    throw new Error(`The backend returned ${payload.city || "another city"} while ${cityKey} was requested.`);
  }
  const returnedSchools = payload.schools || [];
  const mismatched = returnedSchools.filter(school => school.cityKey && school.cityKey !== cityKey);
  if (mismatched.length) {
    throw new Error(`The backend returned ${mismatched.length} school record(s) from another city.`);
  }

  currentCityPayload = payload;
  schools = returnedSchools;
  updateDataMode(payload);
  $("schoolNames").innerHTML = schools.map(s => `<option value="${escapeHtml(s.name)}"></option>`).join("");
  lastNearbyOrigin = CITY_CENTERS[cityKey] || lastNearbyOrigin;
  updateCompareDefaults();
  renderDirectory();
  renderCompare();

  if (!schools.length) {
    const sync = payload.registrySync || {};
    const cityLabel = CITY_LABELS[cityKey] || cityKey;
    const status = $("directoryStatus");
    let message;
    if (sync.status === "running" || payload.syncTriggered) {
      message = `<strong>Loading ${escapeHtml(cityLabel)} schools:</strong> the official Skolverket registry sync is running. This page will retry automatically.`;
    } else if (sync.status === "failed") {
      message = `<strong>Registry sync failed:</strong> ${escapeHtml(sync.error || "Could not download the official school register.")} Redeploy or retry later.`;
    } else {
      const currentSync = metadata?.registrySync || {};
      const reason = currentSync.status === "failed"
        ? `The live registry refresh failed: ${escapeHtml(currentSync.error || "unknown error")}.`
        : "No bundled or live registry records are available for this city.";
      message = `<strong>No schools loaded for ${escapeHtml(cityLabel)}.</strong> ${reason}`;
    }
    if (status) {
      status.hidden = false;
      status.innerHTML = message;
    }
    $("schoolGrid").innerHTML = "";
  }
  scheduleCityReload(cityKey, payload, requestEpoch);
  return payload;
}

async function loadData() {
  const metaResponse = await fetch("/api/metadata");
  if (!metaResponse.ok) throw new Error("Could not load API metadata");
  metadata = await metaResponse.json();
  const selectedCity = $("citySelect")?.value || "goteborg";
  return loadSchoolsForCity(selectedCity);
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

  ["schoolSearch", "typeFilter", "gradeFilter", "sortFilter"].forEach(id => $(id).addEventListener("input", renderDirectory));
  $("citySelect")?.addEventListener("change", async () => {
    updateCityNotice();
    $("schoolSearch").value = "";
    $("typeFilter").value = "all";
    $("gradeFilter").value = "all";
    $("nearbyResults").innerHTML = `<p class="nearby-context">Enter an address in ${escapeHtml(CITY_LABELS[$("citySelect").value] || "the selected city")} to find nearby schools.</p>`;
    try {
      await loadSchoolsForCity($("citySelect").value);
    } catch (err) {
      if (err?.name === "AbortError") return;
      $("schoolGrid").innerHTML = `<p class="empty">Could not load the selected city: ${escapeHtml(err.message)}</p>`;
    }
  });
  $("findNearbyBtn").addEventListener("click", renderNearby);
  $("compareBtn").addEventListener("click", renderCompare);

  updateCityNotice();
  renderMethodology();
  renderDirectory();
  renderNearby();
  renderCompare();
}

init();
