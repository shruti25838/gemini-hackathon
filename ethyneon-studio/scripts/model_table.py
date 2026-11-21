"""Write the demo product table with Shopify links and 3D model links.

Each row links the exact Shopify listing and the app's model URL for that variant,
/studio/scene/models/<numeric variant id>.glb, which serves Codex's file in samples/models/ or the app's
generated model. The 3D column says whether a model is ready now. Rerun after models are added.

    .venv/bin/python scripts/model_table.py [base_url]
"""

import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.scene import SceneStudio  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "samples" / "catalog"
ROOMS = {"gaming_room": "Gaming room", "office": "Working room", "living_room": "Living room", "bedroom": "Bedroom", "kitchen": "Kitchen"}


def main(base_url: str) -> None:
    products = json.loads((CATALOG / "demo_30.json").read_text())
    scenes = SceneStudio(shopify=None, models=None)  # only the local model lookup is used
    rows = []
    for index, product in enumerate(products, 1):
        number = product["variant_id"].rsplit("/", 1)[-1]
        size = product.get("size_m") or product.get("typical_size_m")
        state = scenes.model_state(product["variant_id"])
        rows.append({
            "#": index, "room": ROOMS.get(product["room"], product["room"]), "type": product["object_type"].replace("_", " "),
            "product": product["title"], "price_usd": f"{product['price']['amount'] / 100:.2f}",
            "size_cm": " x ".join(f"{size[axis] * 100:.0f}" for axis in ("width", "depth", "height")) if size else "",
            "shopify_link": product["listing_url"] or "", "model_link": f"{base_url}/studio/scene/models/{number}.glb",
            "model_file": f"samples/models/{number}.glb", "model_ready": "yes" if state == "cached" else "no",
            "variant_id": product["variant_id"],
        })

    with (CATALOG / "demo_30_models.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    ready = sum(row["model_ready"] == "yes" for row in rows)
    clean = lambda text: text.replace("|", "/")  # noqa: E731
    lines = [
        "# Demo products: Shopify and 3D model links", "",
        f"{len(rows)} products · {ready} with a 3D model ready · {len(rows) - ready} still to build.",
        f"3D model link: `{base_url}/studio/scene/models/<variant number>.glb`. Save new models to "
        "`samples/models/<variant number>.glb`, then rerun `.venv/bin/python scripts/model_table.py`.", "",
        "| # | Room | Type | Product | Price | Size W×D×H (cm) | Shopify | 3D model | 3D ready |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        shop = f"[listing]({row['shopify_link']})" if row["shopify_link"] else "—"
        lines.append(
            f"| {row['#']} | {row['room']} | {row['type']} | {clean(row['product'][:70])} | ${row['price_usd']} | {row['size_cm']} | "
            f"{shop} | [{row['model_file'].rsplit('/', 1)[-1]}]({row['model_link']}) | {'✅' if row['model_ready'] == 'yes' else '❌'} |"
        )
    (CATALOG / "demo_30_models.md").write_text("\n".join(lines) + "\n")
    print(f"{len(rows)} rows, {ready} models ready -> {CATALOG / 'demo_30_models.md'}")


if __name__ == "__main__":
    main(sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000")
