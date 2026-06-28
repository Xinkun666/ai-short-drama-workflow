from __future__ import annotations

import argparse
import os
import shlex
from pathlib import Path

from drama_agents.webapp.app import create_app


DEFAULT_LOCAL_API_KEYS = ("DEEPSEEK_API_KEY", "ARK_API_KEY", "OPENAI_API_KEY")


def parse_args() -> argparse.Namespace:
    app_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="启动本地 AI 短剧工作站")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace", default=str(app_root.parent))
    parser.add_argument("--outputs", default=str(app_root / "outputs"))
    return parser.parse_args()


def load_local_api_keys(key_names: tuple[str, ...] = DEFAULT_LOCAL_API_KEYS) -> dict[str, bool]:
    loaded = {key_name: False for key_name in key_names}
    missing_keys = {key_name for key_name in key_names if not os.environ.get(key_name)}
    if not missing_keys:
        return loaded
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return loaded
    for raw_line in zshrc.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not any(key_name in line for key_name in missing_keys):
            continue
        try:
            parts = shlex.split(line, comments=True, posix=True)
        except ValueError:
            continue
        if parts and parts[0] == "export":
            parts = parts[1:]
        for part in parts:
            if "=" not in part:
                continue
            key_name, value = part.split("=", 1)
            if key_name not in missing_keys:
                continue
            value = value.strip()
            if value:
                os.environ[key_name] = value
                loaded[key_name] = True
                missing_keys.remove(key_name)
        if not missing_keys:
            break
    return loaded


def load_local_deepseek_key() -> bool:
    return load_local_api_keys(("DEEPSEEK_API_KEY",))["DEEPSEEK_API_KEY"]


def main() -> None:
    args = parse_args()
    loaded_keys = load_local_api_keys()
    workspace = Path(args.workspace).resolve()
    outputs = Path(args.outputs).resolve()
    app = create_app(workspace=workspace, outputs=outputs)
    print(f"AI短剧工作站: http://{args.host}:{args.port}")
    print(f"workspace: {workspace}")
    print(f"outputs: {outputs}")
    for key_name, loaded in loaded_keys.items():
        if loaded:
            print(f"{key_name}: loaded from local shell config")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
