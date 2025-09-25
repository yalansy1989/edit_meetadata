# -*- coding: utf-8 -*-
import re, base64, io
from io import BytesIO
from datetime import datetime, date, time, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

import streamlit as st
import qrcode
from qrcode.constants import ERROR_CORRECT_M
from PIL import Image
from barcode import Code128
from barcode.writer import ImageWriter
from pypdf import PdfReader, PdfWriter

# ================= إعداد عام + تنسيق =================
st.set_page_config(page_title="حاسبة + ZATCA + Code128 + PDF Metadata", page_icon="💰", layout="wide")
st.markdown("""
<style>
h1, h2, h3 { text-align:center; font-weight:700; }
@media (prefers-color-scheme: light) { h1, h2, h3 { color:#046307 !important; } }
@media (prefers-color-scheme: dark)  { h1, h2, h3 { color:#ffffff !important; } }
.block-container { padding-top: 1rem; }
</style>
""", unsafe_allow_html=True)

st.title("💰 حاسبة الضريبة + مولّد QR (ZATCA) + Code128 + PDF Metadata")

# ================= حالة افتراضية ثابتة =================
if "qr_initialized" not in st.session_state:
    now_time = datetime.now().time().replace(microsecond=0)
    st.session_state.update({
        "qr_total": "0.00",
        "qr_vat": "0.00",
        "qr_date": date.today(),
        "qr_time": now_time,
        "qr_vat_number": "",
        "qr_seller": "",
        "qr_time_hm": now_time.replace(second=0),
        "qr_secs": now_time.second
    })
    st.session_state["qr_initialized"] = True

# ================= أدوات مشتركة =================
def _clean_vat(v: str) -> str: return re.sub(r"\D", "", v or "")

def _fmt2(x: str) -> str:
    try: q = Decimal(x)
    except InvalidOperation: q = Decimal("0")
    return format(q.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")

def _iso_utc(d: date, t: time) -> str:
    local_dt = datetime.combine(d, t.replace(microsecond=0))
    try:
        return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return local_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def _tlv(tag: int, val: str) -> bytes:
    b = val.encode("utf-8")
    if len(b) > 255: raise ValueError("TLV>255B")
    return bytes([tag, len(b)]) + b

def build_zatca_base64(seller, vat, dt_iso, total, vat_s):
    payload = b"".join([_tlv(1,seller), _tlv(2,vat), _tlv(3,dt_iso), _tlv(4,total), _tlv(5,vat_s)])
    return base64.b64encode(payload).decode("ascii")

# ================= QR =================
def make_qr(b64: str) -> bytes:
    qr = qrcode.QRCode(version=14, error_correction=ERROR_CORRECT_M, box_size=2, border=4)
    qr.add_data(b64); qr.make(fit=False)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img = img.resize((640, 640), Image.NEAREST)
    out = BytesIO(); img.save(out, format="PNG"); return out.getvalue()

# ================= Code128 =================
WIDTH_IN, HEIGHT_IN, DPI = 1.86, 0.34, 600
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def sanitize(s: str) -> str:
    s = (s or "").translate(ARABIC_DIGITS)
    s = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]", "", s)
    return "".join(ch for ch in s if ord(ch) < 128).strip()

def render_code128(data: str) -> bytes:
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
    return buf.getvalue()

def resize_code128(png_bytes: bytes) -> bytes:
    with Image.open(BytesIO(png_bytes)) as im:
        im = im.resize((int(WIDTH_IN*DPI), int(HEIGHT_IN*DPI)), Image.NEAREST)
        out = BytesIO(); im.save(out, format="PNG", dpi=(DPI, DPI))
        return out.getvalue()

# ================= PDF Metadata =================
BASE_KEYS = ["/ModDate","/CreationDate","/Producer","/Title","/Author","/Subject","/Keywords","/Creator"]

def pdf_date_to_display_date(s):
    if not s or not isinstance(s, str): return ""
    if s.startswith("D:"): s = s[2:]
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", s)
    if m:
        y,M,d,H,m_,sec = m.groups()
        try: return datetime(int(y),int(M),int(d),int(H),int(m_),int(sec)).strftime("%d/%m/%Y, %H:%M:%S")
        except: return s
    return s

def display_date_to_pdf_date(s):
    try: return datetime.strptime(s,"%d/%m/%Y, %H:%M:%S").strftime("D:%Y%m%d%H%M%S+03'00'")
    except: return s

def parse_display_dt(s: str):
    try:
        dt = datetime.strptime(s.strip(), "%d/%m/%Y, %H:%M:%S")
        return dt.date(), dt.time().replace(microsecond=0)
    except Exception:
        return None, None

def read_meta(file):
    file.seek(0); r = PdfReader(file); md = r.metadata or {}
    keys = BASE_KEYS + [k for k in md.keys() if k not in BASE_KEYS]
    out = {}
    for k in keys:
        v = md.get(k, "")
        out[k] = pdf_date_to_display_date(v) if k in ("/CreationDate","/ModDate") else v
    return out, keys

def write_meta(file, new_md):
    file.seek(0)
    r = PdfReader(file); w = PdfWriter()
    for p in r.pages: w.add_page(p)
    final = {}
    for k,v in new_md.items():
        final[k] = display_date_to_pdf_date(v) if k in ("/CreationDate","/ModDate") else v
    out = io.BytesIO(); w.write(out); out.seek(0); return out

# =========================================================
# واجهة التطبيق
# =========================================================
c1, c2 = st.columns(2)

with c1:
    st.header("📊 حاسبة الضريبة")
    total_incl = st.number_input("المبلغ شامل الضريبة", min_value=0.0, step=0.01)
    tax_rate   = st.number_input("نسبة الضريبة (%)", min_value=1.0, max_value=100.0, value=15.0, step=0.01)

    colA, colB = st.columns(2)
    with colA:
        if st.button("احسب الآن"):
            rate = tax_rate/100.0
            before = round(total_incl/(1+rate), 2)
            vat_amount = round(total_incl - before, 2)
            st.success(f"قبل الضريبة: {before:.2f} | الضريبة: {vat_amount:.2f}")
    with colB:
        if st.button("📤 إرسال القيم إلى مولّد QR"):
            rate = tax_rate/100.0 if tax_rate else 0.0
            before = round(total_incl/(1+rate), 2) if total_incl and rate else 0.0
            vat_amount = round(total_incl - before, 2) if total_incl and rate else 0.0
            st.session_state["qr_total"] = f"{total_incl:.2f}"
            st.session_state["qr_vat"]   = f"{vat_amount:.2f}"
            st.toast("تم إرسال الإجمالي والضريبة إلى قسم مولّد QR ✅")

with c2:
    st.header("📑 PDF Metadata")
    up = st.file_uploader("تحميل PDF", type=["pdf"])
    if up:
        if "meta_dict" not in st.session_state or st.session_state.get("_last_file_name") != up.name:
            meta, keys = read_meta(up)
            st.session_state.meta_keys = keys
            st.session_state.meta_dict = meta
            st.session_state._last_file_name = up.name
            for k, v in meta.items():
                if k not in st.session_state:
                    st.session_state[k] = v
            st.session_state.setdefault("_prev_creation", st.session_state.get("/CreationDate", ""))
            st.session_state.setdefault("_prev_mod",       st.session_state.get("/ModDate", ""))

        auto = st.checkbox("تحديث تلقائي ثنائي الاتجاه بين ModDate و CreationDate", value=True, key="_auto_sync")

        if auto:
            c_now = st.session_state.get("/CreationDate", "")
            m_now = st.session_state.get("/ModDate", "")
            pc = st.session_state.get("_prev_creation", c_now)
            pm = st.session_state.get("_prev_mod", m_now)
            if c_now != pc and m_now == pm:
                st.session_state["/ModDate"] = c_now; m_now = c_now
            elif m_now != pm and c_now == pc:
                st.session_state["/CreationDate"] = m_now; c_now = m_now
            st.session_state["_prev_creation"] = c_now
            st.session_state["_prev_mod"]      = m_now

        ordered = ["/ModDate","/CreationDate"] + [k for k in st.session_state.meta_keys if k not in ("/ModDate","/CreationDate")]
        updated = {}
        for k in ordered:
            label = k[1:] if k.startswith("/") else k
            st.text_input(label, key=k)
            updated[k] = st.session_state.get(k, "")

        if st.button("📨 إرسال CreationDate إلى مولّد QR"):
            cre = st.session_state.get("/CreationDate", "")
            d, t = parse_display_dt(cre)
            if d and t:
                st.session_state["qr_date"]    = d
                st.session_state["qr_time"]    = t
                st.session_state["qr_time_hm"] = t.replace(second=0)
                st.session_state["qr_secs"]    = t.second
                st.success("تم إرسال التاريخ والوقت إلى قسم مولّد QR ✅")
            else:
                st.error("صيغة CreationDate غير صحيحة. الصيغة: dd/mm/YYYY, HH:MM:SS")

        if st.button("حفظ Metadata"):
            out = write_meta(up, updated)
            st.download_button("تحميل الملف المعدّل", data=out, file_name=up.name, mime="application/pdf")

# =========================================================
c3, c4 = st.columns(2)

with c3:
    st.header("🧾 مولّد Code-128")
    v = st.text_input("النص/الرقم")
    if st.button("إنشاء Code-128"):
        s = sanitize(v)
        if not s: st.error("أدخل قيمة.")
        else:
            raw = render_code128(s)
            final = resize_code128(raw)
            st.image(final, caption=f"{WIDTH_IN}×{HEIGHT_IN} inch @ {DPI} DPI")
            st.download_button("⬇️ تحميل", final, "code128.png", "image/png")

with c4:
    st.header("🔖 مولّد QR (ZATCA)")
    st.text_input("الرقم الضريبي (15 رقم)", key="qr_vat_number")
    st.text_input("اسم البائع", key="qr_seller")
    st.text_input("الإجمالي (شامل)", key="qr_total")
    st.text_input("الضريبة", key="qr_vat")

    hm_time = st.time_input("الوقت (ساعة:دقيقة)", key="qr_time_hm", value=st.session_state["qr_time_hm"], step=60)
    secs = st.number_input("الثواني", min_value=0, max_value=59, step=1, key="qr_secs", value=st.session_state["qr_secs"])
    st.session_state["qr_time"] = time(hm_time.hour, hm_time.minute, int(secs))
    st.date_input("التاريخ", key="qr_date", value=st.session_state["qr_date"])

    if st.button("إنشاء رمز QR"):
        vclean = _clean_vat(st.session_state["qr_vat_number"])
        if len(vclean) != 15:
            st.error("الرقم الضريبي يجب أن يكون 15 رقمًا.")
        else:
            iso = _iso_utc(st.session_state["qr_date"], st.session_state["qr_time"])
            b64 = build_zatca_base64(
                st.session_state["qr_seller"].strip(),
                vclean,
                iso,
                _fmt2(st.session_state["qr_total"]),
                _fmt2(st.session_state["qr_vat"])
            )
            st.code(b64, language="text")
            img = make_qr(b64)
            st.image(img, caption="رمز QR ZATCA")
            st.download_button("⬇️ تحميل QR", img, "zatca_qr.png", "image/png")
