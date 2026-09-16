"""Download only this task's assets and executor; reuse an existing RoboTwin installation."""

import argparse
from pathlib import Path
import subprocess
import sys

from huggingface_hub import snapshot_download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('workspace/mem0'))
    parser.add_argument('--robotwin-assets', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    repo = root/'vendor/RMBench'
    if not repo.exists():
        subprocess.run(['git', 'clone', 'https://github.com/RoboTwin-Platform/RMBench', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'checkout', 'e43d39ff102ccc4c800c24e0ec54e51c94673822'], check=True)
    assets = repo/'assets'
    snapshot_download('TianxingChen/RMBench', repo_type='dataset', revision='5d8ff21038fba32ea549e9eee8497aaba9aab5cf',
        allow_patterns=['objects/003_cover/**', 'embodiments/aloha-agilex/**'], local_dir=assets, max_workers=4)
    for relative in ('objects/objaverse', 'objects/same.json', 'background_texture', 'files'):
        target = assets/relative
        if not target.exists():
            target.symlink_to((args.robotwin_assets/relative).resolve())
    for template in (assets/'embodiments/aloha-agilex').rglob('*_tmp.yml'):
        template.with_name(template.name.replace('_tmp.yml', '.yml')).write_text(
            template.read_text().replace('${ASSETS_PATH}', str(repo)))
    snapshot_download('qiuly/Mem-0-mn-RMBench', revision='b08bad88132ec7a232b707ed784cc6c14f0e7c33',
        allow_patterns=['checkpoints/cover_blocks/*','norm_stats/cover_blocks/*','README.md'],
        local_dir=root/'models/executor', max_workers=3)
    weights = root/'models/executor/checkpoints/cover_blocks'
    subprocess.run(['sha256sum', '-c', 'final_step30000.pt.sha256'], cwd=weights, check=True)
    snapshot_download('Qwen/Qwen3-VL-2B-Instruct', revision='89644892e4d85e24eaac8bacfd4f463576704203',
        allow_patterns=['*.json','*.txt','*.jinja','*.tiktoken'],
        local_dir=root/'models/Qwen3-VL-2B-Instruct', max_workers=3)
    subprocess.run([sys.executable, str(Path(__file__).with_name('patch_upstream.py')), str(repo)], check=True)


if __name__ == '__main__':
    main()
