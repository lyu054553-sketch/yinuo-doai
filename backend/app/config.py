"""配置：只从环境变量（以及 backend/.env）读取，不把密钥写进代码。

约定要求 `app.config.settings` 至少提供以下小写字段：
mock_mode / llm_model / image_provider / vision_url / storage_dir（pathlib.Path）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"


def _load_dotenv(path: Path) -> None:
    """极简 .env 读取：不覆盖已存在的环境变量。不引入额外依赖。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_path(value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


def _as_float(value: str | None, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass
class Settings:
    mock_mode: bool = True
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5vl:7b"
    llm_timeout: float = 120.0
    image_provider: str = "none"
    image_base_url: str = ""
    image_api_key: str = ""
    image_model: str = ""
    vision_url: str = "http://127.0.0.1:8001"
    storage_dir: Path = field(default_factory=lambda: BACKEND_DIR / "storage")
    backend_dir: Path = field(default_factory=lambda: BACKEND_DIR)
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)
    data_dir: Path = field(default_factory=lambda: DATA_DIR)

    @classmethod
    def load(cls) -> "Settings":
        _load_dotenv(BACKEND_DIR / ".env")
        return cls(
            # MOCK_MODE 默认开启：不调大模型，直接用 data/cases.json 的标注结果
            mock_mode=_as_bool(os.environ.get("MOCK_MODE"), True),
            llm_base_url=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
            llm_api_key=os.environ.get("LLM_API_KEY", "ollama"),
            llm_model=os.environ.get("LLM_MODEL", "qwen2.5vl:7b"),
            llm_timeout=_as_float(os.environ.get("LLM_TIMEOUT"), 120.0),
            image_provider=os.environ.get("IMAGE_PROVIDER", "none"),
            image_base_url=os.environ.get("IMAGE_BASE_URL", ""),
            image_api_key=os.environ.get("IMAGE_API_KEY", ""),
            image_model=os.environ.get("IMAGE_MODEL", ""),
            vision_url=os.environ.get("VISION_URL", "http://127.0.0.1:8001"),
            storage_dir=_as_path(os.environ.get("STORAGE_DIR"), BACKEND_DIR / "storage"),
        )

    @property
    def sessions_dir(self) -> Path:
        return self.storage_dir / "sessions"

    @property
    def files_dir(self) -> Path:
        return self.storage_dir / "files"


settings = Settings.load()
