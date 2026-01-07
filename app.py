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
BRAND_ORANGE = Color(0.97255, 0.29804, 0.0) 
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
        'hotel_search_query': '', 'found_hotels': [], 
        'hotel_name': '', 'city': '', 'lead_guest': '', 
        'checkin': datetime.now().date(), 
        'checkout': datetime.now().date() + timedelta(days=1),
        'num_rooms': 1, 'room_type': '', 'adults': 2, 
        'meal_plan': 'Breakfast Only',
        'policy_type': 'Non-Refundable', 
        'fetched_room_types': [], 'ai_room_str': '',
        'last_uploaded_file': None, 'bulk_data': [],
        'hotel_images': [None, None, None],
        'selected_hotel_key': None,
        'room_size': '',
        'remarks': '',
        'room_final': ''  # <--- CRITICAL FIX: Added missing key
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    for i in range(50):
        if f'room_{i}_guest' not in st.session_state: st.session_state[f'room_{i}_guest'] = ''
        if f'room_{i}_conf' not in st.session_state: st.session_state[f'room_{i}_conf'] = ''

init_state()

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

def fetch_hotel_data_callback():
    """Callback to populate data immediately upon selection."""
    selected_hotel = st.session_state.selected_hotel_key
    if not selected_hotel: return
    
    st.session_state.hotel_name = selected_hotel
    
    # Use Gemini to get City and Room Types from Google Search snippets
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    # 1. City & Room Types
    try:
        search_res = google_search(f"{selected_hotel} location room types")
        snippets = "\n".join([i.get('snippet','') for i in search_res])
        prompt = f"""Based on these search results for "{selected_hotel}":\n{snippets}\n1. Identify the City.\n2. List 3-5 distinct Room Types found.\nReturn JSON: {{ "city": "CityName", "rooms": ["Type A", "Type B"] }}"""
        raw = model.generate_content(prompt).text
        data = json.loads(raw.replace("```json", "").replace("```", "").strip())
        st.session_state.city = data.get("city", "")
        st.session_state.fetched_room_types = data.get("rooms", [])
    except: 
        st.session_state.city = ""
        st.session_state.fetched_room_types = ["Standard", "Deluxe"]

    # 2. Images
    base_q = f"{selected_hotel} {st.session_state.city}"
    st.session_state.hotel_images = [
        fetch_image(f"{base_q} hotel exterior"),
        fetch_image(f"{base_q} hotel lobby"),
        fetch_image(f"{base_q} hotel room")
    ]

def google_search(query, num=5):
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"q": query, "cx": SEARCH_CX, "key": SEARCH_KEY, "num": num}
        res = requests.get(url, params=params, timeout=5)
        if res.status_code != 200:
            st.error(f"Search API Error: {res.status_code}")
            return []
        return res.json().get("items", [])
    except Exception:
        return []

def find_hotel_options(keyword):
    if not keyword: return []
    results = google_search(f"Hotel {keyword} official site")
    hotels = []
    for item in results:
        title = item.get('title', '').split('|')[0].split('-')[0].strip()
        if title and title not in hotels: hotels.append(title)
    return hotels[:5]

def fetch_image(query):
    try:
        res = requests.get("https://www.googleapis.com/customsearch/v1", 
                           params={"q": query, "cx": SEARCH_CX, "key": SEARCH_KEY, "searchType": "image", "num": 1, "imgSize": "large", "safe": "active"})
        return res.json().get("items", [{}])[0].get("link")
    except: return None

def get_img_reader(url):
    if not url: return None
    try:
        r = requests.get(url, timeout=4)
        if r.status_code == 200: return ImageReader(io.BytesIO(r.content))
    except: return None

# =====================================
# 5) PDF GENERATION
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
    text_top = "CERTIFIED VOUCHER"; angle_start = 140
    for i, char in enumerate(text_top):
        angle = angle_start - (i * 10); rad = radians(angle)
        tx = cx + 32 * cos(rad); ty = cy + 32 * sin(rad)
        c.saveState(); c.translate(tx, ty); c.rotate(angle - 90); c.drawCentredString(0, 0, char); c.restoreState()
    text_bot = "OFFICIAL"; angle_start = 240
    for i, char in enumerate(text_bot):
        angle = angle_start + (i * 12); rad = radians(angle)
        tx = cx + 32 * cos(rad); ty = cy + 32 * sin(rad)
        c.saveState(); c.translate(tx, ty); c.rotate(angle + 90); c.drawCentredString(0, 0, char); c.restoreState()
    c.restoreState()

def _draw_header(c, w, y_top):
    # Centered Logo
    logo_w, logo_h = 140, 55
    try: 
        c.drawImage(LOGO_FILE, (w - logo_w)/2, y_top - logo_h, logo_w, logo_h, mask='auto', preserveAspectRatio=True)
    except: 
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 24); c.drawCentredString(w / 2, y_top - 35, "ODADUU")
    
    # Centered Title
    c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(w / 2, y_top - logo_h - 20, "HOTEL CONFIRMATION VOUCHER")
    return y_top - logo_h - 40

def _draw_merged_info_box(c, x, y, w, guest_rows, hotel_rows, room_rows):
    """
    Draws ONE giant box with thick black lines containing two columns:
    Left: Guest Info
    Right: Hotel Details
    Bottom: Room Info (including Conf No, Meal, Nights)
    """
    
    # Left Column Data (Guest Info)
    g_data = [["GUEST INFORMATION", ""]]; g_data.extend(guest_rows)
    t_guest = Table(g_data, colWidths=[90, (w/2) - 100])
    t_guest.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)), ("ALIGN", (0, 0), (-1, 0), "LEFT"),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_BLUE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 0),
    ]))
    
    # Right Column Data (Hotel Info)
    h_data = [["HOTEL DETAILS", ""]]; h_data.extend(hotel_rows)
    t_hotel = Table(h_data, colWidths=[70, (w/2) - 80])
    t_hotel.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)), ("ALIGN", (0, 0), (-1, 0), "LEFT"),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_BLUE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 0),
    ]))

    # Bottom Row Data (Room Info - Full Width)
    r_data_formatted = [["ROOM INFORMATION", ""]] + room_rows
    t_room = Table(r_data_formatted, colWidths=[90, w - 110])
    t_room.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)), ("ALIGN", (0, 0), (-1, 0), "LEFT"),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_BLUE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 0),
    ]))

    # Master Table: 
    # Row 0: Guest | Hotel
    # Row 1: Room (Span 2)
    master_data = [[t_guest, t_hotel], [t_room, ""]]
    
    master_table = Table(master_data, colWidths=[w/2, w/2])
    master_table.setStyle(TableStyle([
        ("SPAN", (0, 1), (1, 1)), # Span Room
        ("BOX", (0, 0), (-1, -1), 1.5, black), 
        ("LINEBELOW", (0, 0), (1, 0), 0.5, lightgrey), 
        ("LINEAFTER", (0, 0), (0, 0), 0.5, lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    
    tw, th = master_table.wrapOn(c, w, 9999)
    master_table.drawOn(c, x, y - th)
    return y - th - 15

def _draw_image_row(c, x, y, w, imgs, scale_factor=1.0):
    valid = [im for im in imgs if im]
    if not valid: return y

    # 0.5 point gap (stable, distinct)
    gap = 0.5 * scale_factor 
    img_w = (w - (2 * gap)) / 3
    img_h = 100 * scale_factor # Standard height
    
    for i in range(min(3, len(valid))):
        im = valid[i]
        try: c.drawImage(im, x + i * (img_w + gap), y - img_h, img_w, img_h, preserveAspectRatio=True, anchor='c')
        except: pass
    return y - img_h - (10 * scale_factor)

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
        ("GRID", (0, 0), (-1, -1), 0.5, black), ("BOX", (0, 0), (-1, -1), 1.0, black),
        ("PADDING", (0, 0), (-1, -1), 4)
    ]))
    return t

def _build_tnc_table(w, lead_guest, font_size=7):
    styles = getSampleStyleSheet()
    s = ParagraphStyle("tnc", parent=styles["Normal"], fontName="Times-Roman", fontSize=font_size, leading=font_size+1.5, textColor=black)
    lines = [
        "• Voucher Validity: This voucher is for the dates and services specified above. It must be presented at the hotel's front desk upon arrival.",
        f"• Identification: The lead guest, {lead_guest}, must be present at check-in and must present valid government-issued photo identification.",
        '• No-Show Policy: In the event of a "no-show", the hotel reserves the right to charge a fee, typically equivalent to the full cost of the stay.',
        "• Payment/Incidental Charges: The reservation includes the room and breakfast as specified. Any other charges (e.g., mini-bar, laundry) must be settled by the guest directly.",
        "• Occupancy: The room is confirmed for the number of guests mentioned above. Any change in occupancy must be approved by the hotel.",
        "• Hotel Rights: The hotel reserves the right to refuse admission or request a guest to leave for inappropriate conduct.",
        "• Liability: The hotel is not responsible for the loss or damage of personal belongings unless deposited in the hotel's safety deposit box.",
        "• Reservation Non-Transferable: This booking is non-transferable and may not be resold.",
        "• City Tax: City tax (if any) is not included and must be paid and settled directly at the hotel.",
        "• Bed Type: Bed type is subject to availability and cannot be guaranteed."
    ]
    rows = [[Paragraph(l, s)] for l in lines]
    t = Table(rows, colWidths=[w])
    t.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"), ("BOX", (0,0), (-1,-1), 1.0, black),
        ("PADDING", (0,0), (-1,-1), 2), ("LINEBELOW", (0,0), (-1,-2), 0.25, lightgrey)
    ]))
    return t

def generate_pdf_final(data, hotel_info, rooms_list, imgs):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    left = 40; right = w - 40; top = h - 40; content_w = right - left
    styles = getSampleStyleSheet()
    
    # Styles
    addr_style = ParagraphStyle("addr", parent=styles["Normal"], fontSize=7.5, leading=9, fontName="Helvetica-Bold", textColor=black)
    remark_style = ParagraphStyle("remark", parent=styles["Normal"], fontSize=7.5, leading=9, fontName="Helvetica-Bold", textColor=black)

    for idx, room in enumerate(rooms_list):
        if idx > 0: c.showPage()
        
        # --- 1. HEADER ---
        y = top
        y = _draw_header(c, w, y)

        # --- PREPARE DATA ---
        guest_p = Paragraph(room["guest"], addr_style)
        room_p = Paragraph(data["room_type"], addr_style)
        remarks_val = data["remarks"] if data["remarks"] else "N/A"
        remarks_p = Paragraph(remarks_val, remark_style)

        # Guest Info
        guest_rows = [
            ["Guest Name:", guest_p],
            ["No. of Pax:", f'{data["adults"]} Adults'],
            ["Cancellation:", data["cancellation"]],
            ["Remarks:", remarks_p]
        ]
        
        # Hotel Info (Removed Voucher Date)
        addr_str = f"{hotel_info.get('addr1','')}\n{hotel_info.get('addr2','')}".strip()
        addr_para = Paragraph(addr_str.replace('\n', '<br/>'), addr_style)
        hotel_name_p = Paragraph(data["hotel"], addr_style)
        hotel_rows = [
            ["Hotel:", hotel_name_p],
            ["Address:", addr_para],
            ["Check-In:", data["checkin"].strftime("%d %b %Y")],
            ["Check-Out:", data["checkout"].strftime("%d %b %Y")],
        ]
        
        # Room Info (Expanded with Meal, Conf, Nights)
        room_rows = [
            ["Room Type:", room_p],
            ["Room Size:", data["room_size"] or "N/A"],
            ["Confirmation No.:", room["conf"]],
            ["Meal Plan:", data["meal_plan"]],
            ["No. of Nights:", str(data["nights"])],
        ]

        # --- AUTO-FIT LOGIC ---
        scale = 1.0
        tnc_font = 7
        
        # 2. MEGA BOX
