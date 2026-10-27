"""Check a source release without printing credential matches or file contents."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'provider-key': re.compile(r'(?i)(?:sk-(?:ant-|proj-)?[a-z0-9_-]{24,}|AIza[a-z0-9_-]{30,}|gh[pousr]_[a-z0-9_]{25,}|github_pat_[a-z0-9_]{25,})'),
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'literal-secret': re.compile(r'''(?im)(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\s*[=:]\s*["']([a-z0-9_+/=-]{24,})["']'''),
}


def main():
    issues = []
    count = 0
    asset_manifest = ROOT / 'assets/manifest.json'
    assets = json.loads(asset_manifest.read_text(encoding='utf-8')) if asset_manifest.exists() else []
    reviewed_assets = {item['file']: item['sha256'] for item in assets}
    binary_count = 0
    for p in sorted(ROOT.rglob('*')):
        if not p.is_file() or any(part in {'__pycache__', '.git', '.venv', '.pytest_cache'} for part in p.relative_to(ROOT).parts):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if p.is_symlink():
            issues.append({'file': rel, 'rule': 'symlink-not-allowed'})
            continue
        parts = p.relative_to(ROOT).parts
        if (p.name.startswith('.env') and p.name != '.env.example') or p.suffix in {'.pem', '.key', '.mhtml', '.pdf', '.docx', '.pptx'} or any(x == 'secrets' or x == 'logs' or x.startswith('logs_') or x == 'agent_traces' for x in parts):
            issues.append({'file': rel, 'rule': 'private-or-unreviewed-file'})
            continue
        if rel in reviewed_assets:
            payload = p.read_bytes()
            if p.suffix != '.png' or not payload.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(payload).hexdigest() != reviewed_assets[rel]:
                issues.append({'file': rel, 'rule': 'reviewed-image-hash-or-format-mismatch'})
            binary_count += 1
            continue
        try:
            text = p.read_text(encoding='utf-8-sig')
        except UnicodeError:
            issues.append({'file': rel, 'rule': 'unreviewed-binary'})
            continue
        count += 1
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                issues.append({'file': rel, 'line': text.count('\n', 0, match.start()) + 1, 'rule': rule})
        if p.suffix == '.ps1' and not text.isascii():
            issues.append({'file': rel, 'rule': 'non-ascii-powershell'})
        if p.suffix == '.md':
            for target in re.findall(r'!?\[[^\]]+\]\(([^)]+)\)', text):
                target = target.split('#')[0]
                if target and '://' not in target and not (p.parent / target).exists():
                    issues.append({'file': rel, 'rule': 'broken-relative-link', 'target': target})
    for rel in reviewed_assets:
        if not (ROOT / rel).is_file():
            issues.append({'file': rel, 'rule': 'missing-reviewed-asset'})
    for row in json.loads((ROOT / 'docs/source_manifest.json').read_text(encoding='utf-8')):
        p = ROOT / row['file']
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != row['sha256']:
            issues.append({'file': row['file'], 'rule': 'manifest-drift'})
    print(json.dumps({'checked_text_files': count, 'reviewed_image_files': binary_count, 'findings': issues, 'ok': not issues}, ensure_ascii=True, indent=2))
    return 1 if issues else 0


if __name__ == '__main__':
    raise SystemExit(main())
