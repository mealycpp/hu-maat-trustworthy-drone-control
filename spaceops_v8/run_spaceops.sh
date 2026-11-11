#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$HERE" <<'PYTHON'
import socket,sys
sock=socket.socket()
try:
 sock.bind(('127.0.0.1',8766))
except OSError:
 print('Port 8766 already occupied. Use the existing http://localhost:8766 dashboard or stop the old SpaceOps service before relaunching.',file=sys.stderr)
 sys.exit(1)
finally:sock.close()
PYTHON
exec python3 "$HERE/spaceops_server.py" --port 8766
