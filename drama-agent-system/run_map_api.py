from __future__ import annotations

import argparse
from pathlib import Path

from drama_agents.map_api import create_map_app, ensure_default_data


def parse_args() -> argparse.Namespace:
    app_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="启动 Natural Earth 本地地图 API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--data-dir", default=str(app_root / "data" / "natural_earth"))
    parser.add_argument("--outputs", default=str(app_root / "outputs" / "maps"))
    parser.add_argument("--download-only", action="store_true", help="只下载 Natural Earth 数据，不启动 API")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir).resolve()
    outputs = Path(args.outputs).resolve()
    datasets = ensure_default_data(data_dir)
    print("Natural Earth 数据已就绪:")
    for name, path in datasets.items():
        print(f"- {name}: {path}")
    if args.download_only:
        return

    app = create_map_app(data_dir=data_dir, output_dir=outputs)
    print(f"Natural Earth 地图 API: http://{args.host}:{args.port}")
    print(f"data: {data_dir}")
    print(f"outputs: {outputs}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
