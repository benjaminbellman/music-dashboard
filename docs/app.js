// Apple Music Stats — static dashboard.
// Fetches data/aggregates.json, renders tabs with Observable Plot via ESM CDN.

import * as Plot from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6/+esm";
import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7/+esm";
import * as topojson from "https://cdn.jsdelivr.net/npm/topojson-client@3/+esm";

// ─────────────── Flags & country names ───────────────
const countryNames = {
  US:"United States", GB:"United Kingdom", FR:"France", DE:"Germany", JP:"Japan",
  CA:"Canada", AU:"Australia", NL:"Netherlands", SE:"Sweden", NO:"Norway",
  IT:"Italy", ES:"Spain", BR:"Brazil", RU:"Russia", MX:"Mexico", CO:"Colombia",
  BE:"Belgium", IL:"Israel", IE:"Ireland", CH:"Switzerland", DK:"Denmark",
  AT:"Austria", KR:"South Korea", NG:"Nigeria", IN:"India", UA:"Ukraine",
  JM:"Jamaica", CN:"China", NZ:"New Zealand", AR:"Argentina", FI:"Finland",
  GR:"Greece", ZA:"South Africa", PT:"Portugal", TR:"Turkey", PY:"Paraguay",
  CL:"Chile", EE:"Estonia", DZ:"Algeria", LT:"Lithuania", KZ:"Kazakhstan",
  VE:"Venezuela", HU:"Hungary", RO:"Romania", MD:"Moldova", IS:"Iceland",
  BY:"Belarus", EG:"Egypt", MA:"Morocco", HK:"Hong Kong", TW:"Taiwan",
  SI:"Slovenia", PA:"Panama", LR:"Liberia", BB:"Barbados", SR:"Suriname",
  XK:"Kosovo"
};
function flag(cc) {
  if (!cc || typeof cc !== "string" || !/^[A-Z]{2}$/.test(cc)) return "";
  const base = 0x1F1E6;
  return String.fromCodePoint(base + cc.charCodeAt(0) - 65, base + cc.charCodeAt(1) - 65);
}
const countryName = cc => countryNames[cc] || cc;

// ─────────────── Formatting ───────────────
const fmtInt = n => n == null ? "—" : n.toLocaleString("en-US");
const fmtDec = (n, d=1) => n == null ? "—" : Number(n).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });

// ─────────────── Tab router ───────────────
function showTab(name) {
  document.querySelectorAll(".tabs a").forEach(a => a.classList.toggle("active", a.dataset.tab === name));
  document.querySelectorAll(".page").forEach(p => p.hidden = p.id !== `page-${name}`);
  window.scrollTo(0, 0);
}
function routeFromHash() {
  const h = location.hash.replace("#", "") || "overview";
  const valid = ["overview","insights","artists","countries","timeline","genres","tracker","pending"];
  showTab(valid.includes(h) ? h : "overview");
}
window.addEventListener("hashchange", routeFromHash);

// ─────────────── Main ───────────────
(async function main() {
  const data = await fetch("./data/aggregates.json", { cache: "no-cache" }).then(r => r.json());
  _allTracks = data.tracks;
  document.getElementById("loading").hidden = true;
  document.querySelectorAll(".page").forEach(p => p.hidden = false);
  routeFromHash();
  document.getElementById("build-date").textContent = new Date().toISOString().slice(0, 10);

  initDrillPanel();
  renderOverview(data);
  renderArtists(data);
  await renderCountries(data);
  renderTimeline(data);
  renderGenres(data);
  renderTracker(data);
  renderPending(data);
  renderInsights(data);
  initRefreshButton();
})().catch(err => {
  document.getElementById("loading").innerHTML = `<div style="color:#fca5a5">Failed to load: ${err.message}</div>`;
});

// ─────────────── Insights ───────────────
function renderInsights(data) {
  const factEl = document.getElementById("insight-fact");
  const rerollBtn = document.getElementById("fact-reroll");
  const evolEl = document.getElementById("insight-evolution");

  const facts = buildFacts(data);
  const pickFact = () => {
    if (!facts.length) { factEl.textContent = "Not enough data for fun facts yet."; return; }
    factEl.innerHTML = facts[Math.floor(Math.random() * facts.length)];
  };
  pickFact();
  rerollBtn?.addEventListener("click", pickFact);

  evolEl.innerHTML = buildEvolution(data);

  // Ask form: example chips pre-fill the input
  const input = document.querySelector('#ask-form input[name="q"]');
  document.querySelectorAll(".example-ask").forEach(a => {
    a.addEventListener("click", e => {
      e.preventDefault();
      if (input) { input.value = a.textContent.trim(); input.focus(); }
    });
  });

  renderTrends(data);
}

// ─────────────── Recent listening trends ───────────────
function renderTrends(data) {
  const ph = data.play_history;
  const trendsSub = document.getElementById("trends-sub");
  const chartEl = document.getElementById("chart-plays-trend");
  const artistsEl = document.getElementById("trend-artists");
  const genresEl = document.getElementById("trend-genres");
  const tracksEl = document.getElementById("trend-tracks");
  const toolbar = document.getElementById("trend-windows");
  if (!toolbar || !chartEl) return;

  if (!ph || !ph.snapshot_count || ph.snapshot_count < 2) {
    trendsSub.textContent = "Trend data starts collecting today. Come back after the next sync to see plays-since-last-sync here.";
    chartEl.innerHTML = '<div class="sub" style="padding:1.25rem;text-align:center;">Need at least two snapshots to compute deltas.</div>';
    [artistsEl, genresEl, tracksEl].forEach(e => e.innerHTML = '<div class="sub" style="padding:1rem">—</div>');
    return;
  }

  trendsSub.innerHTML = `${ph.snapshot_count} snapshots so far · first <em>${ph.first_snapshot}</em> · latest <em>${ph.latest_snapshot}</em>. Each sync pins the play counter; deltas are what you've actually been listening to since the project started.`;

  const state = { window: "14d" };

  function paint() {
    // Highlight active pill
    toolbar.querySelectorAll(".pill-btn").forEach(b => {
      b.classList.toggle("active", b.dataset.window === state.window);
    });

    const win = ph.windows[state.window] || { top_artists: [], top_genres: [], top_tracks: [], days: 0, total_plays: 0 };

    // Filter daily timeline to the window
    const cutoff = new Date(ph.latest_snapshot);
    cutoff.setDate(cutoff.getDate() - win.days);
    const cutoffISO = cutoff.toISOString().slice(0, 10);
    const dailyInWindow = ph.daily.filter(x => x.date > cutoffISO);

    chartEl.innerHTML = "";
    if (!dailyInWindow.length) {
      chartEl.innerHTML = `<div class="sub" style="padding:1.25rem;text-align:center;">No plays recorded in the last ${win.days} days.</div>`;
    } else {
      chartEl.appendChild(Plot.plot({
        marginLeft: 50, marginTop: 16, marginRight: 16, marginBottom: 32,
        height: 220,
        x: { label: null, type: "band", tickFormat: d => d.slice(5) },
        y: { label: "plays ↑", grid: true, tickFormat: d3.format(",") },
        style: { background: "transparent", color: "var(--fg)" },
        marks: [
          Plot.barY(dailyInWindow, {
            x: "date", y: "plays",
            fill: "var(--accent)",
            tip: true, title: d => `${d.date}: ${d.plays} plays since previous sync`,
          }),
          Plot.ruleY([0], { stroke: "var(--border)" }),
        ],
      }));
    }

    // Top tables (clickable)
    const artistRows = win.top_artists.map((r, i) => ({ rank: i + 1, ...r }));
    const genreRows  = win.top_genres.map((r, i) => ({ rank: i + 1, ...r }));
    const trackRows  = win.top_tracks.map((r, i) => ({ rank: i + 1, ...r }));

    mount("trend-artists", tableEl(artistRows, [
      { key: "rank", label: "#", num: true },
      { key: "artist", label: "Artist" },
      { key: "plays", label: "Plays", num: true, render: fmtInt },
    ], { onRowClick: r => drillArtist(r.artist) }));

    mount("trend-genres", tableEl(genreRows, [
      { key: "rank", label: "#", num: true },
      { key: "genre", label: "Genre" },
      { key: "plays", label: "Plays", num: true, render: fmtInt },
    ], { onRowClick: r => drillGenre(r.genre) }));

    mount("trend-tracks", tableEl(trackRows, [
      { key: "rank", label: "#", num: true },
      { key: "song", label: "Song" },
      { key: "artist", label: "Artist" },
      { key: "plays", label: "Plays", num: true, render: fmtInt },
    ], { onRowClick: r => drillArtist(r.artist) }));
  }

  toolbar.querySelectorAll(".pill-btn").forEach(b => {
    b.addEventListener("click", () => { state.window = b.dataset.window; paint(); });
  });

  paint();
}

function buildFacts(data) {
  const k = data.kpis;
  const tracks = data.tracks || [];
  const topArtistsByPlays = data.top_artists?.by_play_count || [];
  const topArtistsBySongs = data.top_artists?.by_song_count || [];
  const topCountries = data.country_plays || [];
  const topGenres = data.genre_plays || [];
  const yearArtists = data.year_artist || [];
  const facts = [];

  // #1 all-time song
  const topSong = [...tracks].sort((a, b) => (b.plays || 0) - (a.plays || 0))[0];
  if (topSong) {
    facts.push(`Your all-time #1 song is <strong>${escapeHTML(topSong.song)}</strong> by <strong>${escapeHTML(topSong.artist)}</strong>, played <strong>${fmtInt(topSong.plays)}</strong> times.`);
  }

  // Most-played artist overall
  if (topArtistsByPlays[0]) {
    const a = topArtistsByPlays[0];
    facts.push(`You've played <strong>${escapeHTML(a.artist)}</strong> ${a.country ? flag(a.country) + " " : ""}<strong>${fmtInt(a.plays)}</strong> times across your library.`);
  }

  // Artist with the most songs
  if (topArtistsBySongs[0]) {
    const a = topArtistsBySongs[0];
    facts.push(`<strong>${escapeHTML(a.artist)}</strong> has <strong>${a.count}</strong> songs in your library — more than any other artist.`);
  }

  // Top country by plays
  if (topCountries[0]) {
    const c = topCountries[0];
    const pct = (c.plays / k.total_plays * 100).toFixed(0);
    facts.push(`${flag(c.country)} <strong>${countryName(c.country)}</strong> dominates your library with <strong>${pct}%</strong> of all plays (${fmtInt(c.plays)} / ${fmtInt(k.total_plays)}).`);
  }

  // Number of countries
  if (topCountries.length) {
    facts.push(`Your library pulls from <strong>${topCountries.length} countries</strong> — the rarest is ${flag(topCountries.at(-1).country)} ${countryName(topCountries.at(-1).country)} with just ${topCountries.at(-1).plays} plays.`);
  }

  // Top genre
  if (topGenres[0]) {
    const g = topGenres[0];
    const pct = (g.plays / k.total_plays * 100).toFixed(0);
    facts.push(`<strong>${escapeHTML(g.genre)}</strong> is your top genre — <strong>${pct}%</strong> of plays (${fmtInt(g.plays)} across ${g.songs} songs).`);
  }

  // Biggest year addition
  const byYearAdded = new Map();
  for (const t of tracks) {
    if (!t.date_added) continue;
    const y = +t.date_added.slice(0, 4);
    byYearAdded.set(y, (byYearAdded.get(y) || 0) + (t.plays || 0));
  }
  if (byYearAdded.size) {
    const [year, plays] = [...byYearAdded.entries()].sort((a, b) => b[1] - a[1])[0];
    facts.push(`Your biggest year was <strong>${year}</strong> — you added songs that year and have played them <strong>${fmtInt(plays)}</strong> times since.`);
  }

  // Year a genre exploded
  const genreYear = data.genre_year || [];
  if (genreYear.length) {
    // Find the year+genre with the single highest plays (non-Other)
    const best = genreYear.filter(r => r.genre !== "Other")
      .sort((a, b) => b.plays - a.plays)[0];
    if (best) {
      facts.push(`<strong>${escapeHTML(best.genre)}</strong> peaked for you in <strong>${best.year}</strong> — ${fmtInt(best.plays)} plays from songs you added that year.`);
    }
  }

  // Estimated listening time
  const totalSec = tracks.reduce((s, t) => s + ((t.duration_sec || 0) * (t.plays || 0)), 0);
  if (totalSec > 0) {
    const h = Math.round(totalSec / 3600);
    const days = (h / 24).toFixed(1);
    facts.push(`Estimated total listening time: <strong>${fmtInt(h)} hours</strong> — about <strong>${days} days</strong> of nonstop music.`);
  }

  // Longest song you've actually played
  const playedOnce = tracks.filter(t => (t.plays || 0) > 0 && t.duration_sec);
  if (playedOnce.length) {
    const longest = [...playedOnce].sort((a, b) => (b.duration_sec || 0) - (a.duration_sec || 0))[0];
    const m = Math.floor((longest.duration_sec || 0) / 60), s = (longest.duration_sec || 0) % 60;
    facts.push(`The longest song you've actually played is <strong>${escapeHTML(longest.song)}</strong> by ${escapeHTML(longest.artist)} — <strong>${m}m ${s}s</strong>, played ${longest.plays} time${longest.plays === 1 ? "" : "s"}.`);
  }

  // Yearly top-artist streak
  const artistYears = new Map();
  for (const ya of yearArtists) {
    artistYears.set(ya.artist, (artistYears.get(ya.artist) || []).concat(ya.year));
  }
  for (const [artist, years] of artistYears) {
    if (years.length >= 2) {
      facts.push(`<strong>${escapeHTML(artist)}</strong> was your artist of the year in <strong>${years.length} different years</strong> (${years.sort().join(", ")}).`);
      break;
    }
  }

  // Median plays
  if (k.median_plays) {
    facts.push(`Half your songs have been played more than <strong>${k.median_plays}</strong> times, half less.`);
  }

  return facts;
}

function buildEvolution(data) {
  const tracks = data.tracks || [];
  const fenceEarly = 2018, fenceRecent = 2023;

  function slice(from, to) {
    return tracks.filter(t => {
      const y = +((t.date_added || "").slice(0, 4));
      return y >= from && y <= to;
    });
  }

  function topBy(rows, keyFn, n = 1) {
    const m = new Map();
    for (const r of rows) {
      const key = keyFn(r);
      if (!key) continue;
      m.set(key, (m.get(key) || 0) + (r.plays || 0));
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n);
  }

  const early = slice(2004, fenceEarly);
  const recent = slice(fenceRecent, 3000);

  if (!early.length || !recent.length) {
    return "Not enough history yet to compare eras — come back after a few more years of listening.";
  }

  const earlyTotal = early.reduce((s, t) => s + (t.plays || 0), 0);
  const recentTotal = recent.reduce((s, t) => s + (t.plays || 0), 0);

  const [genEarly] = topBy(early, t => t.genre || "Unspecified");
  const [genRecent] = topBy(recent, t => t.genre || "Unspecified");
  const [cEarly] = topBy(early, t => t.country);
  const [cRecent] = topBy(recent, t => t.country);
  const [aEarly] = topBy(early, t => t.artist);
  const [aRecent] = topBy(recent, t => t.artist);

  function pct(bucket, total) {
    if (!bucket || !total) return "—";
    return `${Math.round(bucket[1] / total * 100)}%`;
  }

  return `
    <p>Between <strong>2004–${fenceEarly}</strong> (${fmtInt(earlyTotal)} plays), your library leaned on
    <em>${escapeHTML(genEarly[0])}</em> (${pct(genEarly, earlyTotal)}),
    ${cEarly ? `${flag(cEarly[0])} <em>${countryName(cEarly[0])}</em> artists (${pct(cEarly, earlyTotal)})` : ""},
    with <em>${escapeHTML(aEarly[0])}</em> as the dominant name.</p>

    <p>In <strong>${fenceRecent}–now</strong> (${fmtInt(recentTotal)} plays from new additions), you've shifted toward
    <em>${escapeHTML(genRecent[0])}</em> (${pct(genRecent, recentTotal)}),
    ${cRecent ? `${flag(cRecent[0])} <em>${countryName(cRecent[0])}</em> (${pct(cRecent, recentTotal)})` : ""},
    and <em>${escapeHTML(aRecent[0])}</em> has become the artist you lean on most.</p>
  `;
}

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ─────────────── Drill-down panel ───────────────
let _allTracks = [];

function initDrillPanel() {
  document.getElementById("drill-close")?.addEventListener("click", closeDrill);
  document.getElementById("drill-overlay")?.addEventListener("click", closeDrill);
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrill(); });
}

function closeDrill() {
  const panel = document.getElementById("drill-panel");
  const ov = document.getElementById("drill-overlay");
  if (panel) {
    panel.classList.remove("open");
    setTimeout(() => { panel.hidden = true; }, 250);   // wait for slide-out anim
  }
  if (ov) {
    ov.classList.remove("open");
    setTimeout(() => { ov.hidden = true; }, 250);
  }
}

function openDrill({ title, subtitle, songs }) {
  document.getElementById("drill-title").innerHTML = title;
  document.getElementById("drill-sub").innerHTML = subtitle || "";
  const totalPlays = songs.reduce((s, t) => s + (t.plays || 0), 0);
  const artists = new Set(songs.flatMap(t => t.credits?.length ? t.credits : [t.artist])).size;
  document.getElementById("drill-stats").innerHTML =
    `<span><strong>${fmtInt(songs.length)}</strong> songs</span>` +
    `<span><strong>${fmtInt(totalPlays)}</strong> plays</span>` +
    `<span><strong>${fmtInt(artists)}</strong> artists</span>`;
  const cols = [
    { key: "song", label: "Song" },
    { key: "artist", label: "Artist" },
    { key: "plays", label: "Plays", num: true, render: fmtInt },
    { key: "date_added", label: "Added", render: d => d ? d.slice(0, 7) : "" },
  ];
  songs.sort((a, b) => (b.plays || 0) - (a.plays || 0));
  const body = document.getElementById("drill-body");
  body.innerHTML = "";
  body.appendChild(tableEl(songs, cols));

  const panel = document.getElementById("drill-panel");
  const ov = document.getElementById("drill-overlay");
  // Reveal both BEFORE adding .open so the transform animation actually runs.
  panel.hidden = false;
  ov.hidden = false;
  requestAnimationFrame(() => {
    panel.classList.add("open");
    ov.classList.add("open");
  });
}

// Filter helpers
function songsByCountry(cc) {
  return _allTracks.filter(t => t.country === cc);
}
function songsByCreditedArtist(name) {
  return _allTracks.filter(t => (t.credits?.includes(name)) || t.artist === name);
}
function songsByGenre(g) {
  return _allTracks.filter(t => t.genre === g);
}
function songsByYearAdded(year) {
  const y = String(year);
  return _allTracks.filter(t => (t.date_added || "").startsWith(y));
}

function drillCountry(cc) {
  openDrill({
    title: `${flag(cc)} ${countryName(cc)}`,
    subtitle: `Country drill — songs whose primary artist is from ${countryName(cc)}`,
    songs: songsByCountry(cc),
  });
}
function drillArtist(name) {
  openDrill({
    title: name,
    subtitle: "Every song you've added by this artist (lead or featured)",
    songs: songsByCreditedArtist(name),
  });
}
function drillGenre(g) {
  openDrill({
    title: g,
    subtitle: `All songs tagged ${g}`,
    songs: songsByGenre(g),
  });
}
function drillGenreYear(g, year) {
  const y = String(year);
  const songs = _allTracks.filter(
    t => (t.genre || "Unspecified") === g && (t.date_added || "").startsWith(y)
  );
  openDrill({
    title: `${g} · ${year}`,
    subtitle: `${g} songs added in ${year}`,
    songs,
  });
}
function drillYearAdded(year) {
  openDrill({
    title: `${year}`,
    subtitle: `Songs added to your library in ${year}`,
    songs: songsByYearAdded(year),
  });
}

// ─────────────── Refresh button ───────────────
// The button is a plain <a> link to the local refresh-server's status page
// (http://127.0.0.1:8789/). Cross-origin fetches from HTTPS to localhost are
// blocked by Chrome's mixed-content / Private Network Access policy, but a
// regular navigation is allowed. The localhost page handles the sync
// internally (same-origin) and redirects back to the dashboard when done.
//
// We can't ping the server from this HTTPS page (same blocking rule), so the
// button is always shown. If the server isn't running, clicking just opens a
// "site can't be reached" tab — clear enough.
function initRefreshButton() {
  const btn = document.getElementById("refresh-btn");
  if (btn) btn.hidden = false;
}

// ─────────────── Overview ───────────────
function kpiCard(label, value) {
  const d = document.createElement("div");
  d.className = "kpi";
  d.innerHTML = `<div class="kpi-label">${label}</div><div class="kpi-value">${value}</div>`;
  return d;
}

function renderOverview(data) {
  const k = data.kpis;

  document.getElementById("hero-sub").textContent =
    `${fmtInt(k.total_plays)} plays · ${fmtInt(k.track_count)} songs · ${fmtInt(k.artist_count)} artists`;

  const grid = document.getElementById("kpi-grid");
  grid.append(
    kpiCard("Total plays",      fmtInt(k.total_plays)),
    kpiCard("Songs",            fmtInt(k.track_count)),
    kpiCard("Artists",          fmtInt(k.artist_count)),
    kpiCard("Avg plays / song", fmtDec(k.avg_plays, 1)),
    kpiCard("Median plays",     fmtInt(k.median_plays)),
    kpiCard("σ plays",          fmtDec(k.stdev_plays, 1)),
  );

  const n = data.pending_artists.length;
  if (n > 0) {
    const banner = document.getElementById("pending-banner");
    banner.hidden = false;
    banner.innerHTML = `<strong>${n}</strong> artist${n === 1 ? "" : "s"} need a country assignment. <a href="#pending">Review →</a>`;
  }

  mount("chart-artists-songs", plotBarH(data.top_artists.by_song_count.slice(0, 20), "count", "artist",
    d => `${d.artist}${d.country ? " " + flag(d.country) : ""}: ${d.count} songs`,
    { xLabel: "Songs →", fill: "var(--accent)" }));

  mount("chart-artists-plays", plotBarH(data.top_artists.by_play_count.slice(0, 20), "plays", "artist",
    d => `${d.artist}${d.country ? " " + flag(d.country) : ""}: ${d.plays.toLocaleString()} plays`,
    { xLabel: "Plays →", fill: "#c4b5fd" }));

  const topC = data.country_plays.slice(0, 10);
  mount("table-countries-overview", tableEl(topC, [
    { key: "country", label: "", render: c => `${flag(c)} ${countryName(c)}` },
    { key: "plays", label: "Plays", num: true, render: fmtInt },
    { key: "artists", label: "Artists", num: true, render: fmtInt },
    { key: "songs", label: "Songs", num: true, render: fmtInt },
  ], { onRowClick: r => drillCountry(r.country) }));
  mount("chart-countries-overview", plotBarH(topC, "plays", "country",
    d => `${flag(d.country)} ${countryName(d.country)}: ${d.plays.toLocaleString()} plays`,
    { xLabel: "Plays →", fill: "var(--accent)", yFmt: c => `${flag(c)} ${c}` }));
}

// ─────────────── Artists ───────────────
function renderArtists(data) {
  mount("table-artists-songs", tableEl(data.top_artists.by_song_count, [
    { key: "rank", label: "#" },
    { key: "artist", label: "Artist" },
    { key: "country", label: "Country", render: c => `${flag(c)} ${countryName(c) || ""}` },
    { key: "count", label: "Songs", num: true, render: fmtInt },
  ], { onRowClick: r => drillArtist(r.artist) }));
  mount("table-artists-plays", tableEl(data.top_artists.by_play_count, [
    { key: "rank", label: "#" },
    { key: "artist", label: "Artist" },
    { key: "country", label: "Country", render: c => `${flag(c)} ${countryName(c) || ""}` },
    { key: "plays", label: "Plays", num: true, render: fmtInt },
  ], { onRowClick: r => drillArtist(r.artist) }));
}

// ─────────────── Countries ───────────────
const ISO_NUMERIC_TO_ALPHA2 = {
  "004":"AF","008":"AL","010":"AQ","012":"DZ","020":"AD","024":"AO","028":"AG","031":"AZ","032":"AR",
  "036":"AU","040":"AT","044":"BS","048":"BH","050":"BD","051":"AM","052":"BB","056":"BE","064":"BT",
  "068":"BO","070":"BA","072":"BW","076":"BR","084":"BZ","090":"SB","096":"BN","100":"BG","104":"MM",
  "108":"BI","112":"BY","116":"KH","120":"CM","124":"CA","132":"CV","140":"CF","144":"LK","148":"TD",
  "152":"CL","156":"CN","158":"TW","170":"CO","174":"KM","178":"CG","180":"CD","188":"CR","191":"HR",
  "192":"CU","196":"CY","203":"CZ","204":"BJ","208":"DK","212":"DM","214":"DO","218":"EC","222":"SV",
  "226":"GQ","231":"ET","232":"ER","233":"EE","242":"FJ","246":"FI","250":"FR","258":"PF","260":"TF",
  "262":"DJ","266":"GA","268":"GE","270":"GM","275":"PS","276":"DE","288":"GH","300":"GR","304":"GL",
  "308":"GD","320":"GT","324":"GN","328":"GY","332":"HT","340":"HN","344":"HK","348":"HU","352":"IS",
  "356":"IN","360":"ID","364":"IR","368":"IQ","372":"IE","376":"IL","380":"IT","384":"CI","388":"JM",
  "392":"JP","398":"KZ","400":"JO","404":"KE","408":"KP","410":"KR","414":"KW","417":"KG","418":"LA",
  "422":"LB","426":"LS","428":"LV","430":"LR","434":"LY","438":"LI","440":"LT","442":"LU","450":"MG",
  "454":"MW","458":"MY","462":"MV","466":"ML","470":"MT","478":"MR","480":"MU","484":"MX","492":"MC",
  "496":"MN","498":"MD","499":"ME","504":"MA","508":"MZ","512":"OM","516":"NA","524":"NP","528":"NL",
  "540":"NC","548":"VU","554":"NZ","558":"NI","562":"NE","566":"NG","578":"NO","586":"PK","591":"PA",
  "598":"PG","600":"PY","604":"PE","608":"PH","616":"PL","620":"PT","626":"TL","630":"PR","634":"QA",
  "642":"RO","643":"RU","646":"RW","686":"SN","688":"RS","694":"SL","702":"SG","703":"SK","704":"VN",
  "705":"SI","706":"SO","710":"ZA","716":"ZW","724":"ES","728":"SS","729":"SD","740":"SR","748":"SZ",
  "752":"SE","756":"CH","760":"SY","762":"TJ","764":"TH","768":"TG","776":"TO","780":"TT","784":"AE",
  "788":"TN","792":"TR","795":"TM","800":"UG","804":"UA","807":"MK","818":"EG","826":"GB","834":"TZ",
  "840":"US","854":"BF","858":"UY","860":"UZ","862":"VE","882":"WS","887":"YE","894":"ZM"
};

async function renderCountries(data) {
  const total = d3.sum(data.country_plays, d => d.plays);
  document.getElementById("country-header").textContent =
    `${data.country_plays.length} countries · ${fmtInt(total)} plays total`;

  // Choropleth
  try {
    const world = await fetch("https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json").then(r => r.json());
    const countries = topojson.feature(world, world.objects.countries).features;
    const byCountry = new Map(data.country_plays.map(d => [d.country, d]));
    const maxPlays = d3.max(data.country_plays, d => d.plays);

    const projection = d3.geoNaturalEarth1();
    const path = d3.geoPath(projection);
    const w = 928, h = 460;
    projection.fitSize([w, h], { type: "Sphere" });

    const svg = d3.create("svg")
      .attr("viewBox", `0 0 ${w} ${h}`)
      .attr("style", "max-width:100%;height:auto;");

    svg.append("path").attr("d", path({ type: "Sphere" })).attr("fill", "#110d22").attr("stroke", "var(--border)");
    const color = d3.scaleSequentialLog([1, maxPlays], d3.interpolatePurples);

    const paths = svg.append("g").selectAll("path").data(countries).join("path")
      .attr("d", path)
      .attr("class", d => {
        const a2 = ISO_NUMERIC_TO_ALPHA2[String(d.id).padStart(3, "0")];
        return a2 && byCountry.get(a2) ? "country-path has-data" : "country-path";
      })
      .attr("fill", d => {
        const a2 = ISO_NUMERIC_TO_ALPHA2[String(d.id).padStart(3, "0")];
        const e = a2 && byCountry.get(a2);
        return e ? color(e.plays) : "#1a1532";
      })
      .attr("stroke", "#0b0814").attr("stroke-width", 0.3)
      .on("click", (_evt, d) => {
        const a2 = ISO_NUMERIC_TO_ALPHA2[String(d.id).padStart(3, "0")];
        if (a2 && byCountry.get(a2)) drillCountry(a2);
      });
    paths.append("title").text(d => {
      const a2 = ISO_NUMERIC_TO_ALPHA2[String(d.id).padStart(3, "0")];
      const e = a2 && byCountry.get(a2);
      if (!e) return d.properties.name;
      return `${flag(a2)} ${countryName(a2)}: ${fmtInt(e.plays)} plays · ${e.artists} artists · ${e.songs} songs\nClick to see all songs from this country`;
    });

    document.getElementById("chart-world-map").appendChild(svg.node());
  } catch (err) {
    document.getElementById("chart-world-map").innerHTML =
      `<div class="sub">Couldn't load world map (${err.message}).</div>`;
  }

  mount("table-countries-all", tableEl(data.country_plays, [
    { key: "country", label: "", render: c => `${flag(c)} ${countryName(c)}` },
    { key: "plays", label: "Plays", num: true, render: fmtInt },
    { key: "artists", label: "Artists", num: true, render: fmtInt },
    { key: "songs", label: "Songs", num: true, render: fmtInt },
  ], { sortKey: "plays", sortDir: -1, onRowClick: r => drillCountry(r.country) }));
}

// ─────────────── Timeline ───────────────
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function renderTimeline(data) {
  // Month × year heatmap
  const rows = [];
  for (const [y, months] of Object.entries(data.month_year)) {
    for (const [m, plays] of Object.entries(months)) {
      rows.push({ year: +y, month: +m, plays });
    }
  }
  {
    const years = [...new Set(rows.map(r => r.year))].sort();
    const width = Math.max(1400, 60 + 20 + years.length * 60);   // 60px per year min
    mount("chart-month-year", Plot.plot({
      marginLeft: 60, marginTop: 20, marginRight: 20, marginBottom: 40,
      width,
      height: 560,
      x: { label: "Year", tickFormat: d3.format("d") },
      y: { label: null, domain: d3.range(1, 13), tickFormat: i => MONTHS[i-1] },
      color: { scheme: "purples", type: "sqrt", legend: true, label: "Plays" },
      style: { background: "transparent", color: "var(--fg)" },
      marks: [
        Plot.cell(rows, { x: "year", y: "month", fill: "plays", inset: 1,
          tip: true, title: d => `${MONTHS[d.month-1]} ${d.year}: ${fmtInt(d.plays)} plays` })
      ]
    }));
  }

  // Artist of the year tiles — click to drill into all songs added that year
  const yearArtists = document.getElementById("year-artists");
  for (const ya of data.year_artist) {
    const d = document.createElement("div");
    d.className = "year-tile clickable";
    d.title = `Click to see every song added in ${ya.year}`;
    d.innerHTML = `<div class="year-label">${ya.year}</div>
      <div class="year-artist">${ya.artist}</div>
      <div class="year-plays">${fmtInt(ya.plays)} plays</div>`;
    d.addEventListener("click", () => drillYearAdded(ya.year));
    yearArtists.appendChild(d);
  }

  // Country × year
  const cy = [];
  for (const [year, entries] of Object.entries(data.country_year)) {
    for (const e of entries) cy.push({ year: +year, country: e.country, plays: e.plays });
  }
  const topCountries = data.country_plays.slice(0, 15).map(d => d.country);
  const cyFiltered = cy.filter(d => topCountries.includes(d.country));

  {
    const years = [...new Set(cyFiltered.map(r => r.year))].sort();
    const width = Math.max(1400, 100 + 20 + years.length * 60);
    mount("chart-country-year", Plot.plot({
      marginLeft: 100, marginTop: 20, marginRight: 20, marginBottom: 40,
      width,
      height: 620,
      x: { label: "Year", tickFormat: d3.format("d") },
      y: { label: null, domain: topCountries, tickFormat: c => `${flag(c)} ${c}` },
      color: { scheme: "purples", type: "sqrt", legend: true, label: "Plays" },
      style: { background: "transparent", color: "var(--fg)" },
      marks: [
        Plot.cell(cyFiltered, { x: "year", y: "country", fill: "plays", inset: 1,
          tip: true, title: d => `${flag(d.country)} ${countryName(d.country)} · ${d.year}: ${fmtInt(d.plays)} plays` })
      ]
    }));
  }

  // Genres over time — stacked area (2012+), with clickable legend.
  if (data.genre_year?.length) {
    const START_YEAR = 2012;
    const gy = data.genre_year.filter(r => r.year >= START_YEAR);
    const years = [...new Set(gy.map(r => r.year))].sort();
    const totals = {};
    for (const r of gy) totals[r.genre] = (totals[r.genre] || 0) + r.plays;
    const domain = [...new Set(gy.map(r => r.genre))]
      .sort((a, b) => a === "Other" ? 1 : b === "Other" ? -1 : totals[b] - totals[a]);
    const palette = ["#a78bfa", "#ec4899", "#c4b5fd", "#f472b6", "#8b5cf6",
                     "#d8b4fe", "#fb7185", "#7c3aed", "#4b4564"];
    const colorFor = g => palette[domain.indexOf(g)] || palette[palette.length - 1];

    // Chart now sits in half the viewport, so no ultra-wide stretch.
    mount("chart-genre-year", Plot.plot({
      marginLeft: 70, marginTop: 20, marginRight: 20, marginBottom: 40,
      width: Math.max(640, years.length * 48),
      height: 460,
      x: { label: "Year added", tickFormat: d3.format("d"), ticks: years },
      y: { label: "Plays", grid: true, tickFormat: d3.format(",") },
      color: { domain, range: palette.slice(0, domain.length), legend: false },
      style: { background: "transparent", color: "var(--fg)" },
      marks: [
        Plot.areaY(gy, {
          x: "year", y: "plays", fill: "genre",
          order: domain,
          curve: "monotone-x",
          tip: true,
          title: d => `${d.genre} · ${d.year}: ${fmtInt(d.plays)} plays`,
        }),
        Plot.ruleY([0], { stroke: "var(--border)" }),
      ],
    }));

    // Custom clickable legend (replacing Plot's built-in non-clickable one)
    const legend = document.getElementById("legend-genre-year");
    if (legend) {
      legend.innerHTML = domain.map(g => `
        <div class="pie-legend-item${g === "Other" ? "" : " clickable"}" data-genre="${String(g).replace(/"/g, "&quot;")}">
          <span class="pie-swatch" style="background:${colorFor(g)}"></span>
          <span class="pie-name" title="${String(g).replace(/"/g, "&quot;")}">${g}</span>
          <span class="pie-pct">${fmtInt(totals[g])}</span>
        </div>
      `).join("");
      legend.querySelectorAll(".clickable").forEach(el =>
        el.addEventListener("click", () => drillGenre(el.dataset.genre))
      );
    }

    // Top 5 genres per year tiles (right-hand column)
    renderYearGenreTiles(START_YEAR);
  }
}

function renderYearGenreTiles(startYear = 2012, topN = 5) {
  const container = document.getElementById("genre-year-tiles");
  if (!container) return;
  // Aggregate tracks by (year_added, genre)
  const byYear = new Map();
  for (const t of _allTracks) {
    if (!t.date_added) continue;
    const y = +t.date_added.slice(0, 4);
    if (y < startYear) continue;
    const g = t.genre || "Unspecified";
    if (!byYear.has(y)) byYear.set(y, new Map());
    const m = byYear.get(y);
    m.set(g, (m.get(g) || 0) + (t.plays || 0));
  }
  const years = [...byYear.keys()].sort((a, b) => b - a);   // newest first
  container.innerHTML = years.map(y => {
    const top = [...byYear.get(y).entries()]
      .sort((a, b) => b[1] - a[1]).slice(0, topN);
    return `
      <div class="year-genres-card">
        <div class="year-label">${y}</div>
        ${top.map(([g, plays]) => `
          <div class="year-genre-row" data-genre="${String(g).replace(/"/g, "&quot;")}" data-year="${y}">
            <span class="genre-name" title="${String(g).replace(/"/g, "&quot;")}">${g}</span>
            <span class="genre-plays">${fmtInt(plays)}</span>
          </div>
        `).join("")}
      </div>
    `;
  }).join("");
  container.querySelectorAll(".year-genre-row").forEach(el =>
    el.addEventListener("click", () => drillGenreYear(el.dataset.genre, +el.dataset.year))
  );
}

// ─────────────── Genres ───────────────
function renderGenres(data) {
  const all = data.genre_plays;
  const totalPlays = all.reduce((s, g) => s + g.plays, 0);
  document.getElementById("genres-sub").textContent =
    `${all.length} distinct genres · ${fmtInt(totalPlays)} plays.`;

  mount("chart-genres", genreDonut(all));

  mount("table-genres", tableEl(all, [
    { key: "genre", label: "Genre" },
    { key: "plays", label: "Plays", num: true, render: fmtInt },
    { key: "songs", label: "Songs", num: true, render: fmtInt },
  ], { sortKey: "plays", sortDir: -1, onRowClick: r => drillGenre(r.genre) }));
}

const PIE_COLORS = [
  "#a78bfa", "#ec4899", "#f472b6", "#c4b5fd", "#8b5cf6",
  "#d8b4fe", "#f9a8d4", "#fb7185", "#fda4af", "#7c3aed",
  "#9333ea", "#db2777",
];

function genreDonut(genres) {
  const w = 380, h = 380;
  const r = Math.min(w, h) / 2 - 8;
  const inner = r * 0.58;

  // Top 11 genres + bucket the rest as "Other"
  const top = genres.slice(0, 11);
  const tail = genres.slice(11);
  const otherPlays = tail.reduce((s, g) => s + g.plays, 0);
  const slices = otherPlays > 0
    ? [...top, { genre: `Other (${tail.length})`, plays: otherPlays, _isOther: true }]
    : top;
  const total = slices.reduce((s, g) => s + g.plays, 0);

  const color = d3.scaleOrdinal()
    .domain(slices.map(d => d.genre))
    .range(PIE_COLORS.slice(0, slices.length - (otherPlays > 0 ? 1 : 0)).concat(["#4b4564"]));

  const pie = d3.pie().value(d => d.plays).sort(null);
  const arc = d3.arc().innerRadius(inner).outerRadius(r).cornerRadius(2).padAngle(0.005);
  const arcHover = d3.arc().innerRadius(inner).outerRadius(r + 8).cornerRadius(2).padAngle(0.005);

  const svg = d3.create("svg")
    .attr("viewBox", `${-w/2} ${-h/2} ${w} ${h}`)
    .attr("style", "max-width:100%;height:auto;");

  const arcs = svg.append("g").selectAll("path")
    .data(pie(slices))
    .join("path")
    .attr("d", arc)
    .attr("fill", d => color(d.data.genre))
    .attr("stroke", "var(--bg)")
    .attr("stroke-width", 2);

  arcs
    .style("cursor", d => d.data._isOther ? "default" : "pointer")
    .on("mouseenter", function() { d3.select(this).transition().duration(120).attr("d", arcHover); })
    .on("mouseleave", function() { d3.select(this).transition().duration(120).attr("d", arc); })
    .on("click", (_e, d) => { if (!d.data._isOther) drillGenre(d.data.genre); });

  arcs.append("title").text(d =>
    `${d.data.genre}: ${fmtInt(d.data.plays)} plays (${(d.data.plays / total * 100).toFixed(1)}%)`);

  // Center labels
  const center = svg.append("g");
  center.append("text")
    .attr("text-anchor", "middle").attr("dy", "-0.1em")
    .attr("fill", "var(--fg)")
    .attr("font-size", "1.6rem").attr("font-weight", "600")
    .text(fmtInt(total));
  center.append("text")
    .attr("text-anchor", "middle").attr("dy", "1.4em")
    .attr("fill", "var(--muted)")
    .attr("font-size", "0.78rem").attr("letter-spacing", "0.08em").attr("text-transform", "uppercase")
    .text("total plays");

  // Legend (paired columns under the donut)
  const legend = document.createElement("div");
  legend.className = "pie-legend";
  legend.innerHTML = slices.map(d => {
    const pct = (d.plays / total * 100).toFixed(1);
    const safe = String(d.genre).replace(/"/g, "&quot;");
    return `
      <div class="pie-legend-item${d._isOther ? "" : " clickable"}" data-genre="${safe}">
        <span class="pie-swatch" style="background:${color(d.genre)}"></span>
        <span class="pie-name" title="${safe}">${safe}</span>
        <span class="pie-pct">${pct}%</span>
      </div>`;
  }).join("");
  legend.querySelectorAll(".clickable").forEach(el => {
    el.addEventListener("click", () => drillGenre(el.dataset.genre));
  });

  const wrap = document.createElement("div");
  wrap.className = "pie-wrap";
  wrap.appendChild(svg.node());
  wrap.appendChild(legend);
  return wrap;
}

// ─────────────── Tracker ───────────────
function renderTracker(data) {
  const all = data.tracks;
  document.getElementById("tracker-sub").textContent = `${fmtInt(all.length)} songs. Click a column header to sort.`;

  const cols = [
    { key: "song", label: "Song" },
    { key: "artist", label: "Artist" },
    { key: "country", label: "", render: c => flag(c) },
    { key: "album", label: "Album" },
    { key: "plays", label: "Plays", num: true, render: fmtInt },
    { key: "genre", label: "Genre" },
    { key: "last_played", label: "Last played", render: d => d ? d.slice(0, 10) : "" },
  ];

  const container = document.getElementById("table-tracker");
  const state = { q: "", page: 0, pageSize: 30, sortKey: "plays", sortDir: -1 };
  const input = document.getElementById("tracker-search");

  function render() {
    const q = state.q.toLowerCase().trim();
    let rows = q
      ? all.filter(t =>
          t.song?.toLowerCase().includes(q) ||
          t.artist?.toLowerCase().includes(q) ||
          t.album?.toLowerCase().includes(q) ||
          t.genre?.toLowerCase().includes(q))
      : all.slice();
    rows.sort((a, b) => cmp(a[state.sortKey], b[state.sortKey]) * state.sortDir);
    container.innerHTML = "";
    container.appendChild(tableEl(rows, cols, {
      sortKey: state.sortKey, sortDir: state.sortDir,
      onSort: (k, dir) => { state.sortKey = k; state.sortDir = dir; state.page = 0; render(); },
      page: state.page, pageSize: state.pageSize,
      onPage: p => { state.page = p; render(); },
    }));
  }

  input.addEventListener("input", e => { state.q = e.target.value; state.page = 0; render(); });
  render();

  renderRecent(all);
}

// Recently played / recently added — two mini tables at the bottom of Tracker.
function renderRecent(all) {
  const recentCols = [
    { key: "song", label: "Song" },
    { key: "artist", label: "Artist" },
    { key: "country", label: "", render: c => flag(c) },
    { key: "plays", label: "Plays", num: true, render: fmtInt },
  ];

  const played = all
    .filter(t => t.last_played)
    .sort((a, b) => cmp(a.last_played, b.last_played) * -1)
    .slice(0, 25)
    .map(t => ({ ...t, when: t.last_played?.slice(0, 10) }));

  const added = all
    .filter(t => t.date_added)
    .sort((a, b) => cmp(a.date_added, b.date_added) * -1)
    .slice(0, 25)
    .map(t => ({ ...t, when: t.date_added?.slice(0, 10) }));

  const withDate = label => [
    ...recentCols,
    { key: "when", label },
  ];

  mount("table-recent-played", tableEl(played, withDate("Last played"), {
    onRowClick: t => drillArtist(t.artist),
  }));
  mount("table-recent-added", tableEl(added, withDate("Added"), {
    onRowClick: t => drillArtist(t.artist),
  }));
}

// ─────────────── Pending ───────────────
function renderPending(data) {
  const list = document.getElementById("pending-list");
  const p = data.pending_artists;
  if (!p.length) {
    list.innerHTML = `<div class="card" style="text-align:center;">🎉 <strong>Nothing pending.</strong> Every artist has a country assigned.</div>`;
    return;
  }
  list.appendChild(tableEl(p, [
    { key: "artist", label: "Artist" },
    { key: "attempts", label: "Attempts", num: true },
    { key: "last_error", label: "Last error" },
  ]));
}

// ─────────────── Generic helpers ───────────────
function mount(id, node) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = "";
  if (node instanceof Node) el.appendChild(node);
  else el.innerHTML = String(node);
}

function plotBarH(data, xKey, yKey, titleFn, { xLabel, fill = "var(--accent)", yFmt } = {}) {
  return Plot.plot({
    marginLeft: Math.min(180, Math.max(...data.map(d => String(d[yKey]).length)) * 7 + 20),
    marginTop: 10, marginRight: 20, marginBottom: 30,
    height: Math.max(220, data.length * 22 + 40),
    x: { label: xLabel || null, grid: true, tickFormat: d3.format(",") },
    y: { label: null, domain: [...data].sort((a, b) => b[xKey] - a[xKey]).map(d => d[yKey]), tickFormat: yFmt },
    style: { background: "transparent", color: "var(--fg)" },
    marks: [
      Plot.barX(data, { x: xKey, y: yKey, fill, tip: true, title: titleFn }),
      Plot.ruleX([0], { stroke: "var(--border)" })
    ]
  });
}

function tableEl(rows, cols, opts = {}) {
  const { sortKey, sortDir = -1, onSort, page = 0, pageSize, onPage, onRowClick } = opts;
  const total = rows.length;
  const paged = pageSize ? rows.slice(page * pageSize, (page + 1) * pageSize) : rows;
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const thr = document.createElement("tr");
  for (const c of cols) {
    const th = document.createElement("th");
    th.textContent = c.label;
    if (c.num) th.className = "num";
    if (onSort) {
      th.style.cursor = "pointer";
      th.title = "Click to sort";
      if (c.key === sortKey) th.textContent += sortDir === 1 ? " ▲" : " ▼";
      th.addEventListener("click", () => {
        const dir = c.key === sortKey ? -sortDir : -1;
        onSort(c.key, dir);
      });
    }
    thr.appendChild(th);
  }
  thead.appendChild(thr);
  table.appendChild(thead);
  const tb = document.createElement("tbody");
  for (const r of paged) {
    const tr = document.createElement("tr");
    if (onRowClick) {
      tr.classList.add("row-clickable");
      tr.addEventListener("click", () => onRowClick(r));
    }
    for (const c of cols) {
      const td = document.createElement("td");
      const val = r[c.key];
      const rendered = c.render ? c.render(val, r) : (val == null ? "" : String(val));
      td.innerHTML = rendered;
      if (c.num) td.className = "num";
      tr.appendChild(td);
    }
    tb.appendChild(tr);
  }
  table.appendChild(tb);

  const wrap = document.createElement("div");
  wrap.appendChild(table);
  if (pageSize && onPage) {
    const pages = Math.ceil(total / pageSize);
    if (pages > 1) {
      const pager = document.createElement("div");
      pager.className = "pager";
      pager.innerHTML = `
        <button ${page === 0 ? "disabled" : ""} data-act="prev">← Prev</button>
        <span>Page ${page + 1} / ${pages}</span>
        <button ${page + 1 >= pages ? "disabled" : ""} data-act="next">Next →</button>`;
      pager.querySelector('[data-act="prev"]')?.addEventListener("click", () => onPage(page - 1));
      pager.querySelector('[data-act="next"]')?.addEventListener("click", () => onPage(page + 1));
      wrap.appendChild(pager);
    }
  }
  return wrap;
}

function cmp(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}
