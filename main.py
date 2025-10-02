# main.py
import os
import io
import base64
from uuid import uuid4
from datetime import datetime

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder

import qrcode
from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image
from pymongo import MongoClient

# ---------------- CONFIG ----------------
MONGO_URI = "mongodb+srv://objectidpro_db:Objectid@cluster0.d8dg4nq.mongodb.net/shopdb?retryWrites=true&w=majority"
DB_NAME = "shopdb"
PRODUCTS_COLLECTION = "products"

UPLOAD_DIR = "uploads"
CODES_DIR = "codes"
API_HOST = "http://127.0.0.1:8000"  # for returning full URLs
# ----------------------------------------
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CODES_DIR, exist_ok=True)

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
products_col = db[PRODUCTS_COLLECTION]

app = FastAPI(title="Smart Shop Billing Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/codes", StaticFiles(directory=CODES_DIR), name="codes")


# ----------------- Helpers -----------------
def save_upload(file: UploadFile, folder: str, prefix: str = "") -> str:
    ext = os.path.splitext(file.filename)[1] or ".jpg"
    filename = f"{prefix}{uuid4().hex}{ext}"
    path = os.path.join(folder, filename)
    with open(path, "wb") as f:
        f.write(file.file.read())
    return f"/{folder}/{filename}"


def generate_barcode_png(product_id: str, folder: str) -> str:
    filename = f"barcode_{product_id}.png"
    path = os.path.join(folder, filename)

    code = Code128(product_id, writer=ImageWriter())
    code.save(os.path.join(folder, f"barcode_{product_id}"), options={
        "write_text": True,   # keep human-readable text
        "quiet_zone": 10,     # extra padding
        "module_height": 20,  # taller bars
        "module_width": 0.4   # thinner bars for clarity
    })
    return f"/{folder}/{filename}"


def generate_qr_png(product_id: str, folder: str) -> tuple[str, str]:
    """
    Save QR image using product_id in filename.
    Returns:
    - relative file path
    - base64 data string
    """
    filename = f"qr_{product_id}.png"
    path = os.path.join(folder, filename)

    qr_img = qrcode.make(product_id)
    if hasattr(qr_img, "get_image"):
        qr_img = qr_img.get_image()
    elif not isinstance(qr_img, Image.Image):
        qr_img = Image.fromarray(qr_img)

    qr_img.save(path, format="PNG")

    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)
    qr_base64 = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

    return f"/{folder}/{filename}", qr_base64


def full_url(path: str):
    return f"{API_HOST}{path}" if path else None


# ----------------- Auth -----------------
@app.post("/auth/login")
async def login(username: str = Form(...), password: str = Form(...)):
    if username == "admin" and password == "admin123":
        return {"access_token": "demo-token", "token_type": "bearer"}
    raise HTTPException(status_code=401, detail="Invalid credentials")


# ----------------- Create Product -----------------
@app.post("/products")
async def create_product(
    name: str = Form(...),
    category: str = Form(None),
    subcategory: str = Form(None),
    audience: str = Form(None),
    closure: str = Form(None),
    color: str = Form(None),
    description: str = Form(None),
    location: str = Form(None),
    price: float = Form(None),
    main_image: UploadFile = File(None),
    angle1_image: UploadFile = File(None),
    angle2_image: UploadFile = File(None),
    angle3_image: UploadFile = File(None),
):
    product_id = f"P-{uuid4().hex[:8].upper()}"
    images = []

    try:
        if main_image:
            images.append(save_upload(main_image, UPLOAD_DIR, prefix="main_"))
        if angle1_image:
            images.append(save_upload(angle1_image, UPLOAD_DIR, prefix="a1_"))
        if angle2_image:
            images.append(save_upload(angle2_image, UPLOAD_DIR, prefix="a2_"))
        if angle3_image:
            images.append(save_upload(angle3_image, UPLOAD_DIR, prefix="a3_"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save images: {e}")

    try:
        barcode_rel = generate_barcode_png(product_id, CODES_DIR)
        qr_rel, qr_base64 = generate_qr_png(product_id, CODES_DIR)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate codes: {e}")

    product_doc = {
        "product_id": product_id,
        "name": name,
        "category": category,
        "subcategory": subcategory,
        "audience": audience,
        "closure": closure,
        "color": color,
        "description": description,
        "location": location,
        "price": price,
        "images": images,
        "barcode": barcode_rel,
        "qr_code": qr_rel,
        "created_at": datetime.utcnow(),
        "last_scanned": None,
    }

    res = products_col.insert_one(product_doc)
    product_doc["_id"] = str(res.inserted_id)

    resp = dict(product_doc)
    resp["images"] = [full_url(p) for p in product_doc.get("images", [])]
    resp["barcode"] = full_url(product_doc.get("barcode"))
    resp["qr_code"] = full_url(product_doc.get("qr_code"))
    resp["qr_code_base64"] = qr_base64

    return JSONResponse(jsonable_encoder(resp))


# ----------------- Get Product -----------------
@app.get("/products/{pid}")
async def get_product(pid: str):
    doc = products_col.find_one({"product_id": pid})
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    doc["_id"] = str(doc["_id"])
    doc["images"] = [full_url(p) for p in doc.get("images", [])]
    doc["barcode"] = full_url(doc.get("barcode"))
    doc["qr_code"] = full_url(doc.get("qr_code"))
    return JSONResponse(jsonable_encoder(doc))


# ----------------- List Products -----------------
@app.get("/products")
async def list_products(limit: int = 50):
    docs = list(products_col.find().sort("created_at", -1).limit(limit))
    result = []
    for d in docs:
        d["_id"] = str(d["_id"])
        d["images"] = [full_url(p) for p in d.get("images", [])]
        d["barcode"] = full_url(d.get("barcode"))
        d["qr_code"] = full_url(d.get("qr_code"))
        result.append(d)
    return JSONResponse(jsonable_encoder(result))


# ----------------- Scan Product -----------------
@app.post("/scan")
async def scan_product(payload: dict):
    product_id = payload.get("product_id")
    scanned_by = payload.get("scanned_by", "Unknown")
    if not product_id:
        raise HTTPException(status_code=400, detail="product_id required")

    doc = products_col.find_one({"product_id": product_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")

    products_col.update_one(
        {"product_id": product_id}, {"$set": {"last_scanned": datetime.utcnow()}}
    )
    doc = products_col.find_one({"product_id": product_id})
    doc["_id"] = str(doc["_id"])
    doc["images"] = [full_url(p) for p in doc.get("images", [])]
    doc["barcode"] = full_url(doc.get("barcode"))
    doc["qr_code"] = full_url(doc.get("qr_code"))

    return JSONResponse(
        jsonable_encoder(
            {
                "product": doc,
                "scanned_by": scanned_by,
                "scanned_at": datetime.utcnow().isoformat(),
            }
        )
    )

# ------------------Delete Product-----------------
@app.delete("/products/{pid}")
async def delete_product(pid: str):
    doc = products_col.find_one({"product_id": pid})
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Delete images and codes from disk
    for path in doc.get("images", []):
        file_path = path.lstrip("/")
        if os.path.exists(file_path):
            os.remove(file_path)
    for path in [doc.get("barcode"), doc.get("qr_code")]:
        if path:
            file_path = path.lstrip("/")
            if os.path.exists(file_path):
                os.remove(file_path)

    products_col.delete_one({"product_id": pid})
    return {"detail": f"Product {pid} deleted successfully"}

