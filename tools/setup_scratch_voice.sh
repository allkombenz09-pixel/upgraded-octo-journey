#!/usr/bin/env bash
# Fetch the Kokoro-82M scratch voice (Apache-2.0) for animatics.
# Model weights and voice tensors come from the npm registry:
#   kokoro-q8-shards (model_quantized.onnx in 6 parts) and kokoro-js (voices/*.bin).
set -euo pipefail
DEST="${FILMKIT_CACHE:-$HOME/.cache/filmkit}/kokoro"
mkdir -p "$DEST"
cd "$DEST"
if [ ! -f kokoro-q8.onnx ]; then
  curl -sSfL -o shards.tgz https://registry.npmjs.org/kokoro-q8-shards/-/kokoro-q8-shards-1.0.0.tgz
  mkdir -p shards && tar xzf shards.tgz -C shards
  cat shards/package/kokoro-q8.part{0,1,2,3,4,5}.bin > kokoro-q8.onnx
  echo "fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478  kokoro-q8.onnx" | sha256sum -c -
  rm -rf shards shards.tgz
fi
if [ ! -f voices.bin ]; then
  curl -sSfL -o kjs.tgz https://registry.npmjs.org/kokoro-js/-/kokoro-js-1.2.1.tgz
  mkdir -p kjs && tar xzf kjs.tgz -C kjs
  python3 - <<'EOF'
import glob, os
import numpy as np
voices = {os.path.basename(f)[:-4]: np.fromfile(f, dtype=np.float32).reshape(510, 1, 256)
          for f in sorted(glob.glob("kjs/package/voices/*.bin"))}
with open("voices.bin", "wb") as fh:
    np.savez(fh, **voices)
print(f"{len(voices)} voices")
EOF
  rm -rf kjs kjs.tgz
fi
echo "scratch voice ready in $DEST"
