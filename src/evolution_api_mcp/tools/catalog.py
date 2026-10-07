"""Catalog toolset: the WhatsApp Business app product catalog and collections."""

from typing import Annotated

from pydantic import Field

from evolution_api_mcp import calls, context, jid, registry
from evolution_api_mcp.errors import tool_result
from evolution_api_mcp.tools.contacts import compact, refuse_absent_number

_PRODUCT_KEYS = (
    "id",
    "name",
    "description",
    "price",
    "currency",
    "url",
    "retailerId",
    "availability",
    "imageUrls",
    "isHidden",
)

BusinessNumber = Annotated[
    str | None,
    Field(
        min_length=3,
        max_length=128,
        description="International phone number of the business whose catalog to read. Default: this account.",
    ),
]

_NO_CATALOG_NOTE = (
    "This number is not a WhatsApp Business account, or Evolution could not read its catalog. Catalogs exist "
    "only on WhatsApp Business accounts that published one."
)


def _business(body: dict) -> str | None:
    wuid = body.get("wuid")
    if not isinstance(wuid, str):
        return None
    return jid.phone_of(wuid) or wuid


@registry.tool(
    title="List catalog products",
    toolset="catalog",
    kind="read",
    idempotent=True,
    integrations=frozenset({registry.BAILEYS}),
)
async def list_catalog_products(
    business_number: BusinessNumber = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum products to return.")] = 10,
) -> str:
    """List the products in the WhatsApp Business catalog of this account or of another business.

    Returns the business phone number, its name when known, is_business, product_count (the total Evolution
    found) and up to limit products with id, name, description, price, currency, url, retailerId, availability,
    imageUrls and isHidden as far as the catalog provides them. Accounts without a published catalog come back
    with an empty list and a note. list_catalog_collections returns the product groupings.
    """
    conn, client = await context.resolve()
    body: dict = {"limit": limit}
    hook = None
    if business_number is not None:
        digits = _digits(business_number)
        body["number"] = digits
        hook = refuse_absent_number(digits)
    answer = await calls.call(client, conn.identity, "POST", "business/getCatalog", json=body, on_http_error=hook)
    data = answer if isinstance(answer, dict) else {}
    items = [item for item in data.get("catalog") or [] if isinstance(item, dict)]
    products = [{key: item[key] for key in _PRODUCT_KEYS if item.get(key) is not None} for item in items[:limit]]
    result = compact(
        {
            "business": _business(data),
            "name": data.get("name"),
            "is_business": bool(data.get("isBusiness")),
            "product_count": data.get("catalogLength", len(items)),
            "products": products,
        }
    )
    if not data.get("isBusiness"):
        result["note"] = _NO_CATALOG_NOTE
    return tool_result(result)


def _digits(business_number: str) -> str:
    return calls.parse_phone(business_number)


@registry.tool(
    title="List catalog collections",
    toolset="catalog",
    kind="read",
    idempotent=True,
    integrations=frozenset({registry.BAILEYS}),
)
async def list_catalog_collections(
    business_number: BusinessNumber = None,
    limit: Annotated[int, Field(ge=1, le=20, description="Maximum collections to return.")] = 10,
) -> str:
    """List the product collections (named groups of products) of a WhatsApp Business catalog.

    Reads this account's catalog, or that of another business when business_number is given. Returns the business
    phone number, its name, is_business and the collections, each with collection_id, name, status, product_count
    and the id and name of its products. Accounts without collections come back with an empty list.
    list_catalog_products returns the full product records.
    """
    conn, client = await context.resolve()
    body: dict = {"limit": limit}
    hook = None
    if business_number is not None:
        digits = _digits(business_number)
        body["number"] = digits
        hook = refuse_absent_number(digits)
    answer = await calls.call(client, conn.identity, "POST", "business/getCollections", json=body, on_http_error=hook)
    data = answer if isinstance(answer, dict) else {}
    collections = []
    for raw in data.get("collections") or []:
        if not isinstance(raw, dict) or (raw.get("id") is None and raw.get("name") is None):
            continue  # Evolution answers one empty placeholder when the business has no collections
        products = [item for item in raw.get("products") or [] if isinstance(item, dict)]
        collections.append(
            compact(
                {
                    "collection_id": raw.get("id"),
                    "name": raw.get("name"),
                    "status": raw.get("status"),
                    "product_count": len(products),
                    "products": [compact({"id": item.get("id"), "name": item.get("name")}) for item in products],
                }
            )
        )
    result = compact(
        {
            "business": _business(data),
            "name": data.get("name"),
            "is_business": bool(data.get("isBusiness")),
            "collections": collections[:limit],
        }
    )
    return tool_result(result)
