const form = document.querySelector('#filter-form');
const cards = document.querySelector('#cards');
const loadMore = document.querySelector('#load-more');
const dialog = document.querySelector('#detail-dialog');
const dialogContent = document.querySelector('#dialog-content');
const state = { offset: 0, limit: 24, loading: false, controller: null };

const money = new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
const number = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 });
const labels = {
  excellente: 'Excellente', bonne: 'Bonne', correcte: 'Correcte',
  hors_cible: 'Faible', a_analyser: 'Non évaluée',
  a_verifier: 'À vérifier',
  appartement: 'Appartement', maison: 'Maison', terrain: 'Terrain', bureau: 'Bureau',
  local_commercial: 'Murs commerciaux', fonds_commerce: 'Fonds de commerce', immeuble: 'Immeuble', autre: 'Autre / type à confirmer'
};
const strategyLabels = { decote: 'Sous le marché', rendement: 'Rendement locatif', murs: 'Murs commerciaux', fonds: 'Fonds de commerce' };
const saleModeLabels = { enchere_judiciaire: 'Enchère judiciaire', enchere_notariale: 'Enchère notariale', vente_interactive: 'Vente interactive', cession_publique: 'Cession publique' };
const sourceLabels = {
  superimmo: 'Superimmo', orpi: 'Orpi', laforet: 'Laforêt', pointdevente: 'PointDeVente', immonot: 'Immonot',
  geolocaux: 'Geolocaux', iad: 'iad France', leboncoin: 'Leboncoin', bienici: 'Bien’ici', notaires: 'Notaires de France',
  figaro: 'Figaro Immobilier', safti: 'Safti', century21: 'Century 21', era: 'ERA', citya: 'Citya', foncia: 'Foncia',
  proprietesprivees: 'Propriétés-Privées', entreparticuliers: 'EntreParticuliers', bureauxlocaux: 'BureauxLocaux',
  bpifrance: 'Bpifrance Transmission', placedescommerces: 'Place des Commerces', msimond: 'Michel Simond',
  murscommerciaux: 'MursCommerciaux', cbre: 'CBRE', arthurloyd: 'Arthur Loyd', licitor: 'Licitor', avoventes: 'Avoventes',
  encheresimmo: 'Enchères Immobilières', agorastore: 'Agorastore', heures36: '36h immo', vench: 'Vench'
};
const presets = {
  all: { segment: '', auctions: '', sort: 'deal' },
  decote: { segment: 'residentiel', auctions: 'exclude', sort: 'discount' },
  locatif: { segment: 'residentiel', auctions: 'exclude', sort: 'deal' },
  immeuble: { segment: 'immeuble', auctions: '', sort: 'deal' },
  murs: { segment: 'murs', auctions: '', sort: 'deal' },
  fonds: { segment: 'fonds', auctions: '', sort: 'deal' },
  encheres: { segment: '', auctions: 'only', sort: 'auction_date' }
};
let assumptions = null;
const departments = [
  ['01','Ain'],['02','Aisne'],['03','Allier'],['04','Alpes-de-Haute-Provence'],['05','Hautes-Alpes'],['06','Alpes-Maritimes'],['07','Ardèche'],['08','Ardennes'],['09','Ariège'],['10','Aube'],['11','Aude'],['12','Aveyron'],['13','Bouches-du-Rhône'],['14','Calvados'],['15','Cantal'],['16','Charente'],['17','Charente-Maritime'],['18','Cher'],['19','Corrèze'],['2A','Corse-du-Sud'],['2B','Haute-Corse'],['21','Côte-d’Or'],['22','Côtes-d’Armor'],['23','Creuse'],['24','Dordogne'],['25','Doubs'],['26','Drôme'],['27','Eure'],['28','Eure-et-Loir'],['29','Finistère'],['30','Gard'],['31','Haute-Garonne'],['32','Gers'],['33','Gironde'],['34','Hérault'],['35','Ille-et-Vilaine'],['36','Indre'],['37','Indre-et-Loire'],['38','Isère'],['39','Jura'],['40','Landes'],['41','Loir-et-Cher'],['42','Loire'],['43','Haute-Loire'],['44','Loire-Atlantique'],['45','Loiret'],['46','Lot'],['47','Lot-et-Garonne'],['48','Lozère'],['49','Maine-et-Loire'],['50','Manche'],['51','Marne'],['52','Haute-Marne'],['53','Mayenne'],['54','Meurthe-et-Moselle'],['55','Meuse'],['56','Morbihan'],['57','Moselle'],['58','Nièvre'],['59','Nord'],['60','Oise'],['61','Orne'],['62','Pas-de-Calais'],['63','Puy-de-Dôme'],['64','Pyrénées-Atlantiques'],['65','Hautes-Pyrénées'],['66','Pyrénées-Orientales'],['67','Bas-Rhin'],['68','Haut-Rhin'],['69','Rhône'],['70','Haute-Saône'],['71','Saône-et-Loire'],['72','Sarthe'],['73','Savoie'],['74','Haute-Savoie'],['75','Paris'],['76','Seine-Maritime'],['77','Seine-et-Marne'],['78','Yvelines'],['79','Deux-Sèvres'],['80','Somme'],['81','Tarn'],['82','Tarn-et-Garonne'],['83','Var'],['84','Vaucluse'],['85','Vendée'],['86','Vienne'],['87','Haute-Vienne'],['88','Vosges'],['89','Yonne'],['90','Territoire de Belfort'],['91','Essonne'],['92','Hauts-de-Seine'],['93','Seine-Saint-Denis'],['94','Val-de-Marne'],['95','Val-d’Oise'],['971','Guadeloupe'],['972','Martinique'],['973','Guyane'],['974','La Réunion'],['975','Saint-Pierre-et-Miquelon'],['976','Mayotte']
];
const departmentCombo = document.querySelector('#department-combo');
const departmentSearch = document.querySelector('#department-search');
const departmentValue = document.querySelector('#department-value');
const departmentList = document.querySelector('#department-list');
const cityCombo = document.querySelector('#city-combo');
const citySearch = document.querySelector('#city-search');
const cityValue = document.querySelector('#city-value');
const cityList = document.querySelector('#city-list');
let cityRequest = 0;

function normalized(value) {
  return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
}

function closeCombo(combo) {
  const list = combo.querySelector('.combo-list');
  const input = combo.querySelector('.combo-search');
  list.hidden = true;
  input.setAttribute('aria-expanded', 'false');
  combo.classList.remove('is-open');
}

function openCombo(combo) {
  document.querySelectorAll('.search-combo').forEach(item => { if (item !== combo) closeCombo(item); });
  combo.querySelector('.combo-list').hidden = false;
  combo.querySelector('.combo-search').setAttribute('aria-expanded', 'true');
  combo.classList.add('is-open');
}

function setActiveOption(combo, direction) {
  const options = [...combo.querySelectorAll('.combo-option')];
  if (!options.length) return;
  let index = options.findIndex(option => option.classList.contains('is-active'));
  index = direction === 'first' ? 0 : (index + direction + options.length) % options.length;
  options.forEach(option => option.classList.remove('is-active'));
  options[index].classList.add('is-active');
  options[index].scrollIntoView({ block: 'nearest' });
}

function comboKeyboard(event) {
  const combo = event.currentTarget.closest('.search-combo');
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    if (combo.querySelector('.combo-list').hidden) openCombo(combo);
    setActiveOption(combo, event.key === 'ArrowDown' ? 1 : -1);
  } else if (event.key === 'Enter') {
    const active = combo.querySelector('.combo-option.is-active') || combo.querySelector('.combo-option');
    if (active && !combo.querySelector('.combo-list').hidden) { event.preventDefault(); active.click(); }
  } else if (event.key === 'Escape') {
    closeCombo(combo);
  }
}

function renderDepartments(query='') {
  const needles = normalized(query).split(/[^a-z0-9]+/).filter(Boolean);
  const matches = departments.filter(([code, name]) => {
    const haystack = normalized(`${code} ${name}`);
    return needles.every(needle => haystack.includes(needle));
  });
  departmentList.innerHTML = matches.length ? matches.map(([code, name]) => `
    <button type="button" class="combo-option" role="option" data-code="${esc(code)}" data-name="${esc(name)}">
      <span class="department-code">${esc(code)}</span><span><strong>${esc(name)}</strong><small>Département ${esc(code)}</small></span><i aria-hidden="true">✓</i>
    </button>`).join('') : '<p class="combo-empty"><strong>Aucun département trouvé</strong>Essayez son numéro ou une partie de son nom.</p>';
  openCombo(departmentCombo);
}

function clearCity(reload=true) {
  const hadValue = Boolean(cityValue.value);
  cityValue.value = '';
  citySearch.value = '';
  citySearch.closest('.combo-control').classList.remove('has-value');
  cityCombo.querySelector('.combo-clear').hidden = true;
  closeCombo(cityCombo);
  if (reload && hadValue) loadDeals(false);
}

function selectDepartment(code, name) {
  departmentValue.value = code;
  departmentSearch.value = `${code} · ${name}`;
  departmentSearch.closest('.combo-control').classList.add('has-value');
  departmentCombo.querySelector('.combo-clear').hidden = false;
  closeCombo(departmentCombo);
  clearCity(false);
  document.querySelector('#city-hint').textContent = `Villes disponibles en ${name}`;
  loadDeals(false);
}

async function loadCities(query='') {
  const request = ++cityRequest;
  cityList.innerHTML = '<p class="combo-loading">Recherche des villes…</p>';
  openCombo(cityCombo);
  const search = new URLSearchParams({ limit: '80' });
  if (departmentValue.value) search.set('department', departmentValue.value);
  if (query.trim()) search.set('q', query.trim());
  try {
    const response = await apiFetch(`/api/cities?${search}`);
    const data = await response.json();
    if (request !== cityRequest) return;
    if (!response.ok) throw new Error(data.detail || 'Erreur de lecture');
    cityList.innerHTML = data.items.length ? data.items.map(item => `
      <button type="button" class="combo-option city-option" role="option" data-name="${esc(item.name)}">
        <span class="city-pin" aria-hidden="true"></span><span><strong>${esc(item.name)}</strong><small>${esc(item.postal_code || 'Code postal inconnu')}</small></span><em>${number.format(item.count)} annonce${item.count > 1 ? 's' : ''}</em><i aria-hidden="true">✓</i>
      </button>`).join('') : '<p class="combo-empty"><strong>Aucune ville trouvée</strong>Essayez une autre orthographe ou effacez le département.</p>';
  } catch (error) {
    if (request === cityRequest) cityList.innerHTML = `<p class="combo-empty"><strong>Villes indisponibles</strong>${esc(error.message)}</p>`;
  }
}

function selectCity(name) {
  cityValue.value = name;
  citySearch.value = name;
  citySearch.closest('.combo-control').classList.add('has-value');
  cityCombo.querySelector('.combo-clear').hidden = false;
  closeCombo(cityCombo);
  loadDeals(false);
}

function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}
function euro(value) { return value == null ? '—' : money.format(value); }
function num(value, suffix='') { return value == null ? '—' : `${number.format(value)}${suffix}`; }
function reductionText(value) { return value > 0 && value < .1 ? 'moins de 0,1 %' : `${number.format(value)} %`; }
function safeUrl(value) { try { const url = new URL(value); return ['http:','https:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } }
function jsonValue(value, fallback) { try { return value ? JSON.parse(value) : fallback; } catch { return fallback; } }
function descriptionHtml(value) {
  const lines = String(value || 'Description non disponible.').split(/\n+/).map(line=>line.trim()).filter(Boolean);
  return lines.map(line=>`<p>${esc(line)}</p>`).join('');
}

function displayTitle(item) {
  let type = labels[item.type_bien] || 'Bien immobilier';
  if (item.type_bien === 'local_commercial' && item.categorie === 'location') type = 'Local commercial à louer';
  const surface = item.type_bien === 'terrain'
    ? item.surface_terrain
    : item.surface_bati;
  return [
    type,
    surface != null && `${number.format(surface)} m²`,
    item.ville || (item.code_postal ? `Secteur ${item.code_postal}` : null)
  ].filter(Boolean).join(' · ');
}

async function apiFetch(url, options={}) {
  let response;
  for (let attempt=0; attempt<4; attempt++) {
    if (attempt) await new Promise(resolve => setTimeout(resolve, attempt * 700));
    response = await fetch(url, options);
    if (response.status !== 503) return response;
  }
  return response;
}

function params() {
  const data = new FormData(form); const query = new URLSearchParams();
  for (const [key, value] of data.entries()) if (value !== '') query.append(key, value);
  query.set('sort', document.querySelector('#sort-select').value);
  query.set('limit', state.limit); query.set('offset', state.offset);
  return query;
}

function loadingCards() {
  cards.innerHTML = '';
  const template = document.querySelector('#loading-template');
  for (let i=0;i<4;i++) cards.append(template.content.cloneNode(true));
  cards.setAttribute('aria-busy','true');
}

function negotiation(item) {
  if (item.niveau_affaire === 'a_verifier') {
    return `<div class="data-warning"><strong>Analyse suspendue · contrôle nécessaire</strong>${esc(item.motif_verification || 'Le prix, la surface ou la nature de la vente est incompatible avec une comparaison DVF directe.')} Aucun classement ni prix cible n’est calculé.</div>`;
  }
  if (item.median_eur_m2 == null || item.plafond_excellente == null || item.prix == null || item.score_opportunite == null) {
    const reasons = {
      fonds_commerce: 'Un fonds se valorise avec le chiffre d’affaires, l’EBE et le loyer — pas avec le DVF résidentiel.',
      local_commercial: 'Les murs commerciaux nécessitent une référence dédiée aux locaux professionnels.',
      bureau: 'Les bureaux nécessitent une référence de rendement et de loyer professionnel.',
      terrain: 'Un terrain nécessite un modèle foncier selon sa constructibilité et sa surface.'
    };
    const reason = reasons[item.type_bien] || 'Pas assez de ventes DVF comparables sur ce secteur.';
    return `<div class="no-reference"><strong>Non classable avec la référence actuelle</strong>${esc(reason)} Cette annonce est placée après les affaires classées.</div>`;
  }
  const marketGap = item.decote * 100;
  const excellent = item.plafond_excellente, good = item.plafond_bonne;
  const isCommercial = item.type_bien === 'local_commercial';
  const marketValue = item.median_eur_m2 * item.surface_bati;
  const observedLow = item.q1_eur_m2 * item.surface_bati;
  const observedHigh = item.q3_eur_m2 * item.surface_bati;
  const confidenceLabel = { fiable:'confiance élevée', indicative:'confiance moyenne', fragile:'confiance faible' }[item.confiance] || 'confiance non déterminée';
  const riskNote = item.risques_evaluation ? `<div class="work-warning"><strong>Points à chiffrer avant toute offre</strong><span>${esc(item.risques_evaluation)}</span></div>` : '';
  const context = `<div class="comparable-summary"><strong>${number.format(item.nb_ventes)} ventes DVF similaires · ${esc(confidenceLabel)}</strong>Valeur centrale estimée ${euro(marketValue)} · cœur des ventes observées ${euro(observedLow)} à ${euro(observedHigh)}. Même type de bien, surface, pièces, terrain, proximité et récence pris en compte.</div>${isCommercial ? '<div class="commercial-warning">Référence DVF commerciale indicative : elle regroupe commerces, bureaux et locaux industriels.</div>' : ''}${riskNote}`;
  const zones = `<div class="deal-zones" aria-label="Zones de prix calculées">
    <div class="excellent-zone"><small>Zone très bonne affaire</small><strong>≤ ${euro(excellent)}</strong><span>Au moins 25 % sous le marché</span></div>
    <div class="good-zone"><small>Zone bonne affaire</small><strong>${euro(excellent)} à ${euro(good)}</strong><span>Entre 15 et 25 % sous le marché</span></div>
  </div>`;

  if (item.niveau_affaire === 'excellente') {
    return `${context}${zones}<div class="deal-verdict very-good">
      <div><small>Conclusion</small><strong>Prix déjà très attractif</strong><span>Le bien est déjà dans la meilleure zone : aucune cible de baisse supplémentaire n’est nécessaire.</span></div>
      <div><small>Valeur médiane des comparables</small><strong>${euro(marketValue)}</strong><span>Le prix affiché est ${number.format(Math.abs(marketGap))} % sous cette estimation.</span></div>
    </div>`;
  }

  const reductionGood = Math.max(0, (1 - good / item.prix) * 100);
  const reductionExcellent = Math.max(0, (1 - excellent / item.prix) * 100);
  if (item.niveau_affaire === 'bonne') {
    return `${context}${zones}<div class="target-prices single" aria-label="Prix à viser pour devenir une très bonne affaire">
      <div><small>Objectif · passer en très bonne affaire</small><strong>Viser ${euro(excellent)} ou moins</strong><span>Baisse supplémentaire nécessaire : ${reductionText(reductionExcellent)} du prix affiché.</span></div>
    </div>`;
  }
  return `${context}${zones}<div class="target-prices objectives" aria-label="Prix à viser selon l'objectif">
      <div><small>Objectif · bonne affaire</small><strong>≤ ${euro(good)}</strong><span>Négocier une baisse de ${reductionText(reductionGood)}.</span></div>
      <div><small>Objectif · très bonne affaire</small><strong>≤ ${euro(excellent)}</strong><span>Négocier une baisse de ${reductionText(reductionExcellent)}.</span></div>
    </div><div class="negotiation-note"><strong>Deux objectifs clairs</strong><span>Valeur médiane estimée du bien : ${euro(marketValue)}.</span></div>`;
}

function marketGapBadge(item) {
  if (item.decote == null || item.score_opportunite == null) {
    return '<span class="market-gap unknown"><small>Écart au marché</small><strong>Non calculé</strong></span>';
  }
  const gap = item.decote * 100;
  if (gap < 0) return `<span class="market-gap below"><small>Écart au marché</small><strong>${number.format(Math.abs(gap))} % moins cher</strong></span>`;
  return `<span class="market-gap above"><small>Écart au marché</small><strong>${number.format(Math.max(0, gap))} % plus cher</strong></span>`;
}

function rentalAnalysis(item) {
  if (!['appartement','maison','local_commercial'].includes(item.type_bien) || item.loyer_mensuel_estime == null) return '';
  const annualRent = item.loyer_mensuel_estime * 12;
  const yieldAt = price => price > 0 ? annualRent / price * 100 : null;
  const isCommercial = item.type_bien === 'local_commercial';
  const typology = item.type_bien === 'appartement'
    ? (item.nb_pieces <= 2 ? 'appartement T1–T2' : item.nb_pieces >= 3 ? 'appartement T3+' : 'appartement')
    : isCommercial ? 'murs commerciaux' : 'maison';
  const fragile = isCommercial || (item.nb_observations_loyer != null && item.nb_observations_loyer < 30)
    || (item.r2_loyer != null && item.r2_loyer < .5);
  const yieldValue = item.rendement_brut_prudent ?? item.rendement_brut_affiche;
  const yieldReading = yieldValue < 4
    ? ['Faible','low','Le loyer compense peu le capital investi.']
    : yieldValue < 6 ? ['Modéré','moderate','Projet patrimonial possible, mais rendement limité.']
    : yieldValue < 8 ? ['Correct','correct','Rendement correct, mais pas exceptionnel avant frais et travaux.']
    : yieldValue < 10 ? ['Attractif','attractive','Potentiel intéressant à confirmer avec les charges et l’état du bien.']
    : ['Élevé · à vérifier','high','Un rendement très élevé signale souvent des travaux, un risque locatif ou une donnée atypique.'];
  const conditionText = `${item.titre || ''} ${item.description || ''} ${item.details_json || ''}`.toLowerCase();
  const workRisk = ['à rénover','a renover','travaux','à rafraîchir','a rafraichir','réhabilitation','rehabilitation'].some(word=>conditionText.includes(word))
    || ['F','G'].includes(String(item.dpe || '').toUpperCase());
  return `<section class="rental-analysis" aria-label="Estimation de rentabilité locative brute">
    <div class="rental-heading"><div><small>Projection locative · ${esc(typology)}</small><strong>Rentabilité brute estimée</strong></div><span>${isCommercial ? `${number.format(item.nb_observations_loyer)} annonces pro` : `ANIL ${esc(item.millesime_loyer || '2025')}`}</span></div>
    <div class="rental-grid">
      <div><small>${isCommercial ? 'Loyer mensuel professionnel estimé' : 'Loyer mensuel estimé · hors charges'}</small><strong>${euro(item.loyer_mensuel_estime)} / mois</strong><span>${isCommercial ? 'Fourchette observée' : 'Plage centrale de prévision'} : ${euro(item.loyer_mensuel_bas)} à ${euro(item.loyer_mensuel_haut)}</span></div>
      <div><small>Au prix affiché</small><strong>${num(item.rendement_brut_affiche,' %')}</strong><span>Plage brute ${num(item.rendement_brut_prudent,' %')} à ${num(item.rendement_brut_haut,' %')}</span></div>
      <div><small>Si achat au seuil « bonne »</small><strong>${num(yieldAt(item.plafond_bonne),' %')}</strong><span>Prix d’achat ${euro(item.plafond_bonne)}</span></div>
      <div><small>Si achat au seuil « très bonne »</small><strong>${num(yieldAt(item.plafond_excellente),' %')}</strong><span>Prix d’achat ${euro(item.plafond_excellente)}</span></div>
    </div>
    <div class="yield-reading ${yieldReading[1]}"><strong>Lecture prudente : ${yieldReading[0]} · ${num(yieldValue,' %')} brut</strong><span>${yieldReading[2]} Le niveau est classé sur le bas de la plage, pas sur l’estimation centrale.</span></div>
    ${workRisk ? '<div class="work-warning"><strong>Travaux ou rénovation probablement nécessaires</strong><span>Le rendement affiché utilise seulement le prix d’achat. Les travaux peuvent réduire fortement la rentabilité réelle et doivent être ajoutés au coût total du projet.</span></div>' : ''}
    ${item.rendement_brut_marche == null ? '' : `<div class="yield-bridge"><strong>Pourquoi ${num(item.rendement_brut_affiche,' %')} malgré ${num(Math.abs(item.decote*100),' %')} de décote ?</strong><span>Au prix de marché estimé (${euro(item.median_eur_m2*item.surface_bati)}), le rendement serait d’environ ${num(item.rendement_brut_marche,' %')}. La décote à l’achat l’améliore jusqu’à ${num(item.rendement_brut_affiche,' %')} au prix affiché.</span></div>`}
    <p class="rental-caution ${fragile ? 'fragile' : ''}">${isCommercial ? '<strong>Indication issue des locations pures comparables collectées dans la même zone.</strong> Bail, destination, emplacement et taxe foncière peuvent fortement modifier le loyer. ' : `${fragile ? '<strong>Estimation locale à considérer avec prudence.</strong> ' : ''}Le loyer public ANIL est hors charges. La surface est corrigée par rapport au logement-type ANIL et la plage affichée représente la zone centrale de prévision, plus utile que l’intervalle officiel complet. `}Rendement indicatif = 12 mois de loyer hors charges / prix d’achat. Il ne s’agit pas d’un rendement net : notaire, travaux, crédit, fiscalité, charges non récupérables, taxe foncière et vacance restent à déduire.</p>
  </section>`;
}

function pct(value) { return value == null ? '—' : `${number.format(value)} %`; }

function scoreBadge(item) {
  if (item.score_global == null) return '';
  const strategy = strategyLabels[item.strategie] || '';
  return `<span class="score-badge ${esc(item.niveau_affaire)}" title="Score sur 100 de la meilleure stratégie applicable"><strong>${Math.round(item.score_global)}</strong><small>${esc(strategy)}</small></span>`;
}

function signalChips(item) {
  const chips = [];
  if (item.baisse_prix_pct >= 1) chips.push(['drop', `Prix −${number.format(item.baisse_prix_pct)} %`]);
  if (item.jours_en_ligne != null && item.jours_en_ligne <= 2) chips.push(['new', 'Nouvelle']);
  if (item.loyer_reel) chips.push(['rent', 'Loyer réel publié']);
  if (item.vente_encheres) chips.push(['auction', saleModeLabels[item.mode_vente] || 'Enchère']);
  if (item.nb_publications > 1) chips.push(['dup', `Sur ${item.nb_publications} sites`]);
  if (item.active === false) chips.push(['gone', 'Retirée']);
  if (item.jours_en_ligne > 120) chips.push(['old', `En ligne depuis ${item.jours_en_ligne} j`]);
  return chips.length ? `<div class="signal-chips">${chips.map(([kind, text]) => `<span class="signal ${kind}">${esc(text)}</span>`).join('')}</div>` : '';
}

function investmentBlock(item) {
  const segment = item.segment;
  if (segment === 'fonds') {
    const rows = [
      ['Prix de cession', euro(item.prix)],
      ['Chiffre d’affaires', euro(item.chiffre_affaires)],
      ['EBE', euro(item.ebe)],
      ['Prix / EBE', item.multiple_ebe == null ? '—' : `${number.format(item.multiple_ebe)} ×`],
      ['Prix / CA', item.multiple_ca == null ? '—' : `${number.format(item.multiple_ca * 100)} %`],
      ['Loyer / CA', item.poids_loyer_ca == null ? '—' : pct(item.poids_loyer_ca * 100)]
    ];
    const verdict = item.score_fonds == null
      ? '<p class="invest-note">Ni chiffre d’affaires ni EBE publiés : le fonds ne peut pas être évalué sans le bilan. Demandez les trois dernières liasses fiscales.</p>'
      : `<p class="invest-note">Un petit commerce se négocie généralement entre 2 et 4 fois l’EBE retraité. Score fonds : <strong>${Math.round(item.score_fonds)}/100</strong>.</p>`;
    return `<section class="invest-block"><h4>Fonds de commerce</h4><div class="invest-grid">${rows.map(([k,v])=>`<div><small>${k}</small><strong>${v}</strong></div>`).join('')}</div>${verdict}</section>`;
  }
  if (!['residentiel','immeuble','murs'].includes(segment) || item.rendement_net == null) return '';
  const rentLabel = item.loyer_reel ? 'Loyer réel publié' : 'Loyer estimé prudent';
  const rows = [
    [rentLabel, `${euro(item.loyer_mensuel_retenu)} / mois`],
    ['Coût total', euro(item.cout_total)],
    ['Rendement brut', pct(item.rendement_brut_retenu)],
    ['Rendement net', pct(item.rendement_net)],
  ];
  if (segment !== 'murs') rows.push(['Cash-flow après crédit', item.cashflow_mensuel == null ? '—' : `${item.cashflow_mensuel >= 0 ? '+' : ''}${euro(item.cashflow_mensuel)} / mois`]);
  if (item.travaux_estimes > 0) rows.push(['Travaux intégrés', euro(item.travaux_estimes)]);
  const title = segment === 'murs' ? 'Murs commerciaux' : segment === 'immeuble' ? 'Immeuble de rapport' : 'Investissement locatif';
  return `<section class="invest-block ${item.cashflow_mensuel >= 0 ? 'positive' : ''}"><h4>${title}</h4><div class="invest-grid">${rows.map(([k,v])=>`<div><small>${k}</small><strong>${v}</strong></div>`).join('')}</div></section>`;
}

function auctionBlock(item) {
  if (!item.vente_encheres) return '';
  const date = item.date_vente ? new Date(item.date_vente).toLocaleDateString('fr-FR', { dateStyle: 'long' }) : 'date non précisée';
  return `<div class="auction-note"><strong>${esc(saleModeLabels[item.mode_vente] || 'Vente aux enchères')} · ${esc(date)}</strong>Mise à prix ${euro(item.prix)}${item.prix_adjuge ? ` · adjugé ${euro(item.prix_adjuge)}` : ` · prix final probable ≈ ${euro(item.prix_compare)}`}. La comparaison au marché utilise ce prix probable, pas la mise à prix.</div>`;
}

function card(item) {
  const url = safeUrl(item.url);
  const facts = [
    item.type_bien && labels[item.type_bien], item.surface_bati != null && num(item.surface_bati,' m²'),
    item.surface_terrain != null && `Terrain ${num(item.surface_terrain,' m²')}`,
    item.nb_pieces != null && `${item.nb_pieces} pièce${item.nb_pieces > 1 ? 's' : ''}`,
    item.dpe && `DPE ${item.dpe}`
  ].filter(Boolean).map(value => `<span class="fact">${esc(value)}</span>`).join('');
  const location = [item.ville, item.code_postal].filter(Boolean).join(' · ') || 'Localisation non précisée';
  return `<article class="deal-card">
    <div class="card-top"><span class="source-tag">${esc(sourceLabels[item.source] || item.source)}</span><div class="card-badges">${scoreBadge(item)}<span class="deal-badge ${esc(item.niveau_affaire)}">${esc(labels[item.niveau_affaire])}</span>${marketGapBadge(item)}</div></div>
    <div class="card-content"><div class="card-main">
      <h3>${esc(displayTitle(item))}</h3>
      <div class="location"><i aria-hidden="true"></i><span><small>Localisation</small><strong>${esc(location)}</strong></span></div>
    <div class="price-row"><span class="asking-price"><small>${item.categorie === 'location' ? 'Loyer affiché · bien professionnel' : 'Prix de vente affiché'}</small>${euro(item.categorie === 'location' ? item.loyer : item.prix)}</span><span class="price-m2">${item.prix_trop_bas ? 'Données ou mode de vente à vérifier' : (item.categorie === 'location' ? 'Location commerciale · hors classement DVF' : (item.prix_m2 ? `${euro(item.prix_m2)} / m²` : 'prix au m² indisponible'))}</span></div>
      <div class="facts">${facts || '<span class="fact">Informations partielles</span>'}</div>
      ${signalChips(item)}
    </div><div class="card-analysis">
      ${auctionBlock(item)}
      ${investmentBlock(item)}
      ${item.segment === 'residentiel' ? negotiation(item) : ''}
      <div class="card-actions"><button class="detail-button" data-source="${esc(item.source)}" data-id="${esc(item.external_id)}">Analyse détaillée</button><a class="listing-link" ${url ? `href="${esc(url)}" target="_blank" rel="noopener noreferrer"` : 'aria-disabled="true"'}>Voir l’annonce</a></div>
    </div></div>
  </article>`;
}

function renderMetrics(summary) {
  document.querySelector('#metric-total').textContent = number.format(summary.total || 0);
  document.querySelector('#metric-excellent').textContent = number.format(summary.excellent || 0);
  document.querySelector('#metric-good').textContent = number.format(summary.good || 0);
  document.querySelector('#metric-drops').textContent = number.format(summary.price_drops || 0);
  document.querySelector('#metric-new').textContent = number.format(summary.new || 0);
  const coverage = summary.total ? Math.round(100 * summary.scored / summary.total) : 0;
  document.querySelector('#metric-coverage').textContent = `${coverage} % évaluées · sans doublons`;
  const median = summary.median_net_yield == null ? '' : ` · rendement net médian ${pct(summary.median_net_yield)}`;
  document.querySelector('#result-count').textContent = `${number.format(summary.scored || 0)} évaluée${summary.scored > 1 ? 's' : ''} · ${number.format(summary.unscored || 0)} sans évaluation · ${number.format(summary.total || 0)} au total${median}`;
}

function renderActiveFilters() {
  const entries = [];
  const fieldLabels = {
    q: 'Mot-clé', postal_code: 'Code postal', price_min: 'Prix min.', price_max: 'Prix max.',
    surface_min: 'Habitable min.', surface_max: 'Habitable max.', land_min: 'Terrain min.', land_max: 'Terrain max.',
    yield_min: 'Rendement net min.', new_days: 'Publiée depuis'
  };
  for (const [key, value] of new FormData(form).entries()) {
    if (!value || ['scored_only','segment','auctions'].includes(key)) continue;
    if (key === 'department') {
      entries.push(`Département : ${departmentSearch.value}`);
      continue;
    }
    if (key === 'city') {
      entries.push(`Ville : ${value}`);
      continue;
    }
    if (fieldLabels[key]) {
      const formatted = key.startsWith('price_') ? euro(Number(value))
        : key === 'yield_min' ? `${value} %`
        : key === 'new_days' ? `${value} jour${value > 1 ? 's' : ''}`
        : `${value}${key.includes('surface') || key.startsWith('land_') ? ' m²' : ''}`;
      entries.push(`${fieldLabels[key]} : ${formatted}`);
      continue;
    }
    const input = form.querySelector(`[name="${CSS.escape(key)}"][value="${CSS.escape(value)}"]`);
    const text = input?.closest('label')?.textContent?.trim() || value;
    entries.push(text.replace(/\s+/g, ' '));
  }
  if (form.elements.scored_only?.checked) entries.push('Avec référence DVF');
  document.querySelector('#active-filters').innerHTML = entries.map(value => `<span class="filter-chip">${esc(value)}</span>`).join('');
  document.querySelector('#filter-count').textContent = entries.length;
  document.querySelector('#filter-toggle').setAttribute('aria-label', `Afficher les filtres, ${entries.length} actif${entries.length > 1 ? 's' : ''}`);
}

async function loadDeals(append=false) {
  if (state.loading) state.controller?.abort();
  state.loading = true; state.controller = new AbortController();
  if (!append) { state.offset = 0; loadingCards(); renderActiveFilters(); }
  try {
    const response = await apiFetch(`/api/deals?${params()}`, { signal: state.controller.signal });
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Erreur de lecture');
    renderMetrics(data.summary);
    if (!append) cards.innerHTML = '';
    cards.insertAdjacentHTML('beforeend', data.items.map(card).join(''));
    if (!data.items.length && !append) cards.innerHTML = `<div class="empty"><strong>Aucune annonce pour le moment</strong>La collecte locale alimente cette page automatiquement. Modifiez les filtres ou revenez après le prochain lot.</div>`;
    state.offset += data.items.length; loadMore.hidden = !data.has_more;
    cards.setAttribute('aria-busy','false');
  } catch (error) {
    if (error.name !== 'AbortError') cards.innerHTML = `<div class="empty error"><strong>Base momentanément indisponible</strong>${esc(error.message)}</div>`;
  } finally { state.loading = false; }
}

function option(name, value) {
  const colorClass = name === 'type_bien' ? `type-${value}` : 'source';
  return `<label class="check"><input type="checkbox" name="${name}" value="${esc(value)}"><span class="swatch ${esc(colorClass)}"></span><b>${esc(labels[value] || sourceLabels[value] || value)}</b></label>`;
}
async function loadFilters() {
  try {
    const response = await apiFetch('/api/filters'); const data = await response.json();
    if (!response.ok) throw new Error(data.detail);
    const selectedSources = new Set(new FormData(form).getAll('source'));
    const selectedTypes = new Set(new FormData(form).getAll('type_bien'));
    document.querySelector('#source-options').innerHTML = data.sources.length ? data.sources.map(x=>option('source',x)).join('') : '<small>Aucune source collectée</small>';
    document.querySelector('#type-options').innerHTML = data.types.length ? data.types.map(x=>option('type_bien',x)).join('') : '<small>Aucun type disponible</small>';
    form.querySelectorAll('[name="source"]').forEach(input => { input.checked = selectedSources.has(input.value); });
    form.querySelectorAll('[name="type_bien"]').forEach(input => { input.checked = selectedTypes.has(input.value); });
    const slider = document.querySelector('#price-max-range');
    const ceiling = Math.max(500000, Math.min(5000000, Math.ceil((data.price_p99 || 2000000) / 100000) * 100000));
    if (!form.elements.price_max.value) slider.value = ceiling;
    slider.max = ceiling;
    updatePriceRange();
    const status = document.querySelector('#db-status'); status.innerHTML = `<span class="status-dot"></span>${number.format(data.total)} annonces · ${number.format(data.reference_count)} codes postaux DVF`;
  } catch (error) {
    document.querySelector('#db-status').textContent = 'Base indisponible';
    const retry = '<button class="filter-retry" type="button">Réessayer le chargement</button>';
    document.querySelector('#source-options').innerHTML = retry;
    document.querySelector('#type-options').innerHTML = retry;
  }
}

function updatePriceRange() {
  const slider = document.querySelector('#price-max-range');
  const value = Number(slider.value), maximum = Number(slider.max);
  const progress = maximum ? value / maximum * 100 : 100;
  slider.style.setProperty('--range-progress', `${progress}%`);
  document.querySelector('#price-range-output').textContent = value >= maximum ? 'Aucun plafond' : euro(value);
}

departmentSearch.addEventListener('focus', () => renderDepartments(departmentValue.value ? '' : departmentSearch.value));
departmentSearch.addEventListener('click', () => renderDepartments(departmentValue.value ? '' : departmentSearch.value));
departmentSearch.addEventListener('input', () => {
  const hadValue = Boolean(departmentValue.value);
  if (hadValue) {
    departmentValue.value = '';
    departmentSearch.closest('.combo-control').classList.remove('has-value');
    departmentCombo.querySelector('.combo-clear').hidden = true;
    clearCity(false);
    document.querySelector('#city-hint').textContent = 'Villes disponibles dans toute la France';
    loadDeals(false);
  }
  renderDepartments(departmentSearch.value);
});
departmentSearch.addEventListener('keydown', comboKeyboard);
departmentList.addEventListener('click', event => {
  const option = event.target.closest('.combo-option');
  if (option) selectDepartment(option.dataset.code, option.dataset.name);
});
departmentCombo.querySelector('.combo-clear').addEventListener('click', () => {
  departmentValue.value = '';
  departmentSearch.value = '';
  departmentSearch.closest('.combo-control').classList.remove('has-value');
  departmentCombo.querySelector('.combo-clear').hidden = true;
  closeCombo(departmentCombo);
  clearCity(false);
  document.querySelector('#city-hint').textContent = 'Villes disponibles dans toute la France';
  loadDeals(false);
});

let cityDebounce;
citySearch.addEventListener('focus', () => loadCities(cityValue.value ? '' : citySearch.value));
citySearch.addEventListener('click', () => loadCities(cityValue.value ? '' : citySearch.value));
citySearch.addEventListener('input', () => {
  if (cityValue.value) {
    cityValue.value = '';
    citySearch.closest('.combo-control').classList.remove('has-value');
    cityCombo.querySelector('.combo-clear').hidden = true;
    loadDeals(false);
  }
  clearTimeout(cityDebounce);
  cityDebounce = setTimeout(() => loadCities(citySearch.value), 220);
});
citySearch.addEventListener('keydown', comboKeyboard);
cityList.addEventListener('click', event => {
  const option = event.target.closest('.combo-option');
  if (option) selectCity(option.dataset.name);
});
cityCombo.querySelector('.combo-clear').addEventListener('click', () => clearCity(true));
document.addEventListener('click', event => {
  if (!event.target.closest('.search-combo')) document.querySelectorAll('.search-combo').forEach(closeCombo);
});
document.querySelector('#price-max-range').addEventListener('input', event => {
  const slider = event.currentTarget;
  form.elements.price_max.value = Number(slider.value) >= Number(slider.max) ? '' : slider.value;
  updatePriceRange();
});
document.querySelector('#price-max-number').addEventListener('input', event => {
  const slider = document.querySelector('#price-max-range');
  slider.value = event.currentTarget.value ? Math.min(Number(event.currentTarget.value), Number(slider.max)) : slider.max;
  updatePriceRange();
});

async function openDetail(source, id) {
  dialogContent.innerHTML = '<div class="dialog-body"><p>Chargement de l’analyse…</p></div>'; dialog.showModal();
  try {
    const response = await apiFetch(`/api/annonces/${encodeURIComponent(source)}/${encodeURIComponent(id)}`);
    const data = await response.json(); if (!response.ok) throw new Error(data.detail);
    const item = data.item;
    const details = jsonValue(item.details_json, {});
    const images = jsonValue(item.images_json, []).map(safeUrl).filter(Boolean);
    const listingUrl = safeUrl(item.url);
    const gallery = images.length ? `<div class="detail-gallery">${images.slice(0,8).map((url,index)=>`<a href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}" alt="Photo ${index+1} de ${esc(displayTitle(item))}" loading="lazy"></a>`).join('')}</div>` : '';
    const characteristics = Object.entries(details).filter(([,value])=>value != null && String(value).trim()).map(([key,value])=>`<div><small>${esc(key)}</small><strong>${esc(String(value).replace(/\n/g,' '))}</strong></div>`).join('');
    const comparisonWarnings = [];
    if (item.motif_verification) comparisonWarnings.push(item.motif_verification);
    if (item.risques_evaluation) comparisonWarnings.push(item.risques_evaluation);
    if (item.type_bien === 'maison' && item.surface_terrain == null) comparisonWarnings.push('Surface de terrain inconnue : la valeur d’une maison ne peut pas être comparée précisément.');
    if (item.type_bien === 'maison' && (item.lat == null || item.lng == null)) comparisonWarnings.push('Adresse exacte non fournie par la source : les ventes sont comparées à l’échelle de la commune, sans correction de micro-quartier.');
    if (['F','G'].includes(String(item.dpe || '').toUpperCase())) comparisonWarnings.push(`DPE ${item.dpe} : l’état énergétique n’est pas contenu dans DVF et doit être chiffré séparément avant toute offre.`);
    const comparisonWarningHtml = comparisonWarnings.length ? `<div class="data-warning"><strong>Limites importantes de cette estimation</strong>${comparisonWarnings.map(value=>`<span>${esc(value)}</span>`).join('')}</div>` : '';
    const rows = data.recent_sales.map(sale => `<tr><td>${esc(sale.date_mutation)}</td><td>${esc(sale.commune || '')}</td><td>${num(sale.surface_bati,' m²')}</td><td>${sale.nombre_pieces == null ? '—' : number.format(sale.nombre_pieces)}</td><td>${num(sale.surface_terrain,' m²')}</td><td>${euro(sale.valeur_fonciere)}</td><td>${euro(sale.prix_m2)}</td></tr>`).join('');
    dialogContent.innerHTML = `<div class="dialog-body">
      <header class="detail-header"><div><p class="eyebrow">${esc(sourceLabels[item.source] || item.source)}${item.reference_annonce ? ` · Réf. ${esc(item.reference_annonce)}` : ''}</p><h2 id="dialog-title">${esc(displayTitle(item))}</h2><p class="detail-location">${esc([item.ville,item.code_postal].filter(Boolean).join(' · '))}</p></div><div class="detail-price"><small>${item.categorie==='location'?'Loyer affiché':'Prix affiché'}</small><strong>${euro(item.categorie==='location'?item.loyer:item.prix)}</strong></div></header>
      ${gallery}
      <section class="detail-section"><h3>Caractéristiques du bien</h3><div class="detail-facts"><div><small>Type</small><strong>${esc(labels[item.type_bien]||'Bien immobilier')}</strong></div><div><small>Surface</small><strong>${num(item.surface_bati,' m²')}</strong></div><div><small>Terrain</small><strong>${num(item.surface_terrain,' m²')}</strong></div><div><small>Pièces</small><strong>${item.nb_pieces==null?'—':number.format(item.nb_pieces)}</strong></div><div><small>Chambres</small><strong>${item.nb_chambres==null?'—':number.format(item.nb_chambres)}</strong></div><div><small>DPE / GES</small><strong>${esc(item.dpe||'—')} / ${esc(item.ges||'—')}</strong></div>${characteristics}</div></section>
      <section class="detail-section"><h3>Description complète</h3><div class="detail-description">${descriptionHtml(item.description)}</div></section>
      <section class="detail-section"><h3>Annonceur et provenance</h3><div class="detail-meta"><div><small>Annonceur</small><strong>${esc(item.seller_name||'Non précisé')}</strong></div><div><small>Source</small><strong>${esc(sourceLabels[item.source]||item.source)}</strong></div><div><small>Référence</small><strong>${esc(item.reference_annonce||'Non précisée')}</strong></div></div>${listingUrl?`<a class="detail-source-link" href="${esc(listingUrl)}" target="_blank" rel="noopener">Ouvrir l’annonce originale</a>`:''}</section>
      <section class="detail-section"><h3>Analyse du prix</h3>${comparisonWarningHtml}${item.prix_trop_bas ? `<div class="data-warning"><strong>Données à vérifier</strong>${esc(item.motif_verification || 'La comparaison directe est suspendue.')}</div>` : `<div class="dialog-grid"><div><small>Prix au m²</small><strong>${euro(item.prix_m2)} / m²</strong></div><div><small>Médiane des comparables</small><strong>${euro(item.median_eur_m2)} / m²</strong></div><div><small>Ventes retenues</small><strong>${item.nb_ventes==null?'—':number.format(item.nb_ventes)}</strong></div><div><small>Surface médiane comparée</small><strong>${num(item.surface_mediane_reference,' m²')}</strong></div><div><small>Décote</small><strong>${item.decote==null?'—':`${num(item.decote*100)} %`}</strong></div><div><small>Confiance</small><strong>${esc(item.confiance)}</strong></div></div>`}</section>
      ${investmentBlock(item)}
      ${financingSimulator(item)}
      ${historySection(data.price_history || [])}
      ${duplicatesSection(data.duplicates || [])}
      ${item.segment === 'residentiel' ? rentalAnalysis(item) : ''}
      <section class="detail-section"><h3>Ventes DVF les plus comparables</h3>${rows ? `<div class="sales-wrap"><table class="sales-table"><thead><tr><th>Date</th><th>Commune</th><th>Surface</th><th>Pièces</th><th>Terrain</th><th>Prix</th><th>€/m²</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p>Moins de cinq mutations suffisamment comparables : aucun prix cible n’est calculé.</p>'}</section>
    </div>`;
    bindSimulator(item);
  } catch (error) { dialogContent.innerHTML = `<div class="dialog-body"><h2 id="dialog-title">Analyse indisponible</h2><p>${esc(error.message)}</p></div>`; }
}

function historySection(history) {
  if (history.length < 2) return '';
  const rows = history.map(point => `<tr><td>${esc(new Date(point.date).toLocaleDateString('fr-FR'))}</td><td>${euro(point.prix)}</td></tr>`).join('');
  return `<section class="detail-section"><h3>Historique du prix</h3><div class="sales-wrap"><table class="sales-table"><thead><tr><th>Observé le</th><th>Prix</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function duplicatesSection(duplicates) {
  if (!duplicates.length) return '';
  const rows = duplicates.map(entry => {
    const link = safeUrl(entry.url);
    return `<li><strong>${esc(sourceLabels[entry.source] || entry.source)}</strong> · ${euro(entry.prix)}${link ? ` · <a href="${esc(link)}" target="_blank" rel="noopener">voir</a>` : ''}</li>`;
  }).join('');
  return `<section class="detail-section"><h3>Aussi publiée sur</h3><ul class="duplicate-list">${rows}</ul><p class="invest-note">Un écart de prix entre plateformes est un argument de négociation.</p></section>`;
}

function financingSimulator(item) {
  if (!['residentiel','immeuble','murs'].includes(item.segment) || item.loyer_mensuel_retenu == null || !item.prix) return '';
  const h = assumptions || {};
  return `<section class="detail-section simulator" data-simulator>
    <h3>Simulation de financement</h3>
    <div class="field-grid simulator-inputs">
      <label><span>Prix négocié</span><div class="unit-input"><input data-sim="prix" type="number" step="1000" value="${Math.round(item.prix)}"><i>€</i></div></label>
      <label><span>Loyer mensuel</span><div class="unit-input"><input data-sim="loyer" type="number" step="10" value="${Math.round(item.loyer_mensuel_retenu)}"><i>€</i></div></label>
      <label><span>Travaux</span><div class="unit-input"><input data-sim="travaux" type="number" step="1000" value="${Math.round(item.travaux_estimes || 0)}"><i>€</i></div></label>
      <label><span>Apport</span><div class="unit-input"><input data-sim="apport" type="number" step="1" value="${h.apport_pct ?? 10}"><i>%</i></div></label>
      <label><span>Taux du crédit</span><div class="unit-input"><input data-sim="taux" type="number" step="0.05" value="${h.taux_credit ?? 3.4}"><i>%</i></div></label>
      <label><span>Durée</span><div class="unit-input"><input data-sim="duree" type="number" step="1" value="${h.duree_credit ?? 20}"><i>ans</i></div></label>
    </div>
    <div class="invest-grid" data-sim-output></div>
    <p class="invest-note">Frais d’acquisition ${h.notaire_pct ?? 7.5} %, ${h.vacance_mois ?? 1} mois de vacance par an, taxe foncière ${item.taxe_fonciere ? 'publiée' : 'estimée'} (${euro(item.taxe_fonciere_retenue)}), entretien ${h.entretien_pct ?? 5} % et assurance ${euro(h.pno_eur ?? 200)}. Fiscalité non incluse.</p>
  </section>`;
}

function bindSimulator(item) {
  const root = dialogContent.querySelector('[data-simulator]');
  if (!root) return;
  const h = assumptions || {};
  const compute = () => {
    const value = name => Number(root.querySelector(`[data-sim="${name}"]`).value) || 0;
    const price = value('prix'), rent = value('loyer'), works = value('travaux');
    const total = price * (1 + (h.notaire_pct ?? 7.5) / 100) + works;
    const collected = rent * (12 - (h.vacance_mois ?? 1));
    const net = collected - (item.taxe_fonciere_retenue || 0) - (item.copro_non_recuperable || 0)
      - (h.pno_eur ?? 200) - collected * ((h.entretien_pct ?? 5) + (h.gestion_pct ?? 0)) / 100;
    const borrowed = total * (1 - value('apport') / 100);
    const rate = value('taux') / 1200, months = value('duree') * 12;
    const payment = months ? (rate ? borrowed * rate / (1 - Math.pow(1 + rate, -months)) : borrowed / months) : 0;
    const cashflow = net / 12 - payment;
    root.querySelector('[data-sim-output]').innerHTML = [
      ['Coût total', euro(total)], ['Rendement brut', pct(price ? rent * 12 / price * 100 : null)],
      ['Rendement net', pct(total ? net / total * 100 : null)], ['Mensualité', `${euro(payment)} / mois`],
      ['Cash-flow', `${cashflow >= 0 ? '+' : ''}${euro(cashflow)} / mois`]
    ].map(([k, v]) => `<div><small>${k}</small><strong>${v}</strong></div>`).join('');
    root.querySelector('[data-sim-output]').classList.toggle('positive', cashflow >= 0);
  };
  root.addEventListener('input', compute);
  compute();
}

function applyPreset(name) {
  const preset = presets[name] || presets.all;
  document.querySelector('#segment-value').value = preset.segment;
  document.querySelector('#auctions-value').value = preset.auctions;
  document.querySelector('#sort-select').value = preset.sort;
  document.querySelectorAll('.strategy-tabs [data-preset]').forEach(tab => tab.setAttribute('aria-selected', String(tab.dataset.preset === name)));
  loadDeals(false);
}

document.querySelector('.strategy-tabs').addEventListener('click', event => {
  const tab = event.target.closest('[data-preset]');
  if (tab) applyPreset(tab.dataset.preset);
});

async function loadAssumptions() {
  try {
    const response = await apiFetch('/api/hypotheses');
    assumptions = await response.json();
    document.querySelector('#assumptions-note').textContent = `Hypothèses : crédit ${assumptions.taux_credit} % sur ${assumptions.duree_credit} ans, apport ${assumptions.apport_pct} %, frais ${assumptions.notaire_pct} %, ${assumptions.vacance_mois} mois de vacance, DPE F/G et travaux chiffrés. Réglables dans .env (INVEST_*).`;
  } catch { /* les valeurs par défaut du simulateur restent utilisables */ }
}
loadAssumptions();

let debounce;
form.addEventListener('input', event => {
  if (event.target.classList.contains('combo-search')) return;
  clearTimeout(debounce); debounce = setTimeout(()=>loadDeals(false), 350);
});
form.addEventListener('change', event => { if (!event.target.classList.contains('combo-search')) loadDeals(false); });
document.querySelector('#sort-select').addEventListener('change', () => loadDeals(false));
document.querySelector('#reset-filters').addEventListener('click', () => {
  form.reset();
  departmentSearch.value = ''; departmentValue.value = '';
  departmentSearch.closest('.combo-control').classList.remove('has-value');
  departmentCombo.querySelector('.combo-clear').hidden = true;
  clearCity(false);
  document.querySelector('#city-hint').textContent = 'Villes disponibles dans toute la France';
  document.querySelectorAll('.search-combo').forEach(closeCombo);
  const slider = document.querySelector('#price-max-range'); slider.value = slider.max; updatePriceRange();
  applyPreset('all');
});
document.querySelector('#filter-toggle').addEventListener('click', () => {
  const panel = document.querySelector('#filters-panel');
  const desktop = window.matchMedia('(min-width: 761px)').matches;
  let open;
  if (desktop) {
    open = document.querySelector('#workspace').classList.toggle('filters-collapsed') === false;
  } else {
    open = panel.classList.toggle('is-open');
  }
  document.querySelector('#filter-toggle').setAttribute('aria-expanded', String(open));
  document.querySelector('#filter-toggle-label').textContent = open ? 'Masquer les filtres' : 'Afficher les filtres';
  if (open && !desktop) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
});
loadMore.addEventListener('click', () => loadDeals(true));
cards.addEventListener('click', event => { const button = event.target.closest('.detail-button'); if (button) openDetail(button.dataset.source, button.dataset.id); });
form.addEventListener('click', event => { if (event.target.closest('.filter-retry')) loadFilters(); });
dialog.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });

loadFilters(); loadDeals(false);

// La collecte écrit progressivement en base : la liste se met à jour sans recharger la page.
setInterval(() => {
  if (!document.hidden && !state.loading) {
    loadFilters();
    loadDeals(false);
  }
}, 60_000);


// Recherches sauvegardées : les filtres courants deviennent une alerte.
function currentFilters() {
  const query = params();
  query.delete('limit'); query.delete('offset');
  return query.toString();
}

function applyFilters(queryString) {
  const query = new URLSearchParams(queryString);
  form.reset();
  departmentValue.value = ''; departmentSearch.value = '';
  departmentSearch.closest('.combo-control').classList.remove('has-value');
  departmentCombo.querySelector('.combo-clear').hidden = true;
  clearCity(false);
  for (const [key, value] of query.entries()) {
    if (key === 'sort') { document.querySelector('#sort-select').value = value; continue; }
    if (key === 'department') {
      const match = departments.find(([code]) => code === value);
      if (match) {
        departmentValue.value = match[0]; departmentSearch.value = `${match[0]} · ${match[1]}`;
        departmentSearch.closest('.combo-control').classList.add('has-value');
        departmentCombo.querySelector('.combo-clear').hidden = false;
      }
      continue;
    }
    if (key === 'city') {
      cityValue.value = value; citySearch.value = value;
      citySearch.closest('.combo-control').classList.add('has-value');
      cityCombo.querySelector('.combo-clear').hidden = false;
      continue;
    }
    const fields = [...form.querySelectorAll(`[name="${CSS.escape(key)}"]`)];
    const box = fields.find(field => field.type === 'checkbox' && field.value === value);
    if (box) box.checked = true;
    else if (fields[0] && fields[0].type !== 'checkbox') fields[0].value = value;
  }
  const preset = Object.entries(presets).find(([, item]) =>
    item.segment === (query.get('segment') || '') && item.auctions === (query.get('auctions') || ''));
  document.querySelectorAll('.strategy-tabs [data-preset]').forEach(tab =>
    tab.setAttribute('aria-selected', String(Boolean(preset) && tab.dataset.preset === preset[0])));
  loadDeals(false);
}

async function loadSavedSearches() {
  const list = document.querySelector('#saved-list');
  try {
    const response = await apiFetch('/api/recherches');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail);
    list.innerHTML = data.items.length ? data.items.map(item => `
      <div class="saved-item" data-id="${esc(item.id)}" data-filters="${esc(item.filtres)}">
        <button type="button" class="saved-apply"><strong>${esc(item.nom)}</strong>
          <small>${number.format(item.total)} annonce${item.total > 1 ? 's' : ''}${item.recentes ? ` · <em>${number.format(item.recentes)} nouvelle${item.recentes > 1 ? 's' : ''} ou baissée${item.recentes > 1 ? 's' : ''} (48 h)</em>` : ''}</small>
        </button>
        <button type="button" class="saved-delete" aria-label="Supprimer la recherche ${esc(item.nom)}">×</button>
      </div>`).join('') : '<p class="filter-note">Réglez les filtres puis « Enregistrer et m’alerter ».</p>';
  } catch (error) {
    list.innerHTML = `<p class="filter-note">Recherches indisponibles : ${esc(error.message)}</p>`;
  }
}

document.querySelector('#saved-list').addEventListener('click', async event => {
  const item = event.target.closest('.saved-item');
  if (!item) return;
  if (event.target.closest('.saved-delete')) {
    if (!confirm('Supprimer cette recherche et son alerte ?')) return;
    await apiFetch(`/api/recherches/${encodeURIComponent(item.dataset.id)}`, { method: 'DELETE' });
    loadSavedSearches();
  } else if (event.target.closest('.saved-apply')) {
    applyFilters(item.dataset.filters);
  }
});

document.querySelector('#save-search').addEventListener('click', async () => {
  const chips = [...document.querySelectorAll('#active-filters .filter-chip')].map(chip => chip.textContent);
  const tab = document.querySelector('.strategy-tabs [aria-selected="true"] b')?.textContent;
  const suggestion = [tab && tab !== 'Toutes' ? tab : null, ...chips].filter(Boolean).join(' · ').slice(0, 80) || 'Ma recherche';
  const nom = prompt('Nom de la recherche (une notification signalera ses nouvelles annonces et baisses de prix) :', suggestion);
  if (!nom) return;
  const response = await apiFetch('/api/recherches', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nom, filtres: currentFilters(), alerte: true })
  });
  if (!response.ok) { alert('Enregistrement impossible.'); return; }
  loadSavedSearches();
});

loadSavedSearches();
