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
from reportlab.lib.utils import ImageReader
import re

# --- 1. SETUP & CONFIGURATION ---
st.set_page_config(page_title="Fly Goldfinch Voucher Generator", page_icon="✈️", layout="wide")

try:
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
    SEARCH_KEY = st.secrets["SEARCH_API_KEY"]
    SEARCH_CX = st.secrets["SEARCH_ENGINE_ID"]
    genai.configure(api_key=GEMINI_KEY)
except Exception:
    st.error("⚠️ Secrets not found! Please check your Streamlit settings.")
    st.stop()

# --- 2. SESSION STATE ---
if 'ai_room_str' not in st.session_state:
    st.session_state.ai_room_str = "" # Start empty
if 'hotel_name' not in st.session_state:
    st.session_state.hotel_name = ""
if 'checkin' not in st.session_state:
    st.session_state.checkin = datetime.now().date()
if 'checkout' not in st.session_state:
    st.session_state.checkout = datetime.now().date() + timedelta(days=1)
if 'cancel_days' not in st.session_state:
    st.session_state.cancel_days = 3

# --- 3. HELPER FUNCTIONS ---

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

def clean_extracted_text(raw_val):
    """Basic cleanup to remove obvious JSON artifacts."""
    s = str(raw_val).strip()
    # Kill JSON artifacts
    if s.startswith("{") or s.startswith("[") or '"' in s or "'" in s:
        s = s.replace('"', '').replace("'", "").replace("{", "").replace("}", "")
    
    # If it looks like a variable name, clear it
    if s.lower() in ["room_name", "room_type", "room type"]:
        return ""
        
    return s

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

def extract_pdf_data(pdf_file):
    try:
        pdf_reader = pypdf.PdfReader(pdf_file)
        text = "\n".join([p.extract_text() for p in pdf_reader.pages])
        
        model = genai.GenerativeModel('gemini-2.0-flash')
        prompt = f"""
        Extract booking details.
        
        CRITICAL: Return DATES exactly as they appear (e.g. "28 Sept 2025"). 
        CRITICAL: For 'room_type', extract the ACTUAL name (e.g. "Deluxe King"). Do NOT return "room_name".
        
        Text Snippet: {text[:25000]}
        
        Return JSON:
        {{
            "hotel_name": "Name", "city": "City", 
            "checkin_raw": "Raw Checkin String", 
            "checkout_raw": "Raw Checkout String", 
            "meal_plan": "Plan", 
            "is_refundable": true/false, 
            "cancel_deadline_raw": "Raw Deadline Date String",
            "room_size": "Size string",
            "rooms": [
                {{"guest_name": "Guest 1", "confirmation_no": "Conf 1", "room_type": "Type 1", "adults": 2}}
            ]
        }}
        """
        raw = model.generate_content(prompt).text
        return json.loads(raw.replace("```json", "").replace("```", "").strip())
    except: return None

def fetch_hotel_details_text(hotel, city):
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"""
    Get details for: "{hotel}" in "{city}".
    Return JSON: {{ "addr1": "Street", "addr2": "City/Zip", "phone": "Intl", "in": "3:00 PM", "out": "12:00 PM" }}
    """
    try:
        return json.loads(model.generate_content(prompt).text.replace("```json", "").replace("```", "").strip())
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

# --- 5. PDF GENERATION ---

def draw_vector_seal(c, x, y, size):
    c.saveState()
    fg_blue = Color(0.0, 0.25, 0.5) 
    c.setStrokeColor(fg_blue); c.setFillColor(fg_blue); c.setFillAlpha(0.8); c.setStrokeAlpha(0.8); c.setLineWidth(1.5)
    cx, cy = x + size/2, y + size/2
    r_outer = size/2; r_inner = size/2 - 4
    c.circle(cx, cy, r_outer, stroke=1, fill=0)
    c.setLineWidth(0.5); c.circle(cx, cy, r_inner, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(cx, cy+4, "FLY")
    c.setFont("Helvetica-Bold", 7); c.drawCentredString(cx, cy-6, "GOLDFINCH")
    c.restoreState()

def generate_pdf(data, info, imgs, rooms_list):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    i_ext = get_img_reader(imgs[0]); i_lobby = get_img_reader(imgs[1]); i_room = get_img_reader(imgs[2])
    
    fg_blue = Color(0.0, 0.25, 0.5); fg_gold = Color(0.9, 0.75, 0.1) 
    text_color = Color(0.2, 0.2, 0.2); label_color = Color(0.1, 0.1, 0.1)

    for i, room in enumerate(rooms_list):
        if i > 0: c.showPage()
        
        # Logo placeholder
        try: c.drawImage("fg_logo.png", w/2-80, h-60, 160, 55, mask='auto', preserveAspectRatio=True)
        except: c.setFillColor(fg_blue); c.setFont("Helvetica-Bold", 18); c.drawCentredString(w/2, h-50, "FLY GOLDFINCH")

        c.setFillColor(fg_blue); c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(w/2, h-90, "HOTEL CONFIRMATION VOUCHER")

        y = h - 120; left = 40
        
        # 1. Guest Info
        c.setFillColor(fg_blue); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Guest Information")
        y-=15; c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "Guest Name:")
        c.setFillColor(text_color); c.drawString(left+100, y, room['guest'])
        y-=12; c.setFillColor(label_color); c.drawString(left, y, "Conf No:")
        c.setFillColor(text_color); c.drawString(left+100, y, room['conf'])
        y-=20

        # 2. Hotel Info
        c.setFillColor(fg_blue); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Hotel Details")
        y-=15; c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "Hotel:")
        c.setFillColor(text_color); c.drawString(left+100, y, data['hotel'])
        y-=12; c.setFillColor(label_color); c.drawString(left, y, "Check-in:")
        c.setFillColor(text_color); c.drawString(left+100, y, data['in'].strftime("%d %b %Y"))
        y-=12; c.setFillColor(label_color); c.drawString(left, y, "Check-out:")
        c.setFillColor(text_color); c.drawString(left+100, y, data['out'].strftime("%d %b %Y"))
        y-=20

        # 3. Room Info
        c.setFillColor(fg_blue); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Room Information")
        y-=15; c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "Room Type:")
        c.setFillColor(text_color); c.drawString(left+100, y, data['room_type']) 
        y-=12; c.setFillColor(label_color); c.drawString(left, y, "Meal Plan:")
        c.setFillColor(text_color); c.drawString(left+100, y, data['meal'])
        
        if data['room_size']:
            y-=12; c.setFillColor(label_color); c.drawString(left, y, "Size:")
            c.setFillColor(text_color); c.drawString(left+100, y, data['room_size'])
            
        y-=12; c.setFillColor(label_color); c.drawString(left, y, "Policy:")
        c.setFillColor(text_color); c.drawString(left+100, y, data['policy'])
        y-=20

        # Images
        if i_ext: 
            try: c.drawImage(i_ext, left, y-100, 160, 95)
            except: pass
        
        # Footer
        c.setStrokeColor(fg_gold); c.setLineWidth(3); c.line(0, 45, w, 45)
        draw_vector_seal(c, w-130, 45, 80)
        
    c.save(); buffer.seek(0); return buffer

# --- 6. UI LOGIC ---

st.title("✈️ Fly Goldfinch Voucher Generator")

# === UPLOAD ===
with st.expander("📤 Upload Supplier Voucher (PDF)", expanded=True):
    up_file = st.file_uploader("Drop PDF here", type="pdf")
    if up_file:
        with st.spinner("Processing..."):
            data = extract_pdf_data(up_file)
            if data:
                st.session_state.hotel_name = data.get('hotel_name', '')
                st.session_state.city = data.get('city', '')
                
                # Dates
                d_in = parse_smart_date(data.get('checkin_raw'))
                d_out = parse_smart_date(data.get('checkout_raw'))
                if d_in and d_out and d_in > d_out: d_in, d_out = d_out, d_in
                if d_in: st.session_state.checkin = d_in
                if d_out: st.session_state.checkout = d_out
                
                st.session_state.meal_plan = data.get('meal_plan', 'Breakfast Only')
                
                # Rooms - CLEAN STRING LOGIC
                rooms = data.get('rooms', [])
                if rooms:
                    st.session_state.num_rooms = len(rooms)
                    raw_type = rooms[0].get('room_type', '')
                    st.session_state.ai_room_str = clean_extracted_text(raw_type)
                    
                    for i, r in enumerate(rooms):
                        st.session_state[f'room_{i}_conf'] = r.get('confirmation_no', '')
                        st.session_state[f'room_{i}_guest'] = r.get('guest_name', '')
                
                # Refundable Logic
                is_ref = data.get('is_refundable', False)
                dead_raw = data.get('cancel_deadline_raw')
                if is_ref:
                    st.session_state.policy_type = 'Refundable'
                    if dead_raw:
                        d_date = parse_smart_date(dead_raw)
                        if d_date:
                            st.session_state.cancel_days = max(1, (st.session_state.checkin - d_date).days)
                else:
                    st.session_state.policy_type = 'Non-Refundable'

                st.success("Loaded!")

# === FORM ===
c1, c2 = st.columns(2)
with c1:
    st.text_input("Hotel Name", key="hotel_name")
    st.text_input("City", key="city")
    
    st.subheader("Rooms")
    n_rooms = st.number_input("Count", 1, 10, key="num_rooms")
    same_conf = st.checkbox("Same Confirmation No for all rooms?", key="same_conf_check")
    
    for i in range(n_rooms):
        col_g, col_c = st.columns([2, 1])
        with col_g: st.text_input(f"Room {i+1} Guest Name", key=f"room_{i}_guest")
        with col_c: 
            if i == 0 or not same_conf:
                st.text_input(f"Conf No (Room {i+1})", key=f"room_{i}_conf")
            else:
                st.caption(f"*(Same as Room 1)*")

with c2:
    st.date_input("Check-In", key="checkin")
    st.date_input("Check-Out", key="checkout")
    
    # --- SIMPLIFIED ROOM TYPE ---
    # No dropdown/manual switch. Just a text input pre-filled with the AI result.
    # User can edit it freely.
    final_room_type = st.text_input("Room Type Name", value=st.session_state.ai_room_str, help="Auto-filled from PDF. Edit if incorrect.")

    st.number_input("Adults", 1, key="adults")
    st.text_input("Size (Optional)", key="room_size")
    st.selectbox("Meal", ["Breakfast Only", "Room Only", "Half Board", "Full Board"], key="meal_plan")
    
    # --- RESTORED POLICY LOGIC ---
    st.subheader("Cancellation Policy")
    ptype = st.radio("Type", ["Non-Refundable", "Refundable"], index=0 if st.session_state.get('policy_type') == 'Non-Refundable' else 1, horizontal=True)
    
    final_policy_text = "Non-Refundable & Non-Amendable"
    if ptype == "Refundable":
        days = st.number_input("Free Cancel Days Before Check-in", 0, value=st.session_state.get('cancel_days', 3))
        cancel_date = st.session_state.checkin - timedelta(days=days)
        final_policy_text = f"Free Cancellation until {cancel_date.strftime('%d %b %Y')}"
        st.info(f"Policy: {final_policy_text}")
    else:
        st.info(f"Policy: {final_policy_text}")

if st.button("Generate Voucher", type="primary"):
    with st.spinner("Generating..."):
        rooms_final = []
        primary_conf = st.session_state.get('room_0_conf', '')
        
        for i in range(st.session_state.num_rooms):
            this_conf = primary_conf if same_conf else st.session_state.get(f'room_{i}_conf', '')
            rooms_final.append({
                "guest": st.session_state.get(f'room_{i}_guest', ''),
                "conf": this_conf
            })
            
        info = fetch_hotel_details_text(st.session_state.hotel_name, st.session_state.city)
        imgs = [fetch_image(f"{st.session_state.hotel_name} exterior"), None, None]
        
        pdf_data = {
            "hotel": st.session_state.hotel_name, "in": st.session_state.checkin, "out": st.session_state.checkout,
            "room_type": final_room_type, 
            "adults": st.session_state.adults, "meal": st.session_state.meal_plan,
            "policy": final_policy_text, "room_size": st.session_state.room_size
        }
        
        pdf_bytes = generate_pdf(pdf_data, info, imgs, rooms_final)
        
    st.success("Done!")
    st.download_button("Download PDF", pdf_bytes, "Voucher.pdf", "application/pdf")
