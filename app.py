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

# --- 1. SETUP & CONFIGURATION ---
st.set_page_config(page_title="Odaduu Voucher Tool", page_icon="🌏", layout="wide")

# Branding
BRAND_BLUE = Color(0.05, 0.20, 0.40) 
BRAND_GOLD = Color(0.85, 0.70, 0.20)
COMPANY_NAME = "Odaduu Travel DMC"
LOGO_FILE = "logo.png"

try:
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
    SEARCH_KEY = st.secrets["SEARCH_API_KEY"]
    SEARCH_CX = st.secrets["SEARCH_ENGINE_ID"]
    genai.configure(api_key=GEMINI_KEY)
except Exception:
    st.error("⚠️ Secrets not found! Please check Streamlit settings.")

# --- 2. SESSION STATE ---
def init_state():
    defaults = {
        'hotel_search_query': '', 'found_hotels': [], 
        'hotel_name': '', 'city': '', 
        'checkin': datetime.now().date(), 
        'checkout': datetime.now().date() + timedelta(days=1),
        'num_rooms': 1, 'room_type': '', 'adults': 2, 
        'meal_plan': 'Breakfast Only', 'policy_type': 'Non-Refundable', 
        'fetched_room_types': [], 'ai_room_str': '',
        'last_uploaded_file': None, 'bulk_data': [],
        'hotel_images': [None, None, None]
    }
    for k, v in defaults.items():
        if k not in st.session_state: st.session_state[k] = v
    for i in range(50):
        if f'room_{i}_guest' not in st.session_state: st.session_state[f'room_{i}_guest'] = ''
        if f'room_{i}_conf' not in st.session_state: st.session_state[f'room_{i}_conf'] = ''

init_state()

# --- 3. HELPER FUNCTIONS ---
def parse_smart_date(date_str):
    if not date_str: return None
    clean = str(date_str).strip()
    clean = re.sub(r'\bSept\b', 'Sep', clean, flags=re.IGNORECASE)
    for fmt in ["%d %b %Y", "%Y-%m-%d", "%d %B %Y", "%d-%m-%Y"]:
        try: return datetime.strptime(clean, fmt).date()
        except: continue
    return None

def clean_extracted_text(raw_val):
    s = str(raw_val).strip()
    if s.lower() in ["room_name", "room_type"]: return ""
    return s.strip('{}[]"\'')

# --- 4. SEARCH & AI LOGIC ---
def google_search(query, num=5):
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"q": query, "cx": SEARCH_CX, "key": SEARCH_KEY, "num": num}
        res = requests.get(url, params=params, timeout=5)
        return res.json().get("items", []) if res.status_code == 200 else []
    except: return []

def find_hotel_options(keyword):
    results = google_search(f"Hotel {keyword} official site")
    hotels = []
    for item in results:
        title = item.get('title', '').split('|')[0].split('-')[0].strip()
        if title and title not in hotels: hotels.append(title)
    return hotels[:5]

def fetch_image_url(query):
    try:
        res = requests.get("https://www.googleapis.com/customsearch/v1", 
                           params={"q": query, "cx": SEARCH_CX, "key": SEARCH_KEY, "searchType": "image", "num": 1, "safe": "active"})
        return res.json()['items'][0]['link'] if res.status_code == 200 and 'items' in res.json() else None
    except: return None

def fetch_hotel_data_callback():
    """Callback to populate data immediately upon selection."""
    selected_hotel = st.session_state.selected_hotel_key
    if not selected_hotel: return
    
    st.session_state.hotel_name = selected_hotel
    
    # Use Gemini to get City and Room Types from Google Search snippets
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    # 1. City & Room Types
    try:
        # Search for the hotel to get snippets
        search_res = google_search(f"{selected_hotel} location room types")
        snippets = "\n".join([i.get('snippet','') for i in search_res])
        
        # Ask AI to extract details
        prompt = f"""
        Based on these search results for "{selected_hotel}":
        {snippets}
        
        1. Identify the City.
        2. List 3-5 distinct Room Types found.
        
        Return JSON: {{ "city": "CityName", "rooms": ["Type A", "Type B"] }}
        """
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
        fetch_image_url(f"{base_q} hotel exterior"),
        fetch_image_url(f"{base_q} hotel lobby"),
        fetch_image_url(f"{base_q} hotel room")
    ]

def fetch_hotel_details_text(hotel, city):
    model = genai.GenerativeModel('gemini-2.0-flash')
    try:
        res = model.generate_content(f'Get address/phone for "{hotel}" in "{city}". Return JSON: {{ "addr1": "..." }}').text
        return json.loads(res.replace("```json", "").replace("```", "").strip())
    except: return {}

def get_img_reader(url):
    if not url: return None
    try:
        r = requests.get(url, timeout=3)
        return ImageReader(io.BytesIO(r.content)) if r.status_code == 200 else None
    except: return None

def extract_pdf_data(pdf_file):
    try:
        pdf_reader = pypdf.PdfReader(pdf_file)
        text = "\n".join([p.extract_text() for p in pdf_reader.pages])
        model = genai.GenerativeModel('gemini-2.0-flash')
        prompt = f"""Extract booking details. JSON: {{ "hotel_name": "...", "city": "...", "checkin_raw": "...", "checkout_raw": "...", "meal_plan": "...", "rooms": [ {{ "guest_name": "...", "confirmation_no": "...", "room_type": "..." }} ] }} \n\n {text[:15000]}"""
        return json.loads(model.generate_content(prompt).text.replace("```json", "").replace("```", "").strip())
    except: return None

# --- 5. PDF GENERATION ---

def draw_seal(c, x, y):
    c.saveState()
    c.setStrokeColor(BRAND_BLUE); c.setFillColor(BRAND_BLUE); c.setFillAlpha(0.9); c.setLineWidth(1.5)
    c.circle(x+40, y+40, 40, stroke=1, fill=0)
    c.setLineWidth(0.5); c.circle(x+40, y+40, 36, stroke=1, fill=0)
    
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(x+40, y+42, "ODADUU")
    c.setFont("Helvetica-Bold", 7); c.drawCentredString(x+40, y+32, "TRAVEL DMC")
    
    c.setFont("Helvetica-Bold", 6)
    def draw_arc(text, angle, offset):
        for i, char in enumerate(text):
            rad = radians(angle - (i * 10))
            tx = (x+40) + 32 * cos(rad)
            ty = (y+40) + 32 * sin(rad)
            c.saveState()
            c.translate(tx, ty)
            c.rotate((angle - (i * 10)) + offset)
            c.drawCentredString(0, 0, char)
            c.restoreState()
    draw_arc("CERTIFIED VOUCHER", 140, -90)
    draw_arc("OFFICIAL", 235, 90)
    c.restoreState()

def generate_pdf(data, info, imgs, rooms_list):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    left = 40; right = w - 40; width = right - left
    
    images = [get_img_reader(i) for i in imgs]
    
    for i, room in enumerate(rooms_list):
        if i > 0: c.showPage()
        y = h - 40
        
        # 1. HEADER (Left Logo, Right Title)
        try: 
            logo_w, logo_h = 100, 40
            c.drawImage(LOGO_FILE, left, y-40, logo_w, logo_h, mask='auto', preserveAspectRatio=True)
        except: 
            c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 20); c.drawString(left, y-30, "ODADUU")
            
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 16)
        c.drawRightString(right, y - 30, "HOTEL CONFIRMATION VOUCHER")
        y -= 50

        # 2. IMAGES (3 Column Grid)
        if any(images):
            img_w = 160; img_h = 90; gap = 10
            # Center the block of images
            total_w = (img_w * 3) + (gap * 2)
            ix = (w - total_w) / 2
            
            for img in images[:3]:
                if img: 
                    try: c.drawImage(img, ix, y - img_h, img_w, img_h)
                    except: pass
                ix += (img_w + gap)
            y -= (img_h + 15)
        else: y -= 10

        # DATA BLOCKS
        def draw_block(title, rows):
            nonlocal y
            c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 11); c.drawString(left, y, title)
            y -= 4; c.setStrokeColor(lightgrey); c.line(left, y, right, y); y -= 2
            
            t = Table(rows, colWidths=[110, width - 110])
            t.setStyle(TableStyle([
                ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
                ('FONTNAME', (1,0), (1,-1), 'Helvetica'),
                ('FONTSIZE', (0,0), (-1,-1), 9),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ]))
            _, th = t.wrapOn(c, width, 500)
            if y - th < 50: c.showPage(); y = h - 50
            t.drawOn(c, left, y - th)
            y -= (th + 10)

        draw_block("Guest Information", [
            ["Guest Name(s):", room['guest']],
            ["Confirmation No:", room['conf']],
            ["Booking Date:", datetime.now().strftime("%d %b %Y")]
        ])
        
        addr_p = Paragraph(info.get('addr1', ''), getSampleStyleSheet()['Normal'])
        draw_block("Hotel Details", [
            ["Hotel:", data['hotel']],
            ["Address:", addr_p],
            ["Check-In:", data['in'].strftime("%d %b %Y")],
            ["Check-Out:", data['out'].strftime("%d %b %Y")]
        ])
        
        draw_block("Room Information", [
            ["Room Type:", data['room_type']],
            ["Pax:", f"{data['adults']} Adults"],
            ["Meal Plan:", data['meal']],
            ["Cancellation:", data['policy']]
        ])
        
        # 6. POLICIES (Exact Text)
        if y < 150: c.showPage(); y = h - 50
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 11); c.drawString(left, y, "HOTEL POLICIES"); y -= 12
        
        pol_data = [
            ["Standard Check-in Time:", "3:00 PM"],
            ["Standard Check-out Time:", "12:00 PM"],
            ["Early Check-in/Late Out:", "Subject to availability. Request upon arrival."],
            ["Required at Check-in:", "Passport & Credit Card/Cash Deposit."]
        ]
        # Custom header row
        t_data = [["Policy", "Time / Detail"]] + pol_data
        
        pol_table = Table(t_data, colWidths=[140, width - 140])
        pol_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BRAND_BLUE),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('GRID', (0,0), (-1,-1), 0.5, Color(0.3,0.3,0.3)),
            ('PADDING', (0,0), (-1,-1), 4),
        ]))
        _, ph = pol_table.wrapOn(c, width, 500)
        pol_table.drawOn(c, left, y - ph)
        y -= (ph + 15)
        
        # 7. T&C (Boxed, Exact Text)
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "STANDARD HOTEL BOOKING TERMS & CONDITIONS"); y -= 12
        
        tnc_text = [
            "1. Voucher Validity: This voucher is for the dates and services specified above. It must be presented at the hotel's front desk upon arrival.",
            f"2. Identification: The lead guest, {room['guest']}, must be present at check-in and must present valid government-issued photo identification (e.g., Passport).",
            "3. No-Show Policy: In the event of a \"no-show\" (failure to check in without prior cancellation), the hotel reserves the right to charge a fee, typically equivalent to the full cost of the stay.",
            "4. Payment/Incidental Charges: The reservation includes the room and breakfast as specified. Any other charges (e.g., mini-bar, laundry, extra services, parking) must be settled by the guest directly with the hotel upon check-out.",
            f"5. Occupancy: The room is confirmed for {data['adults']} Adults. Any change in occupancy must be approved by the hotel and may result in additional charges.",
            "6. Hotel Rights: The hotel reserves the right to refuse admission or request a guest to leave for inappropriate conduct or failure to follow hotel policies.",
            "7. Liability: The hotel is not responsible for the loss or damage of personal belongings, including valuables, unless they are deposited in the hotel's safety deposit box (if available).",
            "8. Reservation Non-Transferable: This booking is non-transferable and may not be resold.",
            "9. City Tax: City tax (if any) is not included and must be paid and settled directly at the hotel.",
            "10. Bed Type: Bed type is subject to availability and cannot be guaranteed."
        ]
        
        styleN = getSampleStyleSheet()['Normal']
        styleN.fontSize = 7; styleN.leading = 9
        tnc_rows = [[Paragraph(line, styleN)] for line in tnc_text]
        
        tnc_table = Table(tnc_rows, colWidths=[width])
        tnc_table.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 0.5, black), # Boxed
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('PADDING', (0,0), (-1,-1), 2),
        ]))
        
        _, th = tnc_table.wrapOn(c, width, 500)
        if y - th < 50: c.showPage(); y = h - 50
        tnc_table.drawOn(c, left, y - th)
        y -= (th + 10)
            
        # 8. FOOTER
        draw_seal(c, w-130, 40)
        c.setStrokeColor(BRAND_GOLD); c.setLineWidth(3); c.line(0, 40, w, 40)
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 8); c.drawString(left, 25, f"Issued by: {COMPANY_NAME}")
        
    c.save(); buffer.seek(0); return buffer

# --- 6. UI LOGIC ---
st.title("🌏 Odaduu Voucher Generator")

c1, c2 = st.columns(2)

with c1:
    st.subheader("1. Hotel")
    q = st.text_input("Search Hotel Name")
    if st.button("🔎 Search"):
        st.session_state.found_hotels = find_hotel_options(q)
    
    if st.session_state.found_hotels:
        st.selectbox(
            "Select Hotel", 
            st.session_state.found_hotels, 
            key="selected_hotel_key", 
            on_change=fetch_hotel_data_callback
        )
        
    st.text_input("Final Hotel Name", key="hotel_name")
    st.text_input("City", key="city")
    
    st.subheader("2. Guest")
    if st.radio("Mode", ["Manual", "Bulk CSV"], horizontal=True) == "Manual":
        n = st.number_input("Rooms", 1, 50, key="num_rooms")
        same = st.checkbox("Same Conf?", key="same_conf_check")
        for i in range(n):
            c_a, c_b = st.columns([2, 1])
            c_a.text_input(f"Room {i+1} Guest(s)", key=f"room_{i}_guest")
            if i==0 or not same: c_b.text_input("Conf", key=f"room_{i}_conf")
    else:
        f = st.file_uploader("CSV", type="csv")
        if f: st.session_state.bulk_data = pd.read_csv(f).to_dict('records')

with c2:
    st.subheader("3. Stay")
    if st.session_state.checkout <= st.session_state.checkin:
        st.session_state.checkout = st.session_state.checkin + timedelta(days=1)
    
    ca, cb = st.columns(2)
    ca.date_input("Check-In", key="checkin")
    cb.date_input("Check-Out", key="checkout", min_value=st.session_state.checkin + timedelta(days=1))
    
    # Room Type
    opts = st.session_state.fetched_room_types + ["Manual Entry..."]
    if st.session_state.ai_room_str: opts.insert(0, st.session_state.ai_room_str)
    
    sel = st.selectbox("Room Type", opts)
    room_final = st.text_input("Final Room Name", value="" if sel == "Manual Entry..." else sel)
    
    st.number_input("Adults", 1, key="adults")
    st.selectbox("Meal", ["Breakfast Only", "Room Only", "Half Board", "Full Board"], key="meal_plan")
    
    if st.radio("Policy", ["Non-Ref", "Refundable"], horizontal=True) == "Refundable":
        d = st.number_input("Free Cancel Days", 3)
        pol = f"Free Cancel until {(st.session_state.checkin - timedelta(days=d)).strftime('%d %b %Y')}"
    else: pol = "Non-Refundable & Non-Amendable"

if st.button("Generate Vouchers", type="primary"):
    with st.spinner("Generating..."):
        rooms = []
        if not st.session_state.bulk_data:
            mc = st.session_state.get("room_0_conf", "")
            for i in range(st.session_state.num_rooms):
                c = mc if st.session_state.same_conf_check else st.session_state.get(f"room_{i}_conf", "")
                rooms.append({"guest": st.session_state.get(f"room_{i}_guest", ""), "conf": c})
        else:
            for r in st.session_state.bulk_data:
                rooms.append({"guest": r.get("Guest Name", ""), "conf": str(r.get("Confirmation No", ""))})
        
        if rooms:
            info = fetch_hotel_details_text(st.session_state.hotel_name, st.session_state.city)
            if not any(st.session_state.hotel_images):
                st.session_state.hotel_images = [fetch_image_url(f"{st.session_state.hotel_name} {st.session_state.city} hotel")] * 3
            
            pdf = generate_pdf({
                "hotel": st.session_state.hotel_name, "in": st.session_state.checkin, "out": st.session_state.checkout,
                "room_type": room_final, "adults": st.session_state.adults, "meal": st.session_state.meal_plan, "policy": pol
            }, info, st.session_state.hotel_images, rooms)
            
            st.success("Done!")
            st.download_button("Download", pdf, "Vouchers.pdf", "application/pdf")
        else:
            st.error("No guest data found.")
