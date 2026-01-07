import streamlit as st
import google.generativeai as genai
import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color, lightgrey, black, white
from reportlab.platypus import Table, TableStyle, Paragraph, Image as RLImage
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

# --- BRANDING ---
BRAND_BLUE = Color(0.0, 0.25, 0.5) 
BRAND_GOLD = Color(0.9, 0.75, 0.1) 
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
    keys = {
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
    for k, v in keys.items():
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

def detect_city(hotel_name):
    model = genai.GenerativeModel('gemini-2.0-flash')
    try: return model.generate_content(f'Return ONLY the city name for hotel: "{hotel_name}"').text.strip()
    except: return ""

def fetch_real_room_types(hotel_name, city):
    results = google_search(f"{hotel_name} {city} official site room types accommodation")
    if not results: return []
    snippets = "\n".join([f"- {item.get('title','')}: {item.get('snippet','')}" for item in results])
    model = genai.GenerativeModel('gemini-2.0-flash')
    try:
        res = model.generate_content(f"Extract official hotel room types from these results: {snippets}. Return ONLY a JSON list of strings.").text
        return json.loads(res.replace("```json", "").replace("```", "").strip())
    except: return []

def fetch_hotel_details_text(hotel, city):
    model = genai.GenerativeModel('gemini-2.0-flash')
    try:
        res = model.generate_content(f'Get address/phone for "{hotel}" in "{city}". Return JSON: {{ "addr1": "Street", "addr2": "City/Zip", "phone": "+123..." }}').text
        return json.loads(res.replace("```json", "").replace("```", "").strip())
    except: return {}

def fetch_image_url(query):
    try:
        clean_q = re.sub(r'[^\w\s]', '', query)
        res = requests.get("https://www.googleapis.com/customsearch/v1", 
                           params={"q": clean_q, "cx": SEARCH_CX, "key": SEARCH_KEY, "searchType": "image", "num": 1, "imgSize": "large", "safe": "active"})
        return res.json()['items'][0]['link'] if res.status_code == 200 and 'items' in res.json() else None
    except: return None

def get_smart_images(hotel, city):
    base = f"{hotel} {city}"
    return [
        fetch_image_url(f"{base} hotel exterior building"),
        fetch_image_url(f"{base} hotel lobby reception"),
        fetch_image_url(f"{base} hotel bedroom interior")
    ]

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
        prompt = f"""Extract booking details. JSON format: {{ "hotel_name": "...", "city": "...", "checkin_raw": "...", "checkout_raw": "...", "meal_plan": "...", "is_refundable": true/false, "cancel_deadline_raw": "...", "room_size": "...", "rooms": [ {{ "guest_name": "...", "confirmation_no": "...", "room_type": "..." }} ] }} \n\n Text: {text[:20000]}"""
        res = model.generate_content(prompt).text
        return json.loads(res.replace("```json", "").replace("```", "").strip())
    except: return None

# --- 5. PDF GENERATION (DYNAMIC FLOW LAYOUT - FIXED) ---

def draw_vector_seal(c, x, y):
    c.saveState()
    c.setStrokeColor(BRAND_BLUE); c.setFillColor(BRAND_BLUE); c.setFillAlpha(0.8); c.setLineWidth(1.5)
    
    cx, cy = x + 40, y + 40
    c.circle(cx, cy, 40, stroke=1, fill=0)
    c.setLineWidth(0.5); c.circle(cx, cy, 36, stroke=1, fill=0)
    
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(cx, cy+4, "ODADUU")
    c.setFont("Helvetica-Bold", 7); c.drawCentredString(cx, cy-6, "TRAVEL DMC")
    
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

def generate_pdf(data, info, imgs, rooms_list):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    left_margin = 40
    right_margin = w - 40
    content_width = right_margin - left_margin
    
    # Load images
    i_ext = get_img_reader(imgs[0])
    i_lobby = get_img_reader(imgs[1])
    i_room = get_img_reader(imgs[2])
    
    for i, room in enumerate(rooms_list):
        if i > 0: c.showPage() 
        
        y = h - 40
        
        # 1. HEADER
        try: c.drawImage(LOGO_FILE, left_margin, y-40, 120, 40, mask='auto', preserveAspectRatio=True)
        except: 
            c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 20); c.drawString(left_margin, y-25, COMPANY_NAME)
        
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 16)
        c.drawRightString(right_margin, y - 25, "HOTEL CONFIRMATION VOUCHER")
        y -= 60

        # 2. IMAGES (3 Across)
        img_h = 90
        img_w = 165
        ix = left_margin
        valid_imgs = [x for x in [i_ext, i_lobby, i_room] if x]
        
        if valid_imgs:
            for img in valid_imgs[:3]:
                try: c.drawImage(img, ix, y - img_h, img_w, img_h)
                except: pass
                ix += (img_w + 10)
            y -= (img_h + 20)
        else:
            y -= 10 

        # --- DYNAMIC TABLE DRAWING FUNCTION ---
        def draw_section_table(title, data_list):
            nonlocal y
            
            # Title
            c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 11); c.drawString(left_margin, y, title)
            y -= 4
            c.setStrokeColor(lightgrey); c.line(left_margin, y, right_margin, y)
            y -= 5
            
            # Styles
            t = Table(data_list, colWidths=[110, 390])
            t.setStyle(TableStyle([
                ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
                ('FONTNAME', (1,0), (1,-1), 'Helvetica'),
                ('FONTSIZE', (0,0), (-1,-1), 9),
                ('TEXTCOLOR', (0,0), (-1,-1), Color(0.2,0.2,0.2)),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ]))
            
            # Measure Height
            tw, th = t.wrapOn(c, content_width, 500)
            
            # Page Break Check
            if y - th < 50:
                c.showPage(); y = h - 50
            
            # Draw
            t.drawOn(c, left_margin, y - th)
            y -= (th + 15) # Advance cursor down

        # 3. GUEST INFO
        draw_section_table("Guest Information", [
            ["Guest Name(s):", room['guest']],
            ["Confirmation No:", room['conf']],
            ["Booking Date:", datetime.now().strftime("%d %b %Y")]
        ])

        # 4. HOTEL DETAILS (With Wrapped Address)
        addr_para = Paragraph(info.get('addr1', ''), getSampleStyleSheet()['Normal'])
        draw_section_table("Hotel Details", [
            ["Hotel:", data['hotel']],
            ["Address:", addr_para], 
            ["Check-In:", data['in'].strftime("%d %b %Y")],
            ["Check-Out:", data['out'].strftime("%d %b %Y")]
        ])

        # 5. ROOM INFO
        draw_section_table("Room Information", [
            ["Room Type:", data['room_type']],
            ["Pax:", f"{data['adults']} Adults"],
            ["Meal Plan:", data['meal']],
            ["Cancellation:", data['policy']]
        ])

        # 6. POLICIES (Grid)
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 11); c.drawString(left_margin, y, "HOTEL POLICIES")
        y -= 15
        
        pol_data = [
            ["Policy", "Time / Detail"],
            ["Check-in / Out:", "Check-in: 3:00 PM  |  Check-out: 12:00 PM"],
            ["Early/Late:", "Subject to availability. Request at hotel."],
            ["Requirement:", "Passport & Credit Card/Cash Deposit required."]
        ]
        pol_table = Table(pol_data, colWidths=[110, 390])
        pol_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BRAND_BLUE),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('GRID', (0,0), (-1,-1), 0.5, Color(0.3, 0.3, 0.3)),
            ('PADDING', (0,0), (-1,-1), 3),
        ]))
        pw, ph = pol_table.wrapOn(c, content_width, 150)
        
        if y - ph < 50: c.showPage(); y = h - 50
        pol_table.drawOn(c, left_margin, y - ph)
        y -= (ph + 15)

        # 7. T&C
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 10); c.drawString(left_margin, y, "STANDARD TERMS & CONDITIONS")
        y -= 12
        
        tnc_text = [
            "1. Voucher Validity: Must be presented at hotel front desk.",
            f"2. Identification: Guest(s) {room['guest']} must present valid ID.",
            "3. No-Show: Full charge applies for no-shows.",
            "4. Incidentals: Paid by guest directly.",
            "5. Occupancy: Standard occupancy rules apply.",
            "6. Rights: Hotel reserves right of admission.",
            "7. Liability: Use safety deposit box for valuables.",
            "8. Resale: Booking is non-transferable.",
            "9. Tax: City/Tourism tax payable at hotel if applicable.",
            "10. Bedding: Subject to availability."
        ]
        
        c.setFillColor(black); c.setFont("Helvetica", 7)
        for line in tnc_text:
            if y < 60: c.showPage(); y = h - 50
            c.drawString(left_margin, y, line)
            y -= 9 

        # 8. FOOTER
        draw_vector_seal(c, w - 130, 45)
        c.setStrokeColor(BRAND_GOLD); c.setLineWidth(3); c.line(0, 40, w, 40)
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 8); c.drawString(left_margin, 25, f"Issued by: {COMPANY_NAME}")
        
    c.save(); buffer.seek(0); return buffer

# --- 6. UI LOGIC ---

st.title("🌏 Odaduu Voucher Generator")

if st.button("🔄 Reset App"):
    for k in list(st.session_state.keys()): del st.session_state[k]
    st.rerun()

# UPLOAD
with st.expander("📤 Upload Supplier Voucher (PDF)", expanded=True):
    up_file = st.file_uploader("Drop PDF here", type="pdf")
    if up_file and st.session_state.last_uploaded_file != up_file.name:
        with st.spinner("Reading PDF..."):
            data = extract_pdf_data(up_file)
            if data:
                st.session_state.hotel_name = data.get('hotel_name', '')
                st.session_state.city = data.get('city', '')
                d_in = parse_smart_date(data.get('checkin_raw'))
                if d_in: st.session_state.checkin = d_in
                d_out = parse_smart_date(data.get('checkout_raw'))
                if d_out: st.session_state.checkout = d_out
                st.session_state.meal_plan = data.get('meal_plan', 'Breakfast Only')
                st.session_state.ai_room_str = clean_extracted_text(data.get('rooms', [{}])[0].get('room_type', ''))
                
                rooms = data.get('rooms', [])
                if rooms:
                    st.session_state.num_rooms = len(rooms)
                    for i, r in enumerate(rooms):
                        st.session_state[f'room_{i}_conf'] = r.get('confirmation_no', '')
                        st.session_state[f'room_{i}_guest'] = r.get('guest_name', '')
                
                st.session_state.last_uploaded_file = up_file.name
                st.success("PDF Loaded!")
                st.rerun()

# MAIN FORM
c1, c2 = st.columns(2)

with c1:
    st.subheader("1. Search Hotel")
    search_q = st.text_input("Enter Hotel Name", key="hotel_search_query")
    
    # SEARCH BUTTON (UPDATED)
    def run_search():
        if st.session_state.hotel_search_query:
            st.session_state.found_hotels = find_hotel_options(st.session_state.hotel_search_query)
            
    st.button("🔎 Search", on_click=run_search)
    
    if st.session_state.found_hotels:
        selected = st.selectbox("Select Hotel", st.session_state.found_hotels)
        if selected != st.session_state.hotel_name:
            st.session_state.hotel_name = selected
            # Auto-Fetch on Select
            st.session_state.city = detect_city(selected)
            st.session_state.fetched_room_types = fetch_real_room_types(selected, st.session_state.city)
            st.session_state.hotel_images = get_smart_images(selected, st.session_state.city)
            st.rerun()

    st.text_input("Final Hotel Name", key="hotel_name")
    st.text_input("City", key="city")

    st.subheader("2. Guests")
    mode = st.radio("Mode", ["Manual", "Bulk CSV"], horizontal=True)
    
    if mode == "Manual":
        n = st.number_input("Rooms", 1, 50, key="num_rooms")
        same = st.checkbox("Same Conf?", key="same_conf_check")
        for i in range(n):
            col_a, col_b = st.columns([2, 1])
            col_a.text_input(f"Room {i+1} Guest(s)", key=f"room_{i}_guest", help="Multiple names allowed")
            if i == 0: col_b.text_input("Conf", key=f"room_{i}_conf")
            elif not same: col_b.text_input("Conf", key=f"room_{i}_conf")
    else:
        f = st.file_uploader("CSV", type="csv")
        if f: 
            df = pd.read_csv(f)
            st.session_state.bulk_data = df.to_dict('records')

with c2:
    st.subheader("3. Stay")
    if st.session_state.checkout <= st.session_state.checkin:
        st.session_state.checkout = st.session_state.checkin + timedelta(days=1)
    
    c2a, c2b = st.columns(2)
    c2a.date_input("Check-In", key="checkin")
    c2b.date_input("Check-Out", key="checkout", min_value=st.session_state.checkin + timedelta(days=1))
    
    opts = []
    if st.session_state.ai_room_str: opts.append(st.session_state.ai_room_str)
    if st.session_state.fetched_room_types: opts.extend(st.session_state.fetched_room_types)
    opts.append("Manual Entry...")
    
    sel = st.selectbox("Room Type", opts)
    room_final = st.text_input("Final Room Name", value="" if sel == "Manual Entry..." else sel)
    
    st.number_input("Adults", 1, key="adults")
    st.selectbox("Meal", ["Breakfast Only", "Room Only", "Half Board", "Full Board"], key="meal_plan")
    
    if st.radio("Policy", ["Non-Ref", "Refundable"], horizontal=True) == "Refundable":
        d = st.number_input("Free Cancel Days", 3)
        pol = f"Free Cancel until {(st.session_state.checkin - timedelta(days=d)).strftime('%d %b %Y')}"
    else: pol = "Non-Refundable & Non-Amendable"

if st.button("Generate Vouchers", type="primary"):
    with st.spinner("Processing..."):
        rooms = []
        if mode == "Manual":
            mc = st.session_state.get("room_0_conf", "")
            for i in range(st.session_state.num_rooms):
                c = mc if st.session_state.same_conf_check else st.session_state.get(f"room_{i}_conf", "")
                rooms.append({"guest": st.session_state.get(f"room_{i}_guest", ""), "conf": c})
        else:
            if st.session_state.bulk_data:
                for r in st.session_state.bulk_data:
                    rooms.append({"guest": r.get("Guest Name", ""), "conf": str(r.get("Confirmation No", ""))})
        
        if rooms:
            info = fetch_hotel_details_text(st.session_state.hotel_name, st.session_state.city)
            imgs = st.session_state.hotel_images if any(st.session_state.hotel_images) else get_smart_images(st.session_state.hotel_name, st.session_state.city)
            
            pdf = generate_pdf({
                "hotel": st.session_state.hotel_name, "in": st.session_state.checkin, "out": st.session_state.checkout,
                "room_type": room_final, "adults": st.session_state.adults, "meal": st.session_state.meal_plan, "policy": pol
            }, info, imgs, rooms)
            
            st.success("Done!")
            st.download_button("Download", pdf, "Vouchers.pdf", "application/pdf")
        else:
            st.error("No data!")
