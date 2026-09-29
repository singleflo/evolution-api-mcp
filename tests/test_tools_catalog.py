import json

import pytest

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.tools.catalog import list_catalog_collections, list_catalog_products
from tests.conftest import INSTANCE

GET_CATALOG = f"/business/getCatalog/{INSTANCE}"
GET_COLLECTIONS = f"/business/getCollections/{INSTANCE}"

SHOP = "393331234567@s.whatsapp.net"


def _product(n, **extra):
    return {
        "id": f"prod-{n}",
        "name": f"Bread {n}",
        "description": "Fresh",
        "price": 250 * n,
        "currency": "EUR",
        "url": f"https://shop.example.com/{n}",
        "retailerId": f"SKU-{n}",
        "availability": "in stock",
        "imageUrls": {"requested": f"https://img.example.com/{n}.jpg", "original": f"https://img.example.com/o{n}.jpg"},
        "isHidden": False,
        "reviewStatus": "APPROVED",
        "maxAvailable": 12,
        **extra,
    }


# --- list_catalog_products -------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_own_catalog_request_carries_only_the_limit_and_products_are_projected(evo, bound):
    evo.on(
        "POST",
        GET_CATALOG,
        json={
            "wuid": SHOP,
            "numberExists": True,
            "isBusiness": True,
            "catalogLength": 2,
            "catalog": [_product(1), _product(2)],
        },
    )
    with bound(evo):
        result = json.loads(await list_catalog_products())

    assert evo.last("POST", GET_CATALOG).json == {"limit": 10}
    assert result == {
        "business": "393331234567",
        "is_business": True,
        "product_count": 2,
        "products": [
            {
                "id": "prod-1",
                "name": "Bread 1",
                "description": "Fresh",
                "price": 250,
                "currency": "EUR",
                "url": "https://shop.example.com/1",
                "retailerId": "SKU-1",
                "availability": "in stock",
                "imageUrls": {
                    "requested": "https://img.example.com/1.jpg",
                    "original": "https://img.example.com/o1.jpg",
                },
                "isHidden": False,
            },
            {
                "id": "prod-2",
                "name": "Bread 2",
                "description": "Fresh",
                "price": 500,
                "currency": "EUR",
                "url": "https://shop.example.com/2",
                "retailerId": "SKU-2",
                "availability": "in stock",
                "imageUrls": {
                    "requested": "https://img.example.com/2.jpg",
                    "original": "https://img.example.com/o2.jpg",
                },
                "isHidden": False,
            },
        ],
    }


@pytest.mark.anyio
async def test_another_business_is_addressed_by_digits_and_the_limit_caps_the_products(evo, bound):
    catalog = [_product(n) for n in range(1, 5)]
    evo.on(
        "POST",
        GET_CATALOG,
        json={"wuid": SHOP, "numberExists": True, "isBusiness": True, "catalogLength": 4, "catalog": catalog},
    )
    with bound(evo):
        result = json.loads(await list_catalog_products("+39 333 123 4567", limit=3))

    assert evo.last("POST", GET_CATALOG).json == {"limit": 3, "number": "393331234567"}
    assert result["product_count"] == 4
    assert [p["id"] for p in result["products"]] == ["prod-1", "prod-2", "prod-3"]


@pytest.mark.anyio
async def test_a_product_keeps_only_the_keys_the_catalog_provides(evo, bound):
    sparse = {"id": "prod-9", "name": "Loaf", "price": None, "isHidden": True, "internal": "x"}
    evo.on(
        "POST",
        GET_CATALOG,
        json={"wuid": SHOP, "numberExists": True, "isBusiness": True, "catalogLength": 1, "catalog": [sparse]},
    )
    with bound(evo):
        result = json.loads(await list_catalog_products())

    assert result["products"] == [{"id": "prod-9", "name": "Loaf", "isHidden": True}]


@pytest.mark.anyio
async def test_a_number_that_is_not_a_business_comes_back_empty_with_a_note(evo, bound):
    # Evolution answers this shape when it cannot read a catalog (whatsapp.baileys.service.ts fetchCatalog catch).
    evo.on("POST", GET_CATALOG, json={"wuid": SHOP, "name": None, "isBusiness": False})
    with bound(evo):
        result = json.loads(await list_catalog_products())

    assert result["business"] == "393331234567"
    assert result["is_business"] is False
    assert result["product_count"] == 0
    assert result["products"] == []
    assert "not a WhatsApp Business account" in result["note"]


@pytest.mark.anyio
async def test_a_business_number_without_whatsapp_is_reported(evo, bound):
    evo.on(
        "POST",
        GET_CATALOG,
        status=400,
        json={
            "status": 400,
            "error": "Bad Request",
            "response": {"message": [{"jid": SHOP, "exists": False, "number": "393331234567"}]},
        },
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await list_catalog_products("393331234567")

    assert str(caught.value) == "393331234567 is not on WhatsApp."


@pytest.mark.anyio
async def test_catalog_refuses_a_group_as_business_number(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="is not a phone number"):
        await list_catalog_products("120363012345678901@g.us")

    assert evo.requests == []


# --- list_catalog_collections ----------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_collections_are_projected_with_product_counts(evo, bound):
    evo.on(
        "POST",
        GET_COLLECTIONS,
        json={
            "wuid": SHOP,
            "name": "Panetteria Rossi",
            "numberExists": True,
            "isBusiness": True,
            "collectionsLength": 2,
            "collections": [
                {
                    "id": "col-1",
                    "name": "Bread",
                    "status": {"status": "APPROVED", "canAppeal": False},
                    "products": [_product(1), _product(2)],
                },
                {"id": "col-2", "name": "Cakes", "status": None, "products": []},
            ],
        },
    )
    with bound(evo):
        result = json.loads(await list_catalog_collections())

    assert evo.last("POST", GET_COLLECTIONS).json == {"limit": 10}
    assert result == {
        "business": "393331234567",
        "name": "Panetteria Rossi",
        "is_business": True,
        "collections": [
            {
                "collection_id": "col-1",
                "name": "Bread",
                "status": {"status": "APPROVED", "canAppeal": False},
                "product_count": 2,
                "products": [{"id": "prod-1", "name": "Bread 1"}, {"id": "prod-2", "name": "Bread 2"}],
            },
            {"collection_id": "col-2", "name": "Cakes", "product_count": 0, "products": []},
        ],
    }


@pytest.mark.anyio
async def test_collections_for_another_business_and_the_empty_placeholder(evo, bound):
    # A business without collections gets one placeholder entry from Evolution: `{products: []}` (no id, no name).
    evo.on(
        "POST",
        GET_COLLECTIONS,
        json={
            "wuid": SHOP,
            "name": "Panetteria Rossi",
            "isBusiness": True,
            "collectionsLength": 1,
            "collections": [{"products": []}],
        },
    )
    with bound(evo):
        result = json.loads(await list_catalog_collections("393331234567", limit=5))

    assert evo.last("POST", GET_COLLECTIONS).json == {"limit": 5, "number": "393331234567"}
    assert result["collections"] == []


@pytest.mark.anyio
async def test_collections_of_a_non_business_number(evo, bound):
    evo.on("POST", GET_COLLECTIONS, json={"wuid": SHOP, "name": None, "isBusiness": False})
    with bound(evo):
        result = json.loads(await list_catalog_collections())

    assert result == {"business": "393331234567", "is_business": False, "collections": []}
