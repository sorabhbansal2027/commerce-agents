"""
Salesforce B2B Commerce — IT Hardware product data setup.

Creates Product2 + PricebookEntry records, then attempts to wire them into the
B2B Commerce catalog (ProductCategory → CategoryProduct → entitlement policy).

Usage:
    cd commerce-agents-main
    python examples/salesforce/setup_products.py

Credentials are read from examples/salesforce/.env (same file the API server uses).
"""

import asyncio
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Load credentials the same way the API server does.
_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env", override=False)
load_dotenv(_ROOT.parent.parent / ".env", override=False)

SF_BASE = os.environ.get("SF_INSTANCE_URL", "").rstrip("/")
SF_CLIENT_ID = os.environ.get("SF_CLIENT_ID", "")
SF_CLIENT_SECRET = os.environ.get("SF_CLIENT_SECRET", "")
SF_API = f"{SF_BASE}/services/data/v62.0"

# ---------------------------------------------------------------------------
# IT Hardware catalog — 20 products across 5 categories
# ---------------------------------------------------------------------------

CATEGORIES = [
    "Laptops & Notebooks",
    "Servers & Compute",
    "Monitors & Displays",
    "Networking Equipment",
    "Accessories & Peripherals",
]

PRODUCTS = [
    # Laptops & Notebooks
    {
        "Name": "ProBook Elite 15 Laptop",
        "ProductCode": "ITH-LPT-001",
        "Description": "15.6\" FHD IPS, Intel Core i7-13th Gen, 16GB DDR5, 512GB NVMe SSD. "
                       "Enterprise-grade with TPM 2.0 and vPro support.",
        "Family": "Laptops & Notebooks",
        "IsActive": True,
        "Price": 1299.00,
    },
    {
        "Name": "ProBook Ultra 13 Laptop",
        "ProductCode": "ITH-LPT-002",
        "Description": "13.3\" QHD OLED, Intel Core i5-13th Gen, 8GB DDR5, 256GB NVMe SSD. "
                       "Ultra-thin at 1.2kg, ideal for mobile professionals.",
        "Family": "Laptops & Notebooks",
        "IsActive": True,
        "Price": 949.00,
    },
    {
        "Name": "ProBook Mobile Workstation",
        "ProductCode": "ITH-LPT-003",
        "Description": "17.3\" 4K display, Intel Xeon W-series, 32GB ECC RAM, NVIDIA RTX GPU. "
                       "CAD/simulation workloads in a portable form factor.",
        "Family": "Laptops & Notebooks",
        "IsActive": True,
        "Price": 2499.00,
    },
    {
        "Name": "ProBook Ruggedized Field Laptop",
        "ProductCode": "ITH-LPT-004",
        "Description": "MIL-STD-810H certified, IP65 rated, 14\" sunlight-readable display. "
                       "Designed for warehouse, field service, and outdoor environments.",
        "Family": "Laptops & Notebooks",
        "IsActive": True,
        "Price": 1799.00,
    },
    # Servers & Compute
    {
        "Name": "PowerEdge 2U Rack Server",
        "ProductCode": "ITH-SRV-001",
        "Description": "Dual Intel Xeon Scalable 4th Gen, up to 6TB DDR5, 24x NVMe bays. "
                       "Ideal for virtualization, database, and HPC workloads.",
        "Family": "Servers & Compute",
        "IsActive": True,
        "Price": 5499.00,
    },
    {
        "Name": "PowerEdge Tower Server",
        "ProductCode": "ITH-SRV-002",
        "Description": "Intel Xeon E-2400, 128GB ECC DDR5, 8x SATA/SAS bays. "
                       "Quiet operation for office or SMB data center deployment.",
        "Family": "Servers & Compute",
        "IsActive": True,
        "Price": 2999.00,
    },
    {
        "Name": "HyperDense Blade Chassis",
        "ProductCode": "ITH-SRV-003",
        "Description": "10-slot blade chassis with integrated 10GbE switching and shared PSUs. "
                       "Supports up to 10 half-height blade server modules.",
        "Family": "Servers & Compute",
        "IsActive": True,
        "Price": 8999.00,
    },
    # Monitors & Displays
    {
        "Name": "ProDisplay 27in 4K USB-C Monitor",
        "ProductCode": "ITH-MON-001",
        "Description": "27\" IPS 4K (3840×2160), 99% sRGB, 90W USB-C PD, KVM switch built-in. "
                       "Single-cable workflow for laptop users.",
        "Family": "Monitors & Displays",
        "IsActive": True,
        "Price": 649.00,
    },
    {
        "Name": "ProDisplay 24in FHD Monitor",
        "ProductCode": "ITH-MON-002",
        "Description": "24\" IPS FHD (1920×1080), 100Hz, height/tilt/swivel/pivot adjustable. "
                       "Volume pricing available for large deployments.",
        "Family": "Monitors & Displays",
        "IsActive": True,
        "Price": 299.00,
    },
    {
        "Name": "ProDisplay 49in Ultrawide Curved",
        "ProductCode": "ITH-MON-003",
        "Description": "49\" DQHD (5120×1440) curved VA, 240Hz, replaces dual-monitor setups. "
                       "Ideal for financial analysis, trading, and creative workstations.",
        "Family": "Monitors & Displays",
        "IsActive": True,
        "Price": 1199.00,
    },
    # Networking Equipment
    {
        "Name": "Enterprise Managed Switch 24-Port",
        "ProductCode": "ITH-NET-001",
        "Description": "24x 1GbE PoE+ ports + 4x 10GbE SFP+ uplinks, 370W PoE budget. "
                       "Layer 3 managed with VLAN, QoS, and LACP support.",
        "Family": "Networking Equipment",
        "IsActive": True,
        "Price": 599.00,
    },
    {
        "Name": "WiFi 6E Access Point Indoor",
        "ProductCode": "ITH-NET-002",
        "Description": "Tri-band WiFi 6E (2.4/5/6GHz), up to 9.6Gbps aggregate throughput. "
                       "Supports 500+ concurrent clients, PoE powered.",
        "Family": "Networking Equipment",
        "IsActive": True,
        "Price": 349.00,
    },
    {
        "Name": "Next-Gen Firewall 1Gbps",
        "ProductCode": "ITH-NET-003",
        "Description": "1Gbps NGFW throughput, IPS/IDS, application control, SD-WAN capable. "
                       "Centralized cloud management included for 3 years.",
        "Family": "Networking Equipment",
        "IsActive": True,
        "Price": 1499.00,
    },
    {
        "Name": "Network UPS 1500VA Tower",
        "ProductCode": "ITH-NET-004",
        "Description": "1500VA / 900W pure sinewave UPS, 8 NEMA 5-15 outlets, USB + SNMP card. "
                       "Protects servers and networking gear from power events.",
        "Family": "Networking Equipment",
        "IsActive": True,
        "Price": 399.00,
    },
    # Accessories & Peripherals
    {
        "Name": "USB-C Thunderbolt 4 Docking Station",
        "ProductCode": "ITH-ACC-001",
        "Description": "1x TB4 host, 2x Thunderbolt 4, 3x USB-A, 2x DisplayPort, 96W PD, "
                       "2.5GbE LAN. Single-cable connection to any TB4/USB4 laptop.",
        "Family": "Accessories & Peripherals",
        "IsActive": True,
        "Price": 229.00,
    },
    {
        "Name": "Business Mechanical Keyboard TKL",
        "ProductCode": "ITH-ACC-002",
        "Description": "Tenkeyless layout, quiet tactile switches, USB-C detachable cable. "
                       "N-key rollover for fast data entry; compatible with Windows/Mac.",
        "Family": "Accessories & Peripherals",
        "IsActive": True,
        "Price": 99.00,
    },
    {
        "Name": "Ergonomic Wireless Vertical Mouse",
        "ProductCode": "ITH-ACC-003",
        "Description": "Vertical grip reduces wrist strain, 2.4GHz wireless + BT dual-mode, "
                       "up to 3200 DPI adjustable. 90-day battery life.",
        "Family": "Accessories & Peripherals",
        "IsActive": True,
        "Price": 69.00,
    },
    {
        "Name": "4K Business Webcam with AI Framing",
        "ProductCode": "ITH-ACC-004",
        "Description": "4K 30fps webcam, AI auto-framing and noise cancellation, dual mic. "
                       "Plug-and-play USB-C; compatible with all major conferencing apps.",
        "Family": "Accessories & Peripherals",
        "IsActive": True,
        "Price": 199.00,
    },
    {
        "Name": "1TB PCIe 4.0 NVMe SSD",
        "ProductCode": "ITH-ACC-005",
        "Description": "1TB M.2 2280 NVMe Gen4 SSD, up to 7000MB/s read. "
                       "AES-256 hardware encryption. For laptop upgrades or workstation storage.",
        "Family": "Accessories & Peripherals",
        "IsActive": True,
        "Price": 119.00,
    },
    {
        "Name": "8-Port KVM Switch HDMI 4K",
        "ProductCode": "ITH-ACC-006",
        "Description": "Switch 8 computers via a single 4K HDMI monitor + keyboard + mouse. "
                       "Hotkey switching, USB hub passthrough, no software required.",
        "Family": "Accessories & Peripherals",
        "IsActive": True,
        "Price": 279.00,
    },
]


# ---------------------------------------------------------------------------
# Salesforce REST helpers
# ---------------------------------------------------------------------------

_token: str = ""
_token_expires: float = 0.0


async def get_token(client: httpx.AsyncClient) -> str:
    global _token, _token_expires
    if _token and time.monotonic() < _token_expires:
        return _token
    resp = await client.post(
        f"{SF_BASE}/services/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": SF_CLIENT_ID,
            "client_secret": SF_CLIENT_SECRET,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    _token = data["access_token"]
    _token_expires = time.monotonic() + data.get("expires_in", 3600) - 60
    return _token


async def soql(client: httpx.AsyncClient, query: str) -> list[dict]:
    token = await get_token(client)
    url = f"{SF_API}/query?q={urllib.parse.quote(query)}"
    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    return resp.json().get("records", [])


async def create_record(client: httpx.AsyncClient, sobject: str, fields: dict) -> str:
    token = await get_token(client)
    resp = await client.post(
        f"{SF_API}/sobjects/{sobject}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=fields,
    )
    if resp.is_error:
        body = resp.text[:300]
        print(f"  [warn] {sobject} create failed ({resp.status_code}): {body}")
        return ""
    return resp.json().get("id", "")


async def update_record(client: httpx.AsyncClient, sobject: str, record_id: str, fields: dict) -> bool:
    token = await get_token(client)
    resp = await client.patch(
        f"{SF_API}/sobjects/{sobject}/{record_id}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=fields,
    )
    return not resp.is_error


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------

async def get_standard_pricebook(client: httpx.AsyncClient) -> str:
    rows = await soql(client, "SELECT Id FROM Pricebook2 WHERE IsStandard = true LIMIT 1")
    if not rows:
        raise RuntimeError("No Standard Pricebook found")
    return rows[0]["Id"]


async def get_webstore_id(client: httpx.AsyncClient) -> str:
    rows = await soql(client, "SELECT Id, Name FROM WebStore LIMIT 1")
    if not rows:
        raise RuntimeError("No WebStore found")
    print(f"  WebStore: {rows[0]['Name']} ({rows[0]['Id']})")
    return rows[0]["Id"]


async def get_or_create_catalog(client: httpx.AsyncClient, webstore_id: str) -> str:
    """Return a ProductCatalog Id for this WebStore.

    Tries three approaches in order:
    1. WebStoreCatalog SOQL (may fail on restricted orgs)
    2. Any existing ProductCatalog in the org
    3. Create a new ProductCatalog and link it
    """
    # Approach 1: WebStoreCatalog SOQL
    try:
        rows = await soql(
            client,
            f"SELECT ProductCatalogId FROM WebStoreCatalog WHERE WebStoreId = '{webstore_id}' LIMIT 1",
        )
        if rows:
            cat_id = rows[0].get("ProductCatalogId", "")
            if cat_id:
                print(f"  Existing ProductCatalog (via WebStoreCatalog): {cat_id}")
                return cat_id
    except Exception:
        pass

    # Approach 2: query ProductCatalog directly
    try:
        rows = await soql(client, "SELECT Id, Name FROM ProductCatalog LIMIT 1")
        if rows:
            cat_id = rows[0]["Id"]
            print(f"  Existing ProductCatalog: {rows[0]['Name']} ({cat_id})")
            return cat_id
    except Exception:
        pass

    # Approach 3: create one and try to link it
    cat_id = await create_record(client, "ProductCatalog", {"Name": "IT Hardware Catalog"})
    if cat_id:
        print(f"  Created ProductCatalog: {cat_id}")
        await create_record(
            client, "WebStoreCatalog",
            {"WebStoreId": webstore_id, "ProductCatalogId": cat_id},
        )
    else:
        print("  [warn] Could not find or create a ProductCatalog — CategoryProduct wiring skipped")
    return cat_id


async def get_or_create_category(
    client: httpx.AsyncClient, catalog_id: str, name: str, existing: dict[str, str]
) -> str:
    if name in existing:
        return existing[name]
    cat_id = await create_record(
        client,
        "ProductCategory",
        {"Name": name, "CatalogId": catalog_id},
    )
    if cat_id:
        print(f"  Created category '{name}': {cat_id}")
    existing[name] = cat_id
    return cat_id


async def get_entitlement_policy(client: httpx.AsyncClient, webstore_id: str):
    try:
        rows = await soql(client, "SELECT Id, Name FROM CommerceEntitlementPolicy LIMIT 1")
        if rows:
            print(f"  Entitlement policy: {rows[0]['Name']} ({rows[0]['Id']})")
            return rows[0]["Id"]
    except Exception:
        print("  [info] CommerceEntitlementPolicy not queryable — skipping entitlement wiring")
    return None


async def product_exists(client: httpx.AsyncClient, code: str):
    rows = await soql(client, f"SELECT Id FROM Product2 WHERE ProductCode = '{code}' LIMIT 1")
    return rows[0]["Id"] if rows else None


async def setup_products() -> None:
    if not all([SF_BASE, SF_CLIENT_ID, SF_CLIENT_SECRET]):
        print("ERROR: SF_INSTANCE_URL, SF_CLIENT_ID, and SF_CLIENT_SECRET must be set in examples/salesforce/.env")
        sys.exit(1)

    print(f"\nConnecting to: {SF_BASE}")

    async with httpx.AsyncClient(timeout=30) as client:
        # Validate credentials
        try:
            await get_token(client)
            print("  Auth: OK")
        except Exception as e:
            print(f"ERROR: Auth failed — {e}")
            sys.exit(1)

        # Resolve shared objects
        std_pricebook_id = await get_standard_pricebook(client)
        print(f"  Standard Pricebook: {std_pricebook_id}")

        webstore_id = await get_webstore_id(client)
        catalog_id = await get_or_create_catalog(client, webstore_id)
        entitlement_id = await get_entitlement_policy(client, webstore_id)

        category_ids: dict[str, str] = {}  # name → Id

        print(f"\nCreating {len(PRODUCTS)} products...")
        created = 0
        skipped = 0
        product_ids: list[str] = []

        for p in PRODUCTS:
            code = p["ProductCode"]
            existing_id = await product_exists(client, code)
            if existing_id:
                pid = existing_id
                print(f"  [exists] {code} ({pid}) — wiring catalog/entitlement...")
                skipped += 1
            else:
                # Create Product2
                pid = await create_record(client, "Product2", {
                    "Name": p["Name"],
                    "ProductCode": code,
                    "Description": p["Description"],
                    "Family": p["Family"],
                    "IsActive": p["IsActive"],
                })
                if not pid:
                    continue
                print(f"  [create] {code} — {p['Name']} ({pid})")

                # Standard PricebookEntry
                pbe_std_id = await create_record(client, "PricebookEntry", {
                    "Product2Id": pid,
                    "Pricebook2Id": std_pricebook_id,
                    "UnitPrice": p["Price"],
                    "IsActive": True,
                })
                if pbe_std_id:
                    print(f"    PricebookEntry (Standard): ${p['Price']:.2f}")
                created += 1

            product_ids.append(pid)

            # CategoryProduct — always attempt (idempotent via [warn] on duplicate)
            if catalog_id:
                cat_id = await get_or_create_category(
                    client, catalog_id, p["Family"], category_ids
                )
                if cat_id:
                    cp_id = await create_record(client, "CategoryProduct", {
                        "ProductId": pid,
                        "ProductCategoryId": cat_id,
                    })
                    if cp_id:
                        print(f"    CategoryProduct: → '{p['Family']}'")

            # CommerceEntitlementProduct — always attempt
            if entitlement_id:
                ep_id = await create_record(client, "CommerceEntitlementProduct", {
                    "ProductId": pid,
                    "PolicyId": entitlement_id,
                })
                if ep_id:
                    print(f"    EntitlementProduct: added to policy")

        print(f"\nDone. Created: {created}  Skipped (already existed): {skipped}")
        print(f"Total products in catalog: {len(product_ids)}")

        # Summary of B2B catalog wiring
        print("\nCatalog structure:")
        for name in CATEGORIES:
            count = sum(1 for p in PRODUCTS if p["Family"] == name)
            print(f"  {name}: {count} products")

        if not entitlement_id:
            print(
                "\n[!] No CommerceEntitlementPolicy found — products may not appear in the "
                "B2B Commerce storefront via the Products API.\n"
                "    Go to: Commerce Setup → Buyer Groups → assign products to your entitlement policy.\n"
                "    The shopping agent will still see them via the PricebookEntry fallback."
            )


if __name__ == "__main__":
    asyncio.run(setup_products())
