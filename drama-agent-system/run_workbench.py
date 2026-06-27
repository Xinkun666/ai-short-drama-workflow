from __future__ import annotations

import argparse
import os
import shlex
from pathlib import Path

from drama_agents.webapp.app import create_app


def parse_args() -> argparse.Namespace:
    app_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="启动本地 AI 短剧工作站")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace", default=str(app_root.parent))
    parser.add_argument("--outputs", default=str(app_root / "outputs"))
    return parser.parse_args()


def load_local_deepseek_key() -> bool:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return False
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return False
    for raw_line in zshrc.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "DEEPSEEK_API_KEY" not in line:
            continue
        try:
            parts = shlex.split(line, comments=True, posix=True)
        except ValueError:
            continue
        if parts and parts[0] == "export":
            parts = parts[1:]
        for part in parts:
            if not part.startswith("DEEPSEEK_API_KEY="):
                continue
            value = part.split("=", 1)[1].strip()
            if value:
                os.environ["DEEPSEEK_API_KEY"] = value
                return True
    return False


def main() -> None:
    args = parse_args()
    key_loaded = load_local_deepseek_key()
    workspace = Path(args.workspace).resolve()
    outputs = Path(args.outputs).resolve()
    app = create_app(workspace=workspace, outputs=outputs)
    print(f"AI短剧工作站: http://{args.host}:{args.port}")
    print(f"workspace: {workspace}")
    print(f"outputs: {outputs}")
    if key_loaded:
        print("DEEPSEEK_API_KEY: loaded from local shell config")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
