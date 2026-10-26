// =====================================================================
// Ficha de uma autorização de supressão (ASV) — Google Earth Engine
// Cole este script em https://code.earthengine.google.com e clique em Run.
//
// O que ele faz, sem baixar nenhuma imagem:
//   1. Quanta vegetação nativa havia na área no ano ANTES da licença (MapBiomas).
//   2. Quanto dela virou uso antrópico a partir do ano da licença, e em que ano.
//   3. Gráfico do NDVI mês a mês (Sentinel-2), para ver o mês da supressão.
//   4. Camadas no mapa: imagem antes, imagem depois, ano da supressão.
// =====================================================================

// ------------------------- EDITE AQUI --------------------------------
var ANO_LICENCA = 2019;

// Área da ASV. Opção A: desenhe um polígono com a ferramenta de desenho
// (canto superior esquerdo do mapa); ele vira a variável "geometry".
// Opção B: troque pela linha abaixo, com as coordenadas (lon, lat) do polígono.
// var area = ee.Geometry.Polygon([[[-49.30, -16.60], [-49.28, -16.60], [-49.28, -16.58], [-49.30, -16.58]]]);
var area = geometry;
// ---------------------------------------------------------------------

// MapBiomas Coleção 11 (1985–2025). Fixe a coleção e cite no método.
var MAPBIOMAS = ee.Image(
  'projects/mapbiomas-public/assets/brazil/lulc/collection11/mapbiomas_brazil_collection11_coverage_v3');
var PRIMEIRO_ANO = 1985;
var ULTIMO_ANO = 2025;
var NATURAL = [3, 4, 5, 6, 11, 12, 32, 49, 50];
var ANTROPICO = [9, 15, 19, 20, 21, 24, 30, 31, 35, 36, 39, 40, 41, 46, 47, 48, 62, 75];

function classe(ano) {
  return MAPBIOMAS.select('classification_' + ano);
}
function eh(img, classes) {
  return img.remap(classes, ee.List.repeat(1, classes.length), 0);
}

// Ano em que cada pixel passou de vegetação nativa (ano anterior) para uso antrópico.
var anos = ee.List.sequence(PRIMEIRO_ANO + 1, ULTIMO_ANO);
var conversao = ee.ImageCollection(anos.map(function (a) {
  a = ee.Number(a);
  var antes = eh(MAPBIOMAS.select(ee.String('classification_').cat(a.subtract(1).format('%d'))), NATURAL);
  var depois = eh(MAPBIOMAS.select(ee.String('classification_').cat(a.format('%d'))), ANTROPICO);
  var mudou = antes.multiply(depois);
  return ee.Image.constant(a).toInt16().updateMask(mudou).rename('ano');
})).min();

// --- Números da ficha ------------------------------------------------
var haPixel = ee.Image.pixelArea().divide(10000);
var nativaAntes = eh(classe(ANO_LICENCA - 1), NATURAL);
var suprimida = conversao.gte(ANO_LICENCA).unmask(0).multiply(nativaAntes);

var soma = function (img) {
  return img.multiply(haPixel).reduceRegion({
    reducer: ee.Reducer.sum(), geometry: area, scale: 30, maxPixels: 1e9
  }).values().get(0);
};
var haArea = area.area(1).divide(10000);
var haNativa = ee.Number(soma(nativaAntes));
var haSuprimida = ee.Number(soma(suprimida));

print('Área desenhada (ha)', haArea.format('%.1f'));
print('Vegetação nativa em ' + (ANO_LICENCA - 1) + ' (ha)', haNativa.format('%.1f'));
print('Suprimida a partir de ' + ANO_LICENCA + ' (ha)', haSuprimida.format('%.1f'));
print('Suprimida (% da vegetação nativa)',
  ee.Algorithms.If(haNativa.gt(0.05), haSuprimida.divide(haNativa).multiply(100).format('%.1f'), 'sem vegetação nativa'));

// Em que ano foi suprimida (hectares por ano, só a partir da licença)
var porAno = haPixel.addBands(conversao.updateMask(conversao.gte(ANO_LICENCA)).updateMask(nativaAntes))
  .reduceRegion({
    reducer: ee.Reducer.sum().group({groupField: 1, groupName: 'ano'}),
    geometry: area, scale: 30, maxPixels: 1e9
  });
print('Hectares suprimidos por ano', porAno.get('groups'));

// --- NDVI mês a mês (Sentinel-2) --------------------------------------
function ndviS2(img) {
  var scl = img.select('SCL');
  var limpo = scl.neq(3).and(scl.neq(8)).and(scl.neq(9)).and(scl.neq(10)).and(scl.neq(11));
  return img.updateMask(limpo).normalizedDifference(['B8', 'B4']).rename('NDVI')
    .copyProperties(img, ['system:time_start']);
}
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterBounds(area)
  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 60));

var inicio = ee.Date.fromYMD(ANO_LICENCA - 2, 1, 1);
var meses = ee.List.sequence(0, 12 * 5 - 1);
var mensal = ee.ImageCollection(meses.map(function (m) {
  var d0 = inicio.advance(m, 'month');
  var col = s2.filterDate(d0, d0.advance(1, 'month')).map(ndviS2);
  return col.median().set('system:time_start', d0.millis()).set('n', col.size());
})).filter(ee.Filter.gt('n', 0));

print(ui.Chart.image.series({
  imageCollection: mensal.select('NDVI'), region: area,
  reducer: ee.Reducer.median(), scale: 10
}).setOptions({
  title: 'NDVI mensal (Sentinel-2). Supressão = queda que não volta na chuva seguinte',
  vAxis: {title: 'NDVI', viewWindow: {min: 0, max: 1}}, hAxis: {title: 'mês'},
  pointSize: 3, lineWidth: 1, legend: {position: 'none'}
}));

// --- Camadas no mapa ---------------------------------------------------
function rgbSeco(ano) {
  // Estação seca (jun–set): menos nuvem, contraste maior entre mata e solo.
  return s2.filterDate(ano + '-06-01', ano + '-10-01').median();
}
var visRGB = {bands: ['B4', 'B3', 'B2'], min: 0, max: 2500};
Map.centerObject(area, 15);
Map.addLayer(rgbSeco(ANO_LICENCA - 1).clip(area.buffer(1500)), visRGB, 'Antes: seca de ' + (ANO_LICENCA - 1));
Map.addLayer(rgbSeco(ANO_LICENCA + 1).clip(area.buffer(1500)), visRGB, 'Depois: seca de ' + (ANO_LICENCA + 1), false);
Map.addLayer(conversao.updateMask(conversao.gte(ANO_LICENCA)).clip(area),
  {min: ANO_LICENCA, max: ULTIMO_ANO, palette: ['ffff00', 'ff8800', 'ff0000', '880000']},
  'Ano da supressão (amarelo = cedo, vermelho = tarde)', false);
Map.addLayer(ee.Image().paint(ee.FeatureCollection([ee.Feature(area)]), 0, 2), {palette: ['ff0000']}, 'Limite da ASV');
