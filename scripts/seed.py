#!/usr/bin/env python3
"""
Soko development seed script.

Flow:
  1. Register farmers + buyers via auth service (creates auth_db + user_db records)
  2. Update farmer profiles (bio, farm name) via user service
  3. Farmers create produce listings → activate them via update endpoint
  4. Trigger data-ingestion bootstrap → populates ML feature store
  5. Restart recommendation service → loads new profiles immediately
  6. Print a summary with real IDs for smoke tests

Calls all services directly on their host ports with injected x-user-id / x-user-role
headers — the same headers nginx injects after JWT verification.
"""

import json
import subprocess
import sys
import time

import requests

AUTH     = "http://localhost:8001"
USER     = "http://localhost:8002"
PRODUCE  = "http://localhost:8003"
INGEST   = "http://localhost:8096"

PASS = "Soko2024!"

# ── Farmer data ───────────────────────────────────────────────────────────────

FARMERS = [
    {
        "fullName":    "Nakato Aisha",
        "email":       "nakato.aisha@sokodev.ug",
        "phone":       "+256701123001",
        "district":    "Kampala",
        "village":     "Natete",
        "role":        "farmer",
        "specialties": ["maize_grain", "sorghum"],
        "farmName":    "Nakato Family Grains",
        "farmerBio":   "Third-generation cereal farmer from Natete. Supplying Kampala markets for over 15 years.",
        "listings": [
            {
                "name": "Premium Maize Grain", "category": "Grains",
                "district": "Kampala", "village": "Natete",
                "description": "Sun-dried grade-A maize grain, ready for milling or animal feed.",
                "tags": ["maize", "grains", "wholesale"],
                "price": 1300, "unit": "kg", "totalQty": 500, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-04-10",
                "storage": "Store in cool, dry place. Lasts 6 months.",
                "priceTiers": [{"minQty": 100, "price": 1250, "label": "100+ kg"},
                               {"minQty": 300, "price": 1200, "label": "300+ kg"}],
            },
            {
                "name": "Sorghum Grain", "category": "Grains",
                "district": "Kampala", "village": "Natete",
                "description": "Clean sorghum grain, ideal for brewing and animal nutrition.",
                "tags": ["sorghum", "grains"],
                "price": 1100, "unit": "kg", "totalQty": 300, "minimumOrder": 30,
                "fresh": False, "harvestDate": "2026-04-15",
            },
        ],
    },
    {
        "fullName":    "Ssebuliba John",
        "email":       "ssebuliba.john@sokodev.ug",
        "phone":       "+256702123002",
        "district":    "Mbarara",
        "village":     "Rutooma",
        "role":        "farmer",
        "specialties": ["irish_potatoes", "matoke"],
        "farmName":    "Ssebuliba Highland Farm",
        "farmerBio":   "Highland farmer specialising in Irish potatoes and matoke in the Ankole region.",
        "listings": [
            {
                "name": "Irish Potatoes (Desiree)", "category": "Vegetables",
                "district": "Mbarara", "village": "Rutooma",
                "description": "Clean Desiree potatoes harvested from rich highland soils of Mbarara.",
                "tags": ["potatoes", "vegetables", "fresh"],
                "price": 850, "unit": "kg", "totalQty": 800, "minimumOrder": 100,
                "fresh": True, "harvestDate": "2026-05-01",
                "priceTiers": [{"minQty": 200, "price": 800, "label": "200+ kg"},
                               {"minQty": 500, "price": 750, "label": "500+ kg"}],
            },
            {
                "name": "Matoke (Bogoya Cluster)", "category": "Fruits",
                "district": "Mbarara", "village": "Rutooma",
                "description": "Traditional Ankole matoke bunches. Harvested green, ripens within 3 days.",
                "tags": ["matoke", "banana", "staple"],
                "price": 650, "unit": "bunch", "totalQty": 200, "minimumOrder": 10,
                "fresh": True, "harvestDate": "2026-05-05",
            },
        ],
    },
    {
        "fullName":    "Okello David",
        "email":       "okello.david@sokodev.ug",
        "phone":       "+256703123003",
        "district":    "Gulu",
        "village":     "Layibi",
        "role":        "farmer",
        "specialties": ["sorghum", "millet", "maize_grain"],
        "farmName":    "Okello Northern Grains",
        "farmerBio":   "Northern Uganda grain farmer serving Gulu town markets. Drought-resistant varieties only.",
        "listings": [
            {
                "name": "White Sorghum", "category": "Grains",
                "district": "Gulu", "village": "Layibi",
                "description": "White sorghum, low tannin variety suited for food and malting.",
                "tags": ["sorghum", "northern", "grains"],
                "price": 950, "unit": "kg", "totalQty": 400, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-03-20",
            },
            {
                "name": "Finger Millet (Eleusine)", "category": "Grains",
                "district": "Gulu", "village": "Layibi",
                "description": "Nutrient-dense finger millet. Popular for millet bread and porridge.",
                "tags": ["millet", "grains", "nutritious"],
                "price": 1900, "unit": "kg", "totalQty": 250, "minimumOrder": 25,
                "fresh": False, "harvestDate": "2026-03-25",
            },
        ],
    },
    {
        "fullName":    "Nabukeera Grace",
        "email":       "nabukeera.grace@sokodev.ug",
        "phone":       "+256704123004",
        "district":    "Masaka",
        "village":     "Bukakata",
        "role":        "farmer",
        "specialties": ["matoke", "cassava_chips"],
        "farmName":    "Nabukeera Lakeside Farm",
        "farmerBio":   "Family farm on the shores of Lake Victoria. Matoke and cassava our staples.",
        "listings": [
            {
                "name": "Matoke (Mpologoma)", "category": "Fruits",
                "district": "Masaka", "village": "Bukakata",
                "description": "Large Mpologoma matoke variety from Masaka. Sweet taste, firm flesh.",
                "tags": ["matoke", "lakeside", "premium"],
                "price": 620, "unit": "bunch", "totalQty": 300, "minimumOrder": 10,
                "fresh": True, "harvestDate": "2026-05-08",
            },
            {
                "name": "Sun-Dried Cassava Chips", "category": "Other",
                "district": "Masaka", "village": "Bukakata",
                "description": "High-quality cassava chips dried on raised beds. Free from mould.",
                "tags": ["cassava", "chips", "dried"],
                "price": 920, "unit": "kg", "totalQty": 400, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-04-01",
            },
        ],
    },
    {
        "fullName":    "Mugisha Robert",
        "email":       "mugisha.robert@sokodev.ug",
        "phone":       "+256705123005",
        "district":    "Mbale",
        "village":     "Nakaloke",
        "role":        "farmer",
        "specialties": ["yellow_beans", "maize_grain"],
        "farmName":    "Mugisha Mt Elgon Farms",
        "farmerBio":   "Eastern Uganda bean specialist. Mt Elgon volcanic soils produce exceptionally rich beans.",
        "listings": [
            {
                "name": "Yellow Beans (K132)", "category": "Grains",
                "district": "Mbale", "village": "Nakaloke",
                "description": "K132 yellow beans — high protein, clean grade. Ideal for export and local markets.",
                "tags": ["beans", "yellow", "protein", "export-grade"],
                "price": 2900, "unit": "kg", "totalQty": 500, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-04-20",
                "priceTiers": [{"minQty": 100, "price": 2800, "label": "100+ kg"},
                               {"minQty": 300, "price": 2700, "label": "300+ kg"}],
            },
            {
                "name": "Hybrid Maize Grain (H614)", "category": "Grains",
                "district": "Mbale", "village": "Nakaloke",
                "description": "H614 hybrid maize with high yield. Suitable for milling and silage.",
                "tags": ["maize", "hybrid", "grains"],
                "price": 1150, "unit": "kg", "totalQty": 800, "minimumOrder": 100,
                "fresh": False, "harvestDate": "2026-04-18",
            },
        ],
    },
    {
        "fullName":    "Atim Sarah",
        "email":       "atim.sarah@sokodev.ug",
        "phone":       "+256706123006",
        "district":    "Lira",
        "village":     "Adyel",
        "role":        "farmer",
        "specialties": ["millet", "sorghum"],
        "farmName":    "Atim Savannah Grains",
        "farmerBio":   "Lira-based grain farmer producing traditional Acholi millet and sorghum varieties.",
        "listings": [
            {
                "name": "Finger Millet (Okileng)", "category": "Grains",
                "district": "Lira", "village": "Adyel",
                "description": "Traditional Acholi Okileng millet. Prized for authentic taste in local brewing.",
                "tags": ["millet", "traditional", "acholi"],
                "price": 1850, "unit": "kg", "totalQty": 300, "minimumOrder": 25,
                "fresh": False, "harvestDate": "2026-03-15",
            },
            {
                "name": "Red Sorghum", "category": "Grains",
                "district": "Lira", "village": "Adyel",
                "description": "Red sorghum with high tannin content. Preferred for local brew (Kwete).",
                "tags": ["sorghum", "red", "traditional"],
                "price": 920, "unit": "kg", "totalQty": 500, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-03-18",
            },
        ],
    },
    {
        "fullName":    "Kawesi Peter",
        "email":       "kawesi.peter@sokodev.ug",
        "phone":       "+256707123007",
        "district":    "Kampala",
        "village":     "Wakiso",
        "role":        "farmer",
        "specialties": ["tomatoes", "maize_grain"],
        "farmName":    "Kawesi Peri-Urban Farm",
        "farmerBio":   "Peri-urban farmer supplying fresh tomatoes and grain to Kampala markets year-round.",
        "listings": [
            {
                "name": "Roma Tomatoes", "category": "Vegetables",
                "district": "Kampala", "village": "Wakiso",
                "description": "Firm Roma tomatoes, vine-ripened. Ideal for processing and fresh market.",
                "tags": ["tomatoes", "fresh", "vegetables"],
                "price": 1500, "unit": "kg", "totalQty": 200, "minimumOrder": 20,
                "fresh": True, "harvestDate": "2026-05-12",
            },
            {
                "name": "Dry Maize Grain", "category": "Grains",
                "district": "Kampala", "village": "Wakiso",
                "description": "Well-dried maize grain at 12% moisture. Ready for milling.",
                "tags": ["maize", "grains", "milled"],
                "price": 1300, "unit": "kg", "totalQty": 600, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-04-22",
            },
        ],
    },
    {
        "fullName":    "Nambi Faith",
        "email":       "nambi.faith@sokodev.ug",
        "phone":       "+256708123008",
        "district":    "Mbarara",
        "village":     "Mbarara Town",
        "role":        "farmer",
        "specialties": ["yellow_beans", "irish_potatoes", "matoke"],
        "farmName":    "Nambi Mixed Produce",
        "farmerBio":   "Diversified farm in Mbarara producing beans, potatoes, and matoke for western Uganda trade.",
        "listings": [
            {
                "name": "Yellow Beans (Nambale)", "category": "Grains",
                "district": "Mbarara", "village": "Mbarara Town",
                "description": "Nambale yellow beans — large seed size, excellent cooking quality.",
                "tags": ["beans", "yellow", "western"],
                "price": 3000, "unit": "kg", "totalQty": 400, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-04-28",
            },
            {
                "name": "White Irish Potatoes", "category": "Vegetables",
                "district": "Mbarara", "village": "Mbarara Town",
                "description": "White-skin Irish potatoes from Mbarara highland. Consistent sizing.",
                "tags": ["potatoes", "vegetables", "white"],
                "price": 850, "unit": "kg", "totalQty": 600, "minimumOrder": 100,
                "fresh": True, "harvestDate": "2026-05-02",
            },
        ],
    },
    {
        "fullName":    "Oluru Emmanuel",
        "email":       "oluru.emmanuel@sokodev.ug",
        "phone":       "+256709123009",
        "district":    "Gulu",
        "village":     "Pece",
        "role":        "farmer",
        "specialties": ["maize_grain", "millet"],
        "farmName":    "Oluru Peace Farm",
        "farmerBio":   "Gulu farmer selling certified maize seed and food-grade millet to northern traders.",
        "listings": [
            {
                "name": "Certified Maize (Longe 5)", "category": "Grains",
                "district": "Gulu", "village": "Pece",
                "description": "Longe 5 certified maize — open pollinated, drought tolerant. 10 t/ha potential.",
                "tags": ["maize", "certified", "seed", "drought-tolerant"],
                "price": 1100, "unit": "kg", "totalQty": 1000, "minimumOrder": 100,
                "fresh": False, "harvestDate": "2026-04-05",
                "priceTiers": [{"minQty": 200, "price": 1050, "label": "200+ kg"},
                               {"minQty": 500, "price": 1000, "label": "500+ kg"}],
            },
            {
                "name": "Finger Millet (Seremi 2)", "category": "Grains",
                "district": "Gulu", "village": "Pece",
                "description": "Seremi 2 improved millet. High yielding, early maturing.",
                "tags": ["millet", "improved", "northern"],
                "price": 1900, "unit": "kg", "totalQty": 200, "minimumOrder": 20,
                "fresh": False, "harvestDate": "2026-04-08",
            },
        ],
    },
    {
        "fullName":    "Kyomugisha Miriam",
        "email":       "kyomugisha.miriam@sokodev.ug",
        "phone":       "+256710123010",
        "district":    "Masaka",
        "village":     "Kalungu",
        "role":        "farmer",
        "specialties": ["cassava_chips", "matoke", "yellow_beans"],
        "farmName":    "Kyomugisha Diversified Farms",
        "farmerBio":   "Masaka-based farmer growing cassava, matoke, and beans. NAADS certified processor.",
        "listings": [
            {
                "name": "Cassava Chips (NAADS Grade A)", "category": "Other",
                "district": "Masaka", "village": "Kalungu",
                "description": "NAADS-certified cassava chips processed at 14% moisture. Ready for animal feed or starch.",
                "tags": ["cassava", "chips", "naads", "certified"],
                "price": 920, "unit": "kg", "totalQty": 300, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-03-30",
            },
            {
                "name": "Yellow Beans (Kablanketi)", "category": "Grains",
                "district": "Masaka", "village": "Kalungu",
                "description": "Kablanketi yellow beans from Masaka. Large seed, rich flavour.",
                "tags": ["beans", "yellow", "masaka"],
                "price": 3100, "unit": "kg", "totalQty": 400, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-04-25",
            },
        ],
    },
]

# ── Buyer data ────────────────────────────────────────────────────────────────

BUYERS = [
    {
        "fullName":  "Ssali Martin",
        "email":     "ssali.martin@sokodev.ug",
        "phone":     "+256711200001",
        "district":  "Kampala",
        "role":      "buyer",
        "interests": ["maize_grain", "sorghum"],
    },
    {
        "fullName":  "Nansubuga Rachel",
        "email":     "nansubuga.rachel@sokodev.ug",
        "phone":     "+256712200002",
        "district":  "Mbarara",
        "role":      "buyer",
        "interests": ["irish_potatoes", "tomatoes"],
    },
    {
        "fullName":  "Opio Samuel",
        "email":     "opio.samuel@sokodev.ug",
        "phone":     "+256713200003",
        "district":  "Gulu",
        "role":      "buyer",
        "interests": ["millet", "sorghum"],
    },
    {
        "fullName":  "Nakimuli Diana",
        "email":     "nakimuli.diana@sokodev.ug",
        "phone":     "+256714200004",
        "district":  "Masaka",
        "role":      "buyer",
        "interests": ["matoke", "yellow_beans"],
    },
    {
        "fullName":  "Kiggundu Alex",
        "email":     "kiggundu.alex@sokodev.ug",
        "phone":     "+256715200005",
        "district":  "Mbale",
        "role":      "buyer",
        "interests": ["maize_grain", "yellow_beans"],
    },
    {
        "fullName":  "Katende Brian",
        "email":     "katende.brian@sokodev.ug",
        "phone":     "+256716200006",
        "district":  "Kampala",
        "role":      "buyer",
        "interests": ["tomatoes", "maize_grain"],
    },
    {
        "fullName":  "Birungi Agnes",
        "email":     "birungi.agnes@sokodev.ug",
        "phone":     "+256717200007",
        "district":  "Mbarara",
        "role":      "buyer",
        "interests": ["matoke", "irish_potatoes"],
    },
    {
        "fullName":  "Odong Charles",
        "email":     "odong.charles@sokodev.ug",
        "phone":     "+256718200008",
        "district":  "Lira",
        "role":      "buyer",
        "interests": ["millet", "maize_grain"],
    },
    {
        "fullName":  "Nassaka Joyce",
        "email":     "nassaka.joyce@sokodev.ug",
        "phone":     "+256719200009",
        "district":  "Masaka",
        "role":      "buyer",
        "interests": ["cassava_chips", "yellow_beans"],
    },
    {
        "fullName":  "Tumwesige Paul",
        "email":     "tumwesige.paul@sokodev.ug",
        "phone":     "+256720200010",
        "district":  "Mbale",
        "role":      "buyer",
        "interests": ["yellow_beans", "sorghum"],
    },
]

EXTRA_FARMERS = [
    {
        "fullName":    "Asiimwe Patrick",
        "email":       "asiimwe.patrick@sokodev.ug",
        "phone":       "+256721300001",
        "district":    "Mbarara",
        "village":     "Bwizibwera",
        "role":        "farmer",
        "specialties": ["irish_potatoes", "yellow_beans", "matoke"],
        "farmName":    "Asiimwe Highlands",
        "farmerBio":   "Mixed produce farmer in Mbarara highlands. Supplying hotels and supermarkets.",
        "listings": [
            {
                "name": "Highland Irish Potatoes", "category": "Vegetables",
                "district": "Mbarara", "village": "Bwizibwera",
                "description": "Premium highland potatoes, consistent 60-80g sizing for hotel supply.",
                "tags": ["potatoes", "hotel-grade", "highland"],
                "price": 900, "unit": "kg", "totalQty": 1000, "minimumOrder": 100,
                "fresh": True, "harvestDate": "2026-05-10",
                "priceTiers": [{"minQty": 200, "price": 850, "label": "200+ kg"},
                               {"minQty": 500, "price": 800, "label": "500+ kg"}],
            },
            {
                "name": "Yellow Beans (NABE 4)", "category": "Grains",
                "district": "Mbarara", "village": "Bwizibwera",
                "description": "NABE 4 improved bean variety. High iron, good shelf life.",
                "tags": ["beans", "yellow", "improved", "iron-rich"],
                "price": 3000, "unit": "kg", "totalQty": 300, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-04-30",
            },
        ],
    },
    {
        "fullName":    "Achen Mary",
        "email":       "achen.mary@sokodev.ug",
        "phone":       "+256722300002",
        "district":    "Lira",
        "village":     "Ojwina",
        "role":        "farmer",
        "specialties": ["millet", "sorghum"],
        "farmName":    "Achen Grain Co-op",
        "farmerBio":   "Women-led grain cooperative in Lira. Aggregating from 12 smallholder farms.",
        "listings": [
            {
                "name": "Millet (Co-op Aggregate)", "category": "Grains",
                "district": "Lira", "village": "Ojwina",
                "description": "Aggregated finger millet from 12 farms. Uniform grade, clean.",
                "tags": ["millet", "cooperative", "bulk"],
                "price": 1850, "unit": "kg", "totalQty": 600, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-03-28",
                "priceTiers": [{"minQty": 200, "price": 1800, "label": "200+ kg"},
                               {"minQty": 400, "price": 1750, "label": "400+ kg"}],
            },
        ],
    },
    {
        "fullName":    "Waiswa Daniel",
        "email":       "waiswa.daniel@sokodev.ug",
        "phone":       "+256723300003",
        "district":    "Mbale",
        "village":     "Namawojjolo",
        "role":        "farmer",
        "specialties": ["maize_grain", "yellow_beans"],
        "farmName":    "Waiswa Eastern Farms",
        "farmerBio":   "Eastern Uganda grain trader and farmer. Direct links to Mbale grain market.",
        "listings": [
            {
                "name": "Maize Grain (Mbale Market Grade)", "category": "Grains",
                "district": "Mbale", "village": "Namawojjolo",
                "description": "Market-grade maize at 13% moisture. Bagged in 90kg sacks.",
                "tags": ["maize", "grains", "market-grade"],
                "price": 1150, "unit": "kg", "totalQty": 900, "minimumOrder": 90,
                "fresh": False, "harvestDate": "2026-04-12",
            },
            {
                "name": "Yellow Beans (Bam 1)", "category": "Grains",
                "district": "Mbale", "village": "Namawojjolo",
                "description": "Bam 1 bush bean variety. Good cooking quality, medium seed.",
                "tags": ["beans", "yellow", "eastern"],
                "price": 2800, "unit": "kg", "totalQty": 400, "minimumOrder": 50,
                "fresh": False, "harvestDate": "2026-04-20",
            },
        ],
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def ok(label: str, resp: requests.Response) -> dict:
    if not resp.ok:
        print(f"  ✗ {label}: {resp.status_code} — {resp.text[:200]}")
        sys.exit(1)
    data = resp.json()
    print(f"  ✓ {label}")
    return data


def register_or_login(label: str, payload: dict) -> dict | None:
    """Register a user; if already exists (409) log in instead to recover their ID."""
    resp = requests.post(f"{AUTH}/register", json=payload)
    if resp.status_code == 409:
        login_resp = requests.post(f"{AUTH}/login", json={
            "email": payload["email"], "password": payload["password"]
        })
        if login_resp.ok:
            print(f"  ~ {label} (already exists, recovered)")
            return login_resp.json()
        print(f"  ~ {label} (already exists, skipped)")
        return None
    return ok(label, resp)


def farmer_headers(user_id: str) -> dict:
    return {"X-User-Id": user_id, "X-User-Role": "farmer"}


def buyer_headers(user_id: str) -> dict:
    return {"X-User-Id": user_id, "X-User-Role": "buyer"}


# ── Phase 1: Register all users ───────────────────────────────────────────────

def register_users():
    print("\n── Phase 1: Registering users ──────────────────────────────────")
    created_farmers, created_buyers = [], []

    all_farmers = FARMERS + EXTRA_FARMERS
    for f in all_farmers:
        payload = {
            "fullName":    f["fullName"],
            "email":       f["email"],
            "password":    PASS,
            "phone":       f["phone"],
            "district":    f["district"],
            "role":        f["role"],
            "specialties": f["specialties"],
        }
        data = register_or_login(f"Farmer: {f['fullName']}", payload)
        if data:
            created_farmers.append({**f, "id": data["user"]["id"]})

    for b in BUYERS:
        payload = {
            "fullName":  b["fullName"],
            "email":     b["email"],
            "password":  PASS,
            "phone":     b["phone"],
            "district":  b["district"],
            "role":      b["role"],
            "interests": b["interests"],
        }
        data = register_or_login(f"Buyer:  {b['fullName']}", payload)
        if data:
            created_buyers.append({**b, "id": data["user"]["id"]})

    return created_farmers, created_buyers


# ── Phase 2: Update farmer profiles ──────────────────────────────────────────

def update_profiles(farmers: list):
    print("\n── Phase 2: Updating farmer profiles ───────────────────────────")
    for f in farmers:
        payload = {
            "farmName":    f["farmName"],
            "farmerBio":   f["farmerBio"],
            "village":     f["village"],
            "specialties": f["specialties"],
        }
        resp = requests.put(
            f"{USER}/users/me",
            json=payload,
            headers=farmer_headers(f["id"]),
        )
        ok(f"Profile update: {f['fullName']}", resp)


# ── Phase 3: Create + activate listings ──────────────────────────────────────

def create_listings(farmers: list) -> list:
    print("\n── Phase 3: Creating produce listings ──────────────────────────")
    all_listings = []

    for f in farmers:
        fid     = f["id"]
        headers = farmer_headers(fid)

        for listing_data in f["listings"]:
            # Create as draft
            resp = requests.post(f"{PRODUCE}/listings/", json=listing_data, headers=headers)
            result = ok(f"  Draft: {listing_data['name']} ({f['fullName']})", resp)
            listing_id = result["id"]

            # Activate by setting status — update endpoint has no image requirement
            resp = requests.put(
                f"{PRODUCE}/listings/{listing_id}",
                json={"status": "active"},
                headers=headers,
            )
            ok(f"  Publish: {listing_data['name']}", resp)

            all_listings.append({
                "id":       listing_id,
                "name":     listing_data["name"],
                "farmer":   f["fullName"],
                "district": listing_data["district"],
            })

    return all_listings


# ── Phase 4: Trigger ML bootstrap ────────────────────────────────────────────

def trigger_bootstrap():
    print("\n── Phase 4: Triggering data-ingestion bootstrap ────────────────")
    resp = requests.post(f"{INGEST}/bootstrap")
    ok("Bootstrap triggered", resp)
    print("  Waiting 15s for bootstrap to complete...")
    time.sleep(15)

    # Confirm counts
    resp = requests.get(f"{INGEST}/bootstrap/status")
    if resp.ok:
        s = resp.json()
        print(f"  farmers_ingested : {s['farmers_ingested']}")
        print(f"  buyers_ingested  : {s['buyers_ingested']}")
        print(f"  orders_ingested  : {s['orders_ingested']}")
        print(f"  coverage_pairs   : {s['coverage_pairs']}")


# ── Phase 5: Restart recommendation service ───────────────────────────────────

def reload_recommendation_service():
    print("\n── Phase 5: Reloading recommendation service ───────────────────")
    subprocess.run(
        ["docker", "compose", "restart", "recommendation-service"],
        cwd="/home/the-icemann/Documents/soko/services/soko-ml",
        check=True,
    )
    print("  Waiting 10s for service to come up...")
    time.sleep(10)

    resp = requests.get("http://localhost:8095/health")
    if resp.ok:
        h = resp.json()
        print(f"  farmers_loaded : {h.get('farmers_loaded', '?')}")
        print(f"  buyers_loaded  : {h.get('buyers_loaded', '?')}")


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(farmers: list, buyers: list, listings: list):
    print("\n" + "═" * 60)
    print("SEED COMPLETE — copy these IDs for smoke tests")
    print("═" * 60)

    print(f"\nFarmers ({len(farmers)}):")
    for f in farmers:
        print(f"  {f['id']}  {f['fullName']:<25} {f['district']}")

    print(f"\nBuyers ({len(buyers)}):")
    for b in buyers:
        print(f"  {b['id']}  {b['fullName']:<25} {b['district']}")

    print(f"\nListings ({len(listings)}) — all active:")
    for l in listings:
        print(f"  {l['id']}  {l['name']:<35} {l['farmer']}")

    if farmers:
        print(f"\nSample smoke-test commands:")
        fid = farmers[0]["id"]
        bid = buyers[0]["id"]
        print(f"  curl -s 'http://localhost:8080/recommend/farmers-for-buyer/{bid}?top_n=3' | python3 -m json.tool")
        print(f"  curl -s 'http://localhost:8080/recommend/buyers-for-farmer/{fid}?top_n=3' | python3 -m json.tool")

    print()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Soko seed script starting...")

    farmers, buyers = register_users()
    update_profiles(farmers)
    listings = create_listings(farmers)
    trigger_bootstrap()
    reload_recommendation_service()
    print_summary(farmers, buyers, listings)
