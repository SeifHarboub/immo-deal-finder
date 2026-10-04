import json

from immo.connectors.laforet import LaforetConnector


def test_laforet_extracts_features_and_energy() -> None:
    html = r'''
    <script>window.dataLayer=[]; dataLayer.push(...[
      {"event":"viewItem","transactionType":"acheter","itemType":"Maison",
       "itemName":"Maison 6 COLOMBES","itemId":52702299,"itemCity":"COLOMBES",
       "itemZipcode":"92700","itemPrice":470000,"itemSize":145.84,
       "itemRoomsNb":6,"itemCriteria":"146 m², 6 pièces, 4 chambres"}
    ]);</script>
    <section id="section-features">
      <span>6 pièces</span><span>Surface : 145.84 m²</span>
      <span>Surf. terrain : 172 m²</span><span>Surf. séj : 38.48 m²</span>
      <span>4 chbres</span><span>2 salles de bain</span>
      <span>Cuisine Aménagée</span><span>Chauffage Electrique</span><span>Terrasse</span>
    </section>
    <div data-description="Maison avec cour et travaux.&lt;br /&gt;Stationnement possible." data-x="1"></div>
    <p>Montant estimé des dépenses annuelles d'énergie pour un usage standard entre 4 550,00 € et 6 200,00 €.</p>
    <p>Honoraires à la charge du vendeur</p><div>Référence Agence : 25482</div>
    <div data-map-center-value="{&quot;lat&quot;:48.925246,&quot;lng&quot;:2.24437}"></div>
    <svg><g id="DPE_F"><text id="data_dpe"><tspan>417</tspan></text></g>
      <g id="GES_C"><text id="data_ges"><tspan>14</tspan></text></g></svg>
    '''
    item = next(LaforetConnector().parse_page(html, "https://example.test/52702299"))
    assert item["land_surface"] == 172
    assert item["dpe"] == "F"
    assert item["ges"] == "C"
    assert item["lat"] == 48.925246
    assert item["lng"] == 2.24437
    assert item["bedrooms"] == "4"
    details = json.loads(item["details_json"])
    assert details["Surface séjour"] == 38.48
    assert details["Salles de bain"] == 2
    assert details["Chauffage"] == "Electrique"
    assert details["Dépenses énergie annuelles minimales"] == 4550
    assert details["Consommation énergétique"] == "417 kWhEP/m²/an"
