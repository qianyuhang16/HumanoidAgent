"""Download the configured pinned official weights; never pull duplicate .bin files."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    from huggingface_hub import snapshot_download
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())['perception']
    for key in ('detector', 'segmenter'):
        provenance = config['provenance'][key]
        directory = Path(config[key]).absolute()
        snapshot_download(provenance['repo'], revision=provenance['revision'], local_dir=directory,
                          allow_patterns=['*.json', '*.txt', 'model.safetensors'])
        digest = hashlib.sha256()
        with (directory/'model.safetensors').open('rb') as stream:
            for block in iter(lambda: stream.read(8*1024*1024), b''):
                digest.update(block)
        manifest = dict(provenance, weights_sha256=digest.hexdigest())
        (directory/'provenance.json').write_text(json.dumps(manifest, indent=2)+'\n')
        print(json.dumps({key: manifest}), flush=True)


if __name__ == '__main__':
    main()
