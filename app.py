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
from reportlab.lib.utils import ImageReader
import pypdf
import textwrap
from math import sin, cos, radians

# --- 1. SETUP & CONFIGURATION ---
st.set_page_config(page_title="Odaduu Voucher Generator", page_icon="🏨", layout="wide")

# Load Keys from secrets.toml
try:
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
    SEARCH_KEY = st.secrets["SEARCH_API_KEY"]
    SEARCH_CX = st.secrets["SEARCH_ENGINE_ID"]
    genai.configure(api_key=GEMINI_KEY)
except Exception:
    st.error("⚠️ Secrets not found! Please check your Streamlit settings.")
    st.stop()

# --- 2. AI FUNCTIONS ---

def get_hotel_suggestions(query):
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"""
    The user is searching for a hotel. Input: "{query}".
    Return a JSON list of the 3 most likely FULL OFFICIAL hotel names.
    Return JSON ONLY: ["Name 1", "Name 2", "Name 3"]
    """
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except: return []

def detect_city_from_hotel(hotel_name):
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"""
    What city is the hotel "{hotel_name}" located in?
    Return ONLY the city name as a string (e.g. "Osaka", "Kyoto", "Tokyo").
    Do not include country or state.
    """
    try:
        return model.generate_content(prompt).text.strip()
    except: return ""

def get_room_types_for_hotel(hotel_name):
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"""
    List 10 common room category names for the hotel: "{hotel_name}".
    Return ONLY a JSON list of strings.
    """
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except: return []

def extract_details_from_pdf(pdf_file):
    try:
        pdf_reader = pypdf.PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
            
        model = genai.GenerativeModel('gemini-2.0-flash')
        prompt = f"""
        Extract booking details. Return JSON ONLY. Use null if not found.
        Text: {text[:10000]}
        Structure:
        {{
            "hotel_name": "Name", "city": "City", "guest_name": "Name",
            "confirmation_no": "Ref", "checkin_date": "YYYY-MM-DD",
            "checkout_date": "YYYY-MM-DD", "room_type": "Type",
            "adults": 2, "meal_plan": "Plan",
            "is_refundable": true/false, "cancel_deadline": "YYYY-MM-DD",
            "room_size": "Size string if found"
        }}
        """
        return json.loads(model.generate_content(prompt).text.replace("```json", "").replace("```", "").strip())
    except: return None

def fetch_hotel_details(hotel_name, city, room_type):
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"""
    Get details for hotel: "{hotel_name}" in "{city}".
    Return JSON ONLY:
    {{
        "address_line_1": "Street", "address_line_2": "City, Zip, Country",
        "phone": "Intl format", "checkin_time": "3:00 PM",
        "checkout_time": "12:00 PM" 
    }}
    """
    try:
        return json.loads(model.generate_content(prompt).text.replace("```json", "").replace("```", "").strip())
    except: return None

def fetch_image_url(query):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"q": query, "cx": SEARCH_CX, "key": SEARCH_KEY, "searchType": "image", "num": 1, "imgSize": "large", "safe": "active"}
    try:
        res = requests.get(url, params=params).json()
        if "items" in res: return res["items"][0]["link"]
    except: pass
    return None

def get_safe_image_reader(url):
    if not url: return None
    try:
        response = requests.get(url, timeout=4)
        if response.status_code == 200:
            return ImageReader(io.BytesIO(response.content))
    except: return None

# --- 3. PDF GENERATION ---

def draw_text_on_arc(c, text, cx, cy, radius, start_angle, end_angle, font_size=8, flip=False):
    """Helper function to draw text along a circular path."""
    c.setFont("Helvetica-Bold", font_size)
    if not text: return

    total_angle = end_angle - start_angle
    angle_step = total_angle / (len(text) - 1) if len(text) > 1 else 0

    c.saveState()
    c.translate(cx, cy)

    current_angle = start_angle
    for char in text:
        c.saveState()
        # Calculate position on circle
        theta = radians(current_angle)
        x_pos = radius * cos(theta)
        y_pos = radius * sin(theta)
        c.translate(x_pos, y_pos)
        
        # Rotate character to align with tangent
        rot_angle = current_angle - 90 if not flip else current_angle + 90
        c.rotate(rot_angle)
        
        c.drawCentredString(0, 0, char)
        c.restoreState()
        current_angle += angle_step
    c.restoreState()

def draw_vector_seal(c, x, y, size):
    """Draws a vector seal with text along the ring."""
    c.saveState()
    
    # Seal style (dark blue, semi-transparent)
    seal_color = Color(0.1, 0.2, 0.4)
    c.setStrokeColor(seal_color)
    c.setFillColor(seal_color)
    c.setFillAlpha(0.8)
    c.setStrokeAlpha(0.8)
    c.setLineWidth(1.5)

    # Geometry
    cx, cy = x + size / 2, y + size / 2
    r_outer = size / 2
    r_inner = r_outer - 4
    r_text = r_inner - 7 # Baseline radius for text

    # Draw Rings
    c.circle(cx, cy, r_outer, stroke=1, fill=0)
    c.setLineWidth(0.5)
    c.circle(cx, cy, r_inner, stroke=1, fill=0)

    # Center Icon (Star)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(cx, cy - 4, "★")

    # Draw Arced Text
    # Top Arc: "CERTIFIED VOUCHER" (spanning approx 150 to 30 degrees)
    draw_text_on_arc(c, "CERTIFIED VOUCHER", cx, cy, r_text, 150, 30, font_size=7)

    # Bottom Arc: "OFFICIAL" (spanning approx 220 to 320 degrees), flipped to be readable at bottom
    draw_text_on_arc(c, "OFFICIAL", cx, cy, r_text, 230, 310, font_size=7, flip=True)

    c.restoreState()

def draw_voucher_page(c, width, height, data, hotel_info, img_exterior, img_room, current_conf_no, room_index, total_rooms):
    odaduu_blue = Color(0.05, 0.15, 0.35); odaduu_orange = Color(1, 0.4, 0)
    text_color = Color(0.2, 0.2, 0.2); label_color = Color(0.1, 0.1, 0.1)
    left = 40; center_x = width / 2
    
    # Watermark
    try:
        c.saveState(); c.setFillAlpha(0.04)
        c.drawImage("logo.png", center_x - 200, height/2 - 75, width=400, height=150, mask='auto', preserveAspectRatio=True)
        c.restoreState()
    except: pass

    # Header
    try: c.drawImage("logo.png", center_x - 80, height - 60, width=160, height=55, mask='auto', preserveAspectRatio=True)
    except: c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 18); c.drawCentredString(center_x, height - 50, "ODADUU TRAVEL DMC")

    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 16)
    title = "HOTEL CONFIRMATION VOUCHER" + (f" (Room {room_index}/{total_rooms})" if total_rooms > 1 else "")
    c.drawCentredString(center_x, height - 90, title)

    def row(y, label, val, bold=False):
        c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, label)
        c.setFillColor(text_color); c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        c.drawString(left + 120, y, str(val)); return y - 12

    y = height - 120

    # Guest
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Guest Information")
    y -= 5; c.setStrokeColor(lightgrey); c.line(left, y, width-40, y); y -= 12
    y = row(y, "Guest Name:", data['guest_name'], True)
    y = row(y, "Confirmation No.:", current_conf_no, True)
    y = row(y, "Booking Date:", datetime.now().strftime("%d %b %Y"))
    y -= 5

    # Hotel
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Hotel Details")
    y -= 5; c.line(left, y, width-40, y); y -= 12
    c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "Hotel:")
    c.setFillColor(text_color); c.setFont("Helvetica-Bold", 10); c.drawString(left + 120, y, data['hotel_name']); y -= 12
    c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "Address:")
    c.setFillColor(text_color); c.setFont("Helvetica", 10)
    c.drawString(left + 120, y, hotel_info.get("address_line_1", "")); y -= 10
    if hotel_info.get("address_line_2"): c.drawString(left + 120, y, hotel_info.get("address_line_2", "")); y -= 12
    else: y -= 2
    y = row(y, "Phone:", hotel_info.get("phone", ""))
    y = row(y, "Check-In:", data['checkin'].strftime("%d %b %Y"))
    y = row(y, "Check-Out:", data['checkout'].strftime("%d %b %Y"))
    y = row(y, "Nights:", str((data['checkout'] - data['checkin']).days))
    y -= 5

    # Room
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Room Information")
    y -= 5; c.line(left, y, width-40, y); y -= 12
    y = row(y, "Room Type:", data['room_type'])
    y = row(y, "No. of Pax:", f"{data['adults']} Adults")
    y = row(y, "Meal Plan:", data['meal_plan'])
    if data.get('room_size'): y = row(y, "Room Size:", data['room_size'])
    y = row(y, "Cancellation:", data['policy_text'], "Refundable" in data['policy_text'])
    y -= 5

    # Images
    if img_exterior or img_room:
        img_h = 95; img_w = 180; img_y = y - img_h
        if img_exterior:
            try: c.drawImage(img_exterior, left, img_y, width=img_w, height=img_h)
            except: pass
        if img_room:
            try: c.drawImage(img_room, left + img_w + 10, img_y, width=img_w, height=img_h)
            except: pass
        y = img_y - 35
    else:
        y -= 15

    # Policy Table
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 11); c.drawString(left, y, "HOTEL CHECK-IN & CHECK-OUT POLICY"); y -= 15
    data_t = [["Policy", "Time / Detail"], 
              ["Standard Check-in Time:", hotel_info.get("checkin_time", "3:00 PM")], 
              ["Standard Check-out Time:", hotel_info.get("checkout_time", "12:00 PM")],
              ["Early Check-in/Late Out:", "Subject to availability. Request upon arrival."],
              ["Required at Check-in:", "Passport & Credit Card/Cash Deposit."]]
    t = Table(data_t, colWidths=[130, 380])
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),odaduu_blue),
        ('TEXTCOLOR',(0,0),(-1,0),Color(1,1,1)),
        ('FONTNAME',(0,0),(-1,-1),'Helvetica'),
        ('FONTSIZE',(0,0),(-1,-1),8),
        ('PADDING',(0,0),(-1,-1),3),
        ('GRID', (0,0), (-1,-1), 0.5, Color(0.2, 0.2, 0.2))
    ]))
    t.wrapOn(c, width, height); t.drawOn(c, left, y - 60); 
    
    y -= (60 + 30)

    # T&C - GRID TABLE VERSION
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "STANDARD HOTEL BOOKING TERMS & CONDITIONS"); y -= 10
    
    tnc_raw = [
        "1. Voucher Validity: This voucher is for the dates and services specified above. It must be presented at the hotel's front desk upon arrival.",
        f"2. Identification: The lead guest, {data['guest_name']}, must be present at check-in and must present valid government-issued photo identification (e.g., Passport).",
        "3. No-Show Policy: In the event of a \"no-show\" (failure to check in without prior cancellation), the hotel reserves the right to charge a fee, typically equivalent to the full cost of the stay.",
        "4. Payment/Incidental Charges: The reservation includes the room and breakfast as specified. Any other charges (e.g., mini-bar, laundry, extra services, parking) must be settled by the guest directly with the hotel upon check-out.",
        f"5. Occupancy: The room is confirmed for {data['adults']} Adults. Any change in occupancy must be approved by the hotel and may result in additional charges.",
        "6. Hotel Rights: The hotel reserves the right to refuse admission or request a guest to leave for inappropriate conduct or failure to follow hotel policies.",
        "7. Liability: The hotel is not responsible for the loss or damage of personal belongings, including valuables, unless they are deposited in the hotel's safety deposit box (if available).",
        "8. Reservation Non-Transferable: This booking is non-transferable and may not be resold.",
        "9. City Tax: City tax (if any) is not included and must be paid and settled directly at the hotel.",
        "10. Bed Type: Bed type is subject to availability and cannot be guaranteed."
    ]
    
    styles = getSampleStyleSheet()
    styleTNC = styles["Normal"]
    styleTNC.fontSize = 7
    styleTNC.leading = 8
    
    tnc_data = [[Paragraph(item, styleTNC)] for item in tnc_raw]
    
    t_tnc = Table(tnc_data, colWidths=[510])
    t_tnc.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, Color(0.2, 0.2, 0.2)),
        ('PADDING', (0,0), (-1,-1), 2),
        ('VALIGN', (0,0), (-1,-1), 'TOP')
    ]))
    
    w_tnc, h_tnc = t_tnc.wrapOn(c, width, height)
    t_tnc.drawOn(c, left, y - h_tnc)

    # --- NEW: VECTOR AUTHENTICATION SEAL WITH ARCED TEXT ---
    # Draws the seal directly onto the PDF.
    draw_vector_seal(c, width - 130, 65, 80)

    # Footer
    c.setStrokeColor(odaduu_orange); c.setLineWidth(3); c.line(0, 45, width, 45)
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 9); c.drawString(left, 32, "Issued by: Odaduu Travel DMC")
    c.setFillColor(text_color); c.setFont("Helvetica", 9); c.drawString(left, 20, "Email: aashwin@odaduu.jp")

def generate_multipage_pdf(data, hotel_info, img_exterior_url, img_room_url, conf_numbers_list):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    img_ext = get_safe_image_reader(img_exterior_url)
    img_room = get_safe_image_reader(img_room_url)
    
    for i, conf in enumerate(conf_numbers_list):
        draw_voucher_page(c, width, height, data, hotel_info, img_ext, img_room, conf, i+1, len(conf_numbers_list))
        c.showPage()
    c.save(); buffer.seek(0); return buffer

# --- 4. UI LOGIC ---
if 'form_data' not in st.session_state: st.session_state.form_data = {}
if 'room_options' not in st.session_state: st.session_state.room_options = []

def get_val(k, d): return st.session_state.form_data.get(k, d)

st.title("🏯 Odaduu Voucher Generator")

# === 1. SMART HOTEL SEARCH ===
st.markdown("### 🏨 1. Find Hotel")
col_search, col_res = st.columns([2, 1])
with col_search:
    search_q = st.text_input("Type partial hotel name & press Enter", key="search_box")
if search_q:
    with col_res:
        with st.spinner("Finding..."):
            suggestions = get_hotel_suggestions(search_q)
            if suggestions:
                selected_hotel = st.radio("Select Correct Hotel:", suggestions)
                if selected_hotel:
                    if st.session_state.form_data.get('hotel_name') != selected_hotel:
                        st.session_state.form_data['hotel_name'] = selected_hotel
                        
                        # AUTO DETECT CITY
                        with st.spinner("Detecting details..."):
                            st.session_state.room_options = get_room_types_for_hotel(selected_hotel)
                            detected_city = detect_city_from_hotel(selected_hotel)
                            if detected_city:
                                st.session_state.form_data['city'] = detected_city
                                
                    st.success(f"Selected! City: {st.session_state.form_data.get('city', '')}")

# === 2. UPLOAD ===
with st.expander("📤 Upload Supplier Voucher (Optional)", expanded=False):
    uploaded_file = st.file_uploader("Drop PDF here", type="pdf")
    if uploaded_file and st.session_state.get('last_uploaded') != uploaded_file.name:
        with st.spinner("Reading PDF..."):
            extracted = extract_details_from_pdf(uploaded_file)
            if extracted:
                st.session_state.form_data.update(extracted)
                st.session_state.last_uploaded = uploaded_file.name
                if extracted.get('hotel_name'):
                    st.session_state.room_options = get_room_types_for_hotel(extracted['hotel_name'])
                st.success("Data Extracted!")

# === 3. DETAILS FORM ===
st.markdown("### 📝 2. Booking Details")
col1, col2 = st.columns(2)
with col1:
    hotel_name = st.text_input("Hotel Name", value=get_val("hotel_name", ""))
    city = st.text_input("City", value=get_val("city", "Osaka"))
    guest_name = st.text_input("Lead Guest Name", value=get_val("guest_name", ""))
    
    st.subheader("Room Details")
    num_rooms = st.number_input("Number of Rooms", 1, value=1)
    conf_numbers = []
    default_conf = get_val("confirmation_no", "")
    if num_rooms > 1:
        if st.checkbox("Same Confirmation No?", True):
            main_conf = st.text_input("Confirmation No (All)", value=default_conf)
            conf_numbers = [main_conf] * num_rooms
        else:
            for i in range(num_rooms): conf_numbers.append(st.text_input(f"Room {i+1} Conf No", value=default_conf))
    else: conf_numbers.append(st.text_input("Confirmation No", value=default_conf))

    st.subheader("Policy")
    def_pol = "Refundable" if get_val("is_refundable", False) else "Non-Refundable"
    policy_type = st.radio("Refundable?", ["Non-Refundable", "Refundable"], index=0 if def_pol=="Non-Refundable" else 1, horizontal=True)

with col2:
    try: d_in = datetime.strptime(get_val("checkin_date",""),"%Y-%m-%d").date()
    except: d_in = datetime.now().date()
    try: d_out = datetime.strptime(get_val("checkout_date",""),"%Y-%m-%d").date()
    except: d_out = d_in + timedelta(days=1)
    
    checkin = st.date_input("Check-In", value=d_in)
    checkout = st.date_input("Check-Out", value=d_out)
    
    current_room_val = get_val("room_type", "")
    options = st.session_state.room_options.copy()
    if current_room_val and current_room_val not in options: options.insert(0, current_room_val)
    options.append("Type Manually...")
    
    sel_room = st.selectbox("Room Type", options)
    room_type = st.text_input("Enter Room Name", "") if sel_room == "Type Manually..." else sel_room

    adults = st.number_input("Adults", 1, value=get_val("adults", 2))
    room_size = st.text_input("Room Size (Optional - e.g. 35 sqm)", value=get_val("room_size", ""), help="Leave blank to hide in PDF")
    
    opts = ["Breakfast Only", "Room Only", "Half Board", "Full Board"]
    def_meal = get_val("meal_plan", "Breakfast Only")
    meal_plan = st.selectbox("Meal Plan", opts, index=opts.index(def_meal) if def_meal in opts else 0)
    
    if policy_type == "Refundable":
        days = st.number_input("Free cancel days before?", min_value=1, value=3)
        policy_text = f"Free Cancellation until {(checkin - timedelta(days=days)).strftime('%d %b %Y')}"
        st.info(f"Voucher says: {policy_text}")
    else: policy_text = "Non-Refundable & Non-Amendable"

if st.button("✨ Generate Voucher", type="primary"):
    if not hotel_name: st.warning("Enter Hotel Name")
    else:
        with st.status("Working...") as status:
            status.write(f"Fetching info for {hotel_name}...")
            info = fetch_hotel_details(hotel_name, city, room_type)
            status.write("Finding images...")
            img_ext = fetch_image_url(f"{hotel_name} {city} hotel exterior")
            img_room = fetch_image_url(f"{hotel_name} {city} {room_type} interior")
            
            data = {"hotel_name": hotel_name, "guest_name": guest_name, "checkin": checkin, "checkout": checkout, 
                    "room_type": room_type, "adults": adults, "meal_plan": meal_plan, 
                    "policy_text": policy_text, "room_size": room_size}
            pdf = generate_multipage_pdf(data, info, img_ext, img_room, conf_numbers)
            
            status.update(label="Done!", state="complete")
            st.download_button("⬇️ Download PDF", pdf, f"Voucher_{guest_name.replace(' ','_')}.pdf", "application/pdf")
