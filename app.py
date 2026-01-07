import streamlit as st
import google.generativeai as genai
import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color, lightgrey, black, white
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import inch
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
EMAIL_CONTACT = "reservations@odaduu.com"
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

# --- 5. PDF GENERATION (FIXED LAYOUT) ---

def draw_vector_seal(c, x, y):
    """Draws the Odaduu Seal with 'ODADUU' on top arc and 'OFFICIAL' at bottom."""
    c.saveState()
    c.setStrokeColor(BRAND_BLUE); c.setFillColor(BRAND_BLUE); c.setFillAlpha(0.8); c.setLineWidth(1.5)
    
    # Circles
    cx, cy = x + 40, y + 40
    c.circle(cx, cy, 40, stroke=1, fill=0)
    c.setLineWidth(0.5); c.circle(cx, cy, 36, stroke=1, fill=0)
    
    # Center Text
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(cx, cy+4, "ODADUU")
    c.setFont("Helvetica-Bold", 7); c.drawCentredString(cx, cy-6, "TRAVEL DMC")
    
    # Arched Text
    c.setFont("Helvetica-Bold", 6)
    
    # Top Arc: "CERTIFIED VOUCHER"
    text_top = "CERTIFIED VOUCHER"
    angle_start = 140
    for i, char in enumerate(text_top):
        angle = angle_start - (i * 10) # spacing
        rad = radians(angle)
        # Position relative to center radius 32
        tx = cx + 32 * cos(rad)
        ty = cy + 32 * sin(rad)
        c.saveState()
        c.translate(tx, ty)
        c.rotate(angle - 90) # Rotate text to face inward
        c.drawCentredString(0, 0, char)
        c.restoreState()

    # Bottom Arc: "OFFICIAL"
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
        
        y = h - 50
        
        # --- HEADER ---
        try: c.drawImage(LOGO_FILE, left_margin, y-40, 120, 40, mask='auto', preserveAspectRatio=True)
        except: 
            c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 20); c.drawString(left_margin, y-20, COMPANY_NAME)
        
        y -= 60
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(w/2, y, "HOTEL CONFIRMATION VOUCHER")
        y -= 30

        # --- GUEST INFO ---
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 12); c.drawString(left_margin, y, "Guest Information")
        y -= 5; c.setStrokeColor(lightgrey); c.line(left_margin, y, right_margin, y); y -= 15
        
        # Grid for Guest Info
        g_data = [
            ["Guest Name(s):", room['guest']],
            ["Confirmation No:", room['conf']],
            ["Booking Date:", datetime.now().strftime("%d %b %Y")]
        ]
        g_table = Table(g_data, colWidths=[120, 380])
        g_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
            ('FONTNAME', (1,0), (1,-1), 'Helvetica'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('TEXTCOLOR', (0,0), (-1,-1), Color(0.2,0.2,0.2)),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        g_w, g_h = g_table.wrapOn(c, content_width, 200)
        g_table.drawOn(c, left_margin, y - g_h)
        y -= (g_h + 15)

        # --- HOTEL DETAILS ---
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 12); c.drawString(left_margin, y, "Hotel Details")
        y -= 5; c.line(left_margin, y, right_margin, y); y -= 15
        
        h_data = [
            ["Hotel:", data['hotel']],
            ["Address:", info.get('addr1', '')],
            ["Check-In:", data['in'].strftime("%d %b %Y")],
            ["Check-Out:", data['out'].strftime("%d %b %Y")]
        ]
        h_table = Table(h_data, colWidths=[120, 380])
        h_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
            ('FONTNAME', (1,0), (1,-1), 'Helvetica'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('TEXTCOLOR', (0,0), (-1,-1), Color(0.2,0.2,0.2)),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        h_w, h_h = h_table.wrapOn(c, content_width, 200)
        h_table.drawOn(c, left_margin, y - h_h)
        y -= (h_h + 15)

        # --- ROOM INFO ---
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 12); c.drawString(left_margin, y, "Room Information")
        y -= 5; c.line(left_margin, y, right_margin, y); y -= 15
        
        r_data = [
            ["Room Type:", data['room_type']],
            ["Pax:", f"{data['adults']} Adults"],
            ["Meal Plan:", data['meal']],
            ["Cancellation:", data['policy']]
        ]
        r_table = Table(r_data, colWidths=[120, 380])
        r_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
            ('FONTNAME', (1,0), (1,-1), 'Helvetica'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('TEXTCOLOR', (0,0), (-1,-1), Color(0.2,0.2,0.2)),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        r_w, r_h = r_table.wrapOn(c, content_width, 200)
        r_table.drawOn(c, left_margin, y - r_h)
        y -= (r_h + 15)

        # --- IMAGES (3 GRID) ---
        # Calculate dynamic position to avoid overlap
        img_y = y - 100 
        
        if i_ext or i_lobby or i_room:
            # Draw placeholder rects if image fails, to keep layout
            valid_imgs = [i_ext, i_lobby, i_room]
            ix = left_margin
            img_width = 160
            img_height = 95
            
            for img in valid_imgs:
                if img:
                    try: c.drawImage(img, ix, img_y, img_width, img_height)
                    except: pass
                ix += (img_width + 10) # gap
            y = img_y - 15 # Update Y position below images
        else:
            y -= 10 # small gap if no images

        # --- POLICY TABLE ---
        # Ensure we have space, else new page
        if y < 200: 
            c.showPage(); y = h - 50
        
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 11); c.drawString(left_margin, y, "HOTEL POLICIES")
        y -= 15
        
        pol_data = [
            ["Policy", "Time / Detail"],
            ["Standard Check-in:", "3:00 PM"],
            ["Standard Check-out:", "12:00 PM"],
            ["Early/Late:", "Subject to availability. Request upon arrival."],
            ["Required:", "Passport & Credit Card/Cash Deposit."]
        ]
        pol_table = Table(pol_data, colWidths=[120, 390])
        pol_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BRAND_BLUE),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('GRID', (0,0), (-1,-1), 0.5, Color(0.2, 0.2, 0.2)),
            ('PADDING', (0,0), (-1,-1), 4),
        ]))
        pol_w, pol_h = pol_table.wrapOn(c, content_width, 200)
        pol_table.drawOn(c, left_margin, y - pol_h)
        y -= (pol_h + 20)

        # --- T&C SECTION ---
        if y < 150:
            c.showPage(); y = h - 50
            
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 10); c.drawString(left_margin, y, "STANDARD TERMS & CONDITIONS")
        y -= 12
        
        tnc_text = [
            "1. Voucher Validity: Must be presented at hotel front desk.",
            f"2. Identification: {room['guest']} must present valid ID.",
            "3. No-Show: Full charge applies for no-shows.",
            "4. Incidentals: Extras (mini-bar, etc.) paid by guest directly.",
            "5. Occupancy: Standard occupancy rules apply.",
            "6. Hotel Rights: Hotel may refuse entry for policy violations.",
            "7. Liability: Valuables should be stored in safety deposit box.",
            "8. Non-Transferable: Booking cannot be resold.",
            "9. City Tax: Not included, paid at hotel if applicable.",
            "10. Bed Type: Subject to availability."
        ]
        styleN = getSampleStyleSheet()['Normal']
        styleN.fontSize = 8
        styleN.leading = 10
        
        for line in tnc_text:
            p = Paragraph(line, styleN)
            p_w, p_h = p.wrap(content_width, 50)
            p.drawOn(c, left_margin, y - p_h)
            y -= (p_h + 2)

        # --- FOOTER & SEAL ---
        draw_vector_seal(c, w - 130, 45)
        c.setStrokeColor(BRAND_GOLD); c.setLineWidth(3); c.line(0, 45, w, 45)
        c.setFillColor(BRAND_BLUE); c.setFont("Helvetica-Bold", 9); c.drawString(left_margin, 30, f"Issued by: {COMPANY_NAME}")
        
    c.save(); buffer.seek(0); return buffer

# --- 6. UI LOGIC ---

st.title("🌏 Odaduu Voucher Generator")

# Reset
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
                
                # Auto-Search
                if st.session_state.hotel_name:
                    st.session_state.found_hotels = [st.session_state.hotel_name]
                    if not st.session_state.city:
                        st.session_state.city = detect_city(st.session_state.hotel_name)
                    st.session_state.fetched_room_types = fetch_real_room_types(st.session_state.hotel_name, st.session_state.city)
                    st.session_state.hotel_images = get_smart_images(st.session_state.hotel_name, st.session_state.city)

                st.session_state.last_uploaded_file = up_file.name
                st.success("PDF Loaded!")
                st.rerun()

# MAIN FORM
c1, c2 = st.columns(2)

with c1:
    st.subheader("1. Hotel Search")
    search_q = st.text_input("Enter Keyword (e.g. 'Atlantis')", key="hotel_search_query")
    
    if st.button("🔎 Find Hotels"):
        with st.spinner("Searching..."):
            st.session_state.found_hotels = find_hotel_options(search_q)
            if not st.session_state.found_hotels: st.warning("No hotels found.")
    
    if st.session_state.found_hotels:
        selected_hotel = st.selectbox("Select Correct Hotel", st.session_state.found_hotels, key="hotel_selector")
        # Trigger update on selection change
        if selected_hotel != st.session_state.hotel_name:
            st.session_state.hotel_name = selected_hotel
            with st.spinner(f"Loading data for {selected_hotel}..."):
                st.session_state.city = detect_city(selected_hotel)
                st.session_state.fetched_room_types = fetch_real_room_types(selected_hotel, st.session_state.city)
                st.session_state.hotel_images = get_smart_images(selected_hotel, st.session_state.city)
                st.rerun()
                
    st.text_input("Final Hotel Name", key="hotel_name")
    st.text_input("City", key="city")

    st.subheader("2. Guest Details")
    input_mode = st.radio("Mode", ["Manual", "Bulk CSV"], horizontal=True)
    
    if input_mode == "Manual":
        n = st.number_input("No. of Rooms", 1, 50, key="num_rooms")
        same_conf = st.checkbox("Same Conf No?", key="same_conf_check")
        
        for i in range(n):
            col_a, col_b = st.columns([2, 1])
            col_a.text_input(f"Room {i+1} Guest(s)", key=f"room_{i}_guest", help="e.g. 'John & Jane Doe'")
            
            if i == 0:
                col_b.text_input("Conf No", key=f"room_{i}_conf")
            else:
                if same_conf:
                    col_b.info("Using Room 1 Conf")
                else:
                    col_b.text_input("Conf No", key=f"room_{i}_conf")
    else:
        st.subheader("Bulk Upload")
        csv_file = st.file_uploader("Upload CSV", type="csv")
        if csv_file:
            df = pd.read_csv(csv_file)
            if 'Guest Name' in df.columns and 'Confirmation No' in df.columns:
                st.session_state.bulk_data = df.to_dict('records')
                st.success(f"Loaded {len(df)} records.")
            else:
                st.error("CSV Error: Missing 'Guest Name' or 'Confirmation No' columns.")

with c2:
    st.subheader("3. Stay Details")
    if st.session_state.checkout <= st.session_state.checkin:
        st.session_state.checkout = st.session_state.checkin + timedelta(days=1)
    
    c2a, c2b = st.columns(2)
    c2a.date_input("Check-In", key="checkin")
    c2b.date_input("Check-Out", key="checkout", min_value=st.session_state.checkin + timedelta(days=1))
    
    opts = []
    if st.session_state.ai_room_str: opts.append(st.session_state.ai_room_str)
    if st.session_state.fetched_room_types: opts.extend(st.session_state.fetched_room_types)
    opts.append("Manual Entry...")
    
    sel = st.selectbox("Room Type Options", opts)
    final_room = st.text_input("Final Room Name (Editable)", value="" if sel == "Manual Entry..." else sel)
    
    st.number_input("Adults", 1, key="adults")
    st.selectbox("Meal", ["Breakfast Only", "Room Only", "Half Board", "Full Board"], key="meal_plan")
    
    ptype = st.radio("Policy", ["Non-Refundable", "Refundable"], horizontal=True)
    if ptype == "Refundable":
        d = st.number_input("Free Cancel Days Before", 3)
        policy_txt = f"Free Cancellation until {(st.session_state.checkin - timedelta(days=d)).strftime('%d %b %Y')}"
    else:
        policy_txt = "Non-Refundable & Non-Amendable"
    st.info(f"Policy: {policy_txt}")

if st.button("Generate Vouchers", type="primary"):
    with st.spinner("Generating..."):
        rooms_to_process = []
        if input_mode == "Manual":
            master_conf = st.session_state.get("room_0_conf", "")
            for i in range(st.session_state.num_rooms):
                c = master_conf if st.session_state.same_conf_check else st.session_state.get(f"room_{i}_conf", "")
                g = st.session_state.get(f"room_{i}_guest", "")
                rooms_to_process.append({"guest": g, "conf": c})
        else:
            if st.session_state.bulk_data:
                for row in st.session_state.bulk_data:
                    rooms_to_process.append({"guest": row.get("Guest Name", ""), "conf": str(row.get("Confirmation No", ""))})
        
        if not rooms_to_process:
            st.error("No guest data!")
        else:
            info = fetch_hotel_details_text(st.session_state.hotel_name, st.session_state.city)
            imgs = st.session_state.hotel_images if any(st.session_state.hotel_images) else get_smart_images(st.session_state.hotel_name, st.session_state.city)
            
            pdf_data = {
                "hotel": st.session_state.hotel_name,
                "in": st.session_state.checkin,
                "out": st.session_state.checkout,
                "room_type": final_room,
                "adults": st.session_state.adults,
                "meal": st.session_state.meal_plan,
                "policy": policy_txt
            }
            
            pdf_bytes = generate_pdf(pdf_data, info, imgs, rooms_to_process)
            
            st.success(f"Generated {len(rooms_to_process)} Vouchers!")
            st.download_button("⬇️ Download All Vouchers (PDF)", pdf_bytes, "Vouchers.pdf", "application/pdf")
