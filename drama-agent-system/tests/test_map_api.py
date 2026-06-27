import json
from pathlib import Path

from PIL import Image

from drama_agents.map_api import (
    NATURAL_EARTH_DATASETS,
    create_map_app,
    ensure_dataset,
    render_map,
)


def write_geojson(path: Path, features: list[dict]) -> None:
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )


def polygon_feature(name: str, coordinates: list[list[float]]) -> dict:
    return {
        "type": "Feature",
        "properties": {"NAME": name, "NAME_ZH": name},
        "geometry": {"type": "Polygon", "coordinates": [coordinates]},
    }


def line_feature(name: str, coordinates: list[list[float]]) -> dict:
    return {
        "type": "Feature",
        "properties": {"NAME": name},
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


def point_feature(name: str, coordinates: list[float]) -> dict:
    return {
        "type": "Feature",
        "properties": {"NAME": name, "POP_MAX": 1000000},
        "geometry": {"type": "Point", "coordinates": coordinates},
    }


def seed_map_data(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    write_geojson(
        data_dir / NATURAL_EARTH_DATASETS["countries"].filename,
        [
            polygon_feature("China", [[70, 15], [140, 15], [140, 55], [70, 55], [70, 15]]),
            polygon_feature("India", [[68, 5], [98, 5], [98, 36], [68, 36], [68, 5]]),
        ],
    )
    write_geojson(
        data_dir / NATURAL_EARTH_DATASETS["lakes"].filename,
        [polygon_feature("Lake", [[105, 30], [110, 30], [110, 35], [105, 35], [105, 30]])],
    )
    write_geojson(
        data_dir / NATURAL_EARTH_DATASETS["rivers"].filename,
        [line_feature("River", [[90, 20], [100, 25], [115, 32], [125, 40]])],
    )
    write_geojson(
        data_dir / NATURAL_EARTH_DATASETS["populated_places"].filename,
        [point_feature("Beijing", [116.4, 39.9])],
    )


def test_ensure_dataset_reuses_cached_file(tmp_path):
    dataset = NATURAL_EARTH_DATASETS["countries"]
    cached = tmp_path / dataset.filename
    cached.write_text("cached", encoding="utf-8")

    calls = []
    result = ensure_dataset("countries", tmp_path, downloader=lambda url, path: calls.append((url, path)))

    assert result == cached
    assert calls == []


def test_ensure_dataset_downloads_missing_file(tmp_path):
    def fake_downloader(url: str, destination: Path) -> None:
        destination.write_text(f"downloaded from {url}", encoding="utf-8")

    path = ensure_dataset("countries", tmp_path, downloader=fake_downloader)

    assert path.exists()
    assert "downloaded from https://" in path.read_text(encoding="utf-8")


def test_render_map_writes_png_from_local_geojson(tmp_path):
    data_dir = tmp_path / "natural_earth"
    seed_map_data(data_dir)

    output = render_map(
        data_dir=data_dir,
        output_path=tmp_path / "china.png",
        region="china",
        title="中国区域底图",
        width=640,
        height=420,
        show_cities=True,
    )

    assert output.exists()
    assert output.read_bytes().startswith(b"\x89PNG")
    with Image.open(output) as image:
        assert image.size == (640, 420)


def test_map_api_returns_png_for_region(tmp_path):
    data_dir = tmp_path / "natural_earth"
    seed_map_data(data_dir)
    app = create_map_app(data_dir=data_dir, output_dir=tmp_path / "maps")
    client = app.test_client()

    response = client.get("/api/maps/render?region=china&title=测试地图&cities=1")

    assert response.status_code == 200
    assert response.content_type == "image/png"
    assert response.data.startswith(b"\x89PNG")


def test_map_api_lists_known_regions(tmp_path):
    seed_map_data(tmp_path / "natural_earth")
    app = create_map_app(data_dir=tmp_path / "natural_earth", output_dir=tmp_path / "maps")
    client = app.test_client()

    response = client.get("/api/maps/regions")

    assert response.status_code == 200
    payload = response.get_json()
    assert "china" in payload["regions"]
    assert payload["regions"]["world"]["label"] == "World"
