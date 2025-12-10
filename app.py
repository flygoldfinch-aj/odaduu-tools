import streamlit as st
import google.generativeai as genai
import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color, lightgrey
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from datetime import datetime, timedelta
import io
import json
import pypdf
from reportlab.lib.utils import ImageReader # Ensure ImageReader is available
import textwrap
from math import sin, cos, radians
import re

# --- 1. SETUP & CONFIGURATION ---
st.set_page_config(page_title="Odaduu Voucher Generator", page_icon="🏨", layout="wide")

try:
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
    SEARCH_KEY = st.secrets["SEARCH_API_KEY"]
    SEARCH_CX = st.secrets["SEARCH_ENGINE_ID"]
    genai.configure(api_key=GEMINI_KEY)
except Exception:
    st.error("⚠️ Secrets not found! Please check your Streamlit settings.")
    st.stop()

# --- 2. SESSION STATE MANAGEMENT ---
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

# --- 3. HELPER FUNCTIONS ---

def parse_smart_date(date_str):
    """Parses dates like '28 Sept 2025' by fixing 'Sept' to 'Sep'."""
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
    """Removes residual JSON/quote garbage from room type extraction."""
    if not isinstance(raw_type, str): return raw_type
    
    # Remove literal JSON artifacts like {"room_name": "..."}
    if raw_type.startswith('{"') and raw_type.endswith('"}'):
        try:
            temp_json = json.loads(raw_type)
            # Try to grab the value of the first key (which is often 'room_name')
            return list(temp_json.values())[0].strip('\'\" ')
        except json.JSONDecodeError:
            # If not proper JSON, clean standard quotation marks
            pass
            
    # General cleanup (remove leading/trailing quotes and brackets)
    return raw_type.strip().strip('\'"{} ')

# --- 4. AI FUNCTIONS ---

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
        prompt = f"""
        Extract booking details.
        
        CRITICAL: Return DATES exactly as they appear (e.g. "28 Sept 2025"). Do NOT convert them to YYYY-MM-DD.
        CRITICAL: Look for "Room 1", "Room 2".
        
        Text Snippet: {text[:25000]}
        
        Return JSON:
        {{
            "hotel_name": "Name", "city": "City", 
            "checkin_raw": "Raw Checkin String", 
            "checkout_raw": "Raw Checkout String", 
            "meal_plan": "Plan", 
            "is_refundable": true/false, 
            "cancel_deadline_raw": "Raw Deadline Date String (if found)",
            "room_size": "Size string",
            "rooms": [
                {{"guest_name": "Guest 1", "confirmation_no": "Conf 1", "room_type": "Type 1", "adults": 2}},
                {{"guest_name": "Guest 2", "confirmation_no": "Conf 2", "room_type": "Type 2", "adults": 2}}
            ]
        }}
        """
        raw = model.generate_content(prompt).text
        return json.loads(raw.replace("```json", "").replace("```", "").strip())
    except: return None

def fetch_hotel_details_text(hotel, city, r_type):
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"""
    Get details for: "{hotel}" in "{city}".
    Return JSON: {{ "addr1": "Street", "addr2": "City/Zip", "phone": "Intl", "in": "3:00 PM", "out": "12:00 PM" }}
    """
    try:
        return json.loads(model.generate_content(prompt).text.replace("```json", "").replace("```", "").strip())
    except: return None

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

# --- 5. PDF DRAWING ---

def draw_vector_seal(c, x, y, size):
    c.saveState()
    color = Color(0.1, 0.2, 0.4)
    c.setStrokeColor(color); c.setFillColor(color); c.setFillAlpha(0.8); c.setStrokeAlpha(0.8); c.setLineWidth(1.5)
    cx, cy = x + size/2, y + size/2
    r_outer = size/2; r_inner = size/2 - 4
    c.circle(cx, cy, r_outer, stroke=1, fill=0)
    c.setLineWidth(0.5); c.circle(cx, cy, r_inner, stroke=1, fill=0)
    
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(cx, cy+4, "ODADUU")
    c.setFont("Helvetica-Bold", 7); c.drawCentredString(cx, cy-6, "TRAVEL DMC")
    
    c.setFont("Helvetica-Bold", 6)
    c.saveState(); c.translate(cx, cy)
    for i, char in enumerate("CERTIFIED VOUCHER"):
        angle = 140 - (i * 12); rad = radians(angle)
        c.saveState(); c.translate((size/2-9)*cos(rad), (size/2-9)*sin(rad)); c.rotate(angle-90); c.drawCentredString(0,0,char); c.restoreState()
    for i, char in enumerate("OFFICIAL"):
        angle = 235 + (i * 12); rad = radians(angle)
        c.saveState(); c.translate((size/2-9)*cos(rad), (size/2-9)*sin(rad)); c.rotate(angle+90); c.drawCentredString(0,0,char); c.restoreState()
    c.restoreState(); c.restoreState()

def generate_pdf(data, info, imgs, rooms_list):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    i_ext = get_img_reader(imgs[0]); i_lobby = get_img_reader(imgs[1]); i_room = get_img_reader(imgs[2])

    for i, room in enumerate(rooms_list):
        # Header / Watermark
        try: c.saveState(); c.setFillAlpha(0.04); c.drawImage("logo.png", w/2-200, h/2-75, 400, 150, mask='auto', preserveAspectRatio=True); c.restoreState()
        except: pass
        try: c.drawImage("logo.png", w/2-80, h-60, 160, 55, mask='auto', preserveAspectRatio=True)
        except: c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 18); c.drawCentredString(w/2, h-50, "ODADUU TRAVEL DMC")

        c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 16)
        title = "HOTEL CONFIRMATION VOUCHER" + (f" (Room {i+1}/{len(rooms_list)})" if len(rooms_list)>1 else "")
        c.drawCentredString(w/2, h-90, title)

        y = h - 120; left = 40
        def draw_sect(title, items):
            nonlocal y
            c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, title)
            y-=5; c.setStrokeColor(lightgrey); c.line(left, y, w-40, y); y-=12
            for lbl, val, b in items:
                c.setFillColor(Color(0.1, 0.1, 0.1)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, lbl)
                c.setFillColor(Color(0.2, 0.2, 0.2)); c.setFont("Helvetica-Bold" if b else "Helvetica", 10)
                c.drawString(left+120, y, str(val)); y-=12
            y-=5

        # Guest Info
        draw_sect("Guest Information", [("Guest Name:", room['guest'], True), ("Confirmation No.:", room['conf'], True), ("Booking Date:", datetime.now().strftime("%d %b %Y"), False)])

        # Hotel Info
        c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Hotel Details"); y-=5; c.line(left, y, w-40, y); y-=12
        c.setFillColor(Color(0.1, 0.1, 0.1)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "Hotel:")
        c.setFillColor(Color(0.2, 0.2, 0.2)); c.setFont("Helvetica-Bold", 10); c.drawString(left+120, y, data['hotel']); y-=12
        c.setFillColor(Color(0.1, 0.1, 0.1)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "Address:")
        c.setFillColor(Color(0.2, 0.2, 0.2)); c.setFont("Helvetica", 10)
        c.drawString(left+120, y, info.get('addr1','')); y-=10
        if info.get('addr2'): c.drawString(left+120, y, info.get('addr2','')); y-=12
        else: y-=2
        
        nights = (data['out'] - data['in']).days
        for l, v in [("Phone:", info.get('phone','')), ("Check-In:", data['in'].strftime("%d %b %Y")), ("Check-Out:", data['out'].strftime("%d %b %Y")), ("Nights:", str(nights))]:
            c.setFillColor(Color(0.1, 0.1, 0.1)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, l)
            c.setFillColor(Color(0.2, 0.2, 0.2)); c.setFont("Helvetica", 10); c.drawString(left+120, y, v); y-=12
        y-=5

        # Room Info
        r_items = [("Room Type:", data['room_type'], False), ("No. of Pax:", f"{data['adults']} Adults", False), ("Meal Plan:", data['meal'], False)]
        if data['room_size']: r_items.append(("Room Size:", data['room_size'], False))
        r_items.append(("Cancellation:", data['policy'], "Refundable" in data['policy']))
        draw_sect("Room Information", r_items)

        # Images
        if i_ext or i_lobby or i_room:
            ix=left; ih=95; iw=160; gap=10
            if i_ext: 
                try: c.drawImage(i_ext, ix, y-ih, iw, ih); ix+=iw+gap
                except: pass
            if i_lobby: 
                try: c.drawImage(i_lobby, ix, y-ih, iw, ih); ix+=iw+gap
                except: pass
            if i_room: 
                try: c.drawImage(i_room, ix, y-ih, iw, ih)
                except: pass
            y -= (ih + 30)
        else: y -= 15

        # Policies
        c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 11); c.drawString(left, y, "HOTEL CHECK-IN & CHECK-OUT POLICY"); y-=15
        pt = [["Policy", "Time / Detail"], ["Standard Check-in:", info.get('in', '3:00 PM')], ["Standard Check-out:", info.get('out', '12:00 PM')], ["Early/Late:", "Subject to availability. Request upon arrival."], ["Required:", "Passport & Credit Card."]]
        t = Table(pt, colWidths=[130, 380])
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),Color(0.05, 0.15, 0.35)), ('TEXTCOLOR',(0,0),(-1,0),Color(1,1,1)), ('FONTNAME',(0,0),(-1,-1),'Helvetica'), ('FONTSIZE',(0,0),(-1,-1),8), ('PADDING',(0,0),(-1,-1),3), ('GRID', (0,0), (-1,-1), 0.5, Color(0.2, 0.2, 0.2))]))
        t.wrapOn(c, w, h); t.drawOn(c, left, y-60); y-=(60+30)

        # T&C (Restored Full Version)
        c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "STANDARD HOTEL BOOKING TERMS & CONDITIONS"); y -= 10
        tnc = [
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
        styles = getSampleStyleSheet(); styleN = styles["Normal"]; styleN.fontSize = 7; styleN.leading = 8
        t_data = [[Paragraph(x, styleN)] for x in tnc]
        t2 = Table(t_data, colWidths=[510])
        t2.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.5,Color(0.2,0.2,0.2)), ('PADDING',(0,0),(-1,-1),2), ('VALIGN',(0,0),(-1,-1),'TOP')]))
        tw, th = t2.wrapOn(c, w, h); t2.drawOn(c, left, y-th)

        # Footer
        draw_vector_seal(c, w-130, 45, 80)
        c.setStrokeColor(Color(1, 0.4, 0)); c.setLineWidth(3); c.line(0, 45, w, 45)
        c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 9); c.drawString(left, 32, "Issued by: Odaduu Travel DMC")
        c.setFillColor(Color(0.2, 0.2, 0.2)); c.setFont("Helvetica", 9); c.drawString(left, 20, "Email: aashwin@odaduu.jp")
        
        c.showPage()
    
    c.save(); buffer.seek(0); return buffer

# --- 6. UI LOGIC ---

st.title("🏯 Odaduu Voucher Generator")

# === UPLOAD ===
with st.expander("📤 Upload Supplier Voucher (PDF)", expanded=True):
    up_file = st.file_uploader("Drop PDF here", type="pdf")
    if up_file:
        if st.session_state.last_uploaded_file != up_file.name:
            with st.spinner("Processing New File..."):
                reset_booking_state()
                data = extract_pdf_data(up_file)
                if data:
                    st.session_state.hotel_name = data.get('hotel_name', '')
                    st.session_state.city = data.get('city', '')
                    
                    # DATE PARSING FIX (Using smart date parser on raw AI output)
                    d_in = parse_smart_date(data.get('checkin_raw'))
                    d_out = parse_smart_date(data.get('checkout_raw'))
                    
                    # CRITICAL FIX 1: DATE SWAPPING LOGIC
                    if d_in and d_out and d_in > d_out:
                        d_in, d_out = d_out, d_in
                        
                    if d_in: st.session_state.checkin = d_in
                    if d_out: st.session_state.checkout = d_out
                    
                    st.session_state.meal_plan = data.get('meal_plan', 'Breakfast Only')
                    
                    # Rooms
                    rooms = data.get('rooms', [])
                    if rooms:
                        st.session_state.num_rooms = len(rooms)
                        
                        # --- FINAL FIX FOR ROOM TYPE STRING ---
                        raw_room_type = rooms[0].get('room_type', '')
                        cleaned_room_type = clean_room_type_string(raw_room_type)
                        
                        st.session_state.room_type = cleaned_room_type # Set the clean value
                        
                        for i, r in enumerate(rooms):
                            st.session_state[f'room_{i}_conf'] = r.get('confirmation_no', '')
                            st.session_state[f'room_{i}_guest'] = r.get('guest_name', '')
                    
                    if st.session_state.hotel_name:
                        st.session_state.room_options = get_room_types(st.session_state.hotel_name)
                        if st.session_state.room_type and st.session_state.room_type not in st.session_state.room_options:
                            st.session_state.room_options.insert(0, st.session_state.room_type)
                    
                    # CANCELLATION LOGIC
                    is_ref = data.get('is_refundable', False)
                    dead_raw = data.get('cancel_deadline_raw')
                    
                    if is_ref and dead_raw:
                        dead_date = parse_smart_date(dead_raw)
                        if dead_date:
                            st.session_state.policy_type = 'Refundable'
                            st.session_state.policy_text_manual = '' 
                            
                            delta = (st.session_state.checkin - dead_date).days
                            st.session_state.cancel_days = max(1, delta)
                    else:
                        st.session_state.policy_type = 'Non-Refundable'
                        st.session_state.policy_text_manual = 'Non-Refundable & Non-Amendable'

                    st.session_state.last_uploaded_file = up_file.name
                    st.success("New Booking Loaded!")
                    st.rerun()

# === MANUAL SEARCH ===
st.markdown("### 🏨 Hotel Details")
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
    # SAFE DATE LOGIC
    curr_in = st.session_state.checkin
    min_out = curr_in + timedelta(days=1)
    if st.session_state.checkout <= curr_in: st.session_state.checkout = min_out

    st.date_input("Check-In", key="checkin")
    st.date_input("Check-Out", key="checkout", min_value=min_out)
    
    opts = st.session_state.room_options.copy()
    current = st.session_state.room_type
    
    if current and current not in opts: 
        opts.insert(0, current)
        
    opts.append("Manual...")
    
    def on_room_change():
        if st.session_state.room_sel != "Manual...": 
            st.session_state.room_type = st.session_state.room_sel
            
    idx = 0
    if current in opts: idx = opts.index(current)
    
    # FINAL FIX: Initialize selectbox key value to ensure correct initial selection
    if st.session_state.room_type and st.session_state.room_type != st.session_state.room_sel:
        st.session_state.room_sel = st.session_state.room_type

    st.selectbox("Room Type", opts, index=idx, key="room_sel", on_change=on_room_change)
    if st.session_state.get("room_sel") == "Manual...": 
        # If manual is selected, the final room type is taken from this text input
        st.text_input("Type Name", value=st.session_state.room_type, key="room_type")
    else:
        # If standard is selected, ensure the final room type tracks the dropdown
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
        imgs = [
            fetch_image(f"{st.session_state.hotel_name} {st.session_state.city} hotel exterior"),
            fetch_image(f"{st.session_state.hotel_name} {st.session_state.city} hotel lobby"),
            fetch_image(f"{st.session_state.hotel_name} {st.session_state.city} {st.session_state.room_type} interior")
        ]
        
        pdf_data = {
            "hotel": st.session_state.hotel_name, "in": st.session_state.checkin, "out": st.session_state.checkout,
            "room_type": st.session_state.room_type, "adults": st.session_state.adults, "meal": st.session_state.meal_plan,
            "policy": policy_txt, "room_size": st.session_state.room_size
        }
        
        pdf_bytes = generate_pdf(pdf_data, info, imgs, rooms_final)
        
    st.success("Done!")
    st.download_button("Download PDF", pdf_bytes, "Voucher.pdf", "application/pdf")
