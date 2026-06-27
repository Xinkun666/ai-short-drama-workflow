from __future__ import annotations

import json
import re
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import matplotlib

matplotlib.use("Agg")

from flask import Flask, jsonify, request, send_file
from matplotlib import font_manager
from matplotlib import pyplot as plt


Downloader = Callable[[str, Path], None]
BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class NaturalEarthDataset:
    filename: str
    url: str
    label: str


NATURAL_EARTH_BASE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"

NATURAL_EARTH_DATASETS: dict[str, NaturalEarthDataset] = {
    "countries": NaturalEarthDataset(
        filename="ne_110m_admin_0_countries.geojson",
        url=f"{NATURAL_EARTH_BASE_URL}/ne_110m_admin_0_countries.geojson",
        label="Countries",
    ),
    "lakes": NaturalEarthDataset(
        filename="ne_110m_lakes.geojson",
        url=f"{NATURAL_EARTH_BASE_URL}/ne_110m_lakes.geojson",
        label="Lakes",
    ),
    "rivers": NaturalEarthDataset(
        filename="ne_110m_rivers_lake_centerlines.geojson",
        url=f"{NATURAL_EARTH_BASE_URL}/ne_110m_rivers_lake_centerlines.geojson",
        label="Rivers",
    ),
    "populated_places": NaturalEarthDataset(
        filename="ne_110m_populated_places.geojson",
        url=f"{NATURAL_EARTH_BASE_URL}/ne_110m_populated_places.geojson",
        label="Populated places",
    ),
}


REGIONS: dict[str, dict] = {
    "world": {"label": "World", "bbox": (-180.0, -60.0, 180.0, 85.0)},
    "africa": {"label": "Africa", "bbox": (-20.0, -38.0, 55.0, 38.0)},
    "europe": {"label": "Europe", "bbox": (-12.0, 34.0, 45.0, 72.0)},
    "mediterranean": {"label": "Mediterranean", "bbox": (-10.0, 28.0, 42.0, 48.0)},
    "west_asia": {"label": "West Asia", "bbox": (25.0, 10.0, 75.0, 45.0)},
    "central_asia": {"label": "Central Asia", "bbox": (45.0, 30.0, 95.0, 56.0)},
    "south_asia": {"label": "South Asia", "bbox": (60.0, 5.0, 100.0, 38.0)},
    "east_asia": {"label": "East Asia", "bbox": (73.0, 18.0, 146.0, 54.0)},
    "china": {"label": "China", "bbox": (73.0, 18.0, 136.0, 54.0)},
    "southeast_asia": {"label": "Southeast Asia", "bbox": (88.0, -12.0, 142.0, 28.0)},
    "americas": {"label": "Americas", "bbox": (-170.0, -58.0, -30.0, 75.0)},
    "australasia": {"label": "Australasia", "bbox": (105.0, -48.0, 180.0, 5.0)},
}


def create_map_app(data_dir: Path | str, output_dir: Path | str) -> Flask:
    app = Flask(__name__)
    data_path = Path(data_dir).resolve()
    output_path = Path(output_dir).resolve()
    data_path.mkdir(parents=True, exist_ok=True)
    output_path.mkdir(parents=True, exist_ok=True)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(
            {
                "status": "ok",
                "data_dir": str(data_path),
                "datasets": {
                    name: (data_path / dataset.filename).exists()
                    for name, dataset in NATURAL_EARTH_DATASETS.items()
                },
            }
        )

    @app.route("/api/maps/regions", methods=["GET"])
    def regions():
        return jsonify({"regions": REGIONS})

    @app.route("/api/maps/render", methods=["GET", "POST"])
    def render():
        payload = request.get_json(silent=True) if request.method == "POST" else {}
        payload = payload or {}
        region = str(payload.get("region") or request.args.get("region") or "world")
        bbox_value = payload.get("bbox") or request.args.get("bbox")
        bbox = parse_bbox(bbox_value) if bbox_value else None
        title = str(payload.get("title") or request.args.get("title") or region_label(region))
        width = clamp_int(payload.get("width") or request.args.get("width"), default=1280, minimum=320, maximum=3000)
        height = clamp_int(payload.get("height") or request.args.get("height"), default=800, minimum=240, maximum=2200)
        show_cities = parse_bool(payload.get("cities", request.args.get("cities")), default=False)
        show_rivers = parse_bool(payload.get("rivers", request.args.get("rivers")), default=True)
        show_lakes = parse_bool(payload.get("lakes", request.args.get("lakes")), default=True)

        ensure_default_data(data_path)
        safe_region = slugify(region or "custom")
        with tempfile.NamedTemporaryFile(prefix=f"map_{safe_region}_", suffix=".png", dir=output_path, delete=False) as tmp:
            rendered = render_map(
                data_dir=data_path,
                output_path=Path(tmp.name),
                region=region,
                bbox=bbox,
                title=title,
                width=width,
                height=height,
                show_cities=show_cities,
                show_rivers=show_rivers,
                show_lakes=show_lakes,
            )
        return send_file(rendered, mimetype="image/png", as_attachment=False)

    return app


def ensure_default_data(data_dir: Path | str, dataset_names: Iterable[str] | None = None) -> dict[str, Path]:
    names = list(dataset_names or NATURAL_EARTH_DATASETS.keys())
    return {name: ensure_dataset(name, data_dir) for name in names}


def ensure_dataset(name: str, data_dir: Path | str, downloader: Downloader = None) -> Path:
    if name not in NATURAL_EARTH_DATASETS:
        raise ValueError(f"Unknown Natural Earth dataset: {name}")
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    dataset = NATURAL_EARTH_DATASETS[name]
    destination = data_path / dataset.filename
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    downloader = downloader or download_file
    downloader(dataset.url, destination)
    return destination


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(destination.suffix + ".part")
    urllib.request.urlretrieve(url, temp_path)
    temp_path.replace(destination)


def render_map(
    *,
    data_dir: Path | str,
    output_path: Path | str,
    region: str = "world",
    bbox: BBox | None = None,
    title: str = "",
    width: int = 1280,
    height: int = 800,
    show_cities: bool = False,
    show_rivers: bool = True,
    show_lakes: bool = True,
) -> Path:
    configure_matplotlib_fonts()
    data_path = Path(data_dir)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    bounds = bbox or bbox_for_region(region)

    fig_width = max(width, 320) / 160
    fig_height = max(height, 240) / 160
    fig, ax = plt.subplots(figsize=(fig_width, fig_height), dpi=160)
    ax.set_facecolor("#dbeaf7")
    fig.patch.set_facecolor("#f6f3ec")

    countries = load_features(data_path / NATURAL_EARTH_DATASETS["countries"].filename)
    for feature in countries:
        draw_polygon_feature(ax, feature, facecolor="#ede3ca", edgecolor="#5f6b62", linewidth=0.45)

    if show_lakes:
        for feature in load_features(data_path / NATURAL_EARTH_DATASETS["lakes"].filename):
            draw_polygon_feature(ax, feature, facecolor="#b8d8ef", edgecolor="#7aa8c8", linewidth=0.25)

    if show_rivers:
        for feature in load_features(data_path / NATURAL_EARTH_DATASETS["rivers"].filename):
            draw_line_feature(ax, feature, color="#6ea7c8", linewidth=0.55)

    if show_cities:
        draw_cities(ax, load_features(data_path / NATURAL_EARTH_DATASETS["populated_places"].filename), bounds)

    min_lon, min_lat, max_lon, max_lat = bounds
    ax.set_xlim(min_lon, max_lon)
    ax.set_ylim(min_lat, max_lat)
    ax.set_aspect("equal", adjustable="box")
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_color("#9a9489")
        spine.set_linewidth(0.8)
    if title:
        ax.set_title(title, fontsize=15, color="#25231e", pad=12)
    plt.tight_layout(pad=0.7)
    fig.savefig(output, format="png")
    plt.close(fig)
    return output


def configure_matplotlib_fonts() -> None:
    for font_path in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ):
        path = Path(font_path)
        if not path.exists():
            continue
        font_manager.fontManager.addfont(str(path))
        font = font_manager.FontProperties(fname=str(path))
        plt.rcParams["font.family"] = font.get_name()
        plt.rcParams["axes.unicode_minus"] = False
        return


def load_features(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") == "FeatureCollection":
        return [feature for feature in payload.get("features", []) if isinstance(feature, dict)]
    if payload.get("type") == "Feature":
        return [payload]
    return []


def draw_polygon_feature(ax, feature: dict, *, facecolor: str, edgecolor: str, linewidth: float) -> None:
    geometry = feature.get("geometry") or {}
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        draw_polygon(ax, coordinates, facecolor=facecolor, edgecolor=edgecolor, linewidth=linewidth)
    elif geometry_type == "MultiPolygon":
        for polygon in coordinates:
            draw_polygon(ax, polygon, facecolor=facecolor, edgecolor=edgecolor, linewidth=linewidth)


def draw_polygon(ax, polygon: list, *, facecolor: str, edgecolor: str, linewidth: float) -> None:
    if not polygon:
        return
    outer_ring = polygon[0]
    if len(outer_ring) < 3:
        return
    xs = [point[0] for point in outer_ring]
    ys = [point[1] for point in outer_ring]
    ax.fill(xs, ys, facecolor=facecolor, edgecolor=edgecolor, linewidth=linewidth, zorder=1)


def draw_line_feature(ax, feature: dict, *, color: str, linewidth: float) -> None:
    geometry = feature.get("geometry") or {}
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "LineString":
        draw_line(ax, coordinates, color=color, linewidth=linewidth)
    elif geometry_type == "MultiLineString":
        for line in coordinates:
            draw_line(ax, line, color=color, linewidth=linewidth)


def draw_line(ax, line: list, *, color: str, linewidth: float) -> None:
    if len(line) < 2:
        return
    xs = [point[0] for point in line]
    ys = [point[1] for point in line]
    ax.plot(xs, ys, color=color, linewidth=linewidth, zorder=3)


def draw_cities(ax, features: list[dict], bbox: BBox, max_count: int = 24) -> None:
    cities = []
    min_lon, min_lat, max_lon, max_lat = bbox
    for feature in features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            continue
        lon, lat = geometry.get("coordinates")[:2]
        if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
            properties = feature.get("properties") or {}
            cities.append((float(properties.get("POP_MAX") or 0), lon, lat, properties.get("NAME") or "City"))
    for _, lon, lat, name in sorted(cities, reverse=True)[:max_count]:
        ax.scatter([lon], [lat], s=10, color="#5a2f22", linewidth=0, zorder=5)
        ax.text(lon + 0.4, lat + 0.25, name, fontsize=6.5, color="#3d332d", zorder=6)


def parse_bbox(value) -> BBox:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        numbers = [float(item) for item in value]
    elif isinstance(value, str):
        numbers = [float(item.strip()) for item in value.split(",") if item.strip()]
    else:
        raise ValueError("bbox must be four comma-separated numbers")
    if len(numbers) != 4:
        raise ValueError("bbox must be four comma-separated numbers")
    min_lon, min_lat, max_lon, max_lat = numbers
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("bbox min values must be less than max values")
    return min_lon, min_lat, max_lon, max_lat


def bbox_for_region(region: str) -> BBox:
    return tuple(REGIONS.get(region, REGIONS["world"])["bbox"])


def region_label(region: str) -> str:
    return str(REGIONS.get(region, {}).get("label") or region or "Map")


def parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def clamp_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.lower()).strip("-")
    return slug or "map"
