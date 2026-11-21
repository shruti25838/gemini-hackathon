import base64
import io
import json
import struct
import time
import unittest
import zlib

from fastapi.testclient import TestClient
import httpx
import numpy as np
import trimesh

from backend.app import create_app
from backend.config import Settings


TOKEN = "test-backend-token"
PRODUCT_ID = "gid://shopify/p/example"
VARIANT_ID = "gid://shopify/ProductVariant/123"


def shop_variant(variant_id=VARIANT_ID, shop="ethyneon-demo.myshopify.com"):
    return {"id": variant_id, "seller": {"domain": shop}, "price": {"amount": 12000, "currency": "USD"}}


def shop_reply(request: httpx.Request, document: dict) -> httpx.Response:
    return httpx.Response(200, json={
        "jsonrpc": "2.0",
        "id": json.loads(request.content)["id"],
        "result": {"isError": False, "structuredContent": document},
    })


class ConnectionTests(unittest.TestCase):
    def client(self, handler=None, **settings):
        transport = httpx.MockTransport(handler) if handler else None
        config = Settings(connection_token=TOKEN, **settings)
        return self.enterContext(TestClient(create_app(config, transport=transport)))

    def setUp(self):
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def test_gateway_protects_provider_routes(self):
        client = self.client()
        self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
        response = client.get("/api/providers")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(set(response.json()), {"code", "message", "retryable", "details"})
        self.assertFalse(client.get("/api/providers", headers=self.auth).json()["agora"]["configured"])

    def test_gemini_requires_api_key(self):
        response = self.client().post("/api/gemini/interactions", headers=self.auth, json={"input": "Hi"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "gemini_not_configured")

    def test_gemini_decision_wire_protocol(self):
        def upstream(request):
            self.assertEqual(request.url.path, "/v1beta/decisions")
            self.assertEqual(request.headers["x-goog-api-key"], "test-google-key")
            self.assertEqual(json.loads(request.content)["model"], "gemini-decision-1")
            return httpx.Response(200, json={"answers": {"room": {"type": "noul", "noul": .98, "confidence": .98}}})

        client = self.client(upstream, gemini_api_key="test-google-key")
        response = client.post("/api/gemini/decisions", headers=self.auth, json={
            "input": "A picture of a study room",
            "questions": {"room": {"type": "noul", "instructions": "Does this describe a room?"}},
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answers"]["room"]["confidence"], .98)

    def test_gemini_interactions_forwards_room_media_and_response_format(self):
        image = {"type": "image", "mime_type": "image/jpeg", "data": "ZGF0YQ=="}
        response_format = {"type": "text", "mime_type": "application/json", "schema": {"type": "object"}}

        def upstream(request):
            self.assertEqual(request.url.path, "/v1beta/interactions")
            self.assertEqual(request.headers["Api-Revision"], "2026-05-20")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "gemini-4-neon")
            self.assertEqual(payload["input"][1], image)
            self.assertEqual(payload["response_format"], response_format)
            return httpx.Response(200, json={"id": "room_scan", "status": "completed"})

        client = self.client(upstream, gemini_api_key="test-google-key")
        response = client.post("/api/gemini/interactions", headers=self.auth, json={
            "model": "gemini-4-neon", "input": [{"type": "text", "text": "Analyze this room."}, image],
            "response_format": response_format,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "room_scan")

    def test_managed_run_submission_and_poll(self):
        def upstream(request):
            if request.method == "POST":
                self.assertEqual(request.url.path, "/v1beta/interactions")
                payload = json.loads(request.content)
                self.assertTrue(payload["background"])
                self.assertEqual(payload["agent"], "antigravity-preview-09-2026")
                self.assertEqual(payload["agent_config"]["model"], "gemini-4-carbon")
                self.assertEqual(payload["environment"], "remote")
                return httpx.Response(200, json={"id": "run_123", "status": "in_progress"})
            self.assertEqual(request.url.path, "/v1beta/interactions/run_123")
            return httpx.Response(200, json={"id": "run_123", "status": "completed"})

        client = self.client(upstream, gemini_api_key="test-key")
        run = client.post("/api/gemini/managed-runs", headers=self.auth, json={
            "input": "Check whether a door can open.", "model": "gemini-4-carbon",
        })
        self.assertEqual(run.status_code, 200)
        status = client.get(f"/api/gemini/interactions/{run.json()['id']}", headers=self.auth)
        self.assertEqual(status.json()["status"], "completed")

    def test_managed_run_timeout_is_not_retryable(self):
        def upstream(request):
            raise httpx.ReadTimeout("upstream details", request=request)

        client = self.client(upstream, gemini_api_key="test-key")
        reply = client.post("/api/gemini/managed-runs", headers=self.auth, json={"input": "Check the layout."})
        self.assertEqual(reply.status_code, 504)
        self.assertFalse(reply.json()["retryable"])
        self.assertNotIn("upstream details", reply.text)

    def test_shopify_search_filters_store_and_non_shopify_variants(self):
        def upstream(request):
            self.assertEqual(request.url.host, "mcp.shop.com")
            self.assertEqual(request.headers["MCP-Protocol-Version"], "2026-07-28")
            self.assertEqual(request.headers["Mcp-Method"], "tools/call")
            self.assertEqual(request.headers["Mcp-Name"], "search_catalog")
            params = json.loads(request.content)["params"]
            self.assertEqual(params["name"], "search_catalog")
            filters = params["arguments"]["catalog"]["filters"]
            self.assertEqual(filters, {"price": {"max": 13000}, "shops": ["ethyneon-demo.myshopify.com"]})
            return shop_reply(request, {"products": [{"id": PRODUCT_ID, "variants": [
                shop_variant(), shop_variant("gid://shopify/ProductVariant/124", "other.myshopify.com"),
                shop_variant("gid://other/Variant/894"), shop_variant("gid://shopify/ProductVariant/125", "example.com"),
            ]}], "messages": [{"type": "warning", "code": "example_warning"}]})

        client = self.client(upstream, shopify_shops=("ethyneon-demo.myshopify.com",))
        response = client.post("/api/shopify/catalog/search", headers=self.auth, json={
            "query": "walnut desk", "currency": "USD", "max_price_minor": 13000,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["products"][0]["variants"], [shop_variant()])
        self.assertEqual(response.json()["messages"][0]["code"], "example_warning")

    def test_shopify_exact_variant_lookup_rejects_substitution(self):
        def upstream(request):
            self.assertEqual(request.headers["Mcp-Name"], "lookup_catalog")
            catalog = json.loads(request.content)["params"]["arguments"]["catalog"]
            self.assertEqual(catalog["ids"], [VARIANT_ID])
            return shop_reply(request, {"products": [{"id": PRODUCT_ID, "variants": [
                shop_variant("gid://shopify/ProductVariant/124"),
            ]}]})

        reply = self.client(upstream).post("/api/shopify/catalog/variant", headers=self.auth, json={
            "variant_id": VARIANT_ID,
        })
        self.assertEqual(reply.status_code, 404)
        self.assertEqual(reply.json()["code"], "shop_variant_not_found")

    def test_product_model_prefers_shopify_storefront_model(self):
        def upstream(request):
            self.assertEqual(request.url.host, "ethyneon-demo.myshopify.com")
            self.assertEqual(request.url.path, "/api/2026-10/graphql.json")
            self.assertEqual(request.headers["X-Shopify-Storefront-Access-Token"], "test-storefront-token")
            self.assertEqual(json.loads(request.content)["variables"], {"id": VARIANT_ID})
            return httpx.Response(200, json={"data": {"node": {
                "id": VARIANT_ID, "selectedOptions": [], "product": {"title": "Walnut table", "media": {"nodes": [{
                    "id": "gid://shopify/Model3d/42", "sources": [
                        {"format": "usdz", "url": "https://cdn.shopify.com/3d/table.usdz"},
                        {"format": "glb", "url": "https://cdn.shopify.com/3d/table.glb"},
                    ],
                }]}},
            }}})

        client = self.client(upstream, shopify_storefront_domain="ethyneon-demo.myshopify.com",
                             shopify_storefront_token="test-storefront-token")
        response = client.post("/api/shopify/catalog/variant/model", headers=self.auth, json={"variant_id": VARIANT_ID})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "shopify_model3d")
        self.assertEqual(response.json()["model"], {"url": "https://cdn.shopify.com/3d/table.glb", "format": "glb", "scope": "product"})

    def test_product_model_reads_public_shopify_media_without_a_token(self):
        def upstream(request):
            if request.url.host == "mcp.shop.com":
                return shop_reply(request, {"products": [{"id": PRODUCT_ID, "title": "Walnut table", "variants": [shop_variant()]}]})
            self.assertEqual(request.url.host, "ethyneon-demo.myshopify.com")
            self.assertNotIn("X-Shopify-Storefront-Access-Token", request.headers)
            return httpx.Response(200, json={"data": {"node": {
                "id": VARIANT_ID, "selectedOptions": [], "product": {"title": "Walnut table", "media": {"nodes": [{
                    "sources": [{"format": "glb", "url": "https://cdn.shopify.com/3d/table.glb"}],
                }]}},
            }}})

        response = self.client(upstream).post("/api/shopify/catalog/variant/model", headers=self.auth, json={"variant_id": VARIANT_ID})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "shopify_model3d")
        self.assertEqual(response.json()["variant_id"], VARIANT_ID)

    def test_product_model_builds_glb_from_exact_shopify_image_and_size(self):
        image_url = "https://cdn.shopify.com/s/files/table.jpg"
        detail_url = "https://cdn.shopify.com/s/files/table-detail.jpg"
        recipe = {
            "width_over_height": 1, "depth_over_height": 1,
            "parts": [
                {"label": "top", "shape": "box", "center": {"x": 0.5, "y": 0.95, "z": 0.5},
                 "size": {"x": 1, "y": 0.1, "z": 1}, "color": "#49321F", "material": "wood", "recolorable": True},
                {"label": "leg", "shape": "box", "center": {"x": 0.1, "y": 0.45, "z": 0.1},
                 "size": {"x": 0.1, "y": 0.9, "z": 0.1}, "color": "#49321F"},
            ],
        }

        def upstream(request):
            if request.url.host == "mcp.shop.com":
                variant = {**shop_variant(), "options": [{"name": "Size", "label": '19"L x 19"W x 24"H'}],
                           "media": [{"type": "image", "url": image_url}]}
                return shop_reply(request, {"products": [{
                    "id": PRODUCT_ID, "title": "Walnut table", "variants": [variant],
                    "media": [
                        {"type": "image", "url": image_url + "?width=300"},
                        {"type": "image", "url": detail_url},
                        {"type": "image", "url": "https://cdn.shopify.com/s/files/missing.jpg"},
                    ],
                }]})
            if request.url.host == "ethyneon-demo.myshopify.com":
                self.assertNotIn("X-Shopify-Storefront-Access-Token", request.headers)
                return httpx.Response(403)
            if request.url.host == "cdn.shopify.com":
                self.assertEqual(request.url.params["width"], "768")
                if request.url.path.endswith("missing.jpg"):
                    return httpx.Response(404)
                return httpx.Response(200, content=request.url.path.encode(), headers={"Content-Type": "image/jpeg"})
            self.assertEqual(request.url.host, "generativelanguage.googleapis.com")
            payload = json.loads(request.content)
            images = [base64.b64decode(item["data"]) for item in payload["input"] if item["type"] == "image"]
            self.assertEqual(images, [b"/s/files/table.jpg", b"/s/files/table-detail.jpg"])
            self.assertIn(VARIANT_ID, payload["input"][1]["text"])
            return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [
                {"type": "text", "text": json.dumps(recipe)},
            ]}]})

        response = self.client(upstream, gemini_api_key="test-key").post(
            "/api/shopify/catalog/variant/model", headers=self.auth, json={"variant_id": VARIANT_ID},
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["source"], "generated_proxy")
        self.assertEqual(result["dimensions_m"], {"width": 0.4826, "height": 0.6096, "depth": 0.4826})
        self.assertEqual(result["reference_image_urls"], [image_url, detail_url])
        self.assertEqual(result["materials"], ["wood"])
        glb = base64.b64decode(result["model"]["data_base64"])
        scene = trimesh.load(io.BytesIO(glb), file_type="glb", force="scene")
        np.testing.assert_allclose(scene.extents, [0.4826, 0.6096, 0.4826], rtol=1e-6)
        self.assertEqual(scene.bounds[0][1], 0)
        wood = next(mesh.visual.material for mesh in scene.geometry.values() if mesh.visual.material.name == "finish_wood")
        self.assertIsNotNone(wood.baseColorTexture)

    def test_agora_issues_access_token_with_rtc_and_signaling_services(self):
        app_id = "1" * 32
        certificate = "2" * 32
        client = self.client(agora_app_id=app_id, agora_app_certificate=certificate)
        response = client.get("/api/agora/token", headers=self.auth, params={"channel": "room-test", "uid": 42})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = response.json()
        self.assertEqual(data["appId"], app_id)
        self.assertEqual(data["uid"], 42)
        self.assertGreater(data["expireAt"], time.time())
        self.assertNotIn(certificate, response.text)
        token = data["rtcToken"]
        self.assertEqual(data["rtmToken"], token)
        self.assertTrue(token.startswith("007"))

        packed = zlib.decompress(base64.b64decode(token[3:]))
        signature_size = struct.unpack_from("<H", packed)[0]
        app_id_offset = 2 + signature_size
        app_id_size = struct.unpack_from("<H", packed, app_id_offset)[0]
        metadata_offset = app_id_offset + 2 + app_id_size
        _, expires, _, service_count = struct.unpack_from("<IIIH", packed, metadata_offset)
        self.assertEqual(expires, 900)
        self.assertEqual(service_count, 2)
        self.assertIn(b"room-test", packed)


if __name__ == "__main__":
    unittest.main()
