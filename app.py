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
import json
import pypdf
import textwrap
from math import sin, cos, radians
import re

# =====================================
# 1) STREAMLIT CONFIG & BRANDING
# =====================================
st.set_page_config(page_title="Odaduu Voucher Generator", page_icon="妾", layout="wide")

# Branding Colors
BRAND_BLUE = Color(0.05, 0.20, 0.40)
BRAND_ORANGE = Color(0.95, 0.42, 0.13) # Odaduu Orange

COMPANY_NAME = "Odaduu Travel DMC"
COMPANY_EMAIL = "aashwin@odaduu.jp"
LOGO_FILE = "logo.png"

# Layout Constants
FOOTER_LINE_Y = 40
FOOTER_RESERVED_HEIGHT = 110
MIN_CONTENT_Y = FOOTER_LINE_Y + FOOTER_RESERVED_HEIGHT

try:
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
    SEARCH_KEY = st.secrets["SEARCH_API_KEY"]
    SEARCH_CX = st.secrets["SEARCH_ENGINE_ID"]
    genai.configure(api_key=GEMINI_KEY)
except Exception:
    st.error("笞ｸSecrets not found! Please check your Streamlit settings.")
    st.stop()

# =====================================
# 2) SESSION STATE MANAGEMENT
# =====================================
def init_state():
    defaults = {
        'hotel_name': '', 'city': '', 'lead_guest': '', 
        'checkin': datetime.now().date(), 
        'checkout': datetime.now().date() + timedelta(days=1),
        'num_rooms': 1, 'room_type': '', 'adults': 2, 
        'meal_plan': 'Breakfast Only',
        'policy_type': 'Non-Refundable', 
        'cancel_days': 3, 
        'room_size': '',
        'room_options': [], 'suggestions': [], 'last_uploaded_file': None,
        'policy_text_manual': '',
        'search_query': '',
        'room_sel': ''
    }
    
    for i in range(10):
        defaults[f'room_{i}_guest'] = ''
        defaults[f'room_{i}_conf'] = ''

    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

init_state()

def reset_booking_state():
    """Hard reset of all data fields."""
    st.session_state.hotel_name = ''
    st.session_state.city = ''
    st.session_state.num_rooms = 1
    st.session_state.room_type = ''
    st.session_state.room_size = ''
    st.session_state.room_options = []
    st.session_state.policy_text_manual = ''
    st.session_state.suggestions = []
    st.session_state.room_sel = '' 
    
    for i in range(10):
        st.session_state[f'room_{i}_guest'] = ''
        st.session_state[f'room_{i}_conf'] = ''
    
    if 'search_input' in st.session_state:
        st.session_state.search_input = ""
    st.session_state.search_query = ""

# =====================================
# 3) HELPER FUNCTIONS
# =====================================

def parse_smart_date(date_str):
    if not date_str: return None
    clean_str = date_str.strip()
    clean_str = re.sub(r'\bSept\b', 'Sep', clean_str, flags=re.IGNORECASE)
    clean_str = re.sub(r'\bSeptember\b', 'Sep', clean_str, flags=re.IGNORECASE)
    formats = ["%d %b %Y", "%Y-%m-%d", "%d %B %Y"]
    for fmt in formats:
        try: return datetime.strptime(clean_str, fmt).date()
        except ValueError: continue
    return None

def clean_room_type_string(raw_type):
    if not isinstance(raw_type, str): return str(raw_type)
    if raw_type.strip().startswith(('{', '[')) and raw_type.strip().endswith(('}', ']')):
        try:
            temp_data = json.loads(raw_type)
            if isinstance(temp_data, dict): raw_type = list(temp_data.values())[0]
            elif isinstance(temp_data, list) and temp_data: raw_type = temp_data[0]
        except json.JSONDecodeError: pass
    return str(raw_type).strip().strip('\'"{}[] ')

# =====================================
# 4) AI & SEARCH FUNCTIONS
# =====================================

def get_hotel_suggestions(query):
    model = genai.GenerativeModel('gemini-2.0-flash')
    try:
        res = model.generate_content(f'Return JSON list of 3 official hotel names for: "{query}". JSON ONLY: ["Name 1", "Name 2"]').text
        return json.loads(res.replace("```json", "").replace("```", "").strip())
    except: return []

def detect_city(hotel_name):
    model = genai.GenerativeModel('gemini-2.0-flash')
    try: return model.generate_content(f'What city is "{hotel_name}" in? Return ONLY city name string.').text.strip()
    except: return ""

def get_room_types(hotel_name):
    model = genai.GenerativeModel('gemini-2.0-flash')
    try:
        res = model.generate_content(f'List 10 room names for "{hotel_name}". Return JSON list strings.').text
        return json.loads(res.replace("```json", "").replace("```", "").strip())
    except: return []

def extract_pdf_data(pdf_file):
    try:
        pdf_reader = pypdf.PdfReader(pdf_file)
        text = "\n".join([p.extract_text() for p in pdf_reader.pages])
        model = genai.GenerativeModel('gemini-2.0-flash')
        prompt = f"""Extract booking details. CRITICAL: Return DATES as they appear. Look for 'Room 1', 'Room 2'.
        Text Snippet: {text[:25000]}
        Return JSON: {{ "hotel_name": "Name", "city": "City", "checkin_raw": "String", "checkout_raw": "String", "meal_plan": "Plan", "is_refundable": true/false, "cancel_deadline_raw": "String", "room_size": "String", "rooms": [ {{"guest_name": "G1", "confirmation_no": "C1", "room_type": "T1", "adults": 2}} ] }}"""
        raw = model.generate_content(prompt).text
        return json.loads(raw.replace("```json", "").replace("```", "").strip())
    except: return None

def fetch_hotel_details_text(hotel, city, r_type):
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f'Get details for: "{hotel}" in "{city}". Return JSON: {{ "addr1": "Street", "addr2": "City/Zip", "phone": "Intl", "in": "3:00 PM", "out": "12:00 PM" }}'
    try: return json.loads(model.generate_content(prompt).text.replace("```json", "").replace("```", "").strip())
    except: return {}

def fetch_image(query):
    try:
        res = requests.get("https://www.googleapis.com/customsearch/v1", 
                           params={"q": query, "cx": SEARCH_CX, "key": SEARCH_KEY, "searchType": "image", "num": 1, "imgSize": "large", "safe": "active"})
        return res.json()["items"][0]["link"]
    except: return None

def get_img_reader(url):
    if not url: return None
    try:
        r = requests.get(url, timeout=4)
        if r.status_code == 200: return ImageReader(io.BytesIO(r.content))
    except: return None

# =====================================
# 5) NEW PDF GENERATION ENGINE
# =====================================

def draw_vector_seal(c, x, y):
    """Draws the official Odaduu Seal."""
    c.saveState()
    c.setStrokeColor(BRAND_BLUE); c.setFillColor(BRAND_BLUE); c.setFillAlpha(0.9); c.setLineWidth(1.5)
    
    cx, cy = x + 40, y + 40
    c.circle(cx, cy, 40, stroke=1, fill=0)
    c.setLineWidth(0.5); c.circle(cx, cy, 36, stroke=1, fill=0)
    
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(cx, cy + 4, "ODADUU")
    c.setFont("Helvetica-Bold", 7); c.drawCentredString(cx, cy - 6, "TRAVEL DMC")
    
    c.setFont("Helvetica-Bold", 6)
    # Top Arc text
    text_top = "CERTIFIED VOUCHER"; angle_start = 140
    for i, char in enumerate(text_top):
        angle = angle_start - (i * 10); rad = radians(angle)
        tx = cx + 32 * cos(rad); ty = cy + 32 * sin(rad)
        c.saveState(); c.translate(tx, ty); c.rotate(angle - 90); c.drawCentredString(0, 0, char); c.restoreState()
        
    # Bottom Arc text
    text_bot = "OFFICIAL"; angle_start = 240
    for i, char in enumerate(text_bot):
        angle = angle_start + (i * 12); rad = radians(angle)
        tx = cx + 32 * cos(rad); ty = cy + 32 * sin(rad)
        c.saveState(); c.translate(tx, ty); c.rotate(angle + 90); c.drawCentredString(0, 0, char); c.restoreState()
    c.restoreState()

def _draw_centered_header(c, w, y_top):
    logo_w, logo_h = 240, 60
    logo_x = (w - logo_w) / 2
    logo_y = y_top - logo_h

    try:
        c.drawImage(LOGO_FILE, logo_x, logo_y, logo_w, logo_h, mask="auto", preserveAspectRatio=True)
    except Exception:
        c.setFillColor(BRAND_BLUE)
        c.setFont("Helvetica-Bold", 24); c.drawCentredString(w / 2, y_top - 35, "odaduu")
        c.setFont("Helvetica-Bold", 10); c.drawCentredString(w / 2, y_top - 50, "Travel DMC")

    title_y = logo_y - 35
    c.setFillColor(BRAND_BLUE)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(w / 2, title_y, "HOTEL CONFIRMATION VOUCHER")
    return title_y - 18

def _draw_image_row(c, x, y, w, imgs):
    valid = [im for im in imgs if im is not None]
    if not valid: return y

    gap = 8; img_h = 78; img_w = (w - (2 * gap)) / 3
    
    for i in range(3):
        im = imgs[i] if i < len(imgs) else None
        if im:
            try: c.drawImage(im, x + i * (img_w + gap), y - img_h, img_w, img_h, preserveAspectRatio=True, anchor='c')
            except: pass
    return y - img_h - 10

def _boxed_compact_section(c, x, y, w, title, rows):
    data = [[title, ""]]; data.extend(rows)
    t = Table(data, colWidths=[155, w - 155])
    t.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, 0), 12), ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_BLUE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, lightgrey),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"), ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 9.7), ("TEXTCOLOR", (0, 1), (-1, -1), Color(0.1, 0.1, 0.1)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
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
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE), ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, 0), 8.6),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"), ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 7.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, BRAND_ORANGE), ("BOX", (0, 0), (-1, -1), 1.0, BRAND_ORANGE),
    ]))
    return t

def _build_tnc_table(w, lead_guest_name):
    styles = getSampleStyleSheet()
    tnc_style = ParagraphStyle("tnc", parent=styles["Normal"], fontName="Helvetica", fontSize=6.5, leading=8.0, textColor=black, spaceAfter=1)
    
    lines = [
        "1. Voucher Validity: This voucher is for the dates and services specified above. It must be presented at the hotel's front desk upon arrival.",
        f"2. Identification: The lead guest, {lead_guest_name}, must be present at check-in and must present valid government-issued photo identification.",
        '3. No-Show Policy: In the event of a "no-show", the hotel reserves the right to charge a fee, typically equivalent to the full cost of the stay.',
        "4. Payment/Incidental Charges: Extras (mini-bar, laundry, parking) must be settled by the guest directly with the hotel upon check-out.",
        "5. Occupancy: The room is confirmed for the number of guests mentioned above. Any change must be approved by the hotel.",
        "6. Hotel Rights: The hotel reserves the right to refuse admission for inappropriate conduct.",
        "7. Liability: The hotel is not responsible for loss of valuables unless deposited in the hotel's safety deposit box.",
        "8. Reservation Non-Transferable: This booking is non-transferable and may not be resold.",
        "9. City Tax: City tax (if any) is not included and must be paid directly at the hotel.",
        "10. Bed Type: Bed type is subject to availability and cannot be guaranteed.",
    ]
    rows = [[Paragraph(ln, tnc_style)] for ln in lines]
    t = Table(rows, colWidths=[w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("BOX", (0, 0), (-1, -1), 1.0, BRAND_ORANGE),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, Color(0.82, 0.82, 0.82)),
    ]))
    return t

def generate_pdf_final(data, hotel_info, rooms_list, imgs):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    left = 40; right = w - 40; top = h - 40; content_w = right - left
    styles = getSampleStyleSheet()
    addr_style = ParagraphStyle("addr", parent=styles["Normal"], fontName="Helvetica", fontSize=9.7, leading=11.5, textColor=Color(0.1, 0.1, 0.1))

    for idx, room in enumerate(rooms_list):
        if idx > 0: c.showPage()
        y = top
        y = _draw_centered_header(c, w, y)
        y = _draw_image_row(c, left, y, content_w, imgs)

        addr_str = f"{hotel_info.get('addr1','')}\n{hotel_info.get('addr2','')}".strip()
        addr_para = Paragraph(addr_str.replace('\n', '<br/>'), addr_style) if addr_str else ""
        nights = max((data["checkout"] - data["checkin"]).days, 1)

        y = _boxed_compact_section(c, left, y, content_w, "Guest Information", [
            ["Guest Name:", room["guest"]], ["Confirmation No.:", room["conf"]],
            ["Booking Date:", datetime.now().strftime("%d %b %Y")]
        ])
        y = _boxed_compact_section(c, left, y, content_w, "Hotel Details", [
            ["Hotel:", data["hotel"]], ["Address:", addr_para],
            ["Phone:", hotel_info.get("phone", "")],
            ["Check-In:", data["checkin"].strftime("%d %b %Y")],
            ["Check-Out:", data["checkout"].strftime("%d %b %Y")],
            ["Nights:", str(nights)]
        ])
        
        # Build Room Info rows (Add Size if exists)
        r_rows = [
            ["Room Type:", data["room_type"]], ["No. of Pax:", f'{data["adults"]} Adults'],
            ["Meal Plan:", data["meal_plan"]], ["Cancellation:", data["cancellation"]]
        ]
        if data.get("room_size"): r_rows.insert(1, ["Room Size:", data["room_size"]])
        y = _boxed_compact_section(c, left, y, content_w, "Room Information", r_rows)

        y -= 10
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 10.6); c.drawString(left, y, "HOTEL CHECK-IN & CHECK-OUT POLICY"); y -= 10
        
        pt = _build_policy_table(content_w)
        _, ph = pt.wrapOn(c, content_w, 9999); pt.drawOn(c, left, y - ph); y -= (ph + 10)

        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "STANDARD HOTEL BOOKING TERMS & CONDITIONS"); y -= 8
        
        # TNC Table
        lead_guest = room["guest"].split(',')[0] if room["guest"] else "Guest"
        tnc = _build_tnc_table(content_w, lead_guest)
        _, th = tnc.wrapOn(c, content_w, 9999)
        
        # Logic to ensure footer doesn't overlap
        min_y = FOOTER_LINE_Y + FOOTER_RESERVED_HEIGHT
        if y - th < min_y: y = min_y + th # Push up if needed or overlap slightly better than crash
        
        tnc.drawOn(c, left, y - th)
        
        # Footer
        draw_vector_seal(c, w - 130, 45)
        c.setStrokeColor(BRAND_ORANGE); c.setLineWidth(2); c.line(0, FOOTER_LINE_Y, w, FOOTER_LINE_Y)
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 8); c.drawString(left, 25, f"Issued by: {COMPANY_NAME}"); c.drawString(left, 13, f"Email: {COMPANY_EMAIL}")

    c.save(); buffer.seek(0); return buffer

# =====================================
# 6) UI LOGIC
# =====================================

# ... (Previous UI Code remains exactly the same, but calls generate_pdf_final) ...

# === MANUAL SEARCH ===
st.markdown("### 妾 Hotel Details")
col_s, col_res = st.columns([2,1])
with col_s: 
    search = st.text_input("Search Hotel Name", key="search_input")
    if search and search != st.session_state.search_query:
        st.session_state.search_query = search
        st.session_state.suggestions = get_hotel_suggestions(search)

with col_res:
    if st.session_state.suggestions:
        sel = st.radio("Select:", st.session_state.suggestions, index=None, key="hotel_radio")
        if sel and sel != st.session_state.hotel_name:
            st.session_state.hotel_name = sel
            with st.spinner("Fetching details..."):
                st.session_state.city = detect_city(sel)
                st.session_state.room_options = get_room_types(sel)
            st.rerun()

# === FORM ===
c1, c2 = st.columns(2)
with c1:
    st.text_input("Hotel Name", key="hotel_name")
    st.text_input("City", key="city")
    
    st.subheader("Rooms")
    n_rooms = st.number_input("Count", 1, 10, key="num_rooms")
    same_conf = False
    if n_rooms > 1: same_conf = st.checkbox("Same Confirmation No?", False)
    
    for i in range(n_rooms):
        cols = st.columns(2)
        with cols[0]: st.text_input(f"Guest {i+1}", key=f"room_{i}_guest")
        with cols[1]:
            val = st.session_state.get(f'room_{0}_conf', '') if (same_conf and i > 0) else st.session_state.get(f'room_{i}_conf', '')
            st.text_input(f"Conf {i+1}", value=val, key=f"room_{i}_conf")

    st.subheader("Policy")
    ptype = st.radio("Type", ["Non-Refundable", "Refundable"], horizontal=True, key="policy_type")

with c2:
    curr_in = st.session_state.checkin
    min_out = curr_in + timedelta(days=1)
    if st.session_state.checkout <= curr_in: st.session_state.checkout = min_out

    st.date_input("Check-In", key="checkin")
    st.date_input("Check-Out", key="checkout", min_value=min_out)
    
    opts = st.session_state.room_options.copy()
    current_room_name = st.session_state.room_type
    if current_room_name and current_room_name not in opts: opts.insert(0, current_room_name)
    opts.append("Manual...")
    
    def on_room_change():
        if st.session_state.room_sel != "Manual...": st.session_state.room_type = st.session_state.room_sel

    idx = opts.index(st.session_state.room_sel) if st.session_state.room_sel in opts else 0
    st.selectbox("Room Type", opts, index=idx, key="room_sel", on_change=on_room_change)
    
    if st.session_state.get("room_sel") == "Manual...": 
        st.text_input("Type Name", value=current_room_name, key="room_type")
    else:
        st.session_state.room_type = st.session_state.room_sel

    st.number_input("Adults", 1, key="adults")
    st.text_input("Size (Optional)", key="room_size")
    st.selectbox("Meal", ["Breakfast Only", "Room Only", "Half Board", "Full Board"], key="meal_plan")
    
    policy_txt = "Non-Refundable & Non-Amendable"
    if ptype == "Refundable":
        manual_txt = st.text_input("Policy Description (Optional)", value=st.session_state.policy_text_manual, key="policy_text_manual")
        if manual_txt: policy_txt = manual_txt
        else:
            d = st.number_input("Free Cancel Days Before Check-in", 0, value=st.session_state.cancel_days, key="cancel_days")
            policy_txt = f"Free Cancellation until {(st.session_state.checkin - timedelta(days=d)).strftime('%d %b %Y')}"
            st.info(f"Auto-generated: {policy_txt}")

if st.button("Generate Voucher", type="primary"):
    with st.spinner("Creating PDF..."):
        rooms_final = []
        for i in range(st.session_state.num_rooms):
            rooms_final.append({
                "guest": st.session_state.get(f'room_{i}_guest', ''),
                "conf": st.session_state.get(f'room_{i}_conf', '')
            })
            
        info = fetch_hotel_details_text(st.session_state.hotel_name, st.session_state.city, st.session_state.room_type)
        imgs = get_smart_images(st.session_state.hotel_name, st.session_state.city)
        
        pdf_data = {
            "hotel": st.session_state.hotel_name, "checkin": st.session_state.checkin, "checkout": st.session_state.checkout,
            "room_type": st.session_state.room_type, "adults": st.session_state.adults, "meal_plan": st.session_state.meal_plan,
            "cancellation": policy_txt, "room_size": st.session_state.room_size,
            "booking_date": datetime.now()
        }
        
        # USING NEW ENGINE
        pdf_bytes = generate_pdf_final(pdf_data, info, rooms_final, imgs)
        
    st.success("Done!")
    st.download_button("Download PDF", pdf_bytes, "Voucher.pdf", "application/pdf")
