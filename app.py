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
# 1) STREAMLIT CONFIG & BRANDING
# =====================================
st.set_page_config(page_title="Odaduu Voucher Tool", page_icon="🌏", layout="wide")

BRAND_BLUE = Color(0.05, 0.20, 0.40)
BRAND_ORANGE = Color(0.95, 0.42, 0.13)
COMPANY_NAME = "Odaduu Travel DMC"
COMPANY_EMAIL = "aashwin@odaduu.jp"
LOGO_FILE = "logo.png"

FOOTER_LINE_Y = 40
FOOTER_RESERVED_HEIGHT = 110
MIN_CONTENT_Y = FOOTER_LINE_Y + FOOTER_RESERVED_HEIGHT

try:
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
    SEARCH_KEY = st.secrets["SEARCH_API_KEY"]
    SEARCH_CX = st.secrets["SEARCH_ENGINE_ID"]
    genai.configure(api_key=GEMINI_KEY)
except Exception:
    st.error("⚠️ Secrets not found! Please check Streamlit settings.")

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
# 5) NEW "FROM SCRATCH" PDF ENGINE
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

def _draw_header(c, left, right, y_top):
    # Logo Left
    try: 
        c.drawImage(LOGO_FILE, left, y_top - 40, 100, 40, mask='auto', preserveAspectRatio=True)
    except: 
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 20); c.drawString(left, y_top - 30, "ODADUU")
    
    # Title Right
    c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 16)
    c.drawRightString(right, y_top - 30, "HOTEL CONFIRMATION VOUCHER")
    return y_top - 50

def _draw_image_row(c, x, y, w, imgs):
    valid = [im for im in imgs if im]
    if not valid: return y

    gap = 8; img_h = 78; img_w = (w - (2 * gap)) / 3
    
    for i in range(3):
        im = imgs[i] if i < len(imgs) else None
        if im:
            try: c.drawImage(im, x + i * (img_w + gap), y - img_h, img_w, img_h, preserveAspectRatio=True, anchor='c')
            except: pass
    return y - img_h - 15

def _draw_split_info_block(c, x, y, w, guest_rows, hotel_rows):
    """Draws Guest info on Left and Hotel info on Right (Side-by-Side)"""
    col_w = (w - 10) / 2
    
    # Guest Table (Left)
    g_data = [["Guest Information", ""]]; g_data.extend(guest_rows)
    t_guest = Table(g_data, colWidths=[90, col_w - 90])
    t_guest.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_BLUE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, lightgrey), ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 1, BRAND_ORANGE),
        ("PADDING", (0, 0), (-1, -1), 4)
    ]))
    gw, gh = t_guest.wrapOn(c, col_w, 500)
    
    # Hotel Table (Right)
    h_data = [["Hotel Details", ""]]; h_data.extend(hotel_rows)
    t_hotel = Table(h_data, colWidths=[70, col_w - 70])
    t_hotel.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_BLUE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, lightgrey), ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 1, BRAND_ORANGE),
        ("PADDING", (0, 0), (-1, -1), 4)
    ]))
    hw, hh = t_hotel.wrapOn(c, col_w, 500)
    
    # Draw both
    final_h = max(gh, hh)
    t_guest.drawOn(c, x, y - gh) # Align tops
    t_hotel.drawOn(c, x + col_w + 10, y - hh)
    
    return y - final_h - 10

def _draw_full_width_block(c, x, y, w, title, rows):
    data = [[title, ""]]; data.extend(rows)
    t = Table(data, colWidths=[120, w - 120])
    t.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_BLUE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, lightgrey), ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 1, BRAND_ORANGE),
        ("PADDING", (0, 0), (-1, -1), 4)
    ]))
    tw, th = t.wrapOn(c, w, 500)
    t.drawOn(c, x, y - th)
    return y - th - 10

def _build_policy_table(w):
    data = [
        ["Policy", "Time / Detail"],
        ["Standard Check-in Time:", "3:00 PM"], ["Standard Check-out Time:", "12:00 PM"],
        ["Early Check-in/Late Out:", "Subject to availability. Request upon arrival."],
        ["Required at Check-in:", "Passport & Credit Card/Cash Deposit."]
    ]
    t = Table(data, colWidths=[170, w - 170])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE), ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, BRAND_ORANGE), ("BOX", (0, 0), (-1, -1), 1.0, BRAND_ORANGE),
        ("PADDING", (0, 0), (-1, -1), 4)
    ]))
    return t

def _build_tnc_table(w, lead_guest):
    styles = getSampleStyleSheet()
    s = ParagraphStyle("tnc", parent=styles["Normal"], fontSize=7, leading=8.5, textColor=black)
    lines = [
        "1. Voucher Validity: This voucher is for the dates and services specified above. It must be presented at the hotel's front desk upon arrival.",
        f"2. Identification: The lead guest, {lead_guest}, must be present at check-in and must present valid government-issued photo identification.",
        '3. No-Show Policy: In the event of a "no-show", the hotel reserves the right to charge a fee, typically equivalent to the full cost of the stay.',
        "4. Payment/Incidental Charges: The reservation includes the room and breakfast as specified. Any other charges (e.g., mini-bar, laundry) must be settled by the guest directly.",
        "5. Occupancy: The room is confirmed for the number of guests mentioned above. Any change in occupancy must be approved by the hotel.",
        "6. Hotel Rights: The hotel reserves the right to refuse admission or request a guest to leave for inappropriate conduct.",
        "7. Liability: The hotel is not responsible for the loss or damage of personal belongings unless deposited in the hotel's safety deposit box.",
        "8. Reservation Non-Transferable: This booking is non-transferable and may not be resold.",
        "9. City Tax: City tax (if any) is not included and must be paid and settled directly at the hotel.",
        "10. Bed Type: Bed type is subject to availability and cannot be guaranteed."
    ]
    rows = [[Paragraph(l, s)] for l in lines]
    t = Table(rows, colWidths=[w])
    t.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"), ("BOX", (0,0), (-1,-1), 1.0, BRAND_ORANGE),
        ("PADDING", (0,0), (-1,-1), 2), ("LINEBELOW", (0,0), (-1,-2), 0.25, lightgrey)
    ]))
    return t

def generate_pdf_modern(data, hotel_info, rooms_list, imgs):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    left = 40; right = w - 40; top = h - 40; content_w = right - left
    styles = getSampleStyleSheet()
    addr_style = ParagraphStyle("addr", parent=styles["Normal"], fontSize=9, leading=10, textColor=Color(0.1, 0.1, 0.1))

    for idx, room in enumerate(rooms_list):
        if idx > 0: c.showPage()
        y = top
        y = _draw_header(c, left, right, y)
        y = _draw_image_row(c, left, y, content_w, imgs)

        # Prepare Data for Split Block
        guest_rows = [
            ["Guest Name:", room["guest"]], ["Confirmation No.:", room["conf"]],
            ["Booking Date:", datetime.now().strftime("%d %b %Y")]
        ]
        
        addr_str = f"{hotel_info.get('addr1','')}\n{hotel_info.get('addr2','')}".strip()
        addr_para = Paragraph(addr_str.replace('\n', '<br/>'), addr_style)
        hotel_rows = [
            ["Hotel:", data["hotel"]], ["Address:", addr_para],
            ["Check-In:", data["checkin"].strftime("%d %b %Y")], ["Check-Out:", data["checkout"].strftime("%d %b %Y")]
        ]
        
        # DRAW SPLIT BLOCK (New Feature)
        y = _draw_split_info_block(c, left, y, content_w, guest_rows, hotel_rows)
        
        # Room Block (Full Width)
        r_rows = [
            ["Room Type:", data["room_type"]], ["No. of Pax:", f'{data["adults"]} Adults'],
            ["Meal Plan:", data["meal_plan"]], ["Cancellation:", data["cancellation"]]
        ]
        if data.get("room_size"): r_rows.insert(1, ["Room Size:", data["room_size"]])
        y = _draw_full_width_block(c, left, y, content_w, "Room Information", r_rows)

        y -= 5
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 10.6); c.drawString(left, y, "HOTEL POLICIES"); y -= 10
        pt = _build_policy_table(content_w)
        _, ph = pt.wrapOn(c, content_w, 9999)
        pt.drawOn(c, left, y - ph); y -= (ph + 15)

        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "TERMS & CONDITIONS"); y -= 8
        lead_guest = room["guest"].split(',')[0] if room["guest"] else "Guest"
        tnc = _build_tnc_table(content_w, lead_guest)
        _, th = tnc.wrapOn(c, content_w, 9999)
        
        if y - th < MIN_CONTENT_Y: y = MIN_CONTENT_Y + th # Prevent footer overlap
        tnc.drawOn(c, left, y - th)

        draw_vector_seal(c, w - 130, 45)
        c.setStrokeColor(BRAND_ORANGE); c.setLineWidth(2); c.line(0, 40, w, 40)
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 8); c.drawString(left, 25, f"Issued by: {COMPANY_NAME}"); c.drawString(left, 13, f"Email: {COMPANY_EMAIL}")

    c.save(); buffer.seek(0); return buffer

# =====================================
# 6) UI LOGIC
# =====================================
st.title("🌏 Odaduu Voucher Generator")

if st.button("🔄 Reset"):
    for k in list(st.session_state.keys()): del st.session_state[k]
    st.rerun()

with st.expander("📤 Upload PDF", expanded=True):
    up_file = st.file_uploader("PDF", type="pdf")
    if up_file and st.session_state.last_uploaded_file != up_file.name:
        with st.spinner("Processing..."):
            parsed = extract_pdf_data(up_file)
            if parsed:
                st.session_state.hotel_name = parsed.get("hotel_name", "")
                st.session_state.city = parsed.get("city", "")
                d_in = parse_smart_date(parsed.get("checkin_raw"))
                if d_in: st.session_state.checkin = d_in
                d_out = parse_smart_date(parsed.get("checkout_raw"))
                if d_out: st.session_state.checkout = d_out
                st.session_state.meal_plan = parsed.get("meal_plan", "Breakfast Only")
                st.session_state.ai_room_str = clean_extracted_text(parsed.get("room_type", ""))
                
                rooms = parsed.get("rooms", [])
                if rooms:
                    st.session_state.num_rooms = len(rooms)
                    for i, r in enumerate(rooms):
                        st.session_state[f"room_{i}_conf"] = str(r.get("confirmation_no", ""))
                        st.session_state[f"room_{i}_guest"] = str(r.get("guest_name", ""))
                
                if st.session_state.hotel_name:
                    st.session_state.city = detect_city(st.session_state.hotel_name)
                    st.session_state.fetched_room_types = fetch_real_room_types(st.session_state.hotel_name, st.session_state.city)
                    st.session_state.hotel_images = get_smart_images(st.session_state.hotel_name, st.session_state.city)

                st.session_state.last_uploaded_file = up_file.name
                st.success("Loaded!")
                st.rerun()

c1, c2 = st.columns(2)
with c1:
    q = st.text_input("Search Hotel")
    if st.button("🔎 Search"):
        st.session_state.found_hotels = find_hotel_options(q)
    
    if st.session_state.found_hotels:
        sel = st.selectbox("Select", st.session_state.found_hotels)
        if sel != st.session_state.hotel_name:
            st.session_state.hotel_name = sel
            st.session_state.city = detect_city(sel)
            st.session_state.fetched_room_types = fetch_real_room_types(sel, st.session_state.city)
            st.session_state.hotel_images = get_smart_images(sel, st.session_state.city)
            st.rerun()

    st.text_input("Hotel", key="hotel_name")
    st.text_input("City", key="city")
    
    if st.radio("Mode", ["Manual", "Bulk"]) == "Manual":
        n = st.number_input("Rooms", 1, 50, key="num_rooms")
        same = st.checkbox("Same Conf?", key="same_conf_check")
        for i in range(n):
            c_a, c_b = st.columns([2, 1])
            c_a.text_input(f"Room {i+1} Guest", key=f"room_{i}_guest")
            val = st.session_state.get(f"room_{0}_conf",'') if same and i>0 else st.session_state.get(f"room_{i}_conf",'')
            st.text_input(f"Conf {i+1}", value=val, key=f"room_{i}_conf")
    else:
        f = st.file_uploader("CSV", type="csv")
        if f: st.session_state.bulk_data = pd.read_csv(f).to_dict("records")

with c2:
    if st.session_state.checkout <= st.session_state.checkin: st.session_state.checkout = st.session_state.checkin + timedelta(days=1)
    st.date_input("In", key="checkin"); st.date_input("Out", key="checkout")
    
    opts = st.session_state.fetched_room_types + ["Manual..."]
    if st.session_state.ai_room_str: opts.insert(0, st.session_state.ai_room_str)
    
    s_room = st.selectbox("Room Type", opts)
    if not st.session_state.room_final: st.session_state.room_final = s_room
    if s_room != "Manual..." and s_room != st.session_state.room_final: st.session_state.room_final = s_room
    
    st.text_input("Final Room Name", key="room_final")
    st.number_input("Adults", 1, key="adults")
    st.selectbox("Meal", ["Breakfast Only", "Room Only", "Half Board", "Full Board"], key="meal_plan")
    
    pol = "Non-Refundable"
    if st.radio("Policy", ["Non-Ref", "Ref"], horizontal=True) == "Ref":
        d = st.number_input("Days", 3)
        pol = f"Free Cancel until {(st.session_state.checkin - timedelta(days=d)).strftime('%d %b %Y')}"

if st.button("Generate Voucher", type="primary"):
    with st.spinner("Processing..."):
        rooms = []
        if not st.session_state.bulk_data:
            for i in range(st.session_state.num_rooms):
                rooms.append({"guest": st.session_state.get(f"room_{i}_guest", ""), "conf": st.session_state.get(f"room_{i}_conf", "")})
        else:
            for r in st.session_state.bulk_data:
                rooms.append({"guest": str(r.get("Guest Name", "")), "conf": str(r.get("Confirmation No", ""))})
        
        if rooms:
            info = fetch_hotel_details_text(st.session_state.hotel_name, st.session_state.city, st.session_state.room_final)
            imgs = st.session_state.hotel_images if any(st.session_state.hotel_images) else get_smart_images(st.session_state.hotel_name, st.session_state.city)
            
            pdf = generate_pdf_modern({
                "hotel": st.session_state.hotel_name, "checkin": st.session_state.checkin, "checkout": st.session_state.checkout,
                "room_type": st.session_state.room_final, "adults": st.session_state.adults, "meal_plan": st.session_state.meal_plan,
                "cancellation": pol
            }, info, rooms, imgs)
            
            st.success("Done!")
            st.download_button("Download", pdf, "Voucher.pdf", "application/pdf")
