from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Tuple


def _build_info(base_url: str):
    try:
        from hyperliquid.info import Info  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Hyperliquid SDK not installed. Install hyperliquid-python-sdk.") from e
    try:
        return Info(base_url)
    except Exception:
        return Info(base_url=base_url)


def fetch_spot_and_perp(info) -> Tuple[List[str], List[str]]:
    # Spot pairs via spot_meta: build BASE/QUOTE names
    spot_meta = info.spot_meta()
    tokens = spot_meta.get("tokens", {})
    token_map: Dict[int, Dict[str, Any]] = {int(t[1]["index"]) if isinstance(t, tuple) else int(t["index"]): (t[1] if isinstance(t, tuple) else t) for t in tokens.items()} if isinstance(tokens, dict) else {int(t["index"]): t for t in tokens}
    spot_pairs: List[str] = []
    for u in spot_meta.get("universe", []):
        try:
            base_idx, quote_idx = u.get("tokens", [None, None])
            base = token_map[int(base_idx)]["name"]
            quote = token_map[int(quote_idx)]["name"]
            spot_pairs.append(f"{base}/{quote}")
        except Exception:
            # Fallback to provided name if available
            name = u.get("name")
            if isinstance(name, str):
                spot_pairs.append(name)

    # Perp symbols via meta().universe names
    meta = info.meta()
    perps = [it.get("name") for it in meta.get("universe", []) if isinstance(it.get("name"), str)]
    return sorted(set(spot_pairs)), sorted(set(perps))


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="List Hyperliquid spot and perp markets")
    p.add_argument("--base-url", default="https://api.hyperliquid.xyz", help="Hyperliquid API base URL")
    p.add_argument("--json", action="store_true", help="Output JSON")
    args = p.parse_args(argv)

    info = _build_info(args.base_url)
    spot_pairs, perps = fetch_spot_and_perp(info)

    if args.json:
        print(json.dumps({"spot": spot_pairs, "perp": perps}, ensure_ascii=False, indent=2))
    else:
        print("Spot pairs (BASE/QUOTE):")
        for s in spot_pairs:
            print(f"  {s}")
        print("\nPerp symbols:")
        for s in perps:
            print(f"  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


