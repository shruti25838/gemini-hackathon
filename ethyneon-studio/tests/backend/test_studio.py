import json
import unittest

from fastapi.testclient import TestClient
import httpx

from backend.app import create_app
from backend.config import Settings
from backend.product_dimensions import listing_dimensions


SHOP = "ethyneon-demo.myshopify.com"
CHAIR = "gid://shopify/ProductVariant/102"
HEADREST = "gid://shopify/ProductVariant/104"
AUTH = {"Authorization": "Bearer test-studio-key"}


def product(title, variant_id, description=""):
    return {
        "id": "gid://shopify/Product/" + variant_id.rsplit("/", 1)[-1],
        "title": title,
        "description": description,
        "variants": [{"id": variant_id, "seller": {"domain": SHOP}}],
    }


class StudioTests(unittest.TestCase):
    def test_text_edits_keep_exact_shopify_id_and_mount_a_separate_variant(self):
        first = product("Faux leather gaming chair", "gid://shopify/ProductVariant/101")
        second = product("Mesh gaming chair", CHAIR)
        wrong = product("Gaming chair with a headrest pillow", "gid://shopify/ProductVariant/103")
        accessory = product("Chair Head Rest Pillow", HEADREST, "Universal strap for most gaming chairs.")
        looked_up = []
        queries = []

        def upstream(request):
            payload = json.loads(request.content)
            if request.url.host == "generativelanguage.googleapis.com":
                return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [{
                    "type": "text", "text": json.dumps({"action": "search", "value": "gaming chair"}),
                }]}]})
            if request.url.host == "mcp.shop.com":
                arguments = payload["params"]["arguments"]["catalog"]
                if payload["params"]["name"] == "search_catalog":
                    query = arguments["query"]
                    queries.append(query)
                    records = [first, second] if query == "gaming chair" else [wrong, accessory]
                else:
                    variant = arguments["ids"][0]
                    looked_up.append(variant)
                    records = {CHAIR: [second], HEADREST: [accessory]}[variant]
                return httpx.Response(200, json={
                    "jsonrpc": "2.0", "id": payload["id"], "result": {"structuredContent": {"products": records}},
                })
            self.assertEqual(request.url.host, SHOP)
            self.assertNotIn("X-Shopify-Storefront-Access-Token", request.headers)
            variant_id = payload["variables"]["id"]
            return httpx.Response(200, json={"data": {"node": {
                "id": variant_id,
                "selectedOptions": [],
                "product": {"title": "Shopify product", "media": {"nodes": [{
                    "sources": [{"format": "glb", "url": "https://cdn.shopify.com/3d/product.glb"}],
                }]}},
            }}})

        app = create_app(Settings(connection_token="test-studio-key", gemini_api_key="test-key"),
                         transport=httpx.MockTransport(upstream))
        with TestClient(app) as client:
            def send(command, **scene):
                reply = client.post("/api/studio/command", headers=AUTH, json={"command": command, **scene})
                self.assertEqual(reply.status_code, 200, reply.text)
                return reply.json()

            cards = send("gaing chair")["results"]
            self.assertEqual(cards[1]["variant_id"], CHAIR)
            visible = [{"title": card["title"], "variant_id": card["variant_id"]} for card in cards]
            selection = send("Choose the top right", visible=visible)
            self.assertEqual(selection["product"]["variant_id"], CHAIR)
            rotation = send("Rotate it by 90 degrees left", selected_variant_id=CHAIR)
            self.assertEqual(rotation, {"action": "rotate", "degrees": -90, "variant_id": CHAIR})
            paint = send("Change it to green", selected_variant_id=CHAIR)
            self.assertEqual(paint, {"action": "set_color", "variant_id": CHAIR, "color": "#30a363"})
            mounted = send("Add a head rest on it", selected_variant_id=CHAIR)
            self.assertEqual(mounted["action"], "attach_headrest")
            self.assertEqual(mounted["variant_id"], CHAIR)
            self.assertEqual(mounted["product"]["variant_id"], HEADREST)
            self.assertEqual(mounted["fit_status"], "unverified")
            self.assertEqual(looked_up, [CHAIR, CHAIR, HEADREST])
            self.assertIn("headrest", queries[-1])

    def test_studio_local_route_preserves_backend_authentication(self):
        app = create_app(Settings(connection_token="test-studio-key"))
        with TestClient(app) as private_backend:
            self.assertEqual(private_backend.post("/api/studio/command", json={"command": "Rotate"}).status_code, 401)
            self.assertEqual(private_backend.get("/studio").status_code, 403)
        with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50898)) as browser:
            self.assertEqual(browser.get("/studio").status_code, 200)
            headers = {"Origin": "https://unrelated.example"}
            self.assertEqual(browser.post("/studio/command", headers=headers, json={"command": "Rotate"}).status_code, 403)
            self.assertEqual(browser.post("/studio/command", json={"command": "Rotate"}).json()["action"], "message")

    def test_description_dimensions_use_overall_measurements_and_adjustment_range(self):
        result = listing_dimensions(
            [{"name": "Width", "label": "26.5 in"}, {"name": "Height", "label": "46.75-52 in"}],
            'Overall size: 26.5" W x 25.25" D x 46.75" - 52" H Seat adjusts from 19.5" to 22.5".',
        )
        self.assertEqual(result.dimensions_m, {"width": 0.6731, "depth": 0.64135, "height": 1.18745})
        self.assertEqual(result.height_range_m, (1.18745, 1.3208))
        self.assertEqual(result.source, "shopify_listing_description")
        incomplete = listing_dimensions([], 'Dimensions: Seat back: 32" H x 22" W Seat: 24" D')
        self.assertIsNone(incomplete.dimensions_m)


if __name__ == "__main__":
    unittest.main()
