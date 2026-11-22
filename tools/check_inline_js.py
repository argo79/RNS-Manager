#!/usr/bin/env python3
"""Estrae gli script inline dai template e li valida con `node --check`."""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = [
    ROOT / 'manager/templates/monitor.html',
    ROOT / 'manager/templates/index.html',
    ROOT / 'messenger/templates/lxmf_chat.html',
]


def main():
    node = shutil.which('node')
    if not node:
        print('SKIP: node non trovato nel PATH')
        return 0
    with tempfile.TemporaryDirectory(prefix='rns_inline_js_') as tmp:
        for template in TEMPLATES:
            source = template.read_text(encoding='utf-8')
            scripts = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', source, re.S)
            code = '\n;\n'.join(scripts)
            code = code.replace('{{ version }}', '1.0.0').replace('{{version}}', '1.0.0')
            target = Path(tmp) / f'{template.stem}.js'
            target.write_text(code, encoding='utf-8')
            result = subprocess.run([node, '--check', str(target)], capture_output=True, text=True)
            if result.returncode != 0:
                print(f'FAIL: {template}\n{result.stderr}')
                return 1
            print(f'OK: {template}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
