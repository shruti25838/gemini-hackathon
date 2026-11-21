"""Prepare and cache 3D assets for the Shopify demo alternatives."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import app
from backend.errors import ConnectionError

CATALOG = ROOT / "samples/catalog/demo_120_alternatives.json"
EXPANDED_CATALOG = CATALOG.with_name("demo_250_alternatives.json")
STATUS = ROOT / ".cache/demo-model-progress/status.json"
WORKERS = 6


async def build(expanded: bool = False, retry_failed: bool = False, workers: int = WORKERS):
    catalog = EXPANDED_CATALOG if expanded else CATALOG
    status = STATUS.with_name("expanded-status.json") if expanded else STATUS
    groups = json.loads(catalog.read_text())
    products = [item for group in groups for item in group["alternatives"]]
    if len({item["variant_id"] for item in products}) != len(products):
        raise ValueError("The catalog contains duplicate variants.")
    if expanded:
        original = json.loads(CATALOG.read_text())
        original_ids = {item["variant_id"] for group in original for item in group["alternatives"]}
        products = [item for item in products if item["variant_id"] not in original_ids]
    status.parent.mkdir(parents=True, exist_ok=True)
    total = len(products)
    active = 0
    ready = 0
    failures = []
    if retry_failed:
        previous = json.loads(status.read_text())
        if previous["ready"] + previous["failed"] != total or previous["active"]:
            raise ValueError("Wait for the current batch to finish before retrying.")
        failed_ids = {item["variant_id"] for item in previous["failures"]}
        products = [item for item in products if item["variant_id"] in failed_ids]
        ready = previous["ready"]
        if not products:
            return
    limit = asyncio.Semaphore(workers)

    def publish():
        stats = {"total": total, "ready": ready, "failed": len(failures),
                 "active": active, "failures": failures}
        temporary = status.with_suffix(".tmp")
        temporary.write_text(json.dumps(stats, indent=2) + "\n")
        temporary.replace(status)

    async def prepare(item):
        nonlocal active, ready
        async with limit:
            active += 1
            publish()
            variant_id = item["variant_id"]
            try:
                for attempt in range(2):
                    try:
                        record = await app.state.scenes.product(variant_id)
                        if record["model"].get("format") not in {"glb", "gltf"}:
                            raise ValueError("Unexpected model format")
                        ready += 1
                        break
                    except ConnectionError as error:
                        code = error.payload["code"]
                        if retry_failed and code in {"shop_variant_not_found", "product_image_missing",
                                                    "product_image_unavailable", "product_image_too_large"} and item.get("image_url"):
                            record = await app.state.product_models.generate_snapshot(item)
                            cache = app.state.scenes._cache_file(variant_id)
                            cache.parent.mkdir(parents=True, exist_ok=True)
                            with NamedTemporaryFile("w", dir=cache.parent, delete=False) as output:
                                json.dump(record, output)
                            Path(output.name).replace(cache)
                            ready += 1
                            break
                        if attempt or not (error.payload.get("retryable")
                                           or code in {"product_recipe_invalid", "product_recipe_missing"}):
                            raise
                        await asyncio.sleep(5)
            except Exception as error:
                reason = error.payload["code"] if isinstance(error, ConnectionError) else type(error).__name__
                failures.append({"variant_id": variant_id, "title": item["title"], "reason": reason})
            finally:
                active -= 1
                publish()
                done = ready + len(failures)
                filled = int(20 * done / total)
                print(f"[{'#' * filled}{'.' * (20 - filled)}] {done}/{total} processed, "
                      f"{ready} ready, {len(failures)} failed", flush=True)

    publish()
    async with app.router.lifespan_context(app):
        await asyncio.gather(*(prepare(item) for item in products))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expanded", action="store_true", help="Build the 130 additional variants.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failures from a finished batch.")
    parser.add_argument("--workers", type=int, help="Maximum concurrent model requests.")
    args = parser.parse_args()
    workers = args.workers if args.workers is not None else (4 if args.expanded else WORKERS)
    if workers < 1:
        parser.error("--workers must be at least one")
    asyncio.run(build(args.expanded, args.retry_failed, workers))
