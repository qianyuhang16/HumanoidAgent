"""Download public tabletop model weights into this workspace, with pinned revisions."""

import argparse
import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

from huggingface_hub import HfApi, snapshot_download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("workspace/tabletop-models"))
    parser.add_argument("--refresh", action="store_true", help="Explicitly update previously recorded model revisions")
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() and not args.refresh else {}
    for name, repo in {
        "grounding-dino": "IDEA-Research/grounding-dino-tiny",
        "sam": "facebook/sam-vit-base",
        "depth-anything": "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf",
    }.items():
        print(f"Downloading {repo}", flush=True)
        previous = manifest.get(name, {})
        revision = previous.get("revision") if previous.get("repository") == repo else None
        revision = revision or HfApi(token=False).model_info(repo).sha
        snapshot_download(repo, revision=revision, local_dir=root / name, token=False,
                          allow_patterns=["*.json", "*.safetensors", "vocab.txt", "merges.txt"], max_workers=4)
        manifest[name] = {"repository": repo, "revision": revision}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    url = "https://github.com/dougsm/ggcnn/releases/download/v0.1/ggcnn_weights_cornell.zip"
    print("Downloading GG-CNN Cornell weights", flush=True)
    with urlopen(url, timeout=120) as response:
        content = response.read()
    with ZipFile(io.BytesIO(content)) as archive:
        matches = [name for name in archive.namelist() if name.endswith("_statedict.pt")]
        if len(matches) != 1:
            raise ValueError(f"Unexpected GG-CNN archive: {matches}")
        weights = archive.read(matches[0])
        digest = hashlib.sha256(weights).hexdigest()
        expected = manifest.get("ggcnn", {}).get("sha256")
        if expected and digest != expected:
            raise ValueError("GG-CNN weights differ from the recorded checksum")
        (root / "ggcnn.pt").write_bytes(weights)
    manifest["ggcnn"] = {"url": url, "sha256": digest}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Model manifest: {root / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
