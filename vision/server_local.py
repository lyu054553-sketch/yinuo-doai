#!/usr/bin/env python3
"""Serve the prototype and a local-only image deconstruction endpoint."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INFERENCE_SCRIPT = ROOT / "vision/scripts/predict_local.py"


def inference_python() -> str:
    candidate = ROOT / ".venv-vision/bin/python"
    return str(candidate) if candidate.exists() else sys.executable


class Handler(SimpleHTTPRequestHandler):
    server_version = "YinuoLocal/1.0"
    inference_lock = threading.Lock()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send_response(302)
            self.send_header("Location", "/prototype/case2.html")
            self.end_headers()
            return
        if self.path == "/api/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "mode": "local-only",
                    "inference_script": str(INFERENCE_SCRIPT),
                },
            )
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/deconstruct":
            self.send_json(404, {"error": "not found"})
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 20 * 1024 * 1024:
            self.send_json(400, {"error": "图片为空或超过 20MB。"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            data_url = payload.get("imageDataUrl", "")
            if not data_url.startswith("data:image/") or "," not in data_url:
                raise ValueError("仅接受 image/* 的 data URL。")
            header, encoded = data_url.split(",", 1)
            image_bytes = base64.b64decode(encoded, validate=True)
            suffix = ".png" if "png" in header.lower() else ".jpg"
        except Exception as exc:
            self.send_json(400, {"error": f"图片读取失败：{exc}"})
            return

        temp_dir = Path(tempfile.mkdtemp(prefix="yinuo-vision-"))
        image_path = temp_dir / f"upload{suffix}"
        output_path = temp_dir / "prediction.json"
        debug_path = temp_dir / "prediction.debug.json"
        image_path.write_bytes(image_bytes)
        try:
            with self.inference_lock:
                process = subprocess.run(
                    [
                        inference_python(),
                        str(INFERENCE_SCRIPT),
                        "--image",
                        str(image_path),
                        "--out",
                        str(output_path),
                        "--debug-out",
                        str(debug_path),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            if process.returncode != 0 or not output_path.exists():
                detail = (process.stderr or process.stdout or "推理进程退出").strip()
                self.send_json(500, {"error": detail[-2000:]})
                return
            fields = json.loads(output_path.read_text(encoding="utf-8"))
            diagnostics = (
                json.loads(debug_path.read_text(encoding="utf-8"))
                if debug_path.exists()
                else {}
            )
            self.send_json(
                200,
                {
                    "fields": fields,
                    "diagnostics": diagnostics,
                    "local_only": True,
                },
            )
        except subprocess.TimeoutExpired:
            self.send_json(504, {"error": "本机推理超时（300 秒）。"})
        finally:
            for path in (image_path, output_path, debug_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            try:
                temp_dir.rmdir()
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"案例二样稿：http://{args.host}:{args.port}/prototype/case2.html")
    print("推理仅在本机进行；按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
