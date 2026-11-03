"""python3 -m qual_v2 <bundle_dir> --pin <receipt_sha256> [--registry PATH] [--allow-synthetic]   (run with T2c on PYTHONPATH). Exit 0 only if qualified."""
import argparse
import json
import sys
from .bundle_v2 import try_read_bundle_v2


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle"); ap.add_argument("--pin"); ap.add_argument("--registry"); ap.add_argument("--allow-synthetic", action="store_true")
    a = ap.parse_args(argv)
    b, code = try_read_bundle_v2(a.bundle, pin=a.pin, allow_synthetic=a.allow_synthetic, registry_path=a.registry)
    print(json.dumps({"qualified": b is not None, "error_code": code, "checks": b.checks if b else [], "display_policy": b.display_policy if b else None}, sort_keys=True))
    return 0 if b else 2


if __name__ == "__main__":
    sys.exit(main())
