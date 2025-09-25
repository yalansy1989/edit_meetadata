# -*- coding: utf-8 -*-
import re, base64, io
from io import BytesIO
from datetime import datetime, date, time, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

import gradio as gr
import qrcode
from qrcode.constants import ERROR_CORRECT_M
from PIL import Image
from barcode import Code128
from barcode.writer import ImageWriter
from pypdf import PdfReader, PdfWriter

# ================= أدوات مساعدة =================
def _clean_vat(v: str) -> str:
    return re.sub(r"\D", "", v or "")

def _fmt2(x: str) -> str:
    try:
        q = Decimal(x)
    except InvalidOperation:
        q = Decimal("0")
    return format(q.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")

def _iso_utc(d: date, t: time) -> str:
    local_dt = datetime.combine(d, t.replace(microsecond=0))
    try:
        return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return local_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def _tlv(tag: int, val: str) -> bytes:
    b = val.encode("utf-8")
    if len(b) > 255:
        raise ValueError("TLV>255B")
    return bytes([tag, len(b)]) + b

def build_zatca_base64(seller, vat, dt_iso, total, vat_s):
    payload = b"".join([_tlv(1, seller), _tlv(2, vat), _tlv(3, dt_iso), _tlv(4, total), _tlv(5, vat_s)])
    return base64.b64encode(payload).decode("ascii")

def make_qr(b64: str) -> BytesIO:
    qr = qrcode.QRCode(version=14, error_correction=ERROR_CORRECT_M, box_size=2, border=4)
    qr.add_data(b64)
    qr.make(fit=False)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img = img.resize((640, 640), Image.NEAREST)
    out = BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out

# ---- Code128 ----
WIDTH_IN, HEIGHT_IN, DPI = 1.86, 0.34, 600
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def sanitize(s: str) -> str:
    s = (s or "").translate(ARABIC_DIGITS)
    s = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]", "", s)
    return "".join(ch for ch in s if ord(ch) < 128).strip()

def render_code128(data: str) -> BytesIO:
    code = Code128(data, writer=ImageWriter())
    buf = BytesIO()
    code.write(buf, {
        "write_text": False,
        "dpi": int(DPI),
        "module_height": HEIGHT_IN * 25.4,
        "quiet_zone": 0.0,
        "background": "white",
        "foreground": "black",
    })
    buf.seek(0)
    im = Image.open(buf)
    im = im.resize((int(WIDTH_IN * DPI), int(HEIGHT_IN * DPI)), Image.NEAREST)
    out = BytesIO()
    im.save(out, format="PNG", dpi=(DPI, DPI))
    out.seek(0)
    return out

# ---- PDF Metadata ----
def read_meta(file):
    r = PdfReader(file)
    md = r.metadata or {}
    return md

def write_meta(file, new_md: dict):
    r = PdfReader(file)
    w = PdfWriter()
    for p in r.pages:
        w.add_page(p)
    w.add_metadata(new_md)
    out = BytesIO()
    w.write(out)
    out.seek(0)
    return out

# ================= واجهة Gradio =================
def calc_vat(total_incl, tax_rate):
    try:
        total_incl = float(total_incl)
        tax_rate = float(tax_rate)
    except:
        return "⚠️ قيم غير صحيحة"
    rate = tax_rate/100.0
    before = round(total_incl/(1+rate), 2)
    vat_amount = round(total_incl - before, 2)
    return f"قبل الضريبة: {before:.2f} | الضريبة: {vat_amount:.2f}"

def gen_code128(text):
    s = sanitize(text)
    if not s:
        return None
    img = render_code128(s)
    return img

def gen_qr(seller, vat_number, total, vat, date_str, time_str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        t = datetime.strptime(time_str, "%H:%M:%S").time()
    except:
        return "⚠️ صيغة التاريخ/الوقت غير صحيحة", None
    vclean = _clean_vat(vat_number)
    if len(vclean) != 15:
        return "⚠️ الرقم الضريبي يجب أن يكون 15 رقمًا", None
    iso = _iso_utc(d, t)
    b64 = build_zatca_base64(seller.strip(), vclean, iso, _fmt2(total), _fmt2(vat))
    qr_img = make_qr(b64)
    return b64, qr_img

def extract_pdf_meta(pdf_file):
    if pdf_file is None:
        return {}
    md = read_meta(pdf_file)
    return md

def update_pdf_meta(pdf_file, meta_text):
    import json
    try:
        md = json.loads(meta_text)
    except:
        return None
    out = write_meta(pdf_file, md)
    return out

with gr.Blocks(title="حاسبة ضريبة + QR + Code128 + PDF") as demo:
    gr.Markdown("## 💰 حاسبة الضريبة + QR + Code128 + PDF Metadata")

    with gr.Tab("حاسبة الضريبة"):
        total = gr.Textbox(label="المبلغ شامل الضريبة", value="0.00")
        tax = gr.Textbox(label="نسبة الضريبة %", value="15")
        result = gr.Textbox(label="النتيجة")
        gr.Button("احسب الآن").click(calc_vat, inputs=[total, tax], outputs=result)

    with gr.Tab("مولد Code128"):
        code_text = gr.Textbox(label="النص/الرقم")
        code_img = gr.Image(label="صورة Code128")
        gr.Button("إنشاء Code128").click(gen_code128, inputs=code_text, outputs=code_img)

    with gr.Tab("مولد QR ZATCA"):
        seller = gr.Textbox(label="اسم البائع")
        vat = gr.Textbox(label="الرقم الضريبي (15 رقم)")
        total_qr = gr.Textbox(label="الإجمالي (شامل)", value="0.00")
        vat_qr = gr.Textbox(label="الضريبة", value="0.00")
        date_qr = gr.Textbox(label="التاريخ (YYYY-MM-DD)", value=str(date.today()))
        time_qr = gr.Textbox(label="الوقت (HH:MM:SS)", value=datetime.now().strftime("%H:%M:%S"))
        b64_out = gr.Textbox(label="النص المشفر Base64")
        qr_img = gr.Image(label="رمز QR")
        gr.Button("إنشاء QR").click(gen_qr, inputs=[seller, vat, total_qr, vat_qr, date_qr, time_qr], outputs=[b64_out, qr_img])

    with gr.Tab("PDF Metadata"):
        pdf_up = gr.File(label="رفع PDF", file_types=[".pdf"])
        meta_out = gr.JSON(label="البيانات الحالية")
        gr.Button("قراءة Metadata").click(extract_pdf_meta, inputs=pdf_up, outputs=meta_out)
        meta_in = gr.Textbox(label="أدخل Metadata (بصيغة JSON لتحديث)")
        pdf_out = gr.File(label="تنزيل PDF المعدل")
        gr.Button("تحديث وحفظ Metadata").click(update_pdf_meta, inputs=[pdf_up, meta_in], outputs=pdf_out)

demo.launch()
