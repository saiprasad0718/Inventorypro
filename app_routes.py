import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import jwt
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from database import db
from auth import get_current_user, require_roles
from storage import get_object, put_object

app_router = APIRouter(tags=["app"])

STAFF = ("owner", "manager")
CENTRAL = "Central Store"
DEFAULT_OUTLET = "Main Branch"


def day(offset=0):
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


def now_ts():
    return datetime.now(timezone.utc).isoformat()


def oid():
    return str(uuid.uuid4())


def get_outlet(request: Request) -> str:
    return (request.headers.get("X-Outlet") or DEFAULT_OUTLET).strip() or DEFAULT_OUTLET


async def all_docs(coll, query=None, outlet=None):
    q = dict(query or {})
    if outlet is not None:
        q["outlet"] = outlet
    return await db[coll].find(q, {"_id": 0}).to_list(2000)


def rate_map(ings):
    rates = {}
    for i in ings:
        rates[i["id"]] = i["rate"]
        rates[i.get("key", i["id"])] = i["rate"]
    return rates


def portion_cost(dish, rates):
    return round(sum(i["qty_per_portion"] * rates.get(i["ingredient_id"], 0) for i in dish["items"]), 2)


# ---------- Outlets ----------

@app_router.get("/outlets")
async def list_outlets(user=Depends(get_current_user)):
    docs = await db.outlets.find({}, {"_id": 0}).to_list(50)
    return docs or [{"id": "default", "name": DEFAULT_OUTLET}]


class OutletIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)


@app_router.post("/outlets")
async def create_outlet(input: OutletIn, request: Request, user=Depends(require_roles("owner"))):
    name = input.name.strip()
    if await db.outlets.find_one({"name": name}):
        raise HTTPException(status_code=409, detail="An outlet with this name already exists")
    await db.outlets.insert_one({"id": oid(), "name": name, "created_at": now_ts()})
    src_outlet = get_outlet(request)
    for ing in await all_docs("ingredients", outlet=src_outlet):
        clone = {k: v for k, v in ing.items() if k not in ("id", "variance", "stock_value", "dept_total")}
        clone["id"] = oid()
        clone["outlet"] = name
        clone["key"] = ing.get("key", ing["id"])
        await db.ingredients.insert_one({**clone})
    return {"status": "created", "name": name, "catalog_cloned_from": src_outlet}


@app_router.post("/outlets/seed-demo")
async def seed_outlet_demo_day(request: Request, user=Depends(require_roles("owner"))):
    outlet = get_outlet(request)
    from seed import seed_outlet_demo
    await seed_outlet_demo(outlet)
    return {"status": f"demo week loaded for {outlet}"}


@app_router.get("/outlets/compare")
async def compare_outlets(user=Depends(require_roles("owner"))):
    outlets = await db.outlets.find({}, {"_id": 0}).to_list(50)
    dish_map = {d["id"]: d for d in await all_docs("dishes")}
    t = day()
    out = []
    for o in outlets or [{"name": DEFAULT_OUTLET}]:
        name = o["name"]
        ings = await all_docs("ingredients", outlet=name)
        rates = rate_map(ings)
        pos_today = await all_docs("pos_sales", {"date": t}, name)
        sales = sum(s["qty"] * dish_map[s["dish_id"]]["sell_price"] for s in pos_today if s["dish_id"] in dish_map)
        portions = sum(s["qty"] for s in pos_today)
        cons = await all_docs("consumption", {"date": t}, name)
        cost = sum(c["qty"] * rates.get(c["ingredient_id"], 0) for c in cons)
        wast = await all_docs("wastage", {"date": t}, name)
        wv = sum(w["value"] for w in wast)
        batches = await all_docs("production", {"date": t}, name)
        stock = sum(i["book_stock"] * i["rate"] for i in ings)
        seven = []
        for off in range(-6, 1):
            d = day(off)
            posd = await all_docs("pos_sales", {"date": d}, name)
            seven.append({"date": datetime.fromisoformat(d).strftime("%d %b"),
                          "sales": round(sum(s["qty"] * dish_map[s["dish_id"]]["sell_price"] for s in posd if s["dish_id"] in dish_map))})
        out.append({"name": name, "sales": round(sales), "portions_sold": portions,
                    "food_cost_pct": round(cost / sales * 100, 1) if sales else 0,
                    "wastage_value": round(wv), "produced": sum(b["qty"] for b in batches),
                    "stock_value": round(stock), "seven_day": seven})
    return out


# ---------- Ingredients / Inventory ----------

class IngredientIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    category: str = Field(min_length=2, max_length=40)
    unit: str = Field(default="kg", max_length=10)
    rate: float = Field(gt=0)
    opening_stock: float = Field(default=0, ge=0)


@app_router.get("/ingredients")
async def list_ingredients(request: Request, user=Depends(get_current_user)):
    items = await all_docs("ingredients", outlet=get_outlet(request))
    for it in items:
        it["dept_total"] = round(sum((it.get("dept_stocks") or {}).values()), 3)
        it["variance"] = round(it.get("physical_stock", 0) - it["book_stock"], 3)
        it["stock_value"] = round(it["book_stock"] * it["rate"], 2)
    return items


@app_router.post("/ingredients")
async def create_ingredient(input: IngredientIn, request: Request, user=Depends(require_roles(*STAFF))):
    doc = {"id": oid(), "key": f"ing-{oid()[:8]}", "outlet": get_outlet(request),
           "name": input.name, "category": input.category, "unit": input.unit,
           "rate": input.rate, "book_stock": input.opening_stock, "dept_stocks": {},
           "physical_stock": input.opening_stock}
    await db.ingredients.insert_one({**doc})
    return doc


class PhysicalIn(BaseModel):
    physical_stock: float = Field(ge=0)


@app_router.put("/ingredients/{ing_id}/physical")
async def set_physical(ing_id: str, input: PhysicalIn, request: Request, user=Depends(require_roles(*STAFF))):
    res = await db.ingredients.update_one({"id": ing_id, "outlet": get_outlet(request)}, {"$set": {"physical_stock": input.physical_stock}})
    if not res.matched_count:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    return {"status": "updated"}


# ---------- Purchases (Receiving) ----------

class PurchaseIn(BaseModel):
    ingredient_id: str
    qty: float = Field(gt=0)
    rate: float = Field(gt=0)
    supplier: str = Field(default="", max_length=120)
    invoice_no: str = Field(default="", max_length=60)


@app_router.get("/purchases")
async def list_purchases(request: Request, user=Depends(require_roles(*STAFF))):
    return await db.purchases.find({"outlet": get_outlet(request)}, {"_id": 0}).sort("ts", -1).to_list(50)


@app_router.post("/purchases")
async def create_purchase(input: PurchaseIn, request: Request, user=Depends(require_roles(*STAFF))):
    outlet = get_outlet(request)
    ing = await db.ingredients.find_one({"id": input.ingredient_id, "outlet": outlet}, {"_id": 0})
    if not ing:
        raise HTTPException(status_code=404, detail="Ingredient not found in this outlet")
    await db.ingredients.update_one({"id": ing["id"]}, {"$inc": {"book_stock": input.qty}, "$set": {"rate": input.rate}})
    doc = {"id": oid(), "outlet": outlet, "date": day(), "ts": now_ts(), "ingredient_id": ing["id"], "ingredient_name": ing["name"],
           "qty": input.qty, "rate": input.rate, "amount": round(input.qty * input.rate, 2),
           "supplier": input.supplier, "invoice_no": input.invoice_no}
    await db.purchases.insert_one({**doc})
    return doc


# ---------- Store Issue ----------

class IssueIn(BaseModel):
    ingredient_id: str
    qty: float = Field(gt=0)
    dept: str = Field(min_length=2, max_length=60)
    requested_by: str = Field(default="", max_length=80)


@app_router.get("/issues")
async def list_issues(request: Request, user=Depends(require_roles(*STAFF))):
    return await db.issues.find({"outlet": get_outlet(request)}, {"_id": 0}).sort("ts", -1).to_list(50)


@app_router.post("/issues")
async def create_issue(input: IssueIn, request: Request, user=Depends(require_roles(*STAFF))):
    outlet = get_outlet(request)
    ing = await db.ingredients.find_one({"id": input.ingredient_id, "outlet": outlet}, {"_id": 0})
    if not ing:
        raise HTTPException(status_code=404, detail="Ingredient not found in this outlet")
    if ing["book_stock"] < input.qty:
        raise HTTPException(status_code=400, detail=f"Only {round(ing['book_stock'], 2)} {ing['unit']} available in Central Store")
    await db.ingredients.update_one({"id": ing["id"]}, {"$inc": {"book_stock": -input.qty, f"dept_stocks.{input.dept}": input.qty}})
    doc = {"id": oid(), "outlet": outlet, "date": day(), "ts": now_ts(), "ingredient_id": ing["id"], "ingredient_name": ing["name"],
           "qty": input.qty, "dept": input.dept, "requested_by": input.requested_by or user["name"]}
    await db.issues.insert_one({**doc})
    return doc


# ---------- Recipes / Dishes ----------

class BomItem(BaseModel):
    ingredient_id: str
    qty_per_portion: float = Field(gt=0)


class DishIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    category: str = Field(min_length=2, max_length=40)
    sell_price: float = Field(gt=0)
    items: List[BomItem] = Field(min_length=1)


@app_router.get("/dishes")
async def list_dishes(request: Request, user=Depends(get_current_user)):
    dishes = await all_docs("dishes")
    ings = await all_docs("ingredients", outlet=get_outlet(request))
    rates = rate_map(ings)
    ing_map = {i.get("key", i["id"]): i for i in ings}
    for d in dishes:
        d["cost_per_portion"] = portion_cost(d, rates)
        d["food_cost_pct"] = round(d["cost_per_portion"] / d["sell_price"] * 100, 1) if d["sell_price"] else 0
        d["image_url"] = f"/api/files/{d['image_path']}" if d.get("image_path") else None
        for it in d["items"]:
            ing = ing_map.get(it["ingredient_id"], {})
            it["ingredient_name"] = ing.get("name", "Not in this outlet")
            it["unit"] = ing.get("unit", "")
    return dishes


@app_router.post("/dishes")
async def create_dish(input: DishIn, request: Request, user=Depends(require_roles(*STAFF))):
    id_to_key = {i["id"]: i.get("key", i["id"]) for i in await all_docs("ingredients", outlet=get_outlet(request))}
    items = []
    for i in input.items:
        key = id_to_key.get(i.ingredient_id, i.ingredient_id)
        items.append({"ingredient_id": key, "qty_per_portion": i.qty_per_portion})
    doc = {"id": oid(), "name": input.name, "category": input.category, "sell_price": input.sell_price, "items": items}
    await db.dishes.insert_one({**doc})
    return doc


MAX_PHOTO_BYTES = 8 * 1024 * 1024


@app_router.post("/dishes/{dish_id}/photo")
async def upload_dish_photo(dish_id: str, file: UploadFile = File(...), user=Depends(require_roles(*STAFF))):
    dish = await db.dishes.find_one({"id": dish_id}, {"_id": 0})
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")
    data = await file.read()
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="Photo must be under 8 MB")
    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"
    path = f"inventorypro/dishes/{dish_id}/{oid()}.{ext}"
    result = await put_object(path, data, file.content_type)
    stored = result["path"]
    await db.files.insert_one({"id": oid(), "storage_path": stored, "original_filename": file.filename,
                               "content_type": file.content_type, "size": result.get("size", len(data)),
                               "is_deleted": False, "created_at": now_ts(), "uploaded_by": user["id"]})
    await db.dishes.update_one({"id": dish_id}, {"$set": {"image_path": stored}})
    return {"image_url": f"/api/files/{stored}"}


@app_router.get("/files/{path:path}")
async def download_file(path: str, request: Request, auth: Optional[str] = Query(None)):
    token = auth
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not await db.users.find_one({"id": payload.get("sub")}):
        raise HTTPException(status_code=401, detail="User not found")
    record = await db.files.find_one({"storage_path": path, "is_deleted": False})
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        data, content_type = await get_object(path)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found in storage")
    return Response(content=data, media_type=record.get("content_type", content_type))


# ---------- Production ----------

class ProductionIn(BaseModel):
    dish_id: str
    qty: int = Field(gt=0)
    wastage_qty: int = Field(default=0, ge=0)
    shift: str = Field(default="Lunch", max_length=20)
    dept: str = Field(default="Main Kitchen", max_length=60)


@app_router.get("/production")
async def list_production(request: Request, date: Optional[str] = None, user=Depends(get_current_user)):
    return await db.production.find({"date": date or day(), "outlet": get_outlet(request)}, {"_id": 0}).sort("ts", -1).to_list(100)


@app_router.post("/production")
async def create_batch(input: ProductionIn, request: Request, user=Depends(get_current_user)):
    outlet = get_outlet(request)
    dish = await db.dishes.find_one({"id": input.dish_id}, {"_id": 0})
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")
    ings = {i.get("key", i["id"]): i for i in await all_docs("ingredients", outlet=outlet)}
    needs = [(it, round(it["qty_per_portion"] * input.qty, 3)) for it in dish["items"]]
    for it, need in needs:
        ing = ings.get(it["ingredient_id"])
        if not ing:
            raise HTTPException(status_code=400, detail="A recipe ingredient is missing in this outlet's catalog")
        have = (ing.get("dept_stocks") or {}).get(input.dept, 0)
        if have < need:
            raise HTTPException(status_code=400, detail=f"Insufficient {ing['name']} in {input.dept}: need {need} {ing['unit']}, have {round(have, 2)}")
    t = day()
    count = await db.production.count_documents({"date": t, "outlet": outlet})
    batch_no = f"B-{t.replace('-', '')[2:]}-{count + 1:03d}"
    ts = now_ts()
    for it, need in needs:
        ing = ings[it["ingredient_id"]]
        await db.ingredients.update_one({"id": ing["id"]}, {"$inc": {f"dept_stocks.{input.dept}": -need}})
        await db.consumption.insert_one({"id": oid(), "outlet": outlet, "date": t, "ts": ts, "batch_no": batch_no,
                                         "dish_id": dish["id"], "dish_name": dish["name"],
                                         "ingredient_id": ing["id"], "ingredient_name": ing["name"],
                                         "formula": f"{it['qty_per_portion']} × {input.qty}", "qty": need, "unit": ing["unit"]})
    batch = {"id": oid(), "outlet": outlet, "date": t, "ts": ts, "batch_no": batch_no, "dish_id": dish["id"], "dish_name": dish["name"],
             "qty": input.qty, "wastage_qty": input.wastage_qty, "shift": input.shift, "chef": user["name"], "dept": input.dept}
    await db.production.insert_one({**batch})
    if input.wastage_qty:
        await db.wastage.insert_one({"id": oid(), "outlet": outlet, "date": t, "ts": ts, "item_type": "dish", "item_id": dish["id"],
                                     "item_name": dish["name"], "qty": input.wastage_qty, "reason": "Overproduction",
                                     "dept": input.dept, "recorded_by": user["name"], "remarks": f"Batch {batch_no}",
                                     "value": round(input.wastage_qty * portion_cost(dish, rate_map(list(ings.values()))), 2)})
    return batch


# ---------- Consumption ----------

@app_router.get("/consumption")
async def list_consumption(request: Request, date: Optional[str] = None, user=Depends(get_current_user)):
    return await db.consumption.find({"date": date or day(), "outlet": get_outlet(request)}, {"_id": 0}).sort("ts", -1).to_list(300)


# ---------- Wastage ----------

class WastageIn(BaseModel):
    item_type: str = Field(pattern="^(ingredient|dish)$")
    item_id: str
    qty: float = Field(gt=0)
    reason: str = Field(min_length=2, max_length=60)
    dept: str = Field(default=CENTRAL, max_length=60)
    remarks: str = Field(default="", max_length=300)


@app_router.get("/wastage")
async def list_wastage(request: Request, date: Optional[str] = None, user=Depends(get_current_user)):
    return await db.wastage.find({"date": date or day(), "outlet": get_outlet(request)}, {"_id": 0}).sort("ts", -1).to_list(200)


@app_router.post("/wastage")
async def create_wastage(input: WastageIn, request: Request, user=Depends(get_current_user)):
    outlet = get_outlet(request)
    if input.item_type == "ingredient":
        ing = await db.ingredients.find_one({"id": input.item_id, "outlet": outlet}, {"_id": 0})
        if not ing:
            raise HTTPException(status_code=404, detail="Ingredient not found in this outlet")
        if input.dept == CENTRAL:
            if ing["book_stock"] < input.qty:
                raise HTTPException(status_code=400, detail=f"Only {round(ing['book_stock'], 2)} {ing['unit']} in Central Store")
            await db.ingredients.update_one({"id": ing["id"]}, {"$inc": {"book_stock": -input.qty}})
        else:
            have = (ing.get("dept_stocks") or {}).get(input.dept, 0)
            if have < input.qty:
                raise HTTPException(status_code=400, detail=f"Only {round(have, 2)} {ing['unit']} in {input.dept}")
            await db.ingredients.update_one({"id": ing["id"]}, {"$inc": {f"dept_stocks.{input.dept}": -input.qty}})
        name, value = ing["name"], round(input.qty * ing["rate"], 2)
    else:
        dish = await db.dishes.find_one({"id": input.item_id}, {"_id": 0})
        if not dish:
            raise HTTPException(status_code=404, detail="Dish not found")
        rates = rate_map(await all_docs("ingredients", outlet=outlet))
        name, value = dish["name"], round(input.qty * portion_cost(dish, rates), 2)
    doc = {"id": oid(), "outlet": outlet, "date": day(), "ts": now_ts(), "item_type": input.item_type, "item_id": input.item_id,
           "item_name": name, "qty": input.qty, "reason": input.reason, "dept": input.dept,
           "recorded_by": user["name"], "remarks": input.remarks, "value": value}
    await db.wastage.insert_one({**doc})
    return doc


# ---------- POS Sales & Reconciliation ----------

class PosSaleIn(BaseModel):
    dish_id: str
    qty: int = Field(ge=0)
    date: Optional[str] = None


@app_router.post("/pos-sales")
async def set_pos_sale(input: PosSaleIn, request: Request, user=Depends(require_roles(*STAFF))):
    d = input.date or day()
    outlet = get_outlet(request)
    dish = await db.dishes.find_one({"id": input.dish_id}, {"_id": 0})
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")
    existing = await db.pos_sales.find_one({"date": d, "dish_id": input.dish_id, "outlet": outlet})
    await db.pos_sales.update_one({"date": d, "dish_id": input.dish_id, "outlet": outlet},
                                  {"$set": {"dish_name": dish["name"], "qty": input.qty, "outlet": outlet,
                                            "id": (existing or {}).get("id", oid())}},
                                  upsert=True)
    return {"status": "saved"}


class ReconIn(BaseModel):
    dish_id: str
    date: Optional[str] = None
    complimentary: int = Field(default=0, ge=0)
    staff_meal: int = Field(default=0, ge=0)
    closing_stock: int = Field(default=0, ge=0)


@app_router.post("/reconciliation")
async def set_reconciliation(input: ReconIn, request: Request, user=Depends(require_roles(*STAFF))):
    d = input.date or day()
    outlet = get_outlet(request)
    existing = await db.reconciliation.find_one({"date": d, "dish_id": input.dish_id, "outlet": outlet})
    await db.reconciliation.update_one(
        {"date": d, "dish_id": input.dish_id, "outlet": outlet},
        {"$set": {"complimentary": input.complimentary, "staff_meal": input.staff_meal,
                  "closing_stock": input.closing_stock, "outlet": outlet,
                  "id": (existing or {}).get("id", oid())}},
        upsert=True)
    return {"status": "saved"}


async def build_reconciliation(date_str, outlet):
    dishes = await all_docs("dishes")
    batches = await all_docs("production", {"date": date_str}, outlet)
    sales = await all_docs("pos_sales", {"date": date_str}, outlet)
    adj = await all_docs("reconciliation", {"date": date_str}, outlet)
    wast = await all_docs("wastage", {"date": date_str, "item_type": "dish"}, outlet)
    out = []
    for d in dishes:
        produced = sum(b["qty"] for b in batches if b["dish_id"] == d["id"])
        sold = sum(s["qty"] for s in sales if s["dish_id"] == d["id"])
        a = next((x for x in adj if x["dish_id"] == d["id"]), {})
        dw = sum(w["qty"] for w in wast if w["item_id"] == d["id"])
        comp, staff, closing = a.get("complimentary", 0), a.get("staff_meal", 0), a.get("closing_stock", 0)
        accounted = sold + comp + staff + closing + dw
        unexplained = produced - accounted
        if produced == 0 and sold == 0:
            status = "idle"
        elif unexplained == 0:
            status = "reconciled"
        elif unexplained < 0:
            status = "over-sold"
        else:
            status = "unexplained"
        out.append({"dish_id": d["id"], "dish_name": d["name"], "produced": produced, "sold": sold,
                    "complimentary": comp, "staff_meal": staff, "wastage": dw, "closing_stock": closing,
                    "accounted": accounted, "unexplained": unexplained, "status": status,
                    "image_url": f"/api/files/{d['image_path']}" if d.get("image_path") else None})
    return out


@app_router.get("/reconciliation")
async def get_reconciliation(request: Request, date: Optional[str] = None, user=Depends(get_current_user)):
    return await build_reconciliation(date or day(), get_outlet(request))


# ---------- Variance ----------

@app_router.get("/variance")
async def get_variance(request: Request, user=Depends(require_roles(*STAFF))):
    outlet = get_outlet(request)
    ings = await all_docs("ingredients", outlet=outlet)
    stock_variance = []
    dept_residual = []
    for i in ings:
        v = round(i.get("physical_stock", 0) - i["book_stock"], 3)
        if abs(v) > 1e-9:
            stock_variance.append({"ingredient": i["name"], "unit": i["unit"], "book": round(i["book_stock"], 2),
                                   "physical": i["physical_stock"], "variance": v,
                                   "value": round(v * i["rate"], 2)})
        dept_total = round(sum((i.get("dept_stocks") or {}).values()), 3)
        if dept_total > 0:
            dept_residual.append({"ingredient": i["name"], "unit": i["unit"], "qty": dept_total,
                                  "depts": i.get("dept_stocks") or {},
                                  "value": round(dept_total * i["rate"], 2)})
    recon = await build_reconciliation(day(), outlet)
    dish_variance = [r for r in recon if r["unexplained"] != 0]
    return {"stock_variance": stock_variance, "dish_variance": dish_variance,
            "dept_residual": sorted(dept_residual, key=lambda x: -x["value"])}


# ---------- Dashboard ----------

@app_router.get("/dashboard")
async def dashboard(request: Request, user=Depends(get_current_user)):
    outlet = get_outlet(request)
    t = day()
    ings = await all_docs("ingredients", outlet=outlet)
    rates = rate_map(ings)
    dishes = await all_docs("dishes")
    dish_map = {d["id"]: d for d in dishes}

    pos_today = await all_docs("pos_sales", {"date": t}, outlet)
    sales_value = sum(s["qty"] * dish_map[s["dish_id"]]["sell_price"] for s in pos_today if s["dish_id"] in dish_map)
    portions_sold = sum(s["qty"] for s in pos_today)
    cons_today = await all_docs("consumption", {"date": t}, outlet)
    prod_cost = sum(c["qty"] * rates.get(c["ingredient_id"], 0) for c in cons_today)
    batches = await all_docs("production", {"date": t}, outlet)
    produced = sum(b["qty"] for b in batches)
    wast_today = await all_docs("wastage", {"date": t}, outlet)
    wast_val = round(sum(w["value"] for w in wast_today), 2)
    stock_value = round(sum(i["book_stock"] * i["rate"] for i in ings), 2)
    variance_items = sum(1 for i in ings if abs(i.get("physical_stock", 0) - i["book_stock"]) > 1e-9)
    recon = await build_reconciliation(t, outlet)
    unexplained = sum(abs(r["unexplained"]) for r in recon)
    fc = round(prod_cost / sales_value * 100, 1) if sales_value else 0

    seven_day = []
    for off in range(-6, 1):
        d = day(off)
        posd = await all_docs("pos_sales", {"date": d}, outlet)
        sv = sum(s["qty"] * dish_map[s["dish_id"]]["sell_price"] for s in posd if s["dish_id"] in dish_map)
        consd = await all_docs("consumption", {"date": d}, outlet)
        pc = sum(c["qty"] * rates.get(c["ingredient_id"], 0) for c in consd)
        label = datetime.fromisoformat(d).strftime("%d %b")
        seven_day.append({"date": label, "sales": round(sv), "food_cost": round(pc / sv * 100, 1) if sv else 0})

    cat_totals = {}
    for i in ings:
        cat_totals[i["category"]] = cat_totals.get(i["category"], 0) + i["book_stock"] * i["rate"]
    stock_by_category = [{"name": k, "value": round(v)} for k, v in sorted(cat_totals.items(), key=lambda x: -x[1])]

    wast_week = [w for off in range(-6, 1) for w in await all_docs("wastage", {"date": day(off)}, outlet)]
    reason_totals = {}
    for w in wast_week:
        reason_totals[w["reason"]] = reason_totals.get(w["reason"], 0) + w["value"]
    wastage_pie = [{"name": k, "value": round(v, 2)} for k, v in sorted(reason_totals.items(), key=lambda x: -x[1])]

    prod_totals = {}
    for b in batches:
        if b["dish_id"] in dish_map:
            name = dish_map[b["dish_id"]]["name"]
            prod_totals[name] = prod_totals.get(name, 0) + b["qty"]
    production_pie = [{"name": k, "value": v} for k, v in sorted(prod_totals.items(), key=lambda x: -x[1])]
    sales_cat = {}
    for s in pos_today:
        if s["dish_id"] in dish_map:
            c = dish_map[s["dish_id"]]["category"]
            sales_cat[c] = sales_cat.get(c, 0) + s["qty"] * dish_map[s["dish_id"]]["sell_price"]
    sales_pie = [{"name": k, "value": round(v)} for k, v in sorted(sales_cat.items(), key=lambda x: -x[1])]

    attention = []
    for r in recon:
        if r["unexplained"] < 0:
            attention.append({"tone": "terra", "text": f"{r['dish_name']} — sold {abs(r['unexplained'])} more than produced, check entries"})
        elif r["unexplained"] > 0:
            attention.append({"tone": "gold", "text": f"{r['dish_name']} — {r['unexplained']} portions unaccounted, review required"})
    for i in ings:
        v = round(i.get("physical_stock", 0) - i["book_stock"], 3)
        if abs(v) > 1e-9:
            attention.append({"tone": "gold", "text": f"{i['name']} — stock variance {v:+g} {i['unit']}, review physical count"})
    for i in ings:
        dept_total = round(sum((i.get("dept_stocks") or {}).values()), 3)
        if dept_total * i["rate"] > 800:
            attention.append({"tone": "emerald", "text": f"{i['name']} — {dept_total:g} {i['unit']} issued to kitchen, awaiting consumption"})

    return {
        "date": t, "outlet": outlet, "sales_value": round(sales_value), "portions_sold": portions_sold,
        "food_cost_pct": fc, "food_cost_target": 32,
        "stock_value": stock_value, "ingredients_count": len(ings),
        "produced_portions": produced, "batches_count": len(batches),
        "wastage_value": wast_val, "wastage_entries": len(wast_today),
        "variance_items": variance_items, "unexplained_portions": unexplained,
        "attention": attention[:8], "reconciliation": recon,
        "seven_day": seven_day, "stock_by_category": stock_by_category,
        "wastage_pie": wastage_pie, "production_pie": production_pie, "sales_pie": sales_pie,
    }


# ---------- Reports ----------

@app_router.get("/reports")
async def reports(request: Request, user=Depends(require_roles(*STAFF))):
    outlet = get_outlet(request)
    ings = await all_docs("ingredients", outlet=outlet)
    rates = rate_map(ings)
    dish_map = {d["id"]: d for d in await all_docs("dishes")}
    rows = []
    for off in range(-6, 1):
        d = day(off)
        posd = await all_docs("pos_sales", {"date": d}, outlet)
        sv = sum(s["qty"] * dish_map[s["dish_id"]]["sell_price"] for s in posd if s["dish_id"] in dish_map)
        sold = sum(s["qty"] for s in posd)
        batches = await all_docs("production", {"date": d}, outlet)
        produced = sum(b["qty"] for b in batches)
        consd = await all_docs("consumption", {"date": d}, outlet)
        pc = sum(c["qty"] * rates.get(c["ingredient_id"], 0) for c in consd)
        wast = await all_docs("wastage", {"date": d}, outlet)
        wv = sum(w["value"] for w in wast)
        pur = await all_docs("purchases", {"date": d}, outlet)
        pv = sum(p["amount"] for p in pur)
        rows.append({"date": d, "label": datetime.fromisoformat(d).strftime("%d %b"),
                     "sales": round(sv), "portions_sold": sold, "produced": produced,
                     "production_cost": round(pc), "food_cost_pct": round(pc / sv * 100, 1) if sv else 0,
                     "wastage_value": round(wv), "purchases_value": round(pv)})
    return rows


# ---------- Enquiries (website leads) ----------

@app_router.get("/enquiries")
async def list_enquiries(user=Depends(require_roles("owner"))):
    docs = await db.enquiries.find({}, {"_id": 0}).to_list(500)
    for d in docs:
        d.setdefault("status", "new")
    return sorted(docs, key=lambda x: x.get("created_at", ""), reverse=True)


class EnquiryStatusIn(BaseModel):
    status: str = Field(pattern="^(new|contacted|archived)$")


@app_router.put("/enquiries/{enq_id}/status")
async def set_enquiry_status(enq_id: str, input: EnquiryStatusIn, user=Depends(require_roles("owner"))):
    res = await db.enquiries.update_one({"id": enq_id}, {"$set": {"status": input.status}})
    if not res.matched_count:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    return {"status": input.status}


# ---------- Evening Recap & WhatsApp ----------

async def build_recap_summary():
    outlets = await db.outlets.find({}, {"_id": 0}).to_list(50)
    names = [o["name"] for o in outlets] or [DEFAULT_OUTLET]
    dish_map = {d["id"]: d for d in await all_docs("dishes")}
    t = day()
    per_outlet = []
    for name in names:
        ings = await all_docs("ingredients", outlet=name)
        rates = rate_map(ings)
        posd = await all_docs("pos_sales", {"date": t}, name)
        sales = sum(s["qty"] * dish_map[s["dish_id"]]["sell_price"] for s in posd if s["dish_id"] in dish_map)
        consd = await all_docs("consumption", {"date": t}, name)
        cost = sum(c["qty"] * rates.get(c["ingredient_id"], 0) for c in consd)
        wastd = await all_docs("wastage", {"date": t}, name)
        wv = sum(w["value"] for w in wastd)
        batches = await all_docs("production", {"date": t}, name)
        rows = await build_reconciliation(t, name)
        per_outlet.append({"outlet": name, "sales": round(sales),
                           "food_cost_pct": round(cost / sales * 100, 1) if sales else 0,
                           "wastage_value": round(wv), "produced": sum(b["qty"] for b in batches),
                           "rows": rows})
    return {"date": t, "outlets": per_outlet}


async def send_recap_email(summary):
    from html import escape
    from server import send_email, OWNER_EMAIL
    subject = f"InventoryPro.in evening recap — {summary['date']} — closing reconciliation"
    blocks = []
    for o in summary["outlets"]:
        rows = "".join(
            f'<tr><td style="padding:6px 12px 6px 0;font-size:13px;color:#111827">{escape(r["dish_name"])}</td>'
            f'<td style="padding:6px 12px 6px 0;font-size:13px;color:#374151">{r["produced"]}</td>'
            f'<td style="padding:6px 12px 6px 0;font-size:13px;color:#374151">{r["sold"]}</td>'
            f'<td style="padding:6px 12px 6px 0;font-size:13px;color:#374151">{r["accounted"]}</td>'
            f'<td style="padding:6px 0;font-size:11px;font-weight:700;letter-spacing:1px;color:'
            + ("#047857" if r["status"] == "reconciled" else "#B91C1C" if r["status"] == "over-sold" else "#B45309" if r["status"] == "unexplained" else "#6B7280")
            + f'">{r["status"].upper()}</td></tr>'
            for r in o["rows"] if r["produced"] or r["sold"]
        )
        blocks.append(
            f'<h3 style="margin:18px 0 6px;color:#111827;font-size:15px">{escape(o["outlet"])}</h3>'
            f'<p style="margin:0 0 6px;font-size:13px;color:#374151">Sales ₹{o["sales"]:,} · Food cost {o["food_cost_pct"]}% · '
            f'Wastage ₹{o["wastage_value"]:,} · Produced {o["produced"]} portions</p>'
            '<table role="presentation" cellpadding="0" cellspacing="0">'
            '<tr><td style="font-size:11px;color:#6B7280;padding-right:12px">DISH</td>'
            '<td style="font-size:11px;color:#6B7280;padding-right:12px">MADE</td>'
            '<td style="font-size:11px;color:#6B7280;padding-right:12px">SOLD</td>'
            '<td style="font-size:11px;color:#6B7280;padding-right:12px">ACCOUNTED</td>'
            '<td style="font-size:11px;color:#6B7280">STATUS</td></tr>'
            + rows + "</table>"
        )
    html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:24px;font-family:Arial,sans-serif;background:#FAF8F5">'
        '<p style="font-size:12px;letter-spacing:2px;color:#FF5722;font-weight:700;margin:0 0 8px">INVENTORYPRO.IN — EVENING RECAP</p>'
        f'<h2 style="margin:0 0 8px;color:#111827;font-size:20px">Closing reconciliation for {escape(summary["date"])}</h2>'
        '<p style="font-size:14px;color:#374151;margin:0 0 4px">Every portion produced today, traced to where it went.</p>'
        + "".join(blocks)
        + '<p style="font-size:12px;color:#6B7280;margin-top:20px">Sent by InventoryPro.in evening recap. Turn it off in Settings.</p>'
          '</td></tr></table>'
    )
    return await send_email(to=OWNER_EMAIL, subject=subject, html=html)


@app_router.post("/evening-recap/send")
async def send_evening_recap(user=Depends(require_roles("owner"))):
    summary = await build_recap_summary()
    email_id = await send_recap_email(summary)
    await db.settings.update_one({"id": "settings"}, {"$set": {"last_recap_date": summary["date"]}}, upsert=True)
    return {"status": "sent", "email_id": email_id}


@app_router.get("/variance-alert/whatsapp")
async def variance_whatsapp(user=Depends(require_roles("owner"))):
    from urllib.parse import quote
    summary = await build_alert_summary()
    settings = await db.settings.find_one({"id": "settings"}, {"_id": 0}) or {}
    number = "".join(ch for ch in settings.get("whatsapp_number", "919908659651") if ch.isdigit())
    lines = [
        f"InventoryPro.in variance summary ({summary['date']})",
        f"Unexplained: {summary['total_unexplained']} portions (threshold {summary['threshold']})",
        "",
    ]
    for o in summary["outlets"]:
        lines.append(f"{o['outlet']}: Sales Rs {o['sales']:,} | FC {o['food_cost_pct']}% | Wastage Rs {o['wastage_value']:,}")
        for r in o["unexplained"]:
            lines.append(f"  - {r['dish']}: {r['portions']:+d} portions")
        lines.append("")
    text = "\n".join(lines).strip()
    return {"url": f"https://wa.me/{number}?text={quote(text)}", "text": text}


# ---------- Variance Alerts ----------

async def build_alert_summary():
    settings = await db.settings.find_one({"id": "settings"}, {"_id": 0}) or {}
    threshold = settings.get("alert_threshold", 3)
    outlets = await db.outlets.find({}, {"_id": 0}).to_list(50)
    names = [o["name"] for o in outlets] or [DEFAULT_OUTLET]
    dish_map = {d["id"]: d for d in await all_docs("dishes")}
    t = day()
    per_outlet = []
    total_unexplained = 0
    for name in names:
        ings = await all_docs("ingredients", outlet=name)
        rates = rate_map(ings)
        posd = await all_docs("pos_sales", {"date": t}, name)
        sales = sum(s["qty"] * dish_map[s["dish_id"]]["sell_price"] for s in posd if s["dish_id"] in dish_map)
        consd = await all_docs("consumption", {"date": t}, name)
        cost = sum(c["qty"] * rates.get(c["ingredient_id"], 0) for c in consd)
        wastd = await all_docs("wastage", {"date": t}, name)
        wv = sum(w["value"] for w in wastd)
        recon = await build_reconciliation(t, name)
        un = [r for r in recon if r["unexplained"] != 0]
        total_unexplained += sum(abs(r["unexplained"]) for r in un)
        per_outlet.append({"outlet": name, "sales": round(sales),
                           "food_cost_pct": round(cost / sales * 100, 1) if sales else 0,
                           "wastage_value": round(wv),
                           "unexplained": [{"dish": r["dish_name"], "portions": r["unexplained"]} for r in un]})
    return {"date": t, "threshold": threshold, "total_unexplained": total_unexplained,
            "crossed": 0 < threshold <= total_unexplained, "outlets": per_outlet}


async def send_alert_email(summary):
    from html import escape
    from server import send_email, OWNER_EMAIL
    subject = f"InventoryPro.in morning summary — {summary['date']} — {summary['total_unexplained']} unexplained portions"
    blocks = []
    for o in summary["outlets"]:
        items = "".join(
            f'<li style="font-size:13px;color:#B91C1C;padding:2px 0">{escape(r["dish"])} — {r["portions"]:+d} portions</li>'
            for r in o["unexplained"]
        ) or '<li style="font-size:13px;color:#047857;padding:2px 0">All portions accounted for</li>'
        blocks.append(
            f'<h3 style="margin:16px 0 4px;color:#111827;font-size:15px">{escape(o["outlet"])}</h3>'
            f'<p style="margin:0;font-size:13px;color:#374151">Sales ₹{o["sales"]:,} · Food cost {o["food_cost_pct"]}% · Wastage ₹{o["wastage_value"]:,}</p>'
            f'<ul style="margin:6px 0;padding-left:18px">{items}</ul>'
        )
    tone = "#B91C1C" if summary["crossed"] else "#047857"
    status = "ABOVE" if summary["crossed"] else "within"
    html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:24px;font-family:Arial,sans-serif;background:#FAF8F5">'
        '<p style="font-size:12px;letter-spacing:2px;color:#FF5722;font-weight:700;margin:0 0 8px">INVENTORYPRO.IN — MORNING SUMMARY</p>'
        f'<h2 style="margin:0 0 8px;color:#111827;font-size:20px">Variance report for {escape(summary["date"])}</h2>'
        f'<p style="font-size:14px;color:{tone};font-weight:700;margin:0 0 8px">Unexplained variance: '
        f'{summary["total_unexplained"]} portions — {status} your threshold of {summary["threshold"]}.</p>'
        + "".join(blocks)
        + '<p style="font-size:12px;color:#6B7280;margin-top:20px">Sent by InventoryPro.in variance alerts. '
          'Manage the threshold in Settings.</p>'
          '</td></tr></table>'
    )
    return await send_email(to=OWNER_EMAIL, subject=subject, html=html)


@app_router.post("/variance-alert/send")
async def send_variance_alert(user=Depends(require_roles("owner"))):
    summary = await build_alert_summary()
    email_id = await send_alert_email(summary)
    await db.settings.update_one({"id": "settings"}, {"$set": {"last_alert_date": summary["date"]}}, upsert=True)
    return {"status": "sent", "email_id": email_id, "crossed": summary["crossed"],
            "total_unexplained": summary["total_unexplained"]}


# ---------- Settings & Reset ----------

@app_router.get("/settings")
async def get_settings(user=Depends(get_current_user)):
    s = await db.settings.find_one({"id": "settings"}, {"_id": 0})
    return s or {"restaurant_name": "My Restaurant", "pos_system": "", "currency": "INR"}


class SettingsIn(BaseModel):
    restaurant_name: str = Field(min_length=2, max_length=100)
    pos_system: str = Field(default="", max_length=60)
    currency: str = Field(default="INR", max_length=10)
    alert_enabled: bool = True
    alert_threshold: int = Field(default=3, ge=1, le=200)
    recap_enabled: bool = True
    whatsapp_number: str = Field(default="919908659651", max_length=20)


@app_router.put("/settings")
async def put_settings(input: SettingsIn, user=Depends(require_roles(*STAFF))):
    await db.settings.update_one({"id": "settings"}, {"$set": {"id": "settings", **input.model_dump()}}, upsert=True)
    return {"status": "saved"}


@app_router.post("/seed/reset")
async def reset_demo(user=Depends(require_roles("owner"))):
    from seed import seed_demo_data
    await seed_demo_data(force=True)
    return {"status": "demo data reset"}
