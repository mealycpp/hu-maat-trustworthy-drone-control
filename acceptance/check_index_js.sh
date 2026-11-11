#!/usr/bin/env bash
# Extract the inline <script> of spaceops_v8/index.html and syntax-check it (offline).
T=$(mktemp -d)
/usr/bin/python3 - "$T/page.js" <<'EOF'
import re, sys
src = open('/home/hadedy/hu-telepresence-gazebo/spaceops_v8/index.html', encoding='utf8').read()
js = '\n'.join(re.findall(r'<script>(.*?)</script>', src, re.S))
open(sys.argv[1], 'w', encoding='utf8').write(js)
print('script chars:', len(js))
EOF
if command -v node >/dev/null; then node --check "$T/page.js" && echo "JS SYNTAX OK"; else echo "node not installed; skipped JS syntax check"; fi
rm -rf "$T"
