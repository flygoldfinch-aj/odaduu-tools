import streamlit as st
import google.generativeai as genai
import requests
import pandas as pd
import io
import re
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color, lightgrey
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.utils import ImageReader

# --- 1. SETUP & CONFIGURATION ---
st.set_page_config(page_title="Odaduu Voucher Tool", page_icon="🌏", layout="wide")

# --- BRANDING CONFIGURATION (ODADUU) ---
# Dark Blue & Gold theme
BRAND_COLOR = Color(0.0, 0.25, 0.5) 
ACCENT_COLOR = Color(0.9, 0.75, 0.1) 
COMPANY_NAME = "Odaduu DMC"
EMAIL_CONTACT = "reservations@odaduu.com" 

try:
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
    SEARCH_KEY = st.secrets["SEARCH_API_KEY"]
    SEARCH_CX = st.secrets["SEARCH_ENGINE_ID"]
    genai.configure(api_key=GEMINI_KEY)
except Exception:
    st.error("⚠️ API Secrets not found! Please check Streamlit settings.")

# --- 2. SESSION STATE ---
def init_state():
    keys = {
        'hotel_name': '', 'city': '', 
        'checkin': datetime.now().date(), 
        'checkout': datetime.now().date() + timedelta(days=1),
        'num_rooms': 1, 'room_type': '', 'adults': 2, 
        'meal_plan': 'Breakfast Only',
        'policy_type': 'Non-Refundable', 
        'fetched_room_types': [], # For the dropdown
        'ai_room_str': '',
        'last_uploaded_file': None,
        'bulk_data': [],
        'hotel_images': [None, None, None] # Cache images
    }
    for k, v in keys.items():
        if k not in st.session_state:
            st.session_state[k] = v
            
    for i in range(50):
        if f'room_{i}_guest' not in st.session_state: st.session_state[f'room_{i}_guest'] = ''
        if f'room_{i}_conf' not in st.session_state: st.session_state[f'room_{i}_conf'] = ''

init_state()

# --- 3. HELPER FUNCTIONS ---

def parse_smart_date(date_str):
    if not date_str: return None
    clean_str = str(date_str).strip()
    clean_str = re.sub(r'\bSept\b', 'Sep', clean_str, flags=re.IGNORECASE)
    formats = ["%d %b %Y", "%Y-%m-%d", "%d %B %Y", "%d-%m-%Y"]
    for fmt in formats:
        try: return datetime.strptime(clean_str, fmt).date()
        except ValueError: continue
    return None

def clean_extracted_text(raw_val):
    s = str(raw_val).strip()
    if s.lower() in ["room_name", "room_type"]: return ""
    return s.strip('{}[]"\'')

# --- 4. GOOGLE SEARCH & AI ---

def google_search(query):
    """Base search function."""
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"q": query, "cx": SEARCH_CX, "key": SEARCH_KEY, "num": 5}
        res = requests.get(url, params=params, timeout=5)
        return res.json().get("items", []) if res.status_code == 200 else []
    except: return []

def detect_city(hotel_name):
    """Uses Gemini to guess city from hotel name if missing."""
    model = genai.GenerativeModel('gemini-2.0-flash')
    try:
        return model.generate_content(f'Return ONLY the city name for hotel: "{hotel_name}"').text.strip()
    except: return ""

def fetch_real_room_types(hotel_name, city):
    """Searches Google for room types."""
    results = google_search(f"{hotel_name} {city} official site room types accommodation")
    if not results: return []
    
    snippets = "\n".join([f"- {item.get('title','')}: {item.get('snippet','')}" for item in results])
    model = genai.GenerativeModel('gemini-2.0-flash')
    try:
        # Ask AI to extract list
        res = model.generate_content(f"Extract official hotel room types from these results: {snippets}. Return ONLY a JSON list of strings (e.g. ['Deluxe King', 'Suite']).").text
        import json
        return json.loads(res.replace("```json", "").replace("```", "").strip())
    except: return []

def fetch_hotel_details_text(hotel, city):
    model = genai.GenerativeModel('gemini-2.0-flash')
    try:
        import json
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
    # Try different queries to ensure we get 3 distinct images
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
        import json
        return json.loads(res.replace("```json", "").replace("```", "").strip())
    except: return None

# --- 5. PDF GENERATION (ODADUU BRANDING) ---

def draw_vector_seal(c, x, y):
    c.saveState()
    c.setStrokeColor(BRAND_COLOR); c.setFillColor(BRAND_COLOR); c.setFillAlpha(0.8); c.setLineWidth(1.5)
    c.circle(x+40, y+40, 40, stroke=1, fill=0)
    c.setLineWidth(0.5); c.circle(x+40, y+40, 36, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(x+40, y+44, "ODADUU")
    c.setFont("Helvetica-Bold", 7); c.drawCentredString(x+40, y+34, "OFFICIAL")
    c.restoreState()

def generate_pdf(data, info, imgs, rooms_list):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    
    # Load images
    i_ext = get_img_reader(imgs[0])
    i_lobby = get_img_reader(imgs[1])
    i_room = get_img_reader(imgs[2])
    
    for i, room in enumerate(rooms_list):
        if i > 0: c.showPage() 
        
        # Header / Logo Text (Since we don't have Odaduu logo file, use text)
        # If you have 'odaduu_logo.png', uncomment next line:
        # try: c.drawImage("odaduu_logo.png", w/2-80, h-60, 160, 55, mask='auto', preserveAspectRatio=True)
        # except: 
        c.setFillColor(BRAND_COLOR); c.setFont("Helvetica-Bold", 24); c.drawCentredString(w/2, h-50, COMPANY_NAME)

        c.setFillColor(BRAND_COLOR); c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(w/2, h-80, "HOTEL CONFIRMATION VOUCHER")

        y = h - 120; left = 40
        
        def label_val(lbl, val):
            c.setFillColor(Color(0.1,0.1,0.1)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, lbl)
            c.setFillColor(Color(0.2,0.2,0.2)); c.setFont("Helvetica", 10); c.drawString(left+120, y, str(val))

        # Guest
        c.setFillColor(BRAND_COLOR); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Guest Information"); y-=5
        c.setStrokeColor(lightgrey); c.line(left, y, w-40, y); y-=15
        label_val("Guest Name(s):", room['guest']); y-=15
        label_val("Conf No:", room['conf']); y-=15
        label_val("Booking Date:", datetime.now().strftime("%d %b %Y")); y-=20

        # Hotel
        c.setFillColor(BRAND_COLOR); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Hotel Details"); y-=5
        c.line(left, y, w-40, y); y-=15
        label_val("Hotel:", data['hotel']); y-=15
        label_val("Address:", info.get('addr1','')); y-=15
        label_val("Check-In:", data['in'].strftime("%d %b %Y")); y-=15
        label_val("Check-Out:", data['out'].strftime("%d %b %Y")); y-=20

        # Room
        c.setFillColor(BRAND_COLOR); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Room Information"); y-=5
        c.line(left, y, w-40, y); y-=15
        label_val("Room Type:", data['room_type']); y-=15
        label_val("Pax:", f"{data['adults']} Adults"); y-=15
        label_val("Meal:", data['meal']); y-=15
        label_val("Policy:", data['policy']); y-=20

        # Images (Draw all 3)
        if i_ext or i_lobby or i_room:
            ix = left
            valid_imgs = [x for x in [i_ext, i_lobby, i_room] if x]
            for img in valid_imgs[:3]: 
                try: c.drawImage(img, ix, y-95, 160, 95); ix+=170
                except: pass
            y -= 110

        # T&C
        c.setFillColor(BRAND_COLOR); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "TERMS & CONDITIONS"); y-=15
        tnc = [
            "1. Voucher Validity: Must be presented at front desk.",
            f"2. Identification: Guest(s) {room['guest']} must present valid ID.",
            "3. No-Show: Full charge applies.",
            "4. Incidentals: Paid by guest directly.",
            "5. Occupancy: Standard occupancy applies."
        ]
        styleN = getSampleStyleSheet()["Normal"]; styleN.fontSize = 8
        t = Table([[Paragraph(x, styleN)] for x in tnc], colWidths=[500])
        t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.5,Color(0.8,0.8,0.8))]))
        t.wrapOn(c, w, h); t.drawOn(c, left, y-80)

        draw_vector_seal(c, w-130, 45)
        c.setStrokeColor(ACCENT_COLOR); c.setLineWidth(3); c.line(0, 45, w, 45)
        c.setFillColor(BRAND_COLOR); c.setFont("Helvetica-Bold", 9); c.drawString(left, 30, f"Issued by: {COMPANY_NAME}")
        
        c.showPage()
    
    c.save(); buffer.seek(0); return buffer

# --- 6. UI LOGIC ---

st.title("🌏 Odaduu Voucher Generator")

# Reset
if st.button("🔄 Reset App"):
    for k in list(st.session_state.keys()): del st.session_state[k]
    st.rerun()

# --- UPLOAD SECTION ---
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
                
                # Load first room data into session
                rooms = data.get('rooms', [])
                if rooms:
                    st.session_state.num_rooms = len(rooms)
                    for i, r in enumerate(rooms):
                        st.session_state[f'room_{i}_conf'] = r.get('confirmation_no', '')
                        st.session_state[f'room_{i}_guest'] = format_guest_name(r.get('guest_name', ''))
                
                # Trigger Auto-Search for Images/Rooms
                with st.spinner("Fetching Hotel Images & Rooms..."):
                    if not st.session_state.city:
                        st.session_state.city = detect_city(st.session_state.hotel_name)
                    st.session_state.fetched_room_types = fetch_real_room_types(st.session_state.hotel_name, st.session_state.city)
                    st.session_state.hotel_images = get_smart_images(st.session_state.hotel_name, st.session_state.city)

                st.session_state.last_uploaded_file = up_file.name
                st.success("PDF Loaded & Enhanced with Search Data!")
                st.rerun()

# --- MAIN FORM ---
c1, c2 = st.columns(2)

with c1:
    st.text_input("Hotel Name", key="hotel_name")
    st.text_input("City", key="city")
    
    # --- RESTORED SEARCH BUTTON ---
    if st.button("🔎 Search Hotel Details"):
        with st.spinner("Searching Google..."):
            if not st.session_state.city:
                st.session_state.city = detect_city(st.session_state.hotel_name)
            
            # 1. Fetch Room Types
            st.session_state.fetched_room_types = fetch_real_room_types(st.session_state.hotel_name, st.session_state.city)
            
            # 2. Fetch Images
            st.session_state.hotel_images = get_smart_images(st.session_state.hotel_name, st.session_state.city)
            st.rerun()

    # --- INPUT MODE SELECTOR ---
    input_mode = st.radio("Voucher Input Mode", ["Manual Entry", "Bulk Upload (CSV)"], horizontal=True)
    
    if input_mode == "Manual Entry":
        st.subheader("Guest Details")
        n = st.number_input("Number of Rooms", 1, 50, key="num_rooms")
        same_conf = st.checkbox("Same Confirmation No for all rooms?", key="same_conf_check")
        
        for i in range(n):
            col_a, col_b = st.columns([2, 1])
            # Explicit Label for Multiple Names
            col_a.text_input(f"Room {i+1} Guest Name(s)", key=f"room_{i}_guest", help="e.g. 'John Doe & Jane Smith'")
            
            # Smart Logic: Hide subsequent Conf No inputs if 'Same Conf' is checked
            if i == 0:
                col_b.text_input("Conf No", key=f"room_{i}_conf")
            else:
                if same_conf:
                    col_b.info(f"Using Room 1 Conf")
                else:
                    col_b.text_input("Conf No", key=f"room_{i}_conf")
                    
    else:
        # BULK CSV MODE
        st.subheader("Bulk Upload")
        st.info("Upload CSV with headers: **Guest Name**, **Confirmation No**")
        csv_file = st.file_uploader("Upload CSV", type="csv")
        if csv_file:
            df = pd.read_csv(csv_file)
            st.dataframe(df.head())
            if 'Guest Name' in df.columns and 'Confirmation No' in df.columns:
                st.session_state.bulk_data = df.to_dict('records')
                st.success(f"Loaded {len(df)} records ready for generation.")
            else:
                st.error("CSV Error: Missing 'Guest Name' or 'Confirmation No' columns.")

with c2:
    if st.session_state.checkout <= st.session_state.checkin:
        st.session_state.checkout = st.session_state.checkin + timedelta(days=1)
    
    st.date_input("Check-In", key="checkin")
    st.date_input("Check-Out", key="checkout", min_value=st.session_state.checkin + timedelta(days=1))
    
    # Room Type with Search Results
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

# --- GENERATION ---
if st.button("Generate Vouchers", type="primary"):
    with st.spinner("Processing..."):
        rooms_to_process = []
        
        # 1. Gather Data based on Mode
        if input_mode == "Manual Entry":
            master_conf = st.session_state.get("room_0_conf", "")
            for i in range(st.session_state.num_rooms):
                # Use master conf if checked, otherwise specific
                c = master_conf if st.session_state.same_conf_check else st.session_state.get(f"room_{i}_conf", "")
                g = st.session_state.get(f"room_{i}_guest", "")
                rooms_to_process.append({"guest": g, "conf": c})
        else:
            # Bulk Mode
            if st.session_state.bulk_data:
                for row in st.session_state.bulk_data:
                    rooms_to_process.append({
                        "guest": row.get("Guest Name", ""),
                        "conf": str(row.get("Confirmation No", ""))
                    })
        
        if not rooms_to_process:
            st.error("No guest data found!")
        else:
            # 2. Fetch Info & Images (if not already fetched)
            info = fetch_hotel_details_text(st.session_state.hotel_name, st.session_state.city)
            # Use cached images if available, else fetch
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
            
            # 3. Generate Single PDF with all vouchers
            pdf_bytes = generate_pdf(pdf_data, info, imgs, rooms_to_process)
            
            st.success(f"Generated {len(rooms_to_process)} Vouchers!")
            st.download_button("⬇️ Download All Vouchers (PDF)", pdf_bytes, "Vouchers.pdf", "application/pdf")
