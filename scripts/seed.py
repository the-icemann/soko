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
from pathlib import Path

import requests

AUTH     = "http://localhost:8001"
USER     = "http://localhost:8002"
PRODUCE  = "http://localhost:8003"
ORDER    = "http://localhost:8004"
MESSAGE  = "http://localhost:8006"
BLOG     = "http://localhost:8008"
INGEST   = "http://localhost:8096"

PASS         = "Soko2024!"
DELIVERY_FEE = 5000   # UGX, matches order service constant
MANIFEST     = Path(__file__).parent / ".seed_manifest.json"

# ── Delivery addresses by district ───────────────────────────────────────────
DELIVERY_ADDR = {
    "Kampala": {"district": "Kampala", "subCounty": "Kawempe",  "village": "Bwaise"},
    "Mbarara": {"district": "Mbarara", "subCounty": "Kakoba",   "village": "Rutooma"},
    "Gulu":    {"district": "Gulu",    "subCounty": "Bardege",  "village": "Layibi"},
    "Masaka":  {"district": "Masaka",  "subCounty": "Kimaanya", "village": "Bukakata"},
    "Mbale":   {"district": "Mbale",   "subCounty": "Wanale",   "village": "Nakaloke"},
    "Lira":    {"district": "Lira",    "subCounty": "Adyel",    "village": "Ojwina"},
}

# ── Order pairs: (buyer_index, listing_index)  ───────────────────────────────
# listing_index corresponds to the order listings are created: farmers in order
# of FARMERS list then EXTRA_FARMERS, each with their listings in definition order.
#
#  Listing idx → (Farmer, Listing name)
#   0  → Nakato     → Premium Maize Grain
#   2  → Ssebuliba  → Irish Potatoes (Desiree)
#   4  → Okello     → White Sorghum
#   6  → Nabukeera  → Matoke (Mpologoma)
#   8  → Mugisha    → Yellow Beans (K132)
#  10  → Atim       → Finger Millet (Okileng)
#  12  → Kawesi     → Roma Tomatoes
#  15  → Nambi      → White Irish Potatoes
#  18  → Kyomugisha → Cassava Chips (NAADS Grade A)
#  24  → Waiswa     → Yellow Beans (Bam 1)
ORDER_PAIRS = [
    (0, 0),   # Ssali Martin       → Nakato / Premium Maize Grain
    (1, 2),   # Nansubuga Rachel   → Ssebuliba / Irish Potatoes
    (2, 4),   # Opio Samuel        → Okello / White Sorghum
    (3, 6),   # Nakimuli Diana     → Nabukeera / Matoke
    (4, 8),   # Kiggundu Alex      → Mugisha / Yellow Beans K132
    (5, 12),  # Katende Brian      → Kawesi / Roma Tomatoes
    (6, 15),  # Birungi Agnes      → Nambi / White Irish Potatoes
    (7, 10),  # Odong Charles      → Atim / Finger Millet Okileng
    (8, 18),  # Nassaka Joyce      → Kyomugisha / Cassava Chips NAADS
    (9, 24),  # Tumwesige Paul     → Waiswa / Yellow Beans Bam 1
]

# ── Crop image URLs (Unsplash CDN, publicly accessible) ──────────────────────
# Matched by lowercase keyword in the listing name.
# Used by seed_listing_images() to download → upload via Cloudinary.
CROP_IMAGES = {
    "maize":   "https://images.unsplash.com/photo-1601593346740-925612772716?w=800&q=80",
    "sorghum": "https://images.unsplash.com/photo-1603048588665-791ca8aea617?w=800&q=80",
    "millet":  "https://images.unsplash.com/photo-1574323347407-f5e1ad6d020b?w=800&q=80",
    "bean":    "https://images.unsplash.com/photo-1553682538-a32cf88c62c7?w=800&q=80",
    "potato":  "https://images.unsplash.com/photo-1518977676601-b53f82aba655?w=800&q=80",
    "tomato":  "https://images.unsplash.com/photo-1592924357228-91a4daadcfea?w=800&q=80",
    "matoke":  "https://images.unsplash.com/photo-1571771894821-ce9b6c11b08e?w=800&q=80",
    "banana":  "https://images.unsplash.com/photo-1571771894821-ce9b6c11b08e?w=800&q=80",
    "cassava": "https://images.unsplash.com/photo-1614668701670-d23b2c5aac01?w=800&q=80",
}
CROP_IMAGE_DEFAULT = "https://images.unsplash.com/photo-1500937386664-56d1dfef3854?w=800&q=80"

# Per-post cover images indexed to match BLOG_POSTS order.
# These are stored directly as URLs in the blog DB (no Cloudinary).
BLOG_POST_IMAGES = [
    "https://images.unsplash.com/photo-1601593346740-925612772716?w=800&q=80",  # maize/solar dryer
    "https://images.unsplash.com/photo-1518977676601-b53f82aba655?w=800&q=80",  # potatoes
    "https://images.unsplash.com/photo-1553682538-a32cf88c62c7?w=800&q=80",     # K132 yellow beans
    "https://images.unsplash.com/photo-1592924357228-91a4daadcfea?w=800&q=80",  # tomatoes / drip
]


def pick_crop_image(name: str) -> str:
    n = name.lower()
    for keyword, url in CROP_IMAGES.items():
        if keyword in n:
            return url
    return CROP_IMAGE_DEFAULT


# ── Blog posts authored by seeded farmers ────────────────────────────────────
BLOG_POSTS = [
    {
        "farmer_idx": 0,  # Nakato Aisha
        "title":   "How I halved my post-harvest losses with a simple solar dryer",
        "excerpt": "Moisture was destroying 30% of my maize every season. This is the low-cost solar dryer setup that changed everything for my Natete farm.",
        "category": "Soil & Crops",
        "tags": ["maize", "post-harvest", "solar-drying", "storage"],
        "body": [
            {"type": "heading",   "content": "The problem: moisture ruining stored grain"},
            {"type": "paragraph", "content": "In Natete, the long rains push humidity above 80% for weeks at a time. Before I built my solar dryer, I was losing almost a third of my maize to mould before it ever reached the market. That loss was silent — it happened slowly inside the bag — so it took me two seasons to measure just how bad it was."},
            {"type": "heading",   "content": "Building the dryer for under 400,000 UGX"},
            {"type": "paragraph", "content": "The frame is eucalyptus poles, the drying bed is galvanised wire mesh, and the cover is UV-stabilised polythene sheeting. Total cost was 380,000 UGX for materials. A local welder joined the corners. The dryer sits on raised legs to allow airflow from below, and the cover is angled south-facing to capture maximum sun."},
            {"type": "quote",     "content": "I went from 30% post-harvest loss to under 5% in one season. The dryer paid for itself before the second harvest.", "attribution": "Nakato Aisha, Natete, Kampala"},
            {"type": "heading",   "content": "Moisture targets that matter"},
            {"type": "paragraph", "content": "Maize destined for milling should reach 12–13% moisture. Maize for seed should go even lower — 10–11%. I use a simple grain moisture meter (bought at Owino Market for 45,000 UGX) to check before bagging. Once it is in the bag and sealed, moisture stays stable for up to six months in a cool store."},
        ],
    },
    {
        "farmer_idx": 1,  # Ssebuliba John
        "title":   "Desiree vs. Victoria: choosing the right Irish potato variety for Ugandan highlands",
        "excerpt": "After five seasons of comparative trials on my Rutooma farm, I can tell you which variety outperforms in highland Mbarara soils — and why the answer surprises most buyers.",
        "category": "Soil & Crops",
        "tags": ["potatoes", "highland", "mbarara", "varieties"],
        "body": [
            {"type": "heading",   "content": "Why variety matters more than fertiliser"},
            {"type": "paragraph", "content": "Most highland potato farmers in Mbarara default to whatever seed is cheapest at the agro-input shop. I spent five seasons trialling Desiree, Victoria, and Cruza 148 side by side on the same field with the same inputs. The yield gap between the best and worst variety was larger than anything I could achieve by changing fertiliser rates."},
            {"type": "heading",   "content": "Trial results summary"},
            {"type": "paragraph", "content": "Desiree consistently delivered 18–22 tonnes per hectare on my clay-loam highland soils. Victoria came in at 14–17 t/ha but showed better late-blight resistance in wet seasons. Cruza 148 was the highest yielder at 24 t/ha but buyers reject it because the skin bruises easily during transport — a serious market problem on Mbarara's rough roads."},
            {"type": "quote",     "content": "Marketability matters as much as yield. A 24 t/ha crop that arrives bruised earns you less than a 20 t/ha crop that looks perfect.", "attribution": "Ssebuliba John, Rutooma, Mbarara"},
            {"type": "heading",   "content": "My recommendation"},
            {"type": "paragraph", "content": "For farmers selling to Kampala wholesalers via Soko, Desiree is the safest choice. It stores well, travels well, and buyers recognise the name. Use Victoria for local markets or if you expect a particularly wet season."},
        ],
    },
    {
        "farmer_idx": 4,  # Mugisha Robert
        "title":   "K132 yellow beans: Uganda's most-exported legume and how to grow it right",
        "excerpt": "K132 commands a 15–20% export premium over other yellow bean varieties. Here is exactly how we grow it on Mt Elgon volcanic soils to meet export grade.",
        "category": "Business",
        "tags": ["beans", "export", "k132", "mbale", "quality"],
        "body": [
            {"type": "heading",   "content": "What makes K132 export-grade"},
            {"type": "paragraph", "content": "K132 yellow bean is a climbing variety developed by NARO specifically for Uganda's eastern highlands. Its seed size is large and uniform (above 25g per 100 seeds), its skin is thin enough for quick cooking, and the colour holds after washing — qualities that meet EU and Middle East import standards."},
            {"type": "heading",   "content": "Soil preparation on Mt Elgon slopes"},
            {"type": "paragraph", "content": "Our volcanic soils are naturally rich in phosphorus and potassium, which beans love. We add minimal DAP at planting (just 50 kg/ha) and top-dress with CAN at flowering. Over-fertilising with nitrogen suppresses nodule formation — beans fix their own nitrogen if you inoculate the seed with Rhizobium before planting."},
            {"type": "heading",   "content": "Harvest and grading for export"},
            {"type": "paragraph", "content": "We harvest at 90% pod-yellowing and thresh within 48 hours to avoid discolouration. After threshing, we winnow, then hand-sort to remove split seeds, discoloured seeds, and any foreign matter. Export buyers use a tolerance of less than 2% defects — we aim for under 0.5% to ensure we pass."},
            {"type": "quote",     "content": "The 15% export premium over local market price completely justifies the extra grading labour. One day of sorting earns more than two extra bags of lower-grade beans.", "attribution": "Mugisha Robert, Nakaloke, Mbale"},
        ],
    },
    {
        "farmer_idx": 6,  # Kawesi Peter
        "title":   "Year-round Roma tomato supply near Kampala: my drip irrigation setup",
        "excerpt": "Most Kampala-area tomato farmers harvest only twice a year and watch prices crash at peak supply. Drip irrigation and staggered planting changed my business model completely.",
        "category": "Irrigation",
        "tags": ["tomatoes", "drip-irrigation", "kampala", "year-round"],
        "body": [
            {"type": "heading",   "content": "The seasonal price trap"},
            {"type": "paragraph", "content": "In Wakiso, the two main rainy seasons flood the market with tomatoes and collapse prices to 600–800 UGX/kg. During dry spells, the same tomatoes fetch 2,500–3,000 UGX/kg. I used to join everyone else at the low-price peak. Now I do the opposite."},
            {"type": "heading",   "content": "The drip system — cost and setup"},
            {"type": "paragraph", "content": "I installed a gravity-fed drip system from a 10,000-litre tank elevated 3 metres above the field. Total cost was 2.8 million UGX for 0.4 hectares. Drip lines run every 60 cm, emitters every 30 cm. The tank fills from a borehole with a solar pump — 120,000 UGX for the pump, 400,000 UGX for the borehole contribution. I share the borehole with three neighbours."},
            {"type": "heading",   "content": "Staggered planting for stable income"},
            {"type": "paragraph", "content": "I plant four batches per year, eight weeks apart. At any given time, one batch is flowering, one is fruiting, one is being harvested, and one is just transplanted. Total annual harvest is about 18 tonnes from 0.4 ha. I sell to processors and supermarkets on monthly contracts — the consistent supply is what earns the contract."},
            {"type": "quote",     "content": "Consistent supply is worth more than maximum yield. My buyers pay 1,800–2,200 UGX/kg year-round on a contract. That beats the lottery of seasonal prices.", "attribution": "Kawesi Peter, Wakiso, Kampala"},
        ],
    },
]

# ── Conversation scripts (buyer messages farmer, farmer replies) ──────────────
CONV_SCRIPTS = [
    # (order_pair_index, buyer_opening, farmer_reply)
    (0, "Hello Nakato, I just placed an order for your Premium Maize Grain. Is it current-season stock? I need it for milling so moisture is important.",
        "Hello! Yes, this is from the April 2026 harvest, solar-dried to 12% moisture. I can confirm the grade before dispatch if you send me your lab's requirements."),
    (1, "Hi, I ordered the Desiree potatoes. Can they be delivered to Kakoba? We need them by Friday for our restaurant.",
        "Hello! Kakoba is no problem. I usually deliver Mbarara town every Tuesday and Friday. Friday works perfectly — I will add your address to the route."),
    (2, "Okello, I need the white sorghum for malting. Is it the low-tannin variety suitable for beer? Can you do a 200 kg order next time?",
        "Yes, this is specifically the low-tannin white sorghum — ideal for malting and brewing. 200 kg is fine, I have plenty in stock. Just place the order when you are ready."),
    (3, "Hi Nabukeera, your Matoke Mpologoma looked great in the photos. How green are they right now? I need them to ripen by Sunday.",
        "Hello! The bunches I am harvesting this week are at full green — they will ripen naturally in 3 days at room temperature, so ordering today gets you Sunday-ripe matoke. Perfect timing."),
    (4, "Mugisha, I am interested in the K132 yellow beans for export. What is your grading standard and can you do a certificate of analysis?",
        "Hello! We grade to below 0.5% defects — better than the 2% export tolerance. I can provide a lab certificate from the Mbale NARO office if you need it for your buyer's requirements."),
    (5, "Hi Kawesi, I run a restaurant in Kampala. Do you supply Roma tomatoes on a weekly basis? We need about 30 kg every Monday.",
        "Hello! Yes, weekly supply is exactly what we do. I supply on Monday and Thursday mornings. 30 kg weekly is manageable — let us agree on a monthly contract and I will give you a stable price of 1,800 UGX/kg."),
    (6, "Nambi, I need the white Irish potatoes for my hotel kitchen. How consistent is the sizing? We need 60–80g tubers.",
        "Hello! Our Mbarara highland potatoes are graded before dispatch. The white variety comes out 60–80g very consistently — this is what the hotels in Mbarara already specify from us. I will set aside a hotel-grade batch for your next order."),
    (7, "Atim, is the Okileng millet traditional-variety or improved? My buyer specifically wants traditional Acholi millet for authenticity.",
        "Hello! Okileng is 100% traditional Acholi millet — no hybrid, no improved variety. We have maintained the same seed stock for 20 years. Your buyer will not find more authentic millet than this."),
    (8, "Kyomugisha, the NAADS Cassava Chips — are they free from aflatoxin? I need a safety certificate for my export shipment.",
        "Hello! All our cassava chips are processed under the NAADS quality programme. I can provide the NAADS inspection certificate and the moisture test result. Aflatoxin testing can be arranged at the Masaka district lab if your buyer requires it."),
    (9, "Waiswa, I am looking for Bam 1 yellow beans for a local buyer. What is the minimum I can order and how soon can you deliver?",
        "Hello! Minimum order is 50 kg as listed. I deliver to Mbale town twice a week — Tuesday and Saturday. Place the order today and I will include you on Saturday's delivery run."),
]

# ── Product review data ───────────────────────────────────────────────────────
REVIEWS = [
    # (order_pair_index, rating, body)
    (0, 5, "Excellent maize — 12% moisture as stated, uniform grain size, and the solar drying shows in how clean and dry it arrived. Milled perfectly with zero rejects."),
    (1, 5, "Desiree potatoes were exactly as described: consistent 60–80g sizing, clean skin, no bruising despite the Mbarara road. Restaurant customers loved the taste."),
    (2, 4, "Good quality white sorghum, low tannin as described. One bag had slightly uneven drying but overall very acceptable for malting. Will reorder."),
    (3, 5, "Mpologoma matoke arrived at perfect green stage and ripened beautifully in three days. Large bunches, firm flesh, great flavour. Buying again."),
    (4, 5, "K132 yellow beans are exceptional — uniform large seed, clean grade, minimal defects. Our export buyer passed them first inspection. Mugisha is now our primary bean supplier."),
    (5, 4, "Roma tomatoes were firm and fresh. One crate had a few over-ripe tomatoes at the bottom but Kawesi resolved it immediately with a replacement. Good supplier."),
    (6, 5, "White Irish potatoes are exactly hotel grade — 60–80g, consistent, no greening. Delivered on time as promised. Signed a monthly supply contract."),
    (7, 5, "Traditional Okileng millet is exactly what we needed. Our brewery buyer confirmed authenticity and placed a standing order. Atim is reliable and honest."),
    (8, 5, "NAADS Grade A cassava chips arrived with all certificates. Perfect 14% moisture, no mould, no aflatoxin. Export shipment cleared customs first attempt."),
    (9, 4, "Good Bam 1 beans. Grading was mostly clean though a few split seeds in one bag. Delivery was punctual. Would order again with a note about the split seeds."),
]

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
                "id":           listing_id,
                "name":         listing_data["name"],
                "farmer":       f["fullName"],
                "farmer_id":    f["id"],
                "district":     listing_data["district"],
                "price":        listing_data["price"],
                "minimumOrder": listing_data.get("minimumOrder", 50),
                "unit":         listing_data.get("unit", "kg"),
            })

    return all_listings


# ── Phase 3b: Seed listing images ────────────────────────────────────────────

def seed_listing_images(listings: list) -> None:
    """
    Downloads a crop-appropriate image from Unsplash and uploads it to each
    listing via the existing multipart endpoint (which stores to Cloudinary).
    Skips gracefully if the download or upload fails.
    """
    print("\n── Phase 3b: Seeding listing images ────────────────────────────")
    headers_common = {"User-Agent": "Soko-Seed/1.0 (dev)"}

    for listing in listings:
        img_url = pick_crop_image(listing["name"])
        try:
            dl = requests.get(img_url, timeout=15, headers=headers_common, allow_redirects=True)
            if not dl.ok:
                print(f"  ~ Download failed ({dl.status_code}): {listing['name']}")
                continue

            content_type = dl.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
            ext = ext_map.get(content_type, "jpg")
            if content_type not in ext_map:
                content_type = "image/jpeg"

            resp = requests.post(
                f"{PRODUCE}/listings/{listing['id']}/images",
                files={"files": (f"photo.{ext}", dl.content, content_type)},
                headers=farmer_headers(listing["farmer_id"]),
                timeout=30,
            )
            if resp.ok:
                print(f"  ✓ Image: {listing['name']}")
            else:
                print(f"  ~ Upload failed ({resp.status_code}): {listing['name']}")
        except Exception as e:
            print(f"  ~ Skipped {listing['name']}: {e}")


# ── Phase 4: Place orders ────────────────────────────────────────────────────

def place_orders(buyers: list, listings: list) -> list:
    print("\n── Phase 4: Placing orders (cash on delivery) ──────────────────")
    order_ids = []

    for buyer_idx, listing_idx in ORDER_PAIRS:
        if buyer_idx >= len(buyers) or listing_idx >= len(listings):
            print(f"  SKIP index out of range: buyer={buyer_idx} listing={listing_idx}")
            continue

        b       = buyers[buyer_idx]
        listing = listings[listing_idx]
        qty     = listing["minimumOrder"]
        price   = listing["price"]
        subtotal = qty * price
        total   = subtotal + DELIVERY_FEE

        addr_template = DELIVERY_ADDR.get(
            b["district"],
            {"district": b["district"], "subCounty": "Central", "village": "Town Centre"},
        )

        payload = {
            "items": [{
                "productId": listing["id"],
                "quantity":  qty,
                "unitPrice": price,
                "subtotal":  subtotal,
            }],
            "deliveryAddress": {
                "fullName":  b["fullName"],
                "phone":     b["phone"],
                "district":  addr_template["district"],
                "subCounty": addr_template["subCounty"],
                "village":   addr_template["village"],
                "landmark":  f"Near {addr_template['subCounty']} market",
            },
            "paymentMethod": {"type": "cash_on_delivery"},
            "totalAmount":   total,
            "currency":      "UGX",
        }

        resp = requests.post(
            f"{ORDER}/orders/",
            json=payload,
            headers=buyer_headers(b["id"]),
        )
        if not resp.ok:
            print(f"  ✗ Order {b['fullName']} → {listing['name']}: {resp.status_code} — {resp.text[:120]}")
            continue

        order_id = resp.json().get("id") or resp.json().get("orderId", "")
        order_ids.append(order_id)
        print(f"  ✓ Order: {b['fullName']:<22} → {listing['name']:<30} ({qty} {listing['unit']} × {price:,} UGX)")

    print(f"  {len(order_ids)} order(s) created.")
    return order_ids


# ── Phase 5: Start conversations + replies ────────────────────────────────────

def create_conversations(buyers: list, listings: list) -> None:
    print("\n── Phase 5: Creating buyer–farmer conversations ─────────────────")

    for script_idx, buyer_msg, farmer_reply in CONV_SCRIPTS:
        if script_idx >= len(ORDER_PAIRS):
            continue
        buyer_idx, listing_idx = ORDER_PAIRS[script_idx]
        if buyer_idx >= len(buyers) or listing_idx >= len(listings):
            continue

        b       = buyers[buyer_idx]
        listing = listings[listing_idx]

        # Buyer opens conversation
        resp = requests.post(
            f"{MESSAGE}/conversations",
            json={
                "farmer_id":     listing["farmer_id"],
                "listing_id":    listing["id"],
                "first_message": buyer_msg,
            },
            headers=buyer_headers(b["id"]),
        )
        if not resp.ok:
            print(f"  ✗ Conv start {b['fullName']} → {listing['farmer']}: {resp.status_code}")
            continue

        data = resp.json()
        conv_id = data.get("conversation", {}).get("id") or data.get("id", "")

        # Farmer replies
        if conv_id:
            resp2 = requests.post(
                f"{MESSAGE}/conversations/{conv_id}/messages",
                json={"body": farmer_reply},
                headers=farmer_headers(listing["farmer_id"]),
            )
            if resp2.ok:
                print(f"  ✓ Conv: {b['fullName']:<22} ↔ {listing['farmer']}")
            else:
                print(f"  ~ Conv opened but reply failed: {resp2.status_code}")
        else:
            print(f"  ~ Conv opened (no conv_id in response to reply)")


# ── Phase 6: Blog posts ───────────────────────────────────────────────────────

def create_blog_posts(farmers: list) -> None:
    print("\n── Phase 6: Publishing farmer blog posts ────────────────────────")

    all_farmers = farmers  # same order as FARMERS + EXTRA_FARMERS
    for post_idx, post_def in enumerate(BLOG_POSTS):
        idx = post_def["farmer_idx"]
        if idx >= len(all_farmers):
            print(f"  SKIP blog post (farmer_idx {idx} out of range)")
            continue

        f = all_farmers[idx]
        cover = (
            BLOG_POST_IMAGES[post_idx]
            if post_idx < len(BLOG_POST_IMAGES)
            else BLOG_POST_IMAGES[-1]
        )
        payload = {
            "title":    post_def["title"],
            "excerpt":  post_def["excerpt"],
            "image":    cover,
            "category": post_def["category"],
            "tags":     post_def["tags"],
            "body":     post_def["body"],
        }

        resp = requests.post(
            f"{BLOG}/posts/",
            json=payload,
            headers=farmer_headers(f["id"]),
        )
        if not resp.ok:
            print(f"  ✗ Blog post '{post_def['title'][:50]}': {resp.status_code} — {resp.text[:120]}")
            continue

        post_id = resp.json().get("id", "")

        # Publish the draft
        resp2 = requests.post(
            f"{BLOG}/posts/{post_id}/publish",
            headers=farmer_headers(f["id"]),
        )
        if resp2.ok:
            print(f"  ✓ Published: '{post_def['title'][:55]}'  ({f['fullName']})")
        else:
            print(f"  ~ Draft created but publish failed: {resp2.status_code}")


# ── Phase 7: Product reviews ──────────────────────────────────────────────────

def create_reviews(buyers: list, listings: list) -> None:
    print("\n── Phase 7: Adding product reviews ─────────────────────────────")

    for pair_idx, rating, body in REVIEWS:
        if pair_idx >= len(ORDER_PAIRS):
            continue
        buyer_idx, listing_idx = ORDER_PAIRS[pair_idx]
        if buyer_idx >= len(buyers) or listing_idx >= len(listings):
            continue

        b       = buyers[buyer_idx]
        listing = listings[listing_idx]

        resp = requests.post(
            f"{PRODUCE}/listings/{listing['id']}/reviews",
            json={"rating": rating, "body": body},
            headers={
                **buyer_headers(b["id"]),
                "X-User-Name": b["fullName"],
            },
        )
        if resp.ok:
            print(f"  ✓ Review ({rating}★): {b['fullName']:<22} → {listing['name']}")
        elif resp.status_code == 409:
            print(f"  ~ Already reviewed: {b['fullName']} → {listing['name']}")
        else:
            print(f"  ✗ Review failed {b['fullName']} → {listing['name']}: {resp.status_code} — {resp.text[:100]}")


# ── Write seed manifest ───────────────────────────────────────────────────────

def write_manifest(farmers: list, buyers: list, listings: list, order_ids: list) -> None:
    manifest = {
        "farmers":    [{"id": f["id"], "name": f["fullName"], "email": f["email"]} for f in farmers],
        "buyers":     [{"id": b["id"], "name": b["fullName"], "email": b["email"]} for b in buyers],
        "listing_ids": [l["id"] for l in listings],
        "order_ids":   order_ids,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(f"\n  Manifest written → {MANIFEST.name}")


# ── Phase 8: Trigger ML bootstrap ────────────────────────────────────────────

def trigger_bootstrap():
    print("\n── Phase 8: Triggering data-ingestion bootstrap ────────────────")
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


# ── Phase 9: Restart recommendation service ───────────────────────────────────

def reload_recommendation_service():
    print("\n── Phase 9: Reloading recommendation service ───────────────────")
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

def print_summary(farmers: list, buyers: list, listings: list, order_ids: list):
    print("\n" + "═" * 68)
    print("SEED COMPLETE")
    print("═" * 68)

    print(f"\nFarmers ({len(farmers)}):")
    for f in farmers:
        print(f"  {f['id']}  {f['fullName']:<25} {f['district']}")

    print(f"\nBuyers ({len(buyers)}):")
    for b in buyers:
        print(f"  {b['id']}  {b['fullName']:<25} {b['district']}")

    print(f"\nListings ({len(listings)}) — all active:")
    for l in listings:
        print(f"  {l['id']}  {l['name']:<35} {l['farmer']}")

    print(f"\nOrders placed : {len(order_ids)}")

    if farmers and buyers:
        fid = farmers[0]["id"]
        bid = buyers[0]["id"]
        print(f"\nSample smoke-test commands:")
        print(f"  curl -s 'http://localhost:8080/recommend/farmers-for-buyer/{bid}?top_n=3' | python3 -m json.tool")
        print(f"  curl -s 'http://localhost:8080/recommend/buyers-for-farmer/{fid}?top_n=3' | python3 -m json.tool")
        print(f"  curl -s 'http://localhost:8080/price/predict' \\")
        print(f"       -H 'Content-Type: application/json' \\")
        print(f"       -d '{{\"crop\":\"maize_grain\",\"market\":\"Kampala\",\"forecast_days\":7}}' | python3 -m json.tool")

    print(f"\n  Run 'make destroy-seed' to undo all of the above.")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Soko seed script starting...")

    farmers, buyers = register_users()
    update_profiles(farmers)
    listings        = create_listings(farmers)
    seed_listing_images(listings)
    order_ids       = place_orders(buyers, listings)
    create_conversations(buyers, listings)
    create_blog_posts(farmers)
    create_reviews(buyers, listings)
    trigger_bootstrap()
    reload_recommendation_service()
    write_manifest(farmers, buyers, listings, order_ids)
    print_summary(farmers, buyers, listings, order_ids)
