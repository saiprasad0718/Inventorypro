import os
import uuid
from datetime import datetime, timezone, timedelta

from database import db
from auth import hash_password, verify_password

CENTRAL = "Central Store"


def day(offset=0):
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


INGREDIENTS = [
    {"id": "ing-chicken", "name": "Chicken", "category": "Meat", "unit": "kg", "rate": 220, "book_stock": 18.0, "dept_stocks": {"Main Kitchen": 4.5}, "physical_stock": 18.0},
    {"id": "ing-mutton", "name": "Mutton", "category": "Meat", "unit": "kg", "rate": 650, "book_stock": 9.5, "dept_stocks": {"Biryani Station": 2.0}, "physical_stock": 9.5},
    {"id": "ing-onion", "name": "Onion", "category": "Vegetables", "unit": "kg", "rate": 32, "book_stock": 22.5, "dept_stocks": {"Main Kitchen": 6.0}, "physical_stock": 20.0},
    {"id": "ing-tomato", "name": "Tomato", "category": "Vegetables", "unit": "kg", "rate": 28, "book_stock": 15.0, "dept_stocks": {"Main Kitchen": 4.0}, "physical_stock": 15.0},
    {"id": "ing-basmati-rice", "name": "Basmati Rice", "category": "Dry Goods", "unit": "kg", "rate": 120, "book_stock": 28.0, "dept_stocks": {"Biryani Station": 8.0}, "physical_stock": 28.0},
    {"id": "ing-yogurt", "name": "Yogurt", "category": "Dairy", "unit": "kg", "rate": 60, "book_stock": 6.0, "dept_stocks": {"Main Kitchen": 2.0}, "physical_stock": 6.0},
    {"id": "ing-butter", "name": "Butter", "category": "Dairy", "unit": "kg", "rate": 480, "book_stock": 3.5, "dept_stocks": {"Main Kitchen": 1.2}, "physical_stock": 3.5},
    {"id": "ing-milk", "name": "Milk", "category": "Dairy", "unit": "L", "rate": 55, "book_stock": 20.0, "dept_stocks": {}, "physical_stock": 20.0},
    {"id": "ing-oil", "name": "Sunflower Oil", "category": "Oil", "unit": "L", "rate": 210, "book_stock": 3.5, "dept_stocks": {"Main Kitchen": 1.5}, "physical_stock": 3.5},
    {"id": "ing-spice-mix", "name": "Biryani Spice Mix", "category": "Spices", "unit": "kg", "rate": 540, "book_stock": 8.0, "dept_stocks": {"Biryani Station": 1.5}, "physical_stock": 8.0},
    {"id": "ing-paneer", "name": "Paneer", "category": "Dairy", "unit": "kg", "rate": 340, "book_stock": 5.0, "dept_stocks": {"Curry Section": 1.5}, "physical_stock": 5.0},
]

DISHES = [
    {"id": "dish-chicken-biryani", "name": "Chicken Biryani", "category": "Biryani", "sell_price": 220,
     "items": [{"ingredient_id": "ing-chicken", "qty_per_portion": 0.18}, {"ingredient_id": "ing-basmati-rice", "qty_per_portion": 0.12}, {"ingredient_id": "ing-yogurt", "qty_per_portion": 0.03}, {"ingredient_id": "ing-spice-mix", "qty_per_portion": 0.012}, {"ingredient_id": "ing-oil", "qty_per_portion": 0.02}, {"ingredient_id": "ing-onion", "qty_per_portion": 0.05}]},
    {"id": "dish-mutton-biryani", "name": "Mutton Biryani", "category": "Biryani", "sell_price": 380,
     "items": [{"ingredient_id": "ing-mutton", "qty_per_portion": 0.15}, {"ingredient_id": "ing-basmati-rice", "qty_per_portion": 0.12}, {"ingredient_id": "ing-spice-mix", "qty_per_portion": 0.012}, {"ingredient_id": "ing-oil", "qty_per_portion": 0.02}, {"ingredient_id": "ing-onion", "qty_per_portion": 0.04}]},
    {"id": "dish-chicken-65", "name": "Chicken 65", "category": "Starters", "sell_price": 180,
     "items": [{"ingredient_id": "ing-chicken", "qty_per_portion": 0.15}, {"ingredient_id": "ing-yogurt", "qty_per_portion": 0.02}, {"ingredient_id": "ing-spice-mix", "qty_per_portion": 0.008}, {"ingredient_id": "ing-oil", "qty_per_portion": 0.03}]},
    {"id": "dish-chicken-fried-rice", "name": "Chicken Fried Rice", "category": "Rice & Noodles", "sell_price": 160,
     "items": [{"ingredient_id": "ing-chicken", "qty_per_portion": 0.10}, {"ingredient_id": "ing-basmati-rice", "qty_per_portion": 0.10}, {"ingredient_id": "ing-oil", "qty_per_portion": 0.02}, {"ingredient_id": "ing-onion", "qty_per_portion": 0.04}]},
    {"id": "dish-paneer-butter-masala", "name": "Paneer Butter Masala", "category": "Curries", "sell_price": 200,
     "items": [{"ingredient_id": "ing-paneer", "qty_per_portion": 0.12}, {"ingredient_id": "ing-butter", "qty_per_portion": 0.03}, {"ingredient_id": "ing-tomato", "qty_per_portion": 0.08}, {"ingredient_id": "ing-milk", "qty_per_portion": 0.05}]},
]

TODAY_SALES = {"dish-chicken-biryani": 90, "dish-mutton-biryani": 52, "dish-chicken-65": 32, "dish-chicken-fried-rice": 30, "dish-paneer-butter-masala": 17}
TODAY_PRODUCTION = [
    ("dish-chicken-biryani", 100, 2, "Lunch", "Chef Ravi", "Biryani Station"),
    ("dish-mutton-biryani", 60, 0, "Lunch", "Chef Suresh", "Biryani Station"),
    ("dish-chicken-65", 30, 0, "Dinner", "Chef Ravi", "Main Kitchen"),
    ("dish-chicken-fried-rice", 32, 0, "Dinner", "Chef Meena", "Main Kitchen"),
    ("dish-paneer-butter-masala", 24, 0, "Dinner", "Chef Meena", "Curry Section"),
]
TODAY_RECON = {
    "dish-chicken-biryani": (2, 1, 5),
    "dish-mutton-biryani": (0, 0, 8),
    "dish-chicken-65": (0, 0, 0),
    "dish-chicken-fried-rice": (0, 0, 0),
    "dish-paneer-butter-masala": (0, 0, 7),
}
TODAY_PURCHASES = [
    ("ing-chicken", 10, 220, "Fresh Farms", "INV-1042"),
    ("ing-basmati-rice", 25, 120, "Anna Traders", "INV-208"),
    ("ing-paneer", 5, 340, "Dairy Best", "INV-77"),
]
TODAY_ISSUES = [
    ("ing-chicken", 6, "Main Kitchen", "Chef Ravi"),
    ("ing-basmati-rice", 8, "Biryani Station", "Chef Suresh"),
    ("ing-spice-mix", 1.5, "Biryani Station", "Chef Suresh"),
    ("ing-onion", 6, "Main Kitchen", "Chef Ravi"),
    ("ing-butter", 1.2, "Main Kitchen", "Chef Meena"),
    ("ing-yogurt", 2, "Main Kitchen", "Chef Ravi"),
    ("ing-paneer", 1.5, "Curry Section", "Chef Meena"),
    ("ing-oil", 1.5, "Main Kitchen", "Chef Ravi"),
    ("ing-tomato", 4, "Main Kitchen", "Chef Meena"),
    ("ing-mutton", 2, "Biryani Station", "Chef Suresh"),
]

PAST_MULTS = {-1: 0.95, -2: 1.08, -3: 0.82, -4: 1.12, -5: 0.88, -6: 0.76}
EXTRA = {"dish-chicken-biryani": 9, "dish-mutton-biryani": 7, "dish-chicken-65": 4, "dish-chicken-fried-rice": 4, "dish-paneer-butter-masala": 6}

APP_COLLECTIONS = ["ingredients", "dishes", "purchases", "issues", "production", "consumption", "wastage", "pos_sales", "reconciliation", "settings", "outlets"]
DEFAULT_OUTLET = "Main Branch"


async def seed_users():
    defaults = [
        (os.environ.get("OWNER_SEED_EMAIL", "owner@inventorypro.in"), os.environ.get("OWNER_SEED_PASSWORD", "InventoryPro@2026"), "Sai Prasad", "owner"),
        ("manager@inventorypro.in", "Manager@123", "Ravi Kumar", "manager"),
        ("chef@inventorypro.in", "Chef@123", "Chef Ravi", "chef"),
    ]
    for email, password, name, role in defaults:
        email = email.lower()
        existing = await db.users.find_one({"email": email})
        if not existing:
            await db.users.insert_one({
                "id": str(uuid.uuid4()), "email": email, "name": name, "role": role,
                "password_hash": hash_password(password),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        elif not verify_password(password, existing["password_hash"]):
            await db.users.update_one({"email": email}, {"$set": {"password_hash": hash_password(password)}})


def _portion_cost(dish, rates):
    return round(sum(i["qty_per_portion"] * rates.get(i["ingredient_id"], 0) for i in dish["items"]), 2)


def _make_batch(date_str, seq, dish, qty, wastage_qty, shift, chef, dept, ings):
    batch_no = f"B-{date_str.replace('-', '')[2:]}-{seq:03d}"
    ts = f"{date_str}T{9 + seq:02d}:30:00+00:00"
    batch = {"id": str(uuid.uuid4()), "date": date_str, "ts": ts, "batch_no": batch_no,
             "dish_id": dish["id"], "dish_name": dish["name"], "qty": qty, "wastage_qty": wastage_qty,
             "shift": shift, "chef": chef, "dept": dept}
    cons = []
    for it in dish["items"]:
        ing = ings[it["ingredient_id"]]
        need = round(it["qty_per_portion"] * qty, 3)
        cons.append({"id": str(uuid.uuid4()), "date": date_str, "ts": ts, "batch_no": batch_no,
                     "dish_id": dish["id"], "dish_name": dish["name"], "ingredient_id": ing["id"],
                     "ingredient_name": ing["name"], "formula": f"{it['qty_per_portion']} × {qty}",
                     "qty": need, "unit": ing["unit"]})
    return batch, cons


async def seed_demo_data(force=False):
    saved_photos = {}
    if force:
        async for d in db.dishes.find({"image_path": {"$exists": True}}, {"_id": 0, "id": 1, "image_path": 1}):
            saved_photos[d["id"]] = d["image_path"]
        for c in APP_COLLECTIONS:
            await db[c].delete_many({})
    elif await db.ingredients.count_documents({}):
        return

    ings = {i["id"]: i for i in INGREDIENTS}
    dish_map = {d["id"]: d for d in DISHES}
    rates = {i["id"]: i["rate"] for i in INGREDIENTS}

    for i in INGREDIENTS:
        i["key"] = i["id"]
        i["outlet"] = DEFAULT_OUTLET
    await db.ingredients.insert_many([dict(i) for i in INGREDIENTS])
    await db.dishes.insert_many([dict(d) for d in DISHES])
    for dish_id, path in saved_photos.items():
        await db.dishes.update_one({"id": dish_id}, {"$set": {"image_path": path}})
    await db.outlets.insert_one({"id": str(uuid.uuid4()), "name": DEFAULT_OUTLET, "created_at": datetime.now(timezone.utc).isoformat()})

    batches, consumption, wastage, pos_sales, recon, purchases, issues = [], [], [], [], [], [], []

    for offset, mult in PAST_MULTS.items():
        d = day(offset)
        for seq, dish in enumerate(DISHES, start=1):
            sold = max(1, round(TODAY_SALES[dish["id"]] * mult))
            produced = sold + EXTRA[dish["id"]]
            batch, cons = _make_batch(d, seq, dish, produced, 0, "Lunch" if seq % 2 else "Dinner", "Chef Ravi", "Main Kitchen", ings)
            batches.append(batch)
            consumption.extend(cons)
            pos_sales.append({"id": str(uuid.uuid4()), "date": d, "ts": f"{d}T22:00:00+00:00", "dish_id": dish["id"], "dish_name": dish["name"], "qty": sold})
            wqty = (seq % 3)
            comp, staff = 1, 1
            closing = max(0, produced - sold - comp - staff - wqty)
            recon.append({"id": str(uuid.uuid4()), "date": d, "dish_id": dish["id"], "complimentary": comp, "staff_meal": staff, "closing_stock": closing})
            if wqty:
                wastage.append({"id": str(uuid.uuid4()), "date": d, "ts": f"{d}T21:00:00+00:00", "item_type": "dish",
                                "item_id": dish["id"], "item_name": dish["name"], "qty": wqty, "reason": "Overproduction",
                                "dept": "Main Kitchen", "recorded_by": "Chef Ravi", "remarks": "",
                                "value": round(wqty * _portion_cost(dish, rates), 2)})
        if abs(offset) % 2 == 0:
            purchases.append({"id": str(uuid.uuid4()), "date": d, "ts": f"{d}T08:00:00+00:00", "ingredient_id": "ing-chicken", "ingredient_name": "Chicken", "qty": 12, "rate": 218, "amount": 12 * 218, "supplier": "Fresh Farms", "invoice_no": f"INV-{1000 + abs(offset)}"})
            purchases.append({"id": str(uuid.uuid4()), "date": d, "ts": f"{d}T08:10:00+00:00", "ingredient_id": "ing-basmati-rice", "ingredient_name": "Basmati Rice", "qty": 20, "rate": 118, "amount": 20 * 118, "supplier": "Anna Traders", "invoice_no": f"INV-{200 + abs(offset)}"})
        else:
            purchases.append({"id": str(uuid.uuid4()), "date": d, "ts": f"{d}T08:00:00+00:00", "ingredient_id": "ing-onion", "ingredient_name": "Onion", "qty": 15, "rate": 30, "amount": 450, "supplier": "Market Fresh", "invoice_no": f"INV-{300 + abs(offset)}"})

    t = day()
    for seq, (dish_id, qty, wqty, shift, chef, dept) in enumerate(TODAY_PRODUCTION, start=1):
        dish = dish_map[dish_id]
        batch, cons = _make_batch(t, seq, dish, qty, 0, shift, chef, dept, ings)
        batches.append(batch)
        consumption.extend(cons)
    for dish_id, sold in TODAY_SALES.items():
        pos_sales.append({"id": str(uuid.uuid4()), "date": t, "ts": f"{t}T22:00:00+00:00", "dish_id": dish_id, "dish_name": dish_map[dish_id]["name"], "qty": sold})
    for dish_id, (comp, staff, closing) in TODAY_RECON.items():
        recon.append({"id": str(uuid.uuid4()), "date": t, "dish_id": dish_id, "complimentary": comp, "staff_meal": staff, "closing_stock": closing})
    cb = dish_map["dish-chicken-biryani"]
    wastage.append({"id": str(uuid.uuid4()), "date": t, "ts": f"{t}T15:00:00+00:00", "item_type": "dish",
                    "item_id": cb["id"], "item_name": cb["name"], "qty": 2, "reason": "Overproduction",
                    "dept": "Biryani Station", "recorded_by": "Chef Ravi", "remarks": "Lunch excess",
                    "value": round(2 * _portion_cost(cb, rates), 2)})
    wastage.append({"id": str(uuid.uuid4()), "date": t, "ts": f"{t}T10:00:00+00:00", "item_type": "ingredient",
                    "item_id": "ing-onion", "item_name": "Onion", "qty": 0.4, "reason": "Spoilage",
                    "dept": CENTRAL, "recorded_by": "Ravi Kumar", "remarks": "Soft bulbs",
                    "value": round(0.4 * rates["ing-onion"], 2)})
    for ing_id, qty, rate, supplier, inv in TODAY_PURCHASES:
        purchases.append({"id": str(uuid.uuid4()), "date": t, "ts": f"{t}T08:00:00+00:00", "ingredient_id": ing_id,
                          "ingredient_name": ings[ing_id]["name"], "qty": qty, "rate": rate,
                          "amount": round(qty * rate, 2), "supplier": supplier, "invoice_no": inv})
    for ing_id, qty, dept, req in TODAY_ISSUES:
        issues.append({"id": str(uuid.uuid4()), "date": t, "ts": f"{t}T09:00:00+00:00", "ingredient_id": ing_id,
                       "ingredient_name": ings[ing_id]["name"], "qty": qty, "dept": dept, "requested_by": req})

    for doc_list in (batches, consumption, wastage, pos_sales, recon, purchases, issues):
        for doc in doc_list:
            doc["outlet"] = DEFAULT_OUTLET
    await db.production.insert_many(batches)
    await db.consumption.insert_many(consumption)
    await db.wastage.insert_many(wastage)
    await db.pos_sales.insert_many(pos_sales)
    await db.reconciliation.insert_many(recon)
    await db.purchases.insert_many(purchases)
    await db.issues.insert_many(issues)

    await db.settings.update_one(
        {"id": "settings"},
        {"$set": {"id": "settings", "restaurant_name": "Spice Route Kitchen", "pos_system": "Petpooja (manual entry)", "currency": "INR"}},
        upsert=True,
    )


async def seed_outlet_demo(outlet):
    """Load a full demo week into one outlet, replacing its existing transactions."""
    for c in ("purchases", "issues", "production", "consumption", "wastage", "pos_sales", "reconciliation"):
        await db[c].delete_many({"outlet": outlet})
    ing_list = await db.ingredients.find({"outlet": outlet}, {"_id": 0}).to_list(200)
    ings = {i.get("key", i["id"]): i for i in ing_list}
    rates = {}
    for i in ing_list:
        rates[i["id"]] = i["rate"]
        rates[i.get("key", i["id"])] = i["rate"]
    dish_docs = await db.dishes.find({}, {"_id": 0}).to_list(100)
    dish_map = {d["id"]: d for d in dish_docs}
    known = [d for d in dish_docs if d["id"] in TODAY_SALES and all(it["ingredient_id"] in ings for it in d["items"])]
    if not known:
        return

    batches, consumption, wastage, pos_sales, recon, purchases, issues = [], [], [], [], [], [], []

    for offset, mult in PAST_MULTS.items():
        d = day(offset)
        for seq, dish in enumerate(known, start=1):
            sold = max(1, round(TODAY_SALES[dish["id"]] * mult))
            produced = sold + EXTRA.get(dish["id"], 4)
            batch, cons = _make_batch(d, seq, dish, produced, 0, "Lunch" if seq % 2 else "Dinner", "Chef Ravi", "Main Kitchen", ings)
            batches.append(batch)
            consumption.extend(cons)
            pos_sales.append({"id": str(uuid.uuid4()), "date": d, "ts": f"{d}T22:00:00+00:00",
                              "dish_id": dish["id"], "dish_name": dish["name"], "qty": sold})
            wqty = seq % 3
            comp, staff = 1, 1
            closing = max(0, produced - sold - comp - staff - wqty)
            recon.append({"id": str(uuid.uuid4()), "date": d, "dish_id": dish["id"],
                          "complimentary": comp, "staff_meal": staff, "closing_stock": closing})
            if wqty:
                wastage.append({"id": str(uuid.uuid4()), "date": d, "ts": f"{d}T21:00:00+00:00", "item_type": "dish",
                                "item_id": dish["id"], "item_name": dish["name"], "qty": wqty, "reason": "Overproduction",
                                "dept": "Main Kitchen", "recorded_by": "Chef Ravi", "remarks": "",
                                "value": round(wqty * _portion_cost(dish, rates), 2)})
        pk = "ing-chicken" if abs(offset) % 2 == 0 else "ing-onion"
        if ings.get(pk):
            ing = ings[pk]
            purchases.append({"id": str(uuid.uuid4()), "date": d, "ts": f"{d}T08:00:00+00:00", "ingredient_id": ing["id"],
                              "ingredient_name": ing["name"], "qty": 12, "rate": ing["rate"],
                              "amount": round(12 * ing["rate"], 2), "supplier": "Fresh Farms", "invoice_no": f"INV-{1000 + abs(offset)}"})

    t = day()
    for seq, (dish_id, qty, _w, shift, chef, dept) in enumerate(TODAY_PRODUCTION, start=1):
        dish = dish_map.get(dish_id)
        if not dish or dish not in known:
            continue
        batch, cons = _make_batch(t, seq, dish, qty, 0, shift, chef, dept, ings)
        batches.append(batch)
        consumption.extend(cons)
    for dish_id, sold in TODAY_SALES.items():
        dish = dish_map.get(dish_id)
        if not dish or dish not in known:
            continue
        pos_sales.append({"id": str(uuid.uuid4()), "date": t, "ts": f"{t}T22:00:00+00:00",
                          "dish_id": dish_id, "dish_name": dish["name"], "qty": sold})
        comp, staff, closing = TODAY_RECON.get(dish_id, (0, 0, 0))
        recon.append({"id": str(uuid.uuid4()), "date": t, "dish_id": dish_id,
                      "complimentary": comp, "staff_meal": staff, "closing_stock": closing})
    cb = dish_map.get("dish-chicken-biryani")
    if cb and cb in known:
        wastage.append({"id": str(uuid.uuid4()), "date": t, "ts": f"{t}T15:00:00+00:00", "item_type": "dish",
                        "item_id": cb["id"], "item_name": cb["name"], "qty": 2, "reason": "Overproduction",
                        "dept": "Biryani Station", "recorded_by": "Chef Ravi", "remarks": "Lunch excess",
                        "value": round(2 * _portion_cost(cb, rates), 2)})
    for ing_key, qty, dept, req in TODAY_ISSUES:
        ing = ings.get(ing_key)
        if ing:
            issues.append({"id": str(uuid.uuid4()), "date": t, "ts": f"{t}T09:00:00+00:00", "ingredient_id": ing["id"],
                           "ingredient_name": ing["name"], "qty": qty, "dept": dept, "requested_by": req})
    for ing_key, qty, supplier, inv in (("ing-chicken", 10, "Fresh Farms", "INV-T1"), ("ing-basmati-rice", 25, "Anna Traders", "INV-T2")):
        ing = ings.get(ing_key)
        if ing:
            purchases.append({"id": str(uuid.uuid4()), "date": t, "ts": f"{t}T08:00:00+00:00", "ingredient_id": ing["id"],
                              "ingredient_name": ing["name"], "qty": qty, "rate": ing["rate"],
                              "amount": round(qty * ing["rate"], 2), "supplier": supplier, "invoice_no": inv})

    for doc_list in (batches, consumption, wastage, pos_sales, recon, purchases, issues):
        for doc in doc_list:
            doc["outlet"] = outlet
    for coll, docs in (("production", batches), ("consumption", consumption), ("wastage", wastage),
                       ("pos_sales", pos_sales), ("reconciliation", recon), ("purchases", purchases), ("issues", issues)):
        if docs:
            await db[coll].insert_many(docs)


async def seed_all():
    await db.users.create_index("email", unique=True)
    await db.login_attempts.create_index("identifier")
    await seed_users()
    await seed_demo_data()
