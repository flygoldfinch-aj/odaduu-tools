import streamlit as st
import google.generativeai as genai
import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color, lightgrey, black, white
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.utils import ImageReader
from datetime import datetime, timedelta
import io
import pandas as pd
import pypdf
import re
import json
from math import sin, cos, radians


# =====================================
# 1) STREAMLIT CONFIG
# =====================================
st.set_page_config(page_title="Odaduu Voucher Tool", page_icon="🌏", layout="wide")


# =====================================
# 2) BRANDING / CONSTANTS
# =====================================
BRAND_BLUE = Color(0.05, 0.20, 0.40)

# Odaduu orange
BRAND_ORANGE = Color(0.95, 0.42, 0.13)

COMPANY_NAME = "Odaduu Travel DMC"
COMPANY_EMAIL = "aashwin@odaduu.jp"
LOGO_FILE = "logo.png"

# Footer safe area reservation (prevents overlap)
FOOTER_LINE_Y = 40
FOOTER_RESERVED_HEIGHT = 110  # seal + footer text + breathing space
MIN_CONTENT_Y = FOOTER_LINE_Y + FOOTER_RESERVED_HEIGHT  # everything must stay above this


# =====================================
# 3) SECRETS
# =====================================
try:
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
    SEARCH_KEY = st.secrets["SEARCH_API_KEY"]
    SEARCH_CX = st.secrets["SEARCH_ENGINE_ID"]
    genai.configure(api_key=GEMINI_KEY)
except Exception:
    st.error("⚠️ Secrets not found! Please check Streamlit settings.")


# =====================================
# 4) SESSION STATE
# =====================================
def init_state():
    defaults = {
        "hotel_search_query": "",
        "found_hotels": [],
        "hotel_name": "",
        "city": "",
        "checkin": datetime.now().date(),
        "checkout": (datetime.now().date() + timedelta(days=1)),
        "num_rooms": 1,
        "adults": 2,
        "meal_plan": "Breakfast Only",
        "ai_room_str": "",
        "fetched_room_types": [],
        "last_uploaded_file": None,
        "bulk_data": [],
        "same_conf_check": False,
        "room_final": "",
        "hotel_images": [None, None, None],  # exterior / lobby / room
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    for i in range(50):
        if f"room_{i}_guest" not in st.session_state:
            st.session_state[f"room_{i}_guest"] = ""
        if f"room_{i}_conf" not in st.session_state:
            st.session_state[f"room_{i}_conf"] = ""


init_state()


# =====================================
# 5) UTILITIES
# =====================================
def parse_smart_date(date_str):
    if not date_str:
        return None
    clean = str(date_str).strip()
    clean = re.sub(r"\bSept\b", "Sep", clean, flags=re.IGNORECASE)
    for fmt in ["%d %b %Y", "%Y-%m-%d", "%d %B %Y", "%d-%m-%Y"]:
        try:
            return datetime.strptime(clean, fmt).date()
        except Exception:
            continue
    return None


def clean_extracted_text(raw_val):
    s = str(raw_val).strip()
    if s.lower() in ["room_name", "room_type"]:
        return ""
    return s.strip('{}[]"\'')


def safe_json_loads(s: str):
    if not s:
        return None
    s = s.strip().replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(s)
    except Exception:
        pass

    m = re.search(r"(\{.*\}|\[.*\])", s, flags=re.DOTALL)
    if m:
        candidate = m.group(1)
        try:
            return json.loads(candidate)
        except Exception:
            return None
    return None


# =====================================
# 6) GOOGLE SEARCH / IMAGES / AI
# =====================================
def google_search(query, num=5, search_type=None):
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"q": query, "cx": SEARCH_CX, "key": SEARCH_KEY, "num": num}
        if search_type == "image":
            params.update({"searchType": "image", "imgSize": "large", "safe": "active"})
        res = requests.get(url, params=params, timeout=8)
        return res.json().get("items", []) if res.status_code == 200 else []
    except Exception:
        return []


def find_hotel_options(keyword):
    results = google_search(f"Hotel {keyword} official site", num=5)
    hotels = []
    for item in results:
        title = item.get("title", "").split("|")[0].split("-")[0].strip()
        if title and title not in hotels:
            hotels.append(title)
    return hotels[:5]


def detect_city(hotel_name):
    model = genai.GenerativeModel("gemini-2.0-flash")
    try:
        r = model.generate_content(f'Return ONLY the city name for hotel: "{hotel_name}"')
        return (r.text or "").strip()
    except Exception:
        return ""


def fetch_real_room_types(hotel_name, city):
    results = google_search(f"{hotel_name} {city} official site room types accommodation", num=5)
    if not results:
        return []
    snippets = "\n".join([f"- {i.get('title','')}: {i.get('snippet','')}" for i in results])

    model = genai.GenerativeModel("gemini-2.0-flash")
    try:
        r = model.generate_content(
            "Extract official hotel room types from these results. Return ONLY a JSON list of strings.\n\n"
            f"{snippets}"
        )
        parsed = safe_json_loads(r.text)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def fetch_hotel_details_text(hotel, city):
    model = genai.GenerativeModel("gemini-2.0-flash")
    try:
        r = model.generate_content(
            f'Get address/phone for "{hotel}" in "{city}". '
            'Return JSON: { "addr1": "Street", "addr2": "City/Zip/Country", "phone": "+123..." }'
        )
        parsed = safe_json_loads(r.text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def fetch_image_urls(query, num=4):
    # Return multiple links; we will try them until one loads
    items = google_search(query, num=num, search_type="image")
    urls = []
    for it in items:
        link = it.get("link")
        if link and link not in urls:
            urls.append(link)
    return urls


def get_img_reader_from_urls(urls):
    # Try several URLs (403 is common on hotlinking)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"
    }
    for url in urls or []:
        try:
            r = requests.get(url, timeout=6, headers=headers)
            if r.status_code == 200 and r.content:
                return ImageReader(io.BytesIO(r.content))
        except Exception:
            continue
    return None


def get_smart_images(hotel, city):
    base = f"{hotel} {city}".strip()
    exterior_urls = fetch_image_urls(f"{base} hotel exterior building", num=4)
    lobby_urls = fetch_image_urls(f"{base} hotel lobby reception", num=4)
    room_urls = fetch_image_urls(f"{base} hotel room interior bedroom", num=4)

    return [
        get_img_reader_from_urls(exterior_urls),
        get_img_reader_from_urls(lobby_urls),
        get_img_reader_from_urls(room_urls),
    ]


def extract_pdf_data(pdf_file):
    try:
        reader = pypdf.PdfReader(pdf_file)
        pages_text = []
        for p in reader.pages:
            t = p.extract_text()
            if t:
                pages_text.append(t)
        text = "\n".join(pages_text)

        model = genai.GenerativeModel("gemini-2.0-flash")
        prompt = f"""
Extract booking details strictly as JSON.

JSON schema:
{{
  "hotel_name": "...",
  "city": "...",
  "checkin_raw": "...",
  "checkout_raw": "...",
  "meal_plan": "...",
  "room_type": "...",
  "rooms": [{{ "guest_name": "...", "confirmation_no": "..." }}]
}}

Rules:
- Return ONLY valid JSON.
- If multiple rooms found, include all.
- If room type is unclear, leave "".

TEXT:
{text[:20000]}
"""
        res = model.generate_content(prompt).text
        parsed = safe_json_loads(res)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


# =====================================
# 7) REPORTLAB PDF (LOCKED + 1 PAGE)
# =====================================
def draw_vector_seal(c, x, y):
    c.saveState()
    c.setStrokeColor(BRAND_BLUE)
    c.setFillColor(BRAND_BLUE)
    c.setFillAlpha(0.9)
    c.setLineWidth(1.5)

    cx, cy = x + 40, y + 40
    c.circle(cx, cy, 40, stroke=1, fill=0)
    c.setLineWidth(0.5)
    c.circle(cx, cy, 36, stroke=1, fill=0)

    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(cx, cy + 4, "ODADUU")
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(cx, cy - 6, "TRAVEL DMC")

    c.setFont("Helvetica-Bold", 6)
    text_top = "CERTIFIED VOUCHER"
    angle_start = 140
    for i, char in enumerate(text_top):
        angle = angle_start - (i * 10)
        rad = radians(angle)
        tx = cx + 32 * cos(rad)
        ty = cy + 32 * sin(rad)
        c.saveState()
        c.translate(tx, ty)
        c.rotate(angle - 90)
        c.drawCentredString(0, 0, char)
        c.restoreState()

    text_bot = "OFFICIAL"
    angle_start = 240
    for i, char in enumerate(text_bot):
        angle = angle_start + (i * 12)
        rad = radians(angle)
        tx = cx + 32 * cos(rad)
        ty = cy + 32 * sin(rad)
        c.saveState()
        c.translate(tx, ty)
        c.rotate(angle + 90)
        c.drawCentredString(0, 0, char)
        c.restoreState()

    c.restoreState()


def _draw_centered_header(c, w, y_top):
    logo_w, logo_h = 240, 60
    logo_x = (w - logo_w) / 2
    logo_y = y_top - logo_h

    try:
        c.drawImage(LOGO_FILE, logo_x, logo_y, logo_w, logo_h, mask="auto", preserveAspectRatio=True)
    except Exception:
        c.setFillColor(BRAND_BLUE)
        c.setFont("Helvetica-Bold", 24)
        c.drawCentredString(w / 2, y_top - 35, "odaduu")
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(w / 2, y_top - 50, "Travel DMC")

    title_y = logo_y - 35
    c.setFillColor(BRAND_BLUE)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(w / 2, title_y, "HOTEL CONFIRMATION VOUCHER")

    return title_y - 18


def _draw_image_row(c, x, y, w, imgs):
    # 3 images across, compact like your older vouchers (exterior/lobby/room)
    valid = [im for im in imgs if im is not None]
    if not valid:
        return y

    gap = 8
    img_h = 78
    img_w = (w - (2 * gap)) / 3

    # draw exactly 3 slots; if some are None, skip that slot
    for i in range(3):
        im = imgs[i] if i < len(imgs) else None
        if im:
            try:
                c.drawImage(im, x + i * (img_w + gap), y - img_h, img_w, img_h, preserveAspectRatio=True, anchor='c')
            except Exception:
                pass

    return y - img_h - 10


def _boxed_compact_section(c, x, y, w, title, rows):
    """
    Guest/Hotel/Room:
    - BOX in Odaduu orange
    - compact spacing
    - bold labels
    """
    data = [[title, ""]]
    data.extend(rows)

    t = Table(data, colWidths=[155, w - 155])
    t.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 12),
        ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_BLUE),

        ("LINEBELOW", (0, 0), (-1, 0), 0.6, lightgrey),

        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 9.7),
        ("TEXTCOLOR", (0, 1), (-1, -1), Color(0.1, 0.1, 0.1)),

        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),

        ("VALIGN", (0, 0), (-1, -1), "TOP"),

        ("BOX", (0, 0), (-1, -1), 1.0, BRAND_ORANGE),
    ]))
    tw, th = t.wrapOn(c, w, 9999)
    t.drawOn(c, x, y - th)
    return y - th - 8


def _build_policy_table(w):
    data = [
        ["Policy", "Time / Detail"],
        ["Standard Check-in Time:", "3:00 PM"],
        ["Standard Check-out Time:", "12:00 PM"],
        ["Early Check-in/Late Out:", "Subject to availability. Request upon arrival."],
        ["Required at Check-in:", "Passport & Credit Card/Cash Deposit."],
    ]
    t = Table(data, colWidths=[170, w - 170])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.6),

        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 7.6),

        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),

        ("VALIGN", (0, 0), (-1, -1), "TOP"),

        ("GRID", (0, 0), (-1, -1), 0.5, BRAND_ORANGE),
        ("BOX", (0, 0), (-1, -1), 1.0, BRAND_ORANGE),
    ]))
    return t


def _build_tnc_table(w, lead_guest_name, font_size=7.0, leading=8.6):
    styles = getSampleStyleSheet()
    tnc_style = ParagraphStyle(
        "tnc",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=font_size,
        leading=leading,
        textColor=black,
        spaceAfter=1,
    )

    lines = [
        "1. Voucher Validity: This voucher is for the dates and services specified above. It must be presented at the hotel's front desk upon arrival.",
        f"2. Identification: The lead guest, {lead_guest_name}, must be present at check-in and must present valid government-issued photo identification (e.g., Passport).",
        '3. No-Show Policy: In the event of a "no-show" (failure to check in without prior cancellation), the hotel reserves the right to charge a fee, typically equivalent to the full cost of the stay.',
        "4. Payment/Incidental Charges: The reservation includes the room and breakfast as specified. Any other charges (e.g., mini-bar, laundry, extra services, parking) must be settled by the guest directly with the hotel upon check-out.",
        "5. Occupancy: The room is confirmed for the number of guests mentioned above. Any change in occupancy must be approved by the hotel and may result in additional charges.",
        "6. Hotel Rights: The hotel reserves the right to refuse admission or request a guest to leave for inappropriate conduct or failure to follow hotel policies.",
        "7. Liability: The hotel is not responsible for the loss or damage of personal belongings, including valuables, unless they are deposited in the hotel's safety deposit box (if available).",
        "8. Reservation Non-Transferable: This booking is non-transferable and may not be resold.",
        "9. City Tax: City tax (if any) is not included and must be paid and settled directly at the hotel.",
        "10. Bed Type: Bed type is subject to availability and cannot be guaranteed.",
    ]

    rows = [[Paragraph(ln, tnc_style)] for ln in lines]
    t = Table(rows, colWidths=[w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (-1, -1), 1.0, BRAND_ORANGE),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, Color(0.82, 0.82, 0.82)),
    ]))
    return t


def generate_pdf_locked(data, hotel_info, rooms_list, imgs):
    """
    One page per room voucher.
    Ensures content never crosses footer. Auto-shrinks T&C to fit.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4

    left = 40
    right = w - 40
    top = h - 40
    content_w = right - left

    styles = getSampleStyleSheet()
    addr_style = ParagraphStyle(
        "addr",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.7,
        leading=11.5,
        textColor=Color(0.1, 0.1, 0.1),
    )

    for idx, room in enumerate(rooms_list):
        if idx > 0:
            c.showPage()

        y = top

        # Header + images
        y = _draw_centered_header(c, w, y)
        y = _draw_image_row(c, left, y, content_w, imgs)

        # Prepare hotel fields
        addr1 = (hotel_info.get("addr1") or "").strip()
        addr2 = (hotel_info.get("addr2") or "").strip()
        phone = (hotel_info.get("phone") or "").strip()

        addr_html = ""
        if addr1:
            addr_html += addr1
        if addr2:
            addr_html += ("<br/>" if addr_html else "") + addr2
        addr_para = Paragraph(addr_html, addr_style) if addr_html else ""

        nights = max((data["checkout"] - data["checkin"]).days, 1)

        # Sections in boxes (compact + bold)
        y = _boxed_compact_section(
            c, left, y, content_w, "Guest Information",
            [
                ["Guest Name:", room["guest"]],
                ["Confirmation No.:", room["conf"]],
                ["Booking Date:", data["booking_date"].strftime("%d %b %Y")],
            ],
        )

        y = _boxed_compact_section(
            c, left, y, content_w, "Hotel Details",
            [
                ["Hotel:", data["hotel"]],
                ["Address:", addr_para],
                ["Phone:", phone],
                ["Check-In:", data["checkin"].strftime("%d %b %Y")],
                ["Check-Out:", data["checkout"].strftime("%d %b %Y")],
                ["Nights:", str(nights)],
            ],
        )

        y = _boxed_compact_section(
            c, left, y, content_w, "Room Information",
            [
                ["Room Type:", data["room_type"]],
                ["No. of Pax:", f'{data["adults"]} Adults'],
                ["Meal Plan:", data["meal_plan"]],
                ["Cancellation:", data["cancellation"]],
            ],
        )

        # (1) REQUIRED GAP between Cancellation and Policy title
        y -= 10

        # Policy title
        c.setFillColor(BRAND_BLUE)
        c.setFont("Helvetica-Bold", 10.6)
        c.drawString(left, y, "HOTEL CHECK-IN & CHECK-OUT POLICY")
        y -= 10

        policy_table = _build_policy_table(content_w)
        pw, ph = policy_table.wrapOn(c, content_w, 9999)
        policy_table.drawOn(c, left, y - ph)
        y = y - ph - 10

        # TNC title
        c.setFillColor(BRAND_BLUE)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(left, y, "STANDARD HOTEL BOOKING TERMS & CONDITIONS")
        y -= 8

        # Fit TNC to 1 page above footer: try multiple sizes until it fits
        lead_guest = room["guest"].split(",")[0].strip() if room["guest"] else "Guest"

        candidates = [
            (7.0, 8.6),
            (6.7, 8.2),
            (6.4, 7.8),
            (6.1, 7.5),
            (5.9, 7.2),
            (5.7, 7.0),
        ]

        chosen = None
        chosen_th = None
        chosen_table = None

        available_height = y - MIN_CONTENT_Y
        for fs, ld in candidates:
            tnc_table = _build_tnc_table(content_w, lead_guest, font_size=fs, leading=ld)
            tw, th = tnc_table.wrapOn(c, content_w, 9999)
            if th <= available_height:
                chosen = (fs, ld)
                chosen_th = th
                chosen_table = tnc_table
                break

        # If still doesn't fit, force the smallest and we will reduce padding by drawing slightly higher
        if chosen_table is None:
            fs, ld = candidates[-1]
            chosen_table = _build_tnc_table(content_w, lead_guest, font_size=fs, leading=ld)
            tw, chosen_th = chosen_table.wrapOn(c, content_w, 9999)
            # If even now it goes over, clamp y so bottom sits exactly at MIN_CONTENT_Y
            y = MIN_CONTENT_Y + chosen_th

        # Draw TNC table
        chosen_table.drawOn(c, left, y - chosen_th)
        y = y - chosen_th - 6

        # Footer: line + seal + issuer
        draw_vector_seal(c, w - 130, 45)
        c.setStrokeColor(BRAND_ORANGE)
        c.setLineWidth(2)
        c.line(0, FOOTER_LINE_Y, w, FOOTER_LINE_Y)

        c.setFillColor(BRAND_BLUE)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(left, 25, f"Issued by: {COMPANY_NAME}")
        c.setFont("Helvetica-Bold", 8)
        c.drawString(left, 13, f"Email: {COMPANY_EMAIL}")

    c.save()
    buffer.seek(0)
    return buffer


# =====================================
# 8) STREAMLIT UI
# =====================================
st.title("🌏 Odaduu Voucher Generator (Locked Template)")

if st.button("🔄 Reset App"):
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()

with st.expander("📤 Upload Supplier Voucher (PDF)", expanded=True):
    up_file = st.file_uploader("Drop PDF here", type="pdf")
    if up_file and st.session_state.last_uploaded_file != up_file.name:
        with st.spinner("Reading PDF..."):
            parsed = extract_pdf_data(up_file)
            if parsed:
                st.session_state.hotel_name = parsed.get("hotel_name", "") or ""
                st.session_state.city = parsed.get("city", "") or ""

                d_in = parse_smart_date(parsed.get("checkin_raw"))
                if d_in:
                    st.session_state.checkin = d_in

                d_out = parse_smart_date(parsed.get("checkout_raw"))
                if d_out:
                    st.session_state.checkout = d_out

                st.session_state.meal_plan = parsed.get("meal_plan", "Breakfast Only") or "Breakfast Only"
                st.session_state.ai_room_str = clean_extracted_text(parsed.get("room_type", ""))

                rooms = parsed.get("rooms", []) or []
                if rooms:
                    st.session_state.num_rooms = min(len(rooms), 50)
                    for i, r in enumerate(rooms[:50]):
                        st.session_state[f"room_{i}_conf"] = str(r.get("confirmation_no", "") or "")
                        st.session_state[f"room_{i}_guest"] = str(r.get("guest_name", "") or "")

                if st.session_state.ai_room_str:
                    st.session_state.room_final = st.session_state.ai_room_str

                if st.session_state.hotel_name and not st.session_state.city:
                    st.session_state.city = detect_city(st.session_state.hotel_name)

                if st.session_state.hotel_name:
                    # Fetch room types for dropdown
                    if st.session_state.city:
                        st.session_state.fetched_room_types = fetch_real_room_types(
                            st.session_state.hotel_name, st.session_state.city
                        )
                    # Fetch images (exterior/lobby/room)
                    st.session_state.hotel_images = get_smart_images(st.session_state.hotel_name, st.session_state.city)

                st.session_state.last_uploaded_file = up_file.name
                st.success("PDF Loaded!")
                st.rerun()
            else:
                st.error("Could not extract details from PDF. Please fill manually.")


c1, c2 = st.columns(2)

with c1:
    st.subheader("1) Hotel Search")
    st.text_input("Enter Keyword (e.g. 'Atlantis')", key="hotel_search_query")

    if st.button("🔎 Search"):
        with st.spinner("Searching..."):
            st.session_state.found_hotels = find_hotel_options(st.session_state.hotel_search_query)
            if not st.session_state.found_hotels:
                st.warning("No hotels found.")

    if st.session_state.found_hotels:
        selected = st.selectbox("Select Hotel", st.session_state.found_hotels)
        if selected and selected != st.session_state.hotel_name:
            st.session_state.hotel_name = selected
            st.session_state.city = detect_city(selected)
            st.session_state.fetched_room_types = fetch_real_room_types(selected, st.session_state.city)
            st.session_state.hotel_images = get_smart_images(st.session_state.hotel_name, st.session_state.city)

    st.text_input("Final Hotel Name", key="hotel_name")
    st.text_input("City", key="city")

    st.subheader("2) Guest Details")
    input_mode = st.radio("Mode", ["Manual", "Bulk CSV"], horizontal=True)

    if input_mode == "Manual":
        n = st.number_input("No. of Rooms", 1, 50, key="num_rooms")
        same_conf = st.checkbox("Same Conf No?", key="same_conf_check")

        if same_conf:
            for i in range(1, n):
                st.session_state[f"room_{i}_conf"] = ""

        for i in range(n):
            col_a, col_b = st.columns([2, 1])
            col_a.text_input(f"Room {i+1} Guest(s)", key=f"room_{i}_guest", help="Multiple names allowed")
            if i == 0:
                col_b.text_input("Conf No", key=f"room_{i}_conf")
            elif not same_conf:
                col_b.text_input("Conf No", key=f"room_{i}_conf")
    else:
        st.info("Upload CSV with columns: 'Guest Name' and 'Confirmation No'")
        f = st.file_uploader("CSV", type="csv")
        if f:
            df = pd.read_csv(f)
            st.session_state.bulk_data = df.to_dict("records")


with c2:
    st.subheader("3) Stay Details")

    if st.session_state.checkout <= st.session_state.checkin:
        st.session_state.checkout = st.session_state.checkin + timedelta(days=1)

    c2a, c2b = st.columns(2)
    c2a.date_input("Check-In", key="checkin")
    c2b.date_input("Check-Out", key="checkout", min_value=st.session_state.checkin + timedelta(days=1))

    room_opts = []
    if st.session_state.ai_room_str:
        room_opts.append(st.session_state.ai_room_str)
    if st.session_state.fetched_room_types:
        room_opts.extend(st.session_state.fetched_room_types)
    room_opts.append("Manual Entry...")

    sel = st.selectbox("Room Type (suggested)", room_opts)

    if not st.session_state.room_final:
        st.session_state.room_final = "" if sel == "Manual Entry..." else sel

    if sel != "Manual Entry..." and st.session_state.room_final in ["", st.session_state.ai_room_str] + st.session_state.fetched_room_types:
        st.session_state.room_final = sel

    st.text_input("Final Room Name", key="room_final")

    st.number_input("Adults", 1, key="adults")
    st.selectbox("Meal Plan", ["Breakfast Only", "Room Only", "Half Board", "Full Board"], key="meal_plan")

    if st.radio("Cancellation Type", ["Non-Refundable", "Refundable"], horizontal=True) == "Refundable":
        d = st.number_input("Free Cancel Days", 0, 30, 3)
        cancellation = f"Free Cancel until {(st.session_state.checkin - timedelta(days=d)).strftime('%d %b %Y')}"
    else:
        cancellation = "Non-Refundable & Non-Amendable"


st.divider()

if st.button("Generate Vouchers", type="primary"):
    with st.spinner("Generating..."):
        rooms = []

        if input_mode == "Manual":
            base_conf = st.session_state.get("room_0_conf", "")
            for i in range(int(st.session_state.num_rooms)):
                conf_val = base_conf if st.session_state.same_conf_check else st.session_state.get(f"room_{i}_conf", "")
                guest_val = st.session_state.get(f"room_{i}_guest", "")
                if str(guest_val).strip() or str(conf_val).strip():
                    rooms.append({"guest": str(guest_val).strip(), "conf": str(conf_val).strip()})
        else:
            for r in st.session_state.bulk_data or []:
                guest_val = str(r.get("Guest Name", "") or "").strip()
                conf_val = str(r.get("Confirmation No", "") or "").strip()
                if guest_val or conf_val:
                    rooms.append({"guest": guest_val, "conf": conf_val})

        if not rooms:
            st.error("No room/guest data found. Please enter at least 1 guest.")
            st.stop()

        info = fetch_hotel_details_text(st.session_state.hotel_name, st.session_state.city)
        imgs = st.session_state.hotel_images if any(st.session_state.hotel_images) else get_smart_images(
            st.session_state.hotel_name, st.session_state.city
        )

        pdf = generate_pdf_locked(
            data={
                "hotel": st.session_state.hotel_name,
                "checkin": st.session_state.checkin,
                "checkout": st.session_state.checkout,
                "room_type": st.session_state.room_final,
                "adults": st.session_state.adults,
                "meal_plan": st.session_state.meal_plan,
                "cancellation": cancellation,
                "booking_date": datetime.now().date(),
            },
            hotel_info=info,
            rooms_list=rooms,
            imgs=imgs,
        )

        st.success("Done!")
        st.download_button("Download", pdf, "Vouchers.pdf", "application/pdf")
