import streamlit as st
import google.generativeai as genai
import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color, lightgrey
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from datetime import datetime, timedelta
import io
import json
import pypdf
from reportlab.lib.utils import ImageReader
import textwrap
from math import sin, cos, radians
import re

# --- 1. SETUP & CONFIGURATION ---
st.set_page_config(page_title="Fly Goldfinch Voucher Generator", page_icon="✈️", layout="wide")

try:
    # Use the same API keys
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
    SEARCH_KEY = st.secrets["SEARCH_API_KEY"]
    SEARCH_CX = st.secrets["SEARCH_ENGINE_ID"]
    genai.configure(api_key=GEMINI_KEY)
except Exception:
    st.error("⚠️ Secrets not found! Please check your Streamlit settings and ensure all three keys (GEMINI_API_KEY, SEARCH_API_KEY, SEARCH_ENGINE_ID) are present.")
    # Stop the app if crucial keys are missing
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
        'room_sel': '' # Used for the selectbox display value
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

# --- 3. HELPER FUNCTIONS (FIX APPLIED HERE) ---

def parse_smart_date(date_str):
    """Parses various date strings into datetime.date objects."""
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
    """
    FIXED BUG: Aggressively cleans room type string against AI's inconsistent JSON/quote output.
    """
    if not isinstance(raw_type, str): return str(raw_type)
    
    cleaned_str = str(raw_type).strip()

    # 1. Attempt to handle redundant JSON wrapper (e.g., '{"room_name": "..."}')
    if cleaned_str.startswith(('{', '[')) and cleaned_str.endswith(('}', ']')):
        try:
            temp_data = json.loads(cleaned_str)
            if isinstance(temp_data, dict):
                # Try to extract the first value from the dictionary (where the name usually is)
                value = list(temp_data.values())[0] if temp_data else cleaned_str
            elif isinstance(temp_data, list) and temp_data:
                # Try to extract the first element from the list
                value = temp_data[0]
            else:
                value = cleaned_str
            cleaned_str = str(value)
        except json.JSONDecodeError:
            pass
            
    # 2. Aggressive cleanup: remove any leading/trailing quotes, braces, and brackets
    cleaned_str = re.sub(r'^[\'\"\[\]\{\}\s]+|[\'\"\[\]\{\}\s]+$', '', cleaned_str)
    
    return cleaned_str.strip() # Final cleanup

# --- 4. AI FUNCTIONS (No functional change) ---

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
        # Using placeholder since search keys are needed
        # In a production environment, this requires valid SEARCH_API_KEY and SEARCH_CX
        return None # Return None to prevent crashes

    except: return None

def get_img_reader(url):
    if not url: return None
    try:
        r = requests.get(url, timeout=4)
        if r.status_code == 200: return ImageReader(io.BytesIO(r.content))
    except: return None

# --- 5. PDF DRAWING (Rebranded for Fly Goldfinch) ---

def draw_vector_seal(c, x, y, size):
    c.saveState()
    # FLY GOLDFINCH COLORS: Dark Blue and Gold
    fg_blue = Color(0.0, 0.25, 0.5) 
    
    c.setStrokeColor(fg_blue); c.setFillColor(fg_blue); c.setFillAlpha(0.8); c.setStrokeAlpha(0.8); c.setLineWidth(1.5)
    cx, cy = x + size/2, y + size/2
    r_outer = size/2; r_inner = size/2 - 4
    c.circle(cx, cy, r_outer, stroke=1, fill=0)
    c.setLineWidth(0.5); c.circle(cx, cy, r_inner, stroke=1, fill=0)
    
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(cx, cy+4, "FLY")
    c.setFont("Helvetica-Bold", 7); c.drawCentredString(cx, cy-6, "GOLDFINCH")
    
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
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    Story = []
    
    # PDF generation logic remains the same (drawing on canvas is handled differently in simpledoc template)
    # We simplify this back to canvas drawing for reliability as per the original code structure.
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    
    # Dummy Image Readers to prevent crash
    i_ext = get_img_reader(imgs[0])
    i_lobby = get_img_reader(imgs[1])
    i_room = get_img_reader(imgs[2])

    # BRANDING COLORS
    fg_blue = Color(0.0, 0.25, 0.5)
    fg_gold = Color(0.9, 0.75, 0.1) 
    text_color = Color(0.2, 0.2, 0.2)
    label_color = Color(0.1, 0.1, 0.1)

    for i, room in enumerate(rooms_list):
        # Header / Logo (Simplified Draw for example)
        c.setFillColor(fg_blue); c.setFont("Helvetica-Bold", 18); c.drawCentredString(w/2, h-50, "FLY GOLDFINCH")

        c.setFillColor(fg_blue); c.setFont("Helvetica-Bold", 16)
        title = "HOTEL CONFIRMATION VOUCHER" + (f" (Room {i+1}/{len(rooms_list)})" if len(rooms_list)>1 else "")
        c.drawCentredString(w/2, h-90, title)

        y = h - 120; left = 40 
        
        # Room/Guest Logic Drawing (Simplified for this function block)
        c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, f"Guest: {room['guest']}")
        y -= 12
        c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, f"Conf No: {room['conf']}")
        y -= 30
        
        # Footer
        draw_vector_seal(c, w-130, 45, 80)
        c.setStrokeColor(fg_gold); c.setLineWidth(3); c.line(0, 45, w, 45) # Gold Footer Line

        c.showPage()
    
    c.save(); buffer.seek(0); return buffer

# --- 6. UI LOGIC (Only the Room Type Fix is Shown in Detail) ---

st.title("✈️ Fly Goldfinch Voucher Generator")

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
                    # ... [Omitted parsing logic] ...

                    # --- FIX: Clean Room Type String APPLIED HERE ---
                    rooms = data.get('rooms', [])
                    if rooms:
                        raw_room_type = rooms[0].get('room_type', '')
                        st.session_state.room_type = clean_room_type_string(raw_room_type)
                    # --- FIX END ---
                    
                    # ... [Omitted rest of parsing logic] ...
                    
                    st.session_state.last_uploaded_file = up_file.name
                    st.success("New Booking Loaded!")
                    st.rerun()

# === MANUAL SEARCH ===
# ... [Omitted Search logic] ...

# === FORM ===
c1, c2 = st.columns(2)
with c1:
    st.text_input("Hotel Name", key="hotel_name")
    st.text_input("City", key="city")
    # ... [Omitted Guest/Conf Inputs] ...
    
with c2:
    # ... [Omitted Date/Adults/Meal Inputs] ...

    # ROOM TYPE LOGIC (FIX APPLIED HERE)
    opts = st.session_state.room_options.copy()
    current_room_name = st.session_state.room_type
    
    if current_room_name and current_room_name not in opts: 
        opts.insert(0, current_room_name)
        
    opts.append("Manual...")
    
    def on_room_change():
        # This function updates room_type when a standard option is selected
        if st.session_state.room_sel != "Manual...": 
            st.session_state.room_type = st.session_state.room_sel
            
    idx = 0
    if current_room_name in opts: idx = opts.index(current_room_name)
    
    # FINAL FIX: Set the selectbox value based on extracted data
    if st.session_state.room_type and st.session_state.room_type != st.session_state.room_sel:
        st.session_state.room_sel = st.session_state.room_type

    st.selectbox("Room Type", opts, index=idx, key="room_sel", on_change=on_room_change)
    
    # Conditional Input Logic (FIX APPLIED HERE)
    manual_input_key = "room_type_manual_input" # Consistent key for text input

    if st.session_state.get("room_sel") == "Manual...": 
        # When manual is selected, the final room type is taken from this text input
        manual_val = st.text_input("Type Name", value=current_room_name, key=manual_input_key)
        st.session_state.room_type = manual_val
    else:
        # When standard option is selected, room_type is set by the selectbox value
        st.session_state.room_type = st.session_state.room_sel

    # ... [Omitted Policy/Generate Logic] ...
    
    if st.button("Generate Voucher", type="primary"):
        st.success("PDF generation triggered...") # Placeholder for full logic
