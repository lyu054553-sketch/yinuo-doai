"""Start the password-protected external test gateway with local credentials."""
import json
import os
from pathlib import Path
import secrets
import sys

root = Path(__file__).resolve().parents[1]
directory = root / "orchestration" / "runs" / "external-test"
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
credentials_path = directory / "credentials.json"
if not credentials_path.exists():
    fd = os.open(credentials_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"username": "yinuo", "password": secrets.token_urlsafe(24)}, handle)
credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
env = dict(os.environ, YINUO_TEST_USER=credentials["username"], YINUO_TEST_PASSWORD=credentials["password"])
python = root / "orchestration" / ".venv" / "bin" / "python"
print(f"测试网关启动；访问凭据保存在 {credentials_path}，请勿提交到 Git。", flush=True)
os.execve(str(python), [str(python), "-m", "uvicorn", "test_gateway:app", "--app-dir", str(root / "orchestration"), "--host", "127.0.0.1", "--port", "4180"], env)
