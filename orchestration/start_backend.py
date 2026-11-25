"""Start Yinuo against Magpie; --mock selects the annotation demonstration."""
import argparse
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--mock', action='store_true')
parser.add_argument('--port', type=int, default=8100)
parser.add_argument('--storage-dir', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
python = root / 'orchestration/.venv/bin/python'
if not python.exists():
    raise SystemExit('请先在 orchestration/.venv 安装 backend/requirements.txt。')
env = dict(os.environ)
env['MOCK_MODE'] = '1' if args.mock else '0'
env.setdefault('LLM_BASE_URL', 'http://127.0.0.1:3425/v1')
env.setdefault('LLM_MODEL', 'auto/deepseek-v4.1-flash')
env.setdefault('LLM_API_KEY', env.get('MAGPIE_API_KEY', 'magpie'))
env.setdefault('LLM_TIMEOUT', '180')
env.setdefault('IMAGE_PROVIDER', 'none')
if args.storage_dir:
    env['STORAGE_DIR'] = str(args.storage_dir.resolve())
print(f'衣诺后端：http://127.0.0.1:{args.port}；模式：' + ('示例' if args.mock else 'Magpie 大模型'), flush=True)
os.execve(str(python), [str(python), '-m', 'uvicorn', 'app.main:app', '--app-dir',
                       str(root / 'backend'), '--host', '127.0.0.1', '--port', str(args.port)], env)
