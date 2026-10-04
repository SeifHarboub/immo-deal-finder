// Immo Radar en ligne : déchiffrement local puis filtrage dans le navigateur.
const $ = selector => document.querySelector(selector);
const money = new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
const number = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 });
const STORE_KEY = 'immo-radar-password';
const PAGE = 48;

const labels = {
  excellente: 'Excellente', bonne: 'Bonne', correcte: 'Correcte', hors_cible: 'Faible', a_analyser: 'Non évaluée', a_verifier: 'À vérifier',
  appartement: 'Appartement', maison: 'Maison', terrain: 'Terrain', bureau: 'Bureau', immeuble: 'Immeuble',
  local_commercial: 'Murs commerciaux', fonds_commerce: 'Fonds de commerce', autre: 'Autre'
};
const strategyLabels = { decote: 'Sous le marché', rendement: 'Rendement locatif', murs: 'Murs commerciaux', fonds: 'Fonds de commerce' };
const saleModes = { enchere_judiciaire: 'Enchère judiciaire', enchere_notariale: 'Enchère notariale', vente_interactive: 'Vente interactive', cession_publique: 'Cession publique' };
const sources = {
  orpi: 'Orpi', laforet: 'Laforêt', pointdevente: 'PointDeVente', immonot: 'Immonot', geolocaux: 'Geolocaux', iad: 'iad France',
  bienici: 'Bien’ici', notaires: 'Notaires de France', figaro: 'Figaro Immobilier', safti: 'Safti', century21: 'Century 21', era: 'ERA',
  citya: 'Citya', foncia: 'Foncia', proprietesprivees: 'Propriétés-Privées', entreparticuliers: 'EntreParticuliers',
  bureauxlocaux: 'BureauxLocaux', bpifrance: 'Bpifrance Transmission', placedescommerces: 'Place des Commerces',
  msimond: 'Michel Simond', murscommerciaux: 'MursCommerciaux', cbre: 'CBRE', arthurloyd: 'Arthur Loyd', licitor: 'Licitor',
  avoventes: 'Avoventes', encheresimmo: 'Enchères Immobilières', agorastore: 'Agorastore', heures36: '36h immo', vench: 'Vench'
};
const presets = {
  all: { filter: () => true, sort: 'score' },
  decote: { filter: d => d.segment === 'residentiel' && !d.vente_encheres, sort: 'discount' },
  locatif: { filter: d => d.segment === 'residentiel' && !d.vente_encheres, sort: 'score' },
  immeuble: { filter: d => d.segment === 'immeuble', sort: 'score' },
  murs: { filter: d => d.segment === 'murs', sort: 'score' },
  fonds: { filter: d => d.segment === 'fonds', sort: 'score' },
  encheres: { filter: d => d.vente_encheres, sort: 'auction' }
};
const state = { data: null, rows: [], filtered: [], preset: 'all', shown: 0, view: 'cards' };

const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
const euro = value => value == null ? '—' : money.format(value);
const pct = value => value == null ? '—' : `${number.format(value)} %`;
const signed = value => value == null ? '—' : `${value > 0 ? '+' : ''}${number.format(Math.round(value * 10) / 10)} %`;
const safeUrl = value => { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } };
const typeLabel = d => d.segment === 'immeuble' ? 'Immeuble' : d.segment === 'fonds' && d.type_bien !== 'fonds_commerce' ? 'Fonds de commerce' : labels[d.type_bien] || 'Bien';
const department = d => {
  const cp = d.code_postal || '';
  if (cp.startsWith('97')) return cp.slice(0, 3);
  if (cp.startsWith('20')) return Number(cp) < 20200 ? '2A' : '2B';
  return cp.slice(0, 2);
};

// ---------- Déchiffrement ----------
const fromBase64 = text => Uint8Array.from(atob(text), c => c.charCodeAt(0));

async function decrypt(envelope, password) {
  const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey(
    { name: 'PBKDF2', hash: 'SHA-256', salt: fromBase64(envelope.salt), iterations: envelope.iterations },
    material, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  const compressed = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: fromBase64(envelope.nonce) }, key, fromBase64(envelope.data));
  const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'));
  return JSON.parse(await new Response(stream).text());
}

async function unlock(password, remember) {
  const button = $('#unlock');
  button.disabled = true; button.textContent = 'Déchiffrement…'; $('#lock-error').textContent = '';
  try {
    const response = await fetch(`data.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Données indisponibles');
    const data = await decrypt(await response.json(), password);
    if (remember) { try { localStorage.setItem(STORE_KEY, password); } catch { /* stockage indisponible */ } }
    start(data);
  } catch (error) {
    try { localStorage.removeItem(STORE_KEY); } catch { /* stockage indisponible */ }
    $('#lock-error').textContent = error.name === 'OperationError' ? 'Mot de passe incorrect.' : `Ouverture impossible : ${error.message}`;
  } finally {
    button.disabled = false; button.textContent = 'Ouvrir';
  }
}

$('#lock-form').addEventListener('submit', event => {
  event.preventDefault();
  unlock($('#password').value, $('#remember').checked);
});
$('#logout').addEventListener('click', () => {
  try { localStorage.removeItem(STORE_KEY); } catch { /* stockage indisponible */ }
  location.reload();
});

// ---------- Application ----------
function start(data) {
  state.data = data;
  state.rows = data.annonces;
  $('#lock').hidden = true; $('#app').hidden = false;
  const date = new Date(data.generated_at);
  $('#updated').textContent = `Mis à jour le ${date.toLocaleDateString('fr-FR')} à ${date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })} · ${number.format(data.total_en_ligne)} annonces analysées`;
  const fill = (select, values, label) => {
    select.insertAdjacentHTML('beforeend', [...values].sort((a, b) => String(label(a)).localeCompare(String(label(b)), 'fr')).map(v => `<option value="${esc(v)}">${esc(label(v))}</option>`).join(''));
  };
  fill($('#f-department'), new Set(state.rows.map(department).filter(Boolean)), v => v);
  fill($('#f-type'), new Set(state.rows.map(typeLabel)), v => v);
  fill($('#f-source'), new Set(state.rows.map(d => d.source)), v => sources[v] || v);
  try { state.view = localStorage.getItem('immo-view') === 'table' ? 'table' : 'cards'; } catch { /* stockage indisponible */ }
  if (!matchMedia('(min-width: 861px)').matches) {
    $('#filter-toggle').setAttribute('aria-expanded', 'false');
    $('#filter-toggle-label').textContent = 'Afficher les filtres';
  }
  applyView();
  render();
}

function values() {
  const form = new FormData($('#filters'));
  const get = name => form.get(name) || '';
  const num = name => get(name) === '' ? null : Number(get(name));
  return {
    department: get('department'), place: get('place').trim().toLowerCase(), q: get('q').trim().toLowerCase(),
    priceMin: num('price_min'), priceMax: num('price_max'), surfaceMin: num('surface_min'), surfaceMax: num('surface_max'),
    yieldMin: num('yield_min'), newDays: num('new_days'), type: get('type'), source: get('source'),
    cashflow: form.has('cashflow'), realRent: form.has('real_rent'), drop: form.has('drop'), excellent: form.has('excellent_only')
  };
}

function matches(d, f) {
  if (f.department && department(d) !== f.department) return false;
  if (f.place && !`${d.ville || ''} ${d.code_postal || ''}`.toLowerCase().includes(f.place)) return false;
  if (f.q && !`${d.titre || ''} ${d.extrait || ''} ${d.ville || ''}`.toLowerCase().includes(f.q)) return false;
  if (f.priceMin != null && !(d.prix >= f.priceMin)) return false;
  if (f.priceMax != null && !(d.prix <= f.priceMax)) return false;
  if (f.surfaceMin != null && !(d.surface_bati >= f.surfaceMin)) return false;
  if (f.surfaceMax != null && !(d.surface_bati <= f.surfaceMax)) return false;
  if (f.yieldMin != null && !(d.rendement_net >= f.yieldMin)) return false;
  if (f.newDays != null && !(d.jours_en_ligne <= f.newDays)) return false;
  if (f.type && typeLabel(d) !== f.type) return false;
  if (f.source && d.source !== f.source) return false;
  if (f.cashflow && !(d.cashflow_mensuel >= 0)) return false;
  if (f.realRent && !d.loyer_reel) return false;
  if (f.drop && !(d.baisse_prix_pct >= 1)) return false;
  if (f.excellent && d.niveau_affaire !== 'excellente') return false;
  return true;
}

const sorters = {
  score: (a, b) => (b.score_global ?? -1) - (a.score_global ?? -1),
  yield: (a, b) => (b.rendement_net ?? -99) - (a.rendement_net ?? -99),
  cashflow: (a, b) => (b.cashflow_mensuel ?? -1e9) - (a.cashflow_mensuel ?? -1e9),
  discount: (a, b) => (a.decote ?? 9) - (b.decote ?? 9),
  drop: (a, b) => (b.baisse_prix_pct ?? 0) - (a.baisse_prix_pct ?? 0),
  recent: (a, b) => (a.jours_en_ligne ?? 1e9) - (b.jours_en_ligne ?? 1e9),
  price_asc: (a, b) => (a.prix ?? 1e12) - (b.prix ?? 1e12),
  price_desc: (a, b) => (b.prix ?? 0) - (a.prix ?? 0),
  auction: (a, b) => String(a.date_vente || '9999').localeCompare(String(b.date_vente || '9999'))
};

function render() {
  const f = values();
  const base = state.rows.filter(d => matches(d, f));
  document.querySelectorAll('[data-count]').forEach(node => {
    node.textContent = number.format(base.filter(presets[node.dataset.count].filter).length);
  });
  state.filtered = base.filter(presets[state.preset].filter).sort(sorters[$('#sort').value] || sorters.score);
  const list = state.filtered;
  $('#k-total').textContent = number.format(list.length);
  $('#k-excellent').textContent = number.format(list.filter(d => d.niveau_affaire === 'excellente').length);
  $('#k-good').textContent = number.format(list.filter(d => d.niveau_affaire === 'bonne').length);
  $('#k-drops').textContent = number.format(list.filter(d => d.baisse_prix_pct >= 1).length);
  $('#k-new').textContent = number.format(list.filter(d => d.jours_en_ligne <= 2).length);
  const yields = list.map(d => d.rendement_net).filter(v => v != null).sort((a, b) => a - b);
  const median = yields.length ? yields[Math.floor(yields.length / 2)] : null;
  $('#count').innerHTML = `<strong>${number.format(list.length)}</strong> affaire${list.length > 1 ? 's' : ''}${median != null ? ` · rendement net médian ${pct(median)}` : ''}`;
  renderChips(f);
  state.shown = 0;
  $('#cards').innerHTML = ''; $('#table-body').innerHTML = '';
  if (!list.length) {
    $('#cards').innerHTML = '<div class="empty"><strong>Aucune affaire avec ces filtres</strong>Élargissez la zone ou le budget.</div>';
    $('#table-body').innerHTML = '<tr><td colspan="10" class="table-loading">Aucune affaire avec ces filtres.</td></tr>';
  }
  more();
}

function more() {
  const next = state.filtered.slice(state.shown, state.shown + PAGE);
  $('#cards').insertAdjacentHTML('beforeend', next.map(card).join(''));
  $('#table-body').insertAdjacentHTML('beforeend', next.map(row).join(''));
  state.shown += next.length;
  $('#more').hidden = state.shown >= state.filtered.length;
}

function renderChips(f) {
  const chips = [];
  const form = $('#filters');
  for (const element of form.elements) {
    if (!element.name) continue;
    if (element.type === 'checkbox' ? element.checked : element.value) {
      const label = element.closest('label')?.querySelector('span')?.textContent || element.closest('label')?.textContent || element.name;
      const value = element.type === 'checkbox' ? '' : ` : ${element.tagName === 'SELECT' ? element.selectedOptions[0].textContent : element.value}`;
      chips.push(`<button type="button" class="filter-chip" data-name="${esc(element.name)}">${esc(label.trim())}${esc(value)} <span aria-hidden="true">×</span></button>`);
    }
  }
  $('#chips').innerHTML = chips.join('');
  $('#filter-count').textContent = chips.length;
}

function metrics(d) {
  const out = [];
  if (d.segment === 'fonds') {
    out.push(['Chiffre d’affaires', euro(d.chiffre_affaires)], ['EBE', euro(d.ebe)], ['Prix / EBE', d.multiple_ebe == null ? '—' : `${number.format(d.multiple_ebe)} ×`]);
  } else {
    if (d.vente_encheres) {
      out.push(['Prix probable', euro(d.prix_compare)]);
      if (d.date_vente) out.push(['Vente le', new Date(d.date_vente).toLocaleDateString('fr-FR')]);
    }
    if (d.decote != null && d.median_eur_m2 != null) out.push(['Écart au marché', signed(d.decote * 100), d.decote < 0 ? 'good' : 'bad']);
    if (d.rendement_net != null) out.push(['Rendement net', pct(d.rendement_net), d.rendement_net >= 6 ? 'good' : '']);
    if (d.cashflow_mensuel != null && d.segment !== 'murs') out.push(['Cash-flow', `${d.cashflow_mensuel >= 0 ? '+' : ''}${euro(d.cashflow_mensuel)}/mois`, d.cashflow_mensuel >= 0 ? 'good' : 'bad']);
  }
  return out.slice(0, 4);
}

function chips(d) {
  const out = [];
  if (d.baisse_prix_pct >= 1) out.push(['drop', `Prix −${number.format(d.baisse_prix_pct)} %`]);
  if (d.jours_en_ligne <= 2) out.push(['new', 'Nouvelle']);
  if (d.loyer_reel) out.push(['rent', 'Loyer réel publié']);
  if (d.vente_encheres) out.push(['auction', saleModes[d.mode_vente] || 'Enchère']);
  if (d.nb_publications > 1) out.push(['dup', `Sur ${d.nb_publications} sites`]);
  return out.length ? `<div class="signal-chips">${out.map(([k, t]) => `<span class="signal ${k}">${esc(t)}</span>`).join('')}</div>` : '';
}

function title(d) {
  const surface = d.type_bien === 'terrain' ? d.surface_terrain : d.surface_bati;
  return [typeLabel(d), surface != null && `${number.format(surface)} m²`, d.ville].filter(Boolean).join(' · ');
}

function card(d, index) {
  const image = safeUrl(d.image);
  const url = safeUrl(d.url);
  const facts = [d.surface_bati != null && `${number.format(d.surface_bati)} m²`, d.surface_terrain != null && `terrain ${number.format(d.surface_terrain)} m²`, d.nb_pieces && `${d.nb_pieces} p.`, d.dpe && `DPE ${d.dpe}`].filter(Boolean).join(' · ');
  const score = d.score_global == null ? '' : `<span class="score-ring ${esc(d.niveau_affaire)}" style="--score:${Math.round(d.score_global)}"><strong>${Math.round(d.score_global)}</strong></span>`;
  const list = metrics(d);
  return `<article class="deal-card level-${esc(d.niveau_affaire)}">
    <div class="card-media">${image ? `<img src="${esc(image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}<span class="media-type">${esc(typeLabel(d))}</span>${score}</div>
    <div class="card-body">
      <div class="card-head"><div><h3>${esc(title(d))}</h3><p class="card-location">${esc([d.ville, d.code_postal].filter(Boolean).join(' · '))}</p></div>
        <span class="deal-badge ${esc(d.niveau_affaire)}">${esc(labels[d.niveau_affaire] || '')}${d.strategie ? ` · ${esc(strategyLabels[d.strategie])}` : ''}</span></div>
      <div class="card-price"><strong>${euro(d.prix)}</strong><span>${d.prix_m2 ? `${euro(d.prix_m2)} / m²` : ''}${facts ? ` · ${esc(facts)}` : ''}</span></div>
      ${list.length ? `<dl class="card-metrics">${list.map(([k, v, t]) => `<div class="${t || ''}"><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>` : ''}
      ${chips(d)}
      ${d.risques_evaluation ? `<p class="card-risks">⚠ ${esc(d.risques_evaluation)}</p>` : ''}
      <div class="card-actions"><button class="detail-button" data-index="${state.shown + index}">Analyse détaillée</button>
        <a class="listing-link" ${url ? `href="${esc(url)}" target="_blank" rel="noopener noreferrer"` : 'aria-disabled="true"'}>${esc(sources[d.source] || d.source)} ↗</a></div>
    </div>
  </article>`;
}

function row(d, index) {
  const signals = [d.baisse_prix_pct >= 1 && `−${number.format(d.baisse_prix_pct)} %`, d.loyer_reel && 'loué', d.vente_encheres && 'enchère', d.jours_en_ligne <= 2 && 'nouvelle', d.nb_publications > 1 && `${d.nb_publications} sites`].filter(Boolean).join(' · ');
  return `<tr data-index="${state.shown + index}" tabindex="0">
    <td>${d.score_global == null ? '—' : `<span class="score-pill ${esc(d.niveau_affaire)}">${Math.round(d.score_global)}</span>`}</td>
    <td><strong>${esc(typeLabel(d))}</strong><small>${d.surface_bati != null ? `${number.format(d.surface_bati)} m²` : ''}${d.strategie ? ` · ${esc(strategyLabels[d.strategie])}` : ''}</small></td>
    <td>${esc(d.ville || '—')}<small>${esc(d.code_postal || '')}</small></td>
    <td class="num">${euro(d.prix)}</td><td class="num">${d.prix_m2 ? euro(d.prix_m2) : '—'}</td>
    <td class="num ${d.decote == null ? '' : d.decote < 0 ? 'good' : 'bad'}">${d.decote == null ? '—' : signed(d.decote * 100)}</td>
    <td class="num">${pct(d.rendement_net)}</td>
    <td class="num ${d.cashflow_mensuel == null ? '' : d.cashflow_mensuel >= 0 ? 'good' : 'bad'}">${d.cashflow_mensuel == null || d.segment === 'murs' ? '—' : `${d.cashflow_mensuel >= 0 ? '+' : ''}${euro(d.cashflow_mensuel)}`}</td>
    <td><small>${esc(signals)}</small></td><td><small>${esc(sources[d.source] || d.source)}</small></td>
  </tr>`;
}

// ---------- Détail et simulateur ----------
function openDetail(d) {
  const h = state.data.hypotheses || {};
  const url = safeUrl(d.url);
  const image = safeUrl(d.image);
  const list = metrics(d);
  const simulator = ['residentiel', 'immeuble', 'murs'].includes(d.segment) && d.loyer_mensuel_retenu && d.prix ? `
    <section class="detail-section simulator" id="sim">
      <h3>Simulation de financement</h3>
      <div class="field-grid simulator-inputs">
        <label><span>Prix négocié</span><div class="unit-input"><input data-sim="prix" type="number" step="1000" value="${Math.round(d.prix)}"><i>€</i></div></label>
        <label><span>Loyer mensuel</span><div class="unit-input"><input data-sim="loyer" type="number" step="10" value="${Math.round(d.loyer_mensuel_retenu)}"><i>€</i></div></label>
        <label><span>Travaux</span><div class="unit-input"><input data-sim="travaux" type="number" step="1000" value="${Math.round(d.travaux_estimes || 0)}"><i>€</i></div></label>
        <label><span>Apport</span><div class="unit-input"><input data-sim="apport" type="number" value="${h.apport_pct ?? 10}"><i>%</i></div></label>
        <label><span>Taux</span><div class="unit-input"><input data-sim="taux" type="number" step="0.05" value="${h.taux_credit ?? 3.4}"><i>%</i></div></label>
        <label><span>Durée</span><div class="unit-input"><input data-sim="duree" type="number" value="${h.duree_credit ?? 20}"><i>ans</i></div></label>
      </div>
      <div class="invest-grid" id="sim-out"></div>
      <p class="invest-note">Frais d’acquisition ${h.notaire_pct ?? 7.5} %, ${h.vacance_mois ?? 1} mois de vacance, entretien ${h.entretien_pct ?? 5} %, assurance ${euro(h.pno_eur ?? 200)}, taxe foncière estimée à ${h.tf_mois_loyer ?? 1.2} mois de loyer. Fiscalité non incluse.</p>
    </section>` : '';
  $('#detail-content').innerHTML = `<div class="dialog-body">
    <header class="detail-header"><div><p class="eyebrow">${esc(sources[d.source] || d.source)}</p><h2 id="detail-title">${esc(title(d))}</h2><p class="detail-location">${esc([d.ville, d.code_postal].filter(Boolean).join(' · '))}</p></div>
      <div class="detail-price"><small>Prix affiché</small><strong>${euro(d.prix)}</strong></div></header>
    ${image ? `<img src="${esc(image)}" alt="" referrerpolicy="no-referrer" style="width:100%;max-height:340px;object-fit:cover;border-radius:12px;margin-bottom:16px" onerror="this.remove()">` : ''}
    ${list.length ? `<dl class="card-metrics">${list.map(([k, v, t]) => `<div class="${t || ''}"><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>` : ''}
    <section class="detail-section"><h3>Lecture de l’affaire</h3><div class="dialog-grid">
      <div><small>Score</small><strong>${d.score_global == null ? '—' : Math.round(d.score_global)} / 100 · ${esc(labels[d.niveau_affaire] || '')}</strong></div>
      <div><small>Stratégie</small><strong>${esc(strategyLabels[d.strategie] || '—')}</strong></div>
      <div><small>Médiane DVF comparable</small><strong>${d.median_eur_m2 ? `${euro(d.median_eur_m2)} / m²` : '—'}</strong></div>
      <div><small>Ventes comparées</small><strong>${d.nb_ventes ?? '—'}${d.confiance ? ` · confiance ${esc(d.confiance)}` : ''}</strong></div>
      <div><small>Loyer retenu</small><strong>${d.loyer_mensuel_retenu ? `${euro(d.loyer_mensuel_retenu)} / mois${d.loyer_reel ? ' (réel)' : ' (estimé)'}` : '—'}</strong></div>
      <div><small>Coût total avec frais</small><strong>${euro(d.cout_total)}</strong></div>
    </div>${d.risques_evaluation ? `<div class="data-warning" style="margin-top:10px"><strong>Points à vérifier avant toute offre</strong><span>${esc(d.risques_evaluation)}</span></div>` : ''}</section>
    ${simulator}
    ${d.extrait ? `<section class="detail-section"><h3>Extrait de l’annonce</h3><p class="detail-description">${esc(d.extrait)}…</p></section>` : ''}
    ${d.sources_doublon && d.sources_doublon.length > 1 ? `<section class="detail-section"><h3>Aussi publiée sur</h3><p>${d.sources_doublon.map(s => esc(sources[s] || s)).join(', ')}</p></section>` : ''}
    ${url ? `<a class="detail-source-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Ouvrir l’annonce sur ${esc(sources[d.source] || d.source)} ↗</a>` : ''}
  </div>`;
  $('#detail').showModal();
  const sim = $('#sim');
  if (sim) {
    const compute = () => {
      const v = name => Number(sim.querySelector(`[data-sim="${name}"]`).value) || 0;
      const total = v('prix') * (1 + (h.notaire_pct ?? 7.5) / 100) + v('travaux');
      const collected = v('loyer') * (12 - (h.vacance_mois ?? 1));
      const net = collected - v('loyer') * (h.tf_mois_loyer ?? 1.2) - (h.pno_eur ?? 200) - collected * ((h.entretien_pct ?? 5) + (h.gestion_pct ?? 0)) / 100;
      const borrowed = total * (1 - v('apport') / 100), rate = v('taux') / 1200, months = v('duree') * 12;
      const payment = months ? (rate ? borrowed * rate / (1 - Math.pow(1 + rate, -months)) : borrowed / months) : 0;
      const cashflow = net / 12 - payment;
      $('#sim-out').innerHTML = [['Coût total', euro(total)], ['Rendement net', pct(total ? net / total * 100 : null)], ['Mensualité', `${euro(payment)} / mois`], ['Cash-flow', `${cashflow >= 0 ? '+' : ''}${euro(cashflow)} / mois`]]
        .map(([k, val]) => `<div><small>${k}</small><strong>${val}</strong></div>`).join('');
    };
    sim.addEventListener('input', compute);
    compute();
  }
}

// ---------- Interactions ----------
function applyPreset(name) {
  state.preset = name;
  $('#sort').value = presets[name].sort;
  document.querySelectorAll('[data-preset]').forEach(tab => tab.setAttribute('aria-selected', String(tab.dataset.preset === name)));
  render();
}

function applyView() {
  document.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === state.view)));
  $('#cards').hidden = state.view !== 'cards';
  $('#table-view').hidden = state.view !== 'table';
}

let debounce;
$('#filters').addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(render, 200); });
$('#filters').addEventListener('change', render);
$('#sort').addEventListener('change', render);
$('#reset').addEventListener('click', () => { $('#filters').reset(); applyPreset('all'); });
$('#more').addEventListener('click', more);
document.querySelector('.strategy-tabs').addEventListener('click', e => { const t = e.target.closest('[data-preset]'); if (t) applyPreset(t.dataset.preset); });
document.querySelector('.view-switch').addEventListener('click', e => {
  const b = e.target.closest('[data-view]'); if (!b) return;
  state.view = b.dataset.view;
  try { localStorage.setItem('immo-view', state.view); } catch { /* stockage indisponible */ }
  applyView();
});
document.querySelector('.deals-table thead').addEventListener('click', e => { const b = e.target.closest('[data-sort]'); if (b) { $('#sort').value = b.dataset.sort; render(); } });
$('#chips').addEventListener('click', e => {
  const chip = e.target.closest('[data-name]'); if (!chip) return;
  const field = $('#filters').elements[chip.dataset.name];
  if (field.type === 'checkbox') field.checked = false; else field.value = '';
  render();
});
$('#cards').addEventListener('click', e => { const b = e.target.closest('.detail-button'); if (b) openDetail(state.filtered[Number(b.dataset.index)]); });
$('#table-body').addEventListener('click', e => { const r = e.target.closest('tr[data-index]'); if (r) openDetail(state.filtered[Number(r.dataset.index)]); });
$('#table-body').addEventListener('keydown', e => { const r = e.target.closest('tr[data-index]'); if (r && e.key === 'Enter') openDetail(state.filtered[Number(r.dataset.index)]); });
$('#detail .dialog-close').addEventListener('click', () => $('#detail').close());
$('#detail').addEventListener('click', e => { if (e.target === $('#detail')) $('#detail').close(); });
$('#filter-toggle').addEventListener('click', () => {
  const desktop = matchMedia('(min-width: 861px)').matches;
  const open = desktop ? !$('#workspace').classList.toggle('filters-collapsed') : $('#filters-panel').classList.toggle('is-open');
  $('#filter-toggle').setAttribute('aria-expanded', String(open));
  $('#filter-toggle-label').textContent = open ? 'Masquer les filtres' : 'Afficher les filtres';
});
$('#export').addEventListener('click', () => {
  const columns = [['score_global', 'Score'], ['niveau_affaire', 'Niveau'], ['strategie', 'Stratégie'], ['type_bien', 'Type'], ['ville', 'Ville'], ['code_postal', 'Code postal'], ['prix', 'Prix'], ['surface_bati', 'Surface'], ['prix_m2', 'Prix au m²'], ['decote', 'Décote nette'], ['rendement_net', 'Rendement net %'], ['cashflow_mensuel', 'Cash-flow'], ['loyer_mensuel_retenu', 'Loyer retenu'], ['chiffre_affaires', 'CA'], ['ebe', 'EBE'], ['risques_evaluation', 'Vigilance'], ['source', 'Source'], ['url', 'Annonce']];
  const cell = v => v == null ? '' : typeof v === 'number' ? String(Math.round(v * 100) / 100).replace('.', ',') : `"${String(v).replace(/"/g, '""')}"`;
  const lines = [columns.map(c => c[1]).join(';'), ...state.filtered.map(d => columns.map(([k]) => cell(k === 'decote' && d[k] != null ? d[k] * 100 : d[k])).join(';'))];
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' }));
  link.download = 'immo-radar.csv'; link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
});

// Mot de passe mémorisé sur cet appareil : ouverture directe.
try {
  const saved = localStorage.getItem(STORE_KEY);
  if (saved) { $('#remember').checked = true; unlock(saved, true); }
} catch { /* stockage indisponible */ }
