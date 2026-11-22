"""Exercise the packaged CPU runtime without downloading model weights."""

from __future__ import annotations

import argparse
import asyncio
from base64 import b64decode
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


# ONNX opset 11 fixture: ReduceSum(input_ids, axis=1, keepdims=1), cast to float.
# The normalized embedding of the token with ID 1 is [1.0].
MODEL = b64decode(
    "CAc6swEKOQoJaW5wdXRfaWRzEgNzdW0iCVJlZHVjZVN1bSoLCgRheGVzQAGgAQcqDwoIa2VlcGRpbXMYAaABAgoe"
    "CgNzdW0SBnZlY3RvciIEQ2FzdCoJCgJ0bxgBoAECEg9lbWJlZGRpbmdfc21va2VaJgoJaW5wdXRfaWRzEhkKFwgHEhMKBxIF"
    "YmF0Y2gKCBIGdG9rZW5zYh0KBnZlY3RvchITChEIARINCgcSBWJhdGNoCgIIAUIECgAQCw=="
)


async def smoke_test(models_dir: Path) -> None:
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel

    from src.memory.local_embedding import check_local_embedding_runtime, probe_local_embedding

    model_id = "embedding-smoke"
    root = models_dir / model_id / "1.0.0"
    root.mkdir(parents=True)
    (root / "model.onnx").write_bytes(MODEL)
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "测试": 1}, unk_token="[UNK]"))
    tokenizer.save(str(root / "tokenizer.json"))
    files = []
    for name in ("model.onnx", "tokenizer.json"):
        contents = (root / name).read_bytes()
        files.append({"path": name, "size": len(contents), "sha256": sha256(contents).hexdigest()})
    manifest = {
        "id": model_id, "kind": "embedding", "backend": "onnxruntime", "version": "1.0.0",
        "dimension": 1, "onnx_file": "model.onnx", "tokenizer_file": "tokenizer.json", "files": files,
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    check_local_embedding_runtime()
    if await probe_local_embedding(models_dir, model_id) != 1:
        raise RuntimeError("Unexpected embedding size from packaged CPU runtime")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_dir", type=Path)
    parser.add_argument("site_packages", nargs="?", type=Path)
    args = parser.parse_args()
    if args.site_packages is not None:
        sys.path.insert(0, str(args.site_packages.resolve()))
    sys.path.insert(0, str(args.app_dir.resolve()))
    with TemporaryDirectory() as temp:
        asyncio.run(smoke_test(Path(temp)))
    print("Packaged local embedding CPU inference passed")


if __name__ == "__main__":
    main()
