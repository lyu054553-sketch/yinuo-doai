"""启动冻结的本机视觉服务（vision/server_local.py），不改 vision/ 里的任何代码。

依赖环境按顺序找：
1. 环境变量 YINUO_VISION_VENV 指定的目录；
2. ~/.local/share/yinuo/venv-vision —— 放在 iCloud 同步目录之外。项目在「文稿」里，
   macOS「优化存储」会把仓库里的 .venv 文件清成云端占位（dataless），推理进程读到它们会一直
   等下载、最终 300 秒超时；
3. 仓库内的 orchestration/.venv-vision（旧位置，仅作兜底）。

建环境（uv 有缓存时十几秒）：
    uv venv ~/.local/share/yinuo/venv-vision --python 3.12
    uv pip install --python ~/.local/share/yinuo/venv-vision/bin/python -r vision/requirements.txt
"""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
candidates = [
    Path(os.environ["YINUO_VISION_VENV"]).expanduser() if os.environ.get("YINUO_VISION_VENV") else None,
    Path.home() / ".local/share/yinuo/venv-vision",
    root / "orchestration/.venv-vision",
]
environment = next((c for c in candidates if c and (c / "bin/python").exists()), None)
if environment is None:
    raise SystemExit("找不到视觉依赖环境。请按 orchestration/start_vision.py 顶部说明用 uv 建一个。")
python = environment / "bin/python"

env = dict(
    os.environ,
    HF_HUB_OFFLINE="1",
    TRANSFORMERS_OFFLINE="1",
    PYTHONUNBUFFERED="1",
    # 仓库里的 __pycache__ 也可能被清成占位；字节码改写到 iCloud 之外。
    PYTHONPYCACHEPREFIX=str(Path.home() / ".cache/yinuo-pycache"),
)
env.pop("PYTHONPATH", None)

# server_local.py 会优先用仓库根目录的 .venv-vision 跑推理子进程；这里在不改它代码的前提下，
# 把推理解释器换成当前选中的环境。
bootstrap = (
    "import runpy, sys; sys.path.insert(0, {vision!r}); import server_local as s; "
    "s.inference_python = lambda: {python!r}; sys.argv = ['server_local.py'] + {args!r}; s.main()"
).format(vision=str(root / "vision"), python=str(python), args=sys.argv[1:] or ["--port", "8001"])

print(f"视觉依赖环境：{environment}", flush=True)
os.execve(str(python), [str(python), "-c", bootstrap], env)
