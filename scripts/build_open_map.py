"""Build the QGIS project and the printed map of vehicles reported per state.

Run with the Python that ships with QGIS (3.40 or later), not the project's own:

    "C:\\Program Files\\QGIS 3.44.15\\bin\\python-qgis-ltr.bat" scripts\\build_open_map.py

Reads  open-data/vehicles_by_admin1.csv         (from build_open_dataset.py)
       open-data/map/sdn_admin1.geojson         (OCHA COD-AB Sudan v03, CC BY-IGO)
Writes open-data/map/vehicles_by_admin1.gpkg    boundaries joined to the counts on adm1_pcode
       open-data/map/arabati-wein.qgz           the project: styled layer, labels, print layout
       open-data/map/vehicles_by_state.png/.pdf the layout, exported

The join key is the P-code. A state whose count is withheld (1 to 4) keeps an
empty number and is drawn hatched; it is never drawn as zero.
"""
import csv
import pathlib
import sys

from qgis.core import (
    QgsApplication, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem, QgsFeature,
    QgsField, QgsFillSymbol, QgsLayoutExporter, QgsLayoutItemLabel, QgsLayoutItemLegend,
    QgsLayoutItemMap, QgsLayoutItemScaleBar, QgsLayoutPoint, QgsLayoutSize,
    QgsLinePatternFillSymbolLayer, QgsPalLayerSettings, QgsPrintLayout, QgsProject,
    QgsRendererCategory, QgsTextBufferSettings, QgsTextFormat, QgsUnitTypes,
    QgsVectorFileWriter, QgsVectorLayer, QgsVectorLayerSimpleLabeling, Qgis,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor, QFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
OD = ROOT / "open-data"
MAP = OD / "map"

# (floor, label, fill): the same classes and colours as open-data/dashboard.html
CLASSES = [(5000, "5,000 or more", "#0d366b"), (500, "500 to 4,999", "#256abf"),
           (50, "50 to 499", "#5598e7"), (5, "5 to 49", "#86b6ef")]
WITHHELD, NONE = "1 to 4 (number withheld)", "None reported in these sources"


def counts() -> dict[str, dict]:
    with open(OD / "vehicles_by_admin1.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))[1:]            # drop the HXL hashtag row
    return {r["adm1_pcode"]: r for r in rows}


def class_of(row: dict) -> str:
    if row["vehicles"] == "":
        return WITHHELD
    n = int(row["vehicles"])
    return NONE if n == 0 else next(label for floor, label, _ in CLASSES if n >= floor)


def build_layer() -> QgsVectorLayer:
    src = QgsVectorLayer(str(MAP / "sdn_admin1.geojson"), "src", "ogr")
    if not src.isValid():
        sys.exit("cannot read open-data/map/sdn_admin1.geojson")
    data = counts()
    mem = QgsVectorLayer("MultiPolygon?crs=EPSG:4326", "Vehicles reported per state", "memory")
    pr = mem.dataProvider()
    pr.addAttributes([QgsField("adm1_pcode", QVariant.String), QgsField("adm1_name_en", QVariant.String),
                      QgsField("adm1_name_ar", QVariant.String), QgsField("vehicles", QVariant.Int),
                      QgsField("vehicles_note", QVariant.String), QgsField("class", QVariant.String),
                      QgsField("map_label", QVariant.String)])
    mem.updateFields()
    feats = []
    for f in src.getFeatures():
        row = data[f["adm1_pcode"]]
        n = int(row["vehicles"]) if row["vehicles"] != "" else None
        out = QgsFeature(mem.fields())
        out.setGeometry(f.geometry())
        label = row["adm1_name_en"] + (f"\n{n:,}" if n else "")
        out.setAttributes([row["adm1_pcode"], row["adm1_name_en"], row["adm1_name_ar"], n,
                           row["vehicles_note"], class_of(row), label])
        feats.append(out)
    pr.addFeatures(feats)
    if len(feats) != len(data):
        sys.exit(f"join mismatch: {len(feats)} boundaries, {len(data)} table rows")

    gpkg = MAP / "vehicles_by_admin1.gpkg"
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = "vehicles_by_admin1"
    err = QgsVectorFileWriter.writeAsVectorFormatV3(mem, str(gpkg), QgsProject.instance().transformContext(), opts)
    if err[0] != QgsVectorFileWriter.NoError:
        sys.exit(f"could not write {gpkg}: {err}")
    layer = QgsVectorLayer(f"{gpkg}|layername=vehicles_by_admin1", "Vehicles reported per state", "ogr")
    if not layer.isValid():
        sys.exit("written GeoPackage does not load")
    return layer


def style(layer: QgsVectorLayer) -> None:
    edge = {"outline_color": "#ffffff", "outline_width": "0.4"}
    cats = [QgsRendererCategory(label, QgsFillSymbol.createSimple({"color": fill, **edge}), label)
            for _, label, fill in CLASSES]
    hatch = QgsFillSymbol.createSimple({"color": "#ffffff", "outline_color": "#9aa3b2", "outline_width": "0.3"})
    lines = QgsLinePatternFillSymbolLayer()
    lines.setLineAngle(45)
    lines.setDistance(1.6)
    lines.setLineWidth(0.5)
    lines.setColor(QColor("#5598e7"))
    hatch.appendSymbolLayer(lines)
    cats.append(QgsRendererCategory(WITHHELD, hatch, WITHHELD))
    cats.append(QgsRendererCategory(NONE, QgsFillSymbol.createSimple(
        {"color": "#ffffff", "outline_color": "#9aa3b2", "outline_width": "0.3"}), NONE))
    layer.setRenderer(QgsCategorizedSymbolRenderer("class", cats))

    fmt = QgsTextFormat()
    fmt.setFont(QFont("Segoe UI", 8))
    fmt.setSize(8)
    fmt.setColor(QColor("#10151c"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.9)
    buf.setColor(QColor("#ffffff"))
    fmt.setBuffer(buf)
    pal = QgsPalLayerSettings()
    pal.fieldName = "map_label"
    pal.multilineAlign = Qgis.LabelMultiLineAlignment.Center
    pal.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)


def text(layout, body, x, y, w, h, size, bold=False, color="#10151c"):
    item = QgsLayoutItemLabel(layout)
    item.setText(body)
    fmt = QgsTextFormat()
    font = QFont("Segoe UI")
    font.setBold(bold)
    fmt.setFont(font)
    fmt.setSize(size)
    fmt.setColor(QColor(color))
    item.setTextFormat(fmt)
    layout.addLayoutItem(item)
    item.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    item.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    return item


def build_layout(project, layer, built: str, mapped: int, total: int) -> QgsPrintLayout:
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()                      # A4 landscape, 297 x 210 mm
    layout.setName("Vehicles reported per state")
    project.layoutManager().addLayout(layout)

    text(layout, "Vehicles reported looted or lost in the Sudan war, by state", 12, 9, 200, 10, 17, bold=True)
    text(layout, f"Arabati Wein registry (mycarsd.online). Distinct vehicles whose reported location "
                 f"names one state. Build of {built}.", 12, 20, 200, 7, 9.5, color="#4b5563")

    m = QgsLayoutItemMap(layout)
    m.setRect(0, 0, 180, 160)
    m.setCrs(QgsCoordinateReferenceSystem("EPSG:32636"))      # UTM 36N: Sudan drawn at true shape
    layout.addLayoutItem(m)
    m.attemptMove(QgsLayoutPoint(12, 30, QgsUnitTypes.LayoutMillimeters))
    m.attemptResize(QgsLayoutSize(180, 168, QgsUnitTypes.LayoutMillimeters))
    m.setLayers([layer])
    from qgis.core import QgsCoordinateTransform
    ext = QgsCoordinateTransform(layer.crs(), m.crs(), project).transformBoundingBox(layer.extent())
    ext.scale(1.06)
    m.zoomToExtent(ext)

    share = round(100 * mapped / total)
    text(layout, "Read this map as a picture of the sources", 200, 32, 86, 7, 11, bold=True)
    text(layout,
         f"Only {mapped:,} of {total:,} vehicles ({share}%) name a single state in any post. "
         "The map shows that subset.\n\n"
         "A location is where a vehicle was reported, found or held. It is not where it was taken.\n\n"
         "A blank state is one these groups did not post about. It is not a state without looted "
         "vehicles.\n\n"
         "Vehicles listed under \"Kordofan\" with no state are left off: three states share the name.\n\n"
         "Counts of 1 to 4 are withheld.",
         200, 41, 86, 78, 8.6, color="#4b5563")

    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("Vehicles")
    legend.setLinkedMap(m)
    legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    root.clear()
    root.addLayer(layer)
    layout.addLayoutItem(legend)
    legend.attemptMove(QgsLayoutPoint(200, 122, QgsUnitTypes.LayoutMillimeters))

    bar = QgsLayoutItemScaleBar(layout)
    bar.setStyle("Line Ticks Up")
    bar.setLinkedMap(m)
    bar.setUnits(QgsUnitTypes.DistanceKilometers)
    bar.setUnitLabel("km")
    bar.setNumberOfSegments(2)
    bar.setNumberOfSegmentsLeft(0)
    bar.setUnitsPerSegment(250)
    layout.addLayoutItem(bar)
    bar.attemptMove(QgsLayoutPoint(14, 186, QgsUnitTypes.LayoutMillimeters))

    text(layout,
         "Data: github.com/FirasFirari/arabati-wein/open-data (CC BY 4.0). Boundaries and P-codes: OCHA, "
         "Sudan Subnational Administrative Boundaries v03 (CC BY-IGO). Projection: WGS 84 / UTM zone 36N. "
         "Boundaries shown do not imply endorsement.",
         12, 200, 274, 8, 7.2, color="#7b8494")
    return layout


def main() -> None:
    app = QgsApplication([], False)
    app.initQgis()
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:32636"))
    project.setTitle("Arabati Wein: vehicles reported per state")
    project.setFilePathStorage(Qgis.FilePathType.Relative)

    layer = build_layer()
    style(layer)
    project.addMapLayer(layer)

    import json
    built = json.loads((OD / "build.json").read_text(encoding="utf-8"))["generated_at"][:10]
    with open(OD / "location_mapping_quality.csv", encoding="utf-8", newline="") as f:
        quality = {r["mapping_class"]: r["vehicles"] for r in list(csv.DictReader(f))[1:]}
    layout = build_layout(project, layer, built, int(quality["state"]), int(quality["all_vehicles"]))

    exporter = QgsLayoutExporter(layout)
    img = QgsLayoutExporter.ImageExportSettings()
    img.dpi = 150
    r1 = exporter.exportToImage(str(MAP / "vehicles_by_state.png"), img)
    r2 = exporter.exportToPdf(str(MAP / "vehicles_by_state.pdf"), QgsLayoutExporter.PdfExportSettings())
    ok = project.write(str(MAP / "arabati-wein.qgz"))
    print(f"png={r1} pdf={r2} project_saved={ok} features={layer.featureCount()}")
    app.exitQgis()
    if r1 != QgsLayoutExporter.Success or r2 != QgsLayoutExporter.Success or not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
