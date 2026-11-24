#!/usr/bin/env python3
"""Generate a deterministic, realistic Indian e-commerce dataset.

The business is "BharatCart Retail", a fictional Indian online retailer.
The snapshot covers Oct 2023 - Sep 2025, ending in the pre-festive September
lull ahead of Diwali. Raw CSVs are intentionally dirty; the ingestion pipeline
must clean them back to the clean dataset described by expected_metrics.json.

Usage:
    python scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
EXPECTED_PATH = ROOT / "data" / "expected_metrics.json"

SEED = 42
START = date(2023, 10, 1)
END = date(2025, 9, 30)
AS_OF = END

FIRST_NAMES = [
    "Aarav", "Ananya", "Vivaan", "Diya", "Aditya", "Isha", "Arjun", "Meera",
    "Rohan", "Sneha", "Kabir", "Priya", "Rahul", "Pooja", "Vikram", "Neha",
    "Karan", "Riya", "Siddharth", "Nisha", "Ayaan", "Kavya", "Dev", "Tara",
    "Ishaan", "Sanya", "Nikhil", "Aditi", "Manish", "Shreya", "Rajat", "Anjali",
    "Varun", "Divya", "Amit", "Swati", "Harsh", "Payal", "Suresh", "Deepa",
    "Ajay", "Kritika", "Vijay", "Pallavi", "Sanjay", "Mitali", "Ravi", "Aparna",
    "Gaurav", "Simran", "Yash", "Tanvi", "Abhishek", "Ritika", "Pranav", "Sakshi",
    "Dhruv", "Bhavna", "Naveen", "Rupal",
]

LAST_NAMES = [
    "Sharma", "Patel", "Reddy", "Nair", "Iyer", "Gupta", "Singh", "Verma",
    "Mehta", "Joshi", "Das", "Banerjee", "Rao", "Chawla", "Kulkarni", "Pillai",
    "Agarwal", "Bose", "Chauhan", "Desai", "Ghosh", "Hegde", "Jain", "Kapoor",
    "Khanna", "Kumar", "Malhotra", "Menon", "Mishra", "Mukherjee", "Nayak", "Pandey",
    "Prasad", "Rathore", "Saxena", "Sethi", "Shah", "Shetty", "Sinha", "Srinivasan",
    "Soni", "Sundaram", "Tiwari", "Trivedi", "Vaidya", "Varma", "Yadav", "Bhat",
    "Bhattacharya", "Dutta", "Gandhi", "Gill", "Gowda", "Kaur", "Khan", "Krishnan",
    "Lal", "Mahajan", "Naidu", "Parekh",
]

# city, state, tier, weight
CITIES = [
    ("Mumbai", "Maharashtra", "Tier 1", 0.120),
    ("Delhi", "Delhi", "Tier 1", 0.110),
    ("Bengaluru", "Karnataka", "Tier 1", 0.100),
    ("Hyderabad", "Telangana", "Tier 1", 0.070),
    ("Chennai", "Tamil Nadu", "Tier 1", 0.060),
    ("Kolkata", "West Bengal", "Tier 1", 0.050),
    ("Pune", "Maharashtra", "Tier 1", 0.040),
    ("Ahmedabad", "Gujarat", "Tier 1", 0.030),
    ("Jaipur", "Rajasthan", "Tier 2", 0.040),
    ("Lucknow", "Uttar Pradesh", "Tier 2", 0.035),
    ("Kochi", "Kerala", "Tier 2", 0.030),
    ("Chandigarh", "Punjab", "Tier 2", 0.025),
    ("Indore", "Madhya Pradesh", "Tier 2", 0.025),
    ("Surat", "Gujarat", "Tier 2", 0.025),
    ("Coimbatore", "Tamil Nadu", "Tier 2", 0.020),
    ("Bhubaneswar", "Odisha", "Tier 2", 0.020),
    ("Guwahati", "Assam", "Tier 2", 0.015),
    ("Patna", "Bihar", "Tier 2", 0.020),
    ("Nagpur", "Maharashtra", "Tier 2", 0.020),
    ("Visakhapatnam", "Andhra Pradesh", "Tier 2", 0.015),
    ("Dehradun", "Uttarakhand", "Tier 3", 0.015),
    ("Raipur", "Chhattisgarh", "Tier 3", 0.012),
    ("Ranchi", "Jharkhand", "Tier 3", 0.012),
    ("Imphal", "Manipur", "Tier 3", 0.008),
    ("Bhuj", "Gujarat", "Tier 3", 0.008),
    ("Hubballi", "Karnataka", "Tier 3", 0.010),
    ("Madurai", "Tamil Nadu", "Tier 3", 0.010),
    ("Varanasi", "Uttar Pradesh", "Tier 3", 0.010),
    ("Other India", "India", "Tier 3", 0.020),
]

COUNTRIES = [
    ("India", 0.94),
    ("United Arab Emirates", 0.02),
    ("United States", 0.015),
    ("United Kingdom", 0.010),
    ("Singapore", 0.010),
    ("Canada", 0.005),
]
NRI_CITIES = {
    "United Arab Emirates": ("Dubai", "Dubai", "International"),
    "United States": ("San Francisco", "California", "International"),
    "United Kingdom": ("London", "England", "International"),
    "Singapore": ("Singapore", "Singapore", "International"),
    "Canada": ("Toronto", "Ontario", "International"),
}

COUNTRY_ALIASES = {
    "ind": "India",
    "bharat": "India",
    "india ": "India",
    "uae": "United Arab Emirates",
    "usa": "United States",
    "us": "United States",
    "uk": "United Kingdom",
    "great britain": "United Kingdom",
}

SEGMENTS = [("Consumer", 0.70), ("SMB", 0.20), ("Enterprise", 0.10)]

# Curated catalogue: 20 products per category, 160 in total.
CATALOG: dict[str, list[str]] = {
    "Electronics": [
        'Surya 43" 4K Smart TV', 'Surya 55" QLED Smart TV', "Anand Mixer Grinder 750W",
        "Nimbus Noise-Cancelling Headphones", "Vayu Air Purifier", "Chakra Smartwatch S2",
        "Tejas Laptop Stand", "Vidyut 20000mAh Power Bank", "Rangoli LED Strip 5m",
        "Signal Mesh Wi-Fi Router", "Aero TWS Earbuds", 'Prism 15" Laptop Sleeve',
        "Cobalt Wireless Mouse", "Spark Mini Projector", "Terra 1TB Portable SSD",
        "Lumen Smart Desk Lamp", "Drift Wireless Charger", "Circuit HDMI 2.1 Cable",
        "Nova Tablet Stand", "Beacon Smart Plug 4-Pack",
    ],
    "Home & Kitchen": [
        "Surya Pressure Cooker 5L", "Anand Wet Grinder 2L", "Chaiwala Electric Kettle 1.5L",
        "Rangoli Non-Stick Tawa 28cm", "Surya Cast Iron Kadhai 24cm", "Rangoli Idli Steamer 4-Tier",
        "Masala Dabba Stainless Steel", "Roti Maker 900W", "Cotton Bedsheet Set Queen",
        "Terra Ceramic Planter", "Copper Water Bottle 1L", "Sable Chef's Knife 8\"",
        "Glow Table Lamp", "Willow Serving Board", "Cloud Cotton Bath Towels",
        "Haven Bamboo Organizer", "Stoneware Dinner Set 16-Piece", "Breeze Air Purifier Mini",
        "Nesting Mixing Bowls", "Hearth Scented Candle",
    ],
    "Fashion": [
        "Rangoli Cotton Kurta", "Banarasi Silk Saree", "Chikankari Kurta Set",
        "Kolhapuri Leather Chappals", "Nehru Jacket", "Kota Doria Dupatta",
        "Salwar Suit Set", "Kanjeevaram Silk Saree", "Cotton Dhoti",
        "Sports Jersey", "Block Print Cotton Dress", "Leather Mojari Shoes",
        "Pashmina Shawl", "Denim Jacket", "Cotton Palazzo Pants",
        "Oxidised Jhumka Earrings", "Leather Belt", "Canvas Sneakers",
        "Silk Tie", "Embroidered Potli Bag",
    ],
    "Beauty & Personal Care": [
        "Kumkumadi Facial Serum", "Neem Face Wash", "Bhringraj Hair Oil",
        "Sandalwood Soap", "Kajal Stick", "Henna Mehendi Cone",
        "Aloe Vera Gel", "Rose Water Toner", "Turmeric Face Pack",
        "Beard Oil", "Vitamin C Face Cream", "Ubtan Body Scrub",
        "Lip Balm SPF 15", "Amla Shampoo", "Coconut Hair Mask",
        "Charcoal Face Mask", "Shea Body Butter", "Lavender Bath Salts",
        "Under-Eye Cream", "Nail Care Kit",
    ],
    "Grocery & Staples": [
        "Basmati Rice 5kg", "Whole Wheat Atta 10kg", "Toor Dal 1kg",
        "Cold-Pressed Groundnut Oil 5L", "Assam Tea 500g", "Filter Coffee 500g",
        "Pure Ghee 1kg", "Masala Chai Mix 250g", "Bhujia Namkeen 1kg",
        "Mango Pickle 500g", "Jaggery 1kg", "Turmeric Powder 500g",
        "Red Chilli Powder 500g", "Garam Masala 100g", "Poha 1kg",
        "Moong Dal 1kg", "Sattu 1kg", "Besan 1kg",
        "Sabudana 500g", "Pistachio Butter 250g",
    ],
    "Sports & Outdoors": [
        "Kashmir Willow Cricket Bat", "Cricket Leather Ball", "Apex Yoga Mat 6mm",
        "Badminton Racket Set", "Football Size 5", "Summit Trekking Backpack 40L",
        "Basecamp 2-Person Tent", "Cyclone Cycling Helmet", "Gym Gloves",
        "Skipping Rope", "Resistance Bands Set", "Foam Roller",
        "Trail Running Shoes", "Insulated Water Bottle 1L", "Carbon Trekking Poles",
        "Swim Goggles", "Compression Socks", "Trail Running Vest",
        "Cricket Batting Gloves", "Table Tennis Racket Set",
    ],
    "Books & Media": [
        "The Monsoon Diaries", "Chai & Code", "Ayurveda for Modern Life",
        "The Cricket Chronicles", "Tiffin Tales", "India's Startup Story",
        "Yoga for Every Day", "The Palace Cookbook", "Bharat: A Photo Journey",
        "Mindful Mornings", "The Quiet Algorithm", "Salt & Smoke: Indian Kitchen",
        "Atlas of Small Places", "Midnight Orchard", "The Data Detective",
        "Field Notes on Design", "A Short History of Chai", "The Art of Clear Thinking",
        "Wander: India", "The Science of Sleep",
    ],
    "Toys & Games": [
        "Channapatna Wooden Toys", "Ludo Board Game", "Carrom Board",
        "Kite Set", "Cricket Set", "Mehndi Art Kit",
        "Puzzle Map of India", "Stunt RC Car", "Magnetic Tile Set 60-Piece",
        "Wooden Train Set", "Strategy Board Game", "Constellation Projector",
        "Mini Chef Play Set", "Stack & Sort Blocks", "Ocean Memory Game",
        "Art Studio Kit", "Science Lab Junior", "Bubble Machine",
        "Music Maker Set", "Plush Elephant",
    ],
}

CATEGORY_META = {
    "Electronics": {"weight": 0.25, "price": (299, 59999)},
    "Home & Kitchen": {"weight": 0.18, "price": (149, 9999)},
    "Fashion": {"weight": 0.12, "price": (299, 9999)},
    "Beauty & Personal Care": {"weight": 0.12, "price": (49, 2499)},
    "Grocery & Staples": {"weight": 0.06, "price": (49, 1999)},
    "Sports & Outdoors": {"weight": 0.13, "price": (149, 9999)},
    "Books & Media": {"weight": 0.07, "price": (99, 999)},
    "Toys & Games": {"weight": 0.07, "price": (149, 4999)},
}

PRICE_TIERS = {
    "Electronics": [(8999, 59999), (3999, 19999), (999, 7999), (299, 2499)],
    "Home & Kitchen": [(1999, 9999), (999, 4999), (499, 2499), (149, 999)],
    "Fashion": [(2999, 9999), (1499, 4999), (699, 2499), (299, 999)],
    "Beauty & Personal Care": [(499, 2499), (299, 1299), (149, 699), (49, 299)],
    "Grocery & Staples": [(499, 1999), (299, 999), (149, 599), (49, 249)],
    "Sports & Outdoors": [(1999, 9999), (999, 4999), (499, 2499), (149, 999)],
    "Books & Media": [(399, 999), (299, 699), (199, 499), (99, 299)],
    "Toys & Games": [(1499, 4999), (799, 2499), (399, 1299), (149, 599)],
}

ELECTRONICS_ACCESSORIES = (
    "Charger", "Cable", "Hub", "Stand", "Plug", "Mouse", "Sleeve", "Power Bank", "Ring Light",
)
ELECTRONICS_PREMIUM = ("TV", "Projector", "Router", "SSD")
ELECTRONICS_MID = ("Headphones", "Speaker", "Keyboard", "Smartwatch", "Earbuds", "Webcam")

# Indian retail calendar: festive Oct-Nov, wedding Jan-Feb, monsoon Jun-Jul,
# pre-festive Shradh lull in September.
SEASONAL = {
    1: 0.95, 2: 1.05, 3: 1.08, 4: 0.92, 5: 0.95, 6: 0.88,
    7: 0.90, 8: 0.96, 9: 0.98, 10: 1.35, 11: 1.30, 12: 1.10,
}

CHANNELS = [("Website", 0.36), ("Mobile App", 0.40), ("Marketplace", 0.14), ("WhatsApp", 0.05), ("Retail Store", 0.05)]
STATUSES = [("completed", 0.900), ("cancelled", 0.040), ("returned", 0.035), ("pending", 0.025)]

PAYMENTS_BY_TIER = {
    "Tier 1": [("UPI", 0.60), ("Credit Card", 0.18), ("Debit Card", 0.10), ("Net Banking", 0.05), ("Cash on Delivery", 0.04), ("Wallet", 0.03)],
    "Tier 2": [("UPI", 0.55), ("Credit Card", 0.10), ("Debit Card", 0.12), ("Net Banking", 0.04), ("Cash on Delivery", 0.15), ("Wallet", 0.04)],
    "Tier 3": [("UPI", 0.50), ("Credit Card", 0.05), ("Debit Card", 0.12), ("Net Banking", 0.03), ("Cash on Delivery", 0.25), ("Wallet", 0.05)],
    "International": [("Credit Card", 0.55), ("UPI", 0.20), ("Debit Card", 0.15), ("Net Banking", 0.10)],
}


def weighted_choice(rng: random.Random, items: list[tuple[str, float]]) -> str:
    return rng.choices([x[0] for x in items], weights=[x[1] for x in items], k=1)[0]


def month_iter(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def days_in_month(y: int, m: int) -> int:
    if m == 12:
        return 31
    return (date(y, m + 1, 1) - date(y, m, 1)).days


def make_products(rng: random.Random) -> list[dict]:
    products = []
    pid = 1
    for category, names in CATALOG.items():
        lo, hi = CATEGORY_META[category]["price"]
        for idx, name in enumerate(names):
            if category == "Electronics":
                if name.startswith("Surya 43"):
                    price = round(rng.uniform(24999, 34999), 0)
                elif name.startswith("Surya 55"):
                    price = round(rng.uniform(44999, 59999), 0)
                elif "Mixer Grinder" in name:
                    price = round(rng.uniform(2499, 5999), 0)
                elif "Air Purifier" in name:
                    price = round(rng.uniform(8999, 19999), 0)
                elif any(k in name for k in ELECTRONICS_ACCESSORIES):
                    price = round(rng.uniform(299, 2999), 0)
                elif any(k in name for k in ELECTRONICS_PREMIUM):
                    price = round(rng.uniform(8999, 59999), 0)
                elif any(k in name for k in ELECTRONICS_MID):
                    price = round(rng.uniform(1499, 9999), 0)
                else:
                    price = round(rng.uniform(999, 4999), 0)
            else:
                tier = min(3, idx // 5)
                band_lo, band_hi = PRICE_TIERS[category][tier]
                price = round(rng.uniform(band_lo, band_hi), 0)
            price = float(max(lo, min(hi, price)))
            margin = rng.uniform(0.42, 0.66)
            cost = round(price * margin, 2)
            launch_roll = rng.random()
            if launch_roll < 0.70:
                launch = START - timedelta(days=rng.randint(30, 700))
            elif launch_roll < 0.90:
                launch = date(2023, 12, 1) + timedelta(days=rng.randint(0, 395))
            else:
                launch = date(2025, 1, 1) + timedelta(days=rng.randint(0, 250))
            active = rng.random() > 0.04
            popularity = rng.uniform(0.45, 2.2)
            if name.startswith("Surya 43"):
                popularity = 3.0
            if rng.random() < 0.06:
                popularity = rng.uniform(0.08, 0.25)
            products.append({
                "product_id": f"P{pid:04d}",
                "name": name,
                "category": category,
                "unit_price": price,
                "unit_cost": cost,
                "launch_date": launch.isoformat(),
                "active": "true" if active else "false",
                "_popularity": popularity,
                "_launch": launch,
                "_active": active,
            })
            pid += 1
    return products


def make_customers(rng: random.Random, products: list[dict]) -> list[dict]:
    customers = []
    used_names: set[str] = set()
    category_weights = [(c, CATEGORY_META[c]["weight"]) for c in CATALOG]
    for i in range(1, 2201):
        cid = f"C{i:04d}"
        for _ in range(30):
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            key = f"{first} {last}"
            if key not in used_names:
                used_names.add(key)
                break
        else:
            first, last = "Aarav", f"Customer{i}"
            key = f"{first} {last}"
        name = key
        country = weighted_choice(rng, COUNTRIES)
        if country == "India":
            city, state, tier, _ = rng.choices(CITIES, weights=[c[3] for c in CITIES], k=1)[0]
        else:
            city, state, tier = NRI_CITIES[country]
        segment = weighted_choice(rng, SEGMENTS)
        email = f"{first.lower()}.{last.lower().replace(chr(39), '')}{i}@example.com"

        signup_roll = rng.random()
        if signup_roll < 0.28:
            signup = date(2022, 6, 1) + timedelta(days=rng.randint(0, 485))
        elif signup_roll < 0.70:
            signup = date(2023, 10, 1) + timedelta(days=rng.randint(0, 425))
        else:
            signup = date(2025, 1, 1) + timedelta(days=rng.randint(0, 260))
        signup = min(signup, date(2025, 9, 20))

        propensity = float(np.random.default_rng(SEED + i).beta(2.2, 4.8))
        churn = None
        if rng.random() < 0.24:
            churn = date(2024, 1, 1) + timedelta(days=rng.randint(0, 610))
        never_buy = rng.random() < 0.11
        max_orders = 1 if rng.random() < 0.40 else (2 if rng.random() < 0.15 else 99)
        customers.append({
            "customer_id": cid,
            "name": name,
            "email": email,
            "country": country,
            "state": state,
            "city": city,
            "city_tier": tier,
            "segment": segment,
            "signup_date": signup.isoformat(),
            "_signup": signup,
            "_propensity": propensity,
            "_preferred": weighted_choice(rng, category_weights),
            "_churn": churn,
            "_never_buy": never_buy,
            "_max_orders": max_orders,
        })
    return customers


def generate_orders(rng: random.Random, customers: list[dict], products: list[dict]) -> tuple[list[dict], list[dict]]:
    products_by_cat: dict[str, list[dict]] = {}
    for p in products:
        products_by_cat.setdefault(p["category"], []).append(p)

    customer_state = {
        c["customer_id"]: {
            "first": None, "last": None, "orders": 0,
            "signup": c["_signup"], "churn": c["_churn"], "never": c["_never_buy"],
            "propensity": c["_propensity"], "preferred": c["_preferred"],
            "segment": c["segment"], "tier": c["city_tier"], "max_orders": c["_max_orders"],
        }
        for c in customers
    }
    customer_ids = list(customer_state)

    orders: list[dict] = []
    items: list[dict] = []
    order_seq = 1
    item_seq = 1
    global_cat_weights = [(c, CATEGORY_META[c]["weight"]) for c in CATALOG]

    months = list(month_iter(START, END))
    for idx, (y, m) in enumerate(months):
        last_day = days_in_month(y, m)
        month_start = date(y, m, 1)
        month_end = date(y, m, last_day)
        is_decline_month = (y, m) == (2025, 9)

        base = 520 * (1 + 0.007 * idx) * SEASONAL[m] * rng.uniform(0.95, 1.05)
        n_orders = max(40, int(round(base)))

        eligible = [
            cid for cid, s in customer_state.items()
            if not s["never"] and s["signup"] <= month_end
            and (s["churn"] is None or month_start < s["churn"])
            and (s["first"] is None or s["orders"] < s["max_orders"])
        ]
        new_pool = [cid for cid in eligible if customer_state[cid]["first"] is None]
        returning_pool = [
            cid for cid in eligible
            if customer_state[cid]["first"] is not None
            and customer_state[cid]["orders"] < customer_state[cid]["max_orders"]
        ]

        for _ in range(n_orders):
            new_pool = [cid for cid in new_pool if customer_state[cid]["first"] is None]
            returning_pool = [
                cid for cid in returning_pool
                if customer_state[cid]["first"] is not None
                and customer_state[cid]["orders"] < customer_state[cid]["max_orders"]
            ]
            if not new_pool and not returning_pool:
                break
            is_new = rng.random() < (0.18 if is_decline_month else 0.16)
            chosen = None
            if is_new and new_pool:
                weights = []
                for cid in new_pool:
                    days_since = max(0, (month_end - customer_state[cid]["signup"]).days)
                    weights.append(customer_state[cid]["propensity"] * math.exp(-days_since / 180.0) + 0.01)
                chosen = rng.choices(new_pool, weights=weights, k=1)[0]
            if chosen is None and returning_pool:
                weights = []
                for cid in returning_pool:
                    s = customer_state[cid]
                    days_since = max(0, (month_end - s["last"]).days)
                    weights.append(s["propensity"] * math.exp(-days_since / 150.0) + 0.005)
                chosen = rng.choices(returning_pool, weights=weights, k=1)[0]
            if chosen is None and new_pool:
                chosen = rng.choice(new_pool)
            if chosen is None and returning_pool:
                chosen = rng.choice(returning_pool)
            if chosen is None:
                continue

            state = customer_state[chosen]
            if state["first"] is None and state["signup"] > month_start:
                day = rng.randint(state["signup"].day, last_day) if state["signup"].month == m else rng.randint(1, last_day)
                order_date = date(y, m, min(day, last_day))
            else:
                order_date = date(y, m, rng.randint(1, last_day))

            status = weighted_choice(rng, STATUSES)
            if is_decline_month and status == "completed" and rng.random() < 0.02:
                status = "cancelled"
            channel = weighted_choice(rng, CHANNELS)
            payment = weighted_choice(rng, PAYMENTS_BY_TIER.get(state["tier"], PAYMENTS_BY_TIER["Tier 1"]))

            cat_weights = []
            for cat, weight in global_cat_weights:
                w = weight
                if is_decline_month:
                    if cat == "Electronics":
                        w *= 0.80
                    elif cat == "Fashion":
                        w *= 1.05
                    elif cat == "Sports & Outdoors":
                        w *= 0.95
                    elif cat == "Grocery & Staples":
                        w *= 1.12
                    elif cat == "Beauty & Personal Care":
                        w *= 1.05
                cat_weights.append((cat, w))

            n_items = rng.choices([1, 2, 3, 4], weights=[0.42, 0.34, 0.18, 0.06], k=1)[0]
            used_products: set[str] = set()
            order_items = []
            for _ in range(n_items):
                preferred = state["preferred"]
                category = preferred if rng.random() < 0.48 else weighted_choice(rng, cat_weights)
                candidates = [
                    p for p in products_by_cat.get(category, [])
                    if p["_launch"] <= order_date and p["_active"] and p["product_id"] not in used_products
                ]
                if not candidates:
                    candidates = [
                        p for p in products
                        if p["_launch"] <= order_date and p["_active"] and p["product_id"] not in used_products
                    ]
                if not candidates:
                    continue
                product = rng.choices(candidates, weights=[p["_popularity"] for p in candidates], k=1)[0]
                used_products.add(product["product_id"])
                qty_weights = [0.62, 0.24, 0.10, 0.04]
                if category in {"Grocery & Staples", "Books & Media"}:
                    qty_weights = [0.38, 0.32, 0.20, 0.10]
                qty = rng.choices([1, 2, 3, 4], weights=qty_weights, k=1)[0]
                unit_price = round(product["unit_price"] * rng.uniform(0.97, 1.03), 2)

                discount_roll = rng.random()
                if discount_roll < 0.55:
                    discount = 0
                elif discount_roll < 0.80:
                    discount = rng.choice([5, 10])
                elif discount_roll < 0.95:
                    discount = rng.choice([15, 20])
                else:
                    discount = rng.choice([25, 30])
                if is_decline_month and rng.random() < 0.20:
                    discount = min(30, discount + rng.choice([2, 3, 5]))

                line_revenue = round(qty * unit_price * (1 - discount / 100.0), 2)
                order_items.append({
                    "item_id": f"I{item_seq:07d}",
                    "order_id": f"O{order_seq:06d}",
                    "product_id": product["product_id"],
                    "quantity": qty,
                    "unit_price": unit_price,
                    "discount_pct": discount,
                    "_revenue": line_revenue,
                })
                item_seq += 1

            if not order_items:
                continue

            order_revenue = sum(x["_revenue"] for x in order_items)
            shipping = 0.0 if order_revenue >= 499 else 49.0
            orders.append({
                "order_id": f"O{order_seq:06d}",
                "customer_id": chosen,
                "order_date": order_date.isoformat(),
                "status": status,
                "channel": channel,
                "payment_method": payment,
                "shipping_cost": shipping,
                "_revenue": round(order_revenue, 2),
            })
            items.extend(order_items)
            order_seq += 1

            if state["first"] is None:
                state["first"] = order_date
            state["last"] = order_date
            state["orders"] += 1

    # Guarantee the snapshot ends exactly on 2025-09-30.
    if not any(o["order_date"] == AS_OF.isoformat() and o["status"] == "completed" for o in orders):
        cid = next(c for c in customer_ids if customer_state[c]["first"] is not None)
        pid = next(p for p in products if p["name"].startswith("Surya 43"))
        order_id = f"O{order_seq:06d}"
        item_id = f"I{item_seq:07d}"
        orders.append({
            "order_id": order_id, "customer_id": cid, "order_date": AS_OF.isoformat(),
            "status": "completed", "channel": "Mobile App", "payment_method": "UPI",
            "shipping_cost": 0.0, "_revenue": round(pid["unit_price"], 2),
        })
        items.append({
            "item_id": item_id, "order_id": order_id, "product_id": pid["product_id"],
            "quantity": 1, "unit_price": pid["unit_price"], "discount_pct": 0,
            "_revenue": round(pid["unit_price"], 2),
        })

    return orders, items


def compute_expected(products: list[dict], customers: list[dict], orders: list[dict], items: list[dict]) -> dict:
    p = pd.DataFrame(products).drop(columns=[c for c in products[0] if c.startswith("_")])
    c = pd.DataFrame(customers).drop(columns=[x for x in customers[0] if x.startswith("_")])
    o = pd.DataFrame(orders)
    i = pd.DataFrame(items).drop(columns=["_revenue"])

    o["order_date"] = pd.to_datetime(o["order_date"])
    completed = o[o["status"] == "completed"].copy()
    merged = i.merge(
        completed[["order_id", "order_date", "customer_id", "payment_method"]],
        on="order_id", how="inner",
    )
    merged = merged.merge(p[["product_id", "name", "category", "unit_price"]], on="product_id", how="left", suffixes=("", "_product"))
    merged = merged.merge(c[["customer_id", "city_tier"]], on="customer_id", how="left")
    merged["revenue"] = (merged["quantity"] * merged["unit_price"] * (1 - merged["discount_pct"] / 100.0)).round(2)

    as_of = completed["order_date"].max().date()
    latest_start = pd.Timestamp(as_of.year, as_of.month, 1)
    prev_start = latest_start - pd.offsets.MonthBegin(1)
    prev_end = latest_start - pd.Timedelta(days=1)

    def period_metrics(df: pd.DataFrame) -> dict:
        rev = float(df["revenue"].sum())
        order_count = int(df["order_id"].nunique())
        cust_count = int(df["customer_id"].nunique())
        units = int(df["quantity"].sum())
        return {
            "revenue": round(rev, 2),
            "orders": order_count,
            "customers": cust_count,
            "aov": round(rev / order_count, 2) if order_count else 0.0,
            "units": units,
        }

    latest = merged[merged["order_date"] >= latest_start]
    previous = merged[(merged["order_date"] >= prev_start) & (merged["order_date"] <= prev_end)]
    all_time = period_metrics(merged)
    latest_metrics = period_metrics(latest)
    previous_metrics = period_metrics(previous)

    by_product = merged.groupby(["product_id", "name", "category"], as_index=False).agg(
        revenue=("revenue", "sum"), units=("quantity", "sum"), orders=("order_id", "nunique")
    ).sort_values("revenue", ascending=False)
    by_category = merged.groupby("category", as_index=False).agg(
        revenue=("revenue", "sum"), units=("quantity", "sum"), orders=("order_id", "nunique")
    ).sort_values("revenue", ascending=False)
    by_payment = merged.groupby("payment_method", as_index=False).agg(
        revenue=("revenue", "sum"), orders=("order_id", "nunique")
    ).sort_values("revenue", ascending=False)
    by_tier = merged.groupby("city_tier", as_index=False).agg(
        revenue=("revenue", "sum"), orders=("order_id", "nunique"), customers=("customer_id", "nunique")
    ).sort_values("revenue", ascending=False)

    last_orders = merged.groupby("customer_id")["order_date"].max()
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=90)
    inactive = int((last_orders < cutoff).sum())
    never = int(len(c) - len(last_orders))
    monthly = merged.assign(month=merged["order_date"].dt.strftime("%Y-%m")).groupby("month").agg(
        revenue=("revenue", "sum"), orders=("order_id", "nunique")
    ).reset_index()

    top_product = by_product.iloc[0]
    top_category = by_category.iloc[0]
    total_revenue = all_time["revenue"] or 1.0
    return {
        "seed": SEED,
        "as_of": as_of.isoformat(),
        "row_counts": {
            "customers": int(len(c)),
            "products": int(len(p)),
            "orders": int(len(completed)),
            "order_items": int(len(merged)),
        },
        "totals": all_time,
        "latest_month": {"month": latest_start.strftime("%Y-%m"), **latest_metrics},
        "previous_month": {"month": prev_start.strftime("%Y-%m"), **previous_metrics},
        "top_product": {
            "product_id": str(top_product["product_id"]),
            "name": str(top_product["name"]),
            "revenue": round(float(top_product["revenue"]), 2),
            "units": int(top_product["units"]),
        },
        "top_category": {
            "category": str(top_category["category"]),
            "revenue": round(float(top_category["revenue"]), 2),
        },
        "payment_mix": [
            {
                "payment_method": str(r["payment_method"]),
                "revenue": round(float(r["revenue"]), 2),
                "orders": int(r["orders"]),
                "share_pct": round(float(r["revenue"]) / total_revenue * 100, 2),
            }
            for _, r in by_payment.iterrows()
        ],
        "city_tier_mix": [
            {
                "city_tier": str(r["city_tier"]),
                "revenue": round(float(r["revenue"]), 2),
                "orders": int(r["orders"]),
                "customers": int(r["customers"]),
                "share_pct": round(float(r["revenue"]) / total_revenue * 100, 2),
            }
            for _, r in by_tier.iterrows()
        ],
        "inactive_customers_90d": inactive,
        "never_purchased_customers": never,
        "monthly_revenue": {
            str(r["month"]): round(float(r["revenue"]), 2) for _, r in monthly.iterrows()
        },
    }


def inject_dirt(rng: random.Random, products: list[dict], customers: list[dict], orders: list[dict], items: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Return dirty copies. Destructive changes are additive so cleaning
    restores the original dataset exactly."""
    products = [dict(r) for r in products]
    customers = [dict(r) for r in customers]
    orders = [dict(r) for r in orders]
    items = [dict(r) for r in items]

    for row in customers:
        if rng.random() < 0.06:
            row["name"] = f"  {row['name']} "
        if rng.random() < 0.07:
            row["country"] = rng.choice([row["country"].upper(), f" {row['country'].lower()} ", "IND" if row["country"] == "India" else row["country"]])
        if rng.random() < 0.05:
            row["city_tier"] = rng.choice([row["city_tier"].lower(), row["city_tier"].replace("Tier ", "tier-"), "1" if row["city_tier"] == "Tier 1" else row["city_tier"]])
        if rng.random() < 0.02:
            row["email"] = ""
        if rng.random() < 0.04:
            row["segment"] = row["segment"].lower()
    customers += [dict(r) for r in rng.sample(customers, 12)]

    sold_ids = {r["product_id"] for r in items}
    unsold = [p for p in products if p["product_id"] not in sold_ids and p["active"] == "false"]
    for row in products:
        if rng.random() < 0.03:
            row["unit_price"] = f"₹{row['unit_price']:,.2f}"
        if rng.random() < 0.03:
            row["name"] = f" {row['name']} "
    for row in unsold[:4]:
        row["category"] = ""
    products += [dict(r) for r in rng.sample(products, 6)]

    for row in orders:
        if row["status"] == "completed" and rng.random() < 0.08:
            row["status"] = rng.choice(["Completed", "COMPLETE", "completed "])
        if rng.random() < 0.07:
            row["channel"] = rng.choice(["website", "mobile app", "WhatsApp", "whatsapp ", "marketplace "])
        if rng.random() < 0.06:
            variants = {
                "UPI": ["upi", "U.P.I.", "UPI "],
                "Credit Card": ["Credit card", "credit card", "CREDIT CARD"],
                "Debit Card": ["Debit card", "debit card", "DEBIT CARD"],
                "Net Banking": ["netbanking", "Net banking", "NET BANKING"],
                "Cash on Delivery": ["COD", "cash on delivery", "Cash On Delivery"],
                "Wallet": ["wallet", "Wallet ", "WALLET"],
            }
            row["payment_method"] = rng.choice(variants.get(row["payment_method"], [row["payment_method"]]))
        if rng.random() < 0.04:
            d = date.fromisoformat(row["order_date"])
            row["order_date"] = d.strftime("%d/%m/%Y")
    orders += [dict(r) for r in rng.sample(orders, int(len(orders) * 0.015))]

    next_order = len(orders) + 100000
    for _ in range(12):
        row = dict(rng.choice(orders))
        row["order_id"] = f"O{next_order:06d}"
        next_order += 1
        row["customer_id"] = ""
        orders.append(row)
    for _ in range(9):
        row = dict(rng.choice(orders))
        row["order_id"] = f"O{next_order:06d}"
        next_order += 1
        row["order_date"] = "not-a-date"
        orders.append(row)
    for _ in range(6):
        row = dict(rng.choice(orders))
        row["order_id"] = f"O{next_order:06d}"
        next_order += 1
        row["customer_id"] = "C9999"
        orders.append(row)

    valid_products = [p["product_id"] for p in products if p["_active"]]
    for row in items:
        if row["discount_pct"] == 0 and rng.random() < 0.06:
            row["discount_pct"] = ""
        if rng.random() < 0.03:
            row["unit_price"] = f"₹{row['unit_price']:,.2f}"
    items += [dict(r) for r in rng.sample(items, int(len(items) * 0.01))]

    next_item = len(items) + 1000000
    for _ in range(14):
        row = dict(rng.choice(items))
        row["item_id"] = f"I{next_item:07d}"
        next_item += 1
        row["quantity"] = -1
        items.append(row)
    for _ in range(10):
        row = dict(rng.choice(items))
        row["item_id"] = f"I{next_item:07d}"
        next_item += 1
        row["product_id"] = "P9999"
        items.append(row)
    for _ in range(8):
        row = dict(rng.choice(items))
        row["item_id"] = f"I{next_item:07d}"
        next_item += 1
        row["order_id"] = "O999999"
        items.append(row)
    for _ in range(5):
        row = dict(rng.choice(items))
        row["item_id"] = f"I{next_item:07d}"
        next_item += 1
        row["order_id"] = ""
        items.append(row)

    return products, customers, orders, items


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    products = make_products(rng)
    customers = make_customers(rng, products)
    orders, items = generate_orders(rng, customers, products)

    expected = compute_expected(products, customers, orders, items)
    EXPECTED_PATH.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")

    dirt_rng = random.Random(99)
    dirty_products, dirty_customers, dirty_orders, dirty_items = inject_dirt(
        dirt_rng, products, customers, orders, items
    )

    write_csv(RAW_DIR / "customers.csv", dirty_customers,
              ["customer_id", "name", "email", "country", "state", "city", "city_tier", "segment", "signup_date"])
    write_csv(RAW_DIR / "products.csv", dirty_products,
              ["product_id", "name", "category", "unit_price", "unit_cost", "launch_date", "active"])
    write_csv(RAW_DIR / "orders.csv", dirty_orders,
              ["order_id", "customer_id", "order_date", "status", "channel", "payment_method", "shipping_cost"])
    write_csv(RAW_DIR / "order_items.csv", dirty_items,
              ["item_id", "order_id", "product_id", "quantity", "unit_price", "discount_pct"])

    print(f"Wrote raw data to {RAW_DIR}")
    print(f"Expected snapshot: {EXPECTED_PATH}")
    print(f"Clean rows -> customers={expected['row_counts']['customers']}, "
          f"products={expected['row_counts']['products']}, "
          f"orders={expected['row_counts']['orders']}, "
          f"items={expected['row_counts']['order_items']}")
    print(f"Revenue (all time): ₹{expected['totals']['revenue']:,.2f}")
    print(f"Latest month {expected['latest_month']['month']}: ₹{expected['latest_month']['revenue']:,.2f} "
          f"({expected['latest_month']['orders']} orders)")
    print(f"Previous month {expected['previous_month']['month']}: ₹{expected['previous_month']['revenue']:,.2f} "
          f"({expected['previous_month']['orders']} orders)")


if __name__ == "__main__":
    main()
