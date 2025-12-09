import streamlit as st
import google.generativeai as genai
import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color, lightgrey
from reportlab.platypus import Table, TableStyle
from datetime import datetime, timedelta
import io
import json
from reportlab.lib.utils import ImageReader
import pypdf

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

def extract_details_from_pdf(pdf_file):
    try:
        pdf_reader = pypdf.PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
            
        model = genai.GenerativeModel('gemini-2.0-flash')
        prompt = f"""
        Extract booking details from text. Return JSON ONLY. Use null if not found.
        Text: {text[:10000]}
        Required JSON Structure:
        {{
            "hotel_name": "Name of hotel",
            "city": "City name",
            "guest_name": "Lead guest name",
            "confirmation_no": "Booking/Confirmation reference number",
            "checkin_date": "YYYY-MM-DD",
            "checkout_date": "YYYY-MM-DD",
            "room_type": "Room category description",
            "adults": 2, 
            "meal_plan": "Breakfast Only/Room Only/etc",
            "is_refundable": true/false,
            "cancel_deadline": "YYYY-MM-DD (if found)"
        }}
        """
        response = model.generate_content(prompt)
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except: return None

def fetch_hotel_details(hotel_name, city, room_type):
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"""
    Get details for hotel: "{hotel_name}" in "{city}", room: "{room_type}".
    Return JSON ONLY:
    {{
        "address_line_1": "Street address",
        "address_line_2": "City, Zip, Country",
        "phone": "International format phone number",
        "checkin_time": "Standard checkin time (e.g. 3:00 PM)",
        "checkout_time": "Standard checkout time (e.g. 11:00 AM)",
        "room_size": "Room size in sqm or sqft (e.g. 35 sqm / 376 sqft)"
    }}
    """
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except: return None

def fetch_image_url(query):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"q": query, "cx": SEARCH_CX, "key": SEARCH_KEY, "searchType": "image", "num": 1, "imgSize": "large", "safe": "active"}
    try:
        res = requests.get(url, params=params).json()
        if "items" in res: return res["items"][0]["link"]
    except: pass
    return None

def download_image(url):
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200: return io.BytesIO(response.content)
    except: return None

# --- 3. PDF GENERATION (COMPACT LAYOUT) ---
def draw_voucher_page(c, width, height, data, hotel_info, img_exterior, img_room, current_conf_no, room_index, total_rooms):
    odaduu_blue = Color(0.05, 0.15, 0.35)
    odaduu_orange = Color(1, 0.4, 0)
    text_color = Color(0.2, 0.2, 0.2)
    label_color = Color(0.1, 0.1, 0.1)
    
    left_margin = 40
    right_margin = width - 40
    center_x = width / 2
    
    # 1. Watermark (Lighter: 4%)
    try:
        c.saveState()
        c.setFillAlpha(0.04)
        wm_width = 400; wm_height = 150 
        c.drawImage("logo.png", center_x - (wm_width/2), height/2 - (wm_height/2), width=wm_width, height=wm_height, mask='auto', preserveAspectRatio=True)
        c.restoreState()
    except: pass

    # 2. Header
    try:
        logo_w = 160; logo_h = 55
        c.drawImage("logo.png", center_x - (logo_w/2), height - 75, width=logo_w, height=logo_h, mask='auto', preserveAspectRatio=True)
    except:
        c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 18); c.drawCentredString(center_x, height - 60, "ODADUU TRAVEL DMC")

    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 16)
    title_text = "HOTEL CONFIRMATION VOUCHER"
    if total_rooms > 1: title_text += f" (Room {room_index} of {total_rooms})"
    c.drawCentredString(center_x, height - 100, title_text)

    # Row Helper (Tight spacing: 14pts)
    def draw_row(y_pos, label, value, bold_value=False):
        c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left_margin, y_pos, label)
        c.setFillColor(text_color); c.setFont("Helvetica-Bold" if bold_value else "Helvetica", 10)
        c.drawString(left_margin + 120, y_pos, str(value))
        return y_pos - 14

    current_y = height - 135

    # --- Guest Info ---
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 12); c.drawString(left_margin, current_y, "Guest Information")
    current_y -= 6; c.setStrokeColor(lightgrey); c.line(left_margin, current_y, right_margin, current_y); current_y -= 15

    current_y = draw_row(current_y, "Guest Name:", data['guest_name'], bold_value=True)
    current_y = draw_row(current_y, "Confirmation No.:", current_conf_no, bold_value=True)
    current_y = draw_row(current_y, "Booking Date:", datetime.now().strftime("%d %b %Y"))
    current_y -= 8

    # --- Hotel Details ---
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 12); c.drawString(left_margin, current_y, "Hotel Details")
    current_y -= 6; c.line(left_margin, current_y, right_margin, current_y); current_y -= 15

    c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left_margin, current_y, "Hotel:")
    c.setFillColor(text_color); c.setFont("Helvetica-Bold", 10); c.drawString(left_margin + 120, current_y, data['hotel_name']); current_y -= 14
    
    c.setFillColor(label_color); c.setFont("Helvetica-Bold", 10); c.drawString(left_margin, current_y, "Address:")
    c.setFillColor(text_color); c.setFont("Helvetica", 10)
    # Handle long address
    addr1 = hotel_info.get("address_line_1", "")
    addr2 = hotel_info.get("address_line_2", "")
    c.drawString(left_margin + 120, current_y, addr1); current_y -= 12
    if addr2: c.drawString(left_margin + 120, current_y, addr2); current_y -= 14
    else: current_y -= 2
    
    current_y = draw_row(current_y, "Phone:", hotel_info.get("phone", ""))
    
    c_in = data['checkin'].strftime("%d %b %Y")
    c_out = data['checkout'].strftime("%d %b %Y")
    nights = (data['checkout'] - data['checkin']).days
    current_y = draw_row(current_y, "Check-In:", c_in)
    current_y = draw_row(current_y, "Check-Out:", c_out)
    current_y = draw_row(current_y, "Nights:", str(nights))
    current_y -= 8

    # --- Room Info ---
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 12); c.drawString(left_margin, current_y, "Room Information")
    current_y -= 6; c.line(left_margin, current_y, right_margin, current_y); current_y -= 15

    current_y = draw_row(current_y, "Room Type:", data['room_type'])
    current_y = draw_row(current_y, "No. of Pax:", f"{data['adults']} Adults")
    current_y = draw_row(current_y, "Meal Plan:", data['meal_plan'])
    current_y = draw_row(current_y, "Room Size:", hotel_info.get("room_size", "N/A"))
    
    is_refundable = "Refundable" in data['policy_text']
    current_y = draw_row(current_y, "Cancellation:", data['policy_text'], bold_value=is_refundable)
    
    # --- Images (Slightly smaller to fit) ---
    current_y -= 8
    img_height = 110 # Reduced height
    img_width = 200
    img_y_pos = current_y - img_height
    
    if img_exterior:
        try: c.drawImage(img_exterior, left_margin, img_y_pos, width=img_width, height=img_height)
        except: pass
    if img_room:
        try: c.drawImage(img_room, left_margin + img_width + 10, img_y_pos, width=img_width, height=img_height)
        except: pass
        
    current_y = img_y_pos - 25

    # --- Policies ---
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 11); c.drawString(left_margin, current_y, "HOTEL CHECK-IN & CHECK-OUT POLICY"); current_y -= 15
    
    table_data = [
        ["Policy", "Time / Detail"],
        ["Standard Check-in Time:", hotel_info.get("checkin_time", "3:00 PM")],
        ["Standard Check-out Time:", hotel_info.get("checkout_time", "11:00 AM")],
        ["Early Check-in/Late Out:", "Subject to availability. Request upon arrival."],
        ["Required at Check-in:", "Passport & Credit Card/Cash Deposit."]
    ]
    table = Table(table_data, colWidths=[140, 370])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), odaduu_blue), ('TEXTCOLOR', (0,0), (-1,0), Color(1,1,1)),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9), ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('BACKGROUND', (0,1), (-1,-1), Color(0.95,0.95,0.95)), ('GRID', (0,0), (-1,-1), 0.5, Color(0.8,0.8,0.8)),
        ('FONTNAME', (0,1), (-1,-1), 'Helvetica'), ('FONTSIZE', (0,1), (-1,-1), 8), ('PADDING', (0,0), (-1,-1), 4)
    ]))
    w, h = table.wrapOn(c, width, height)
    table.drawOn(c, left_margin, current_y - h)
    current_y -= (h + 15)

    # --- T&C ---
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 10); c.drawString(left_margin, current_y, "STANDARD HOTEL BOOKING TERMS & CONDITIONS"); current_y -= 10
    tnc_lines = [
        "1. Voucher Validity: Valid only for dates/services specified.",
        f"2. Identification: Lead guest ({data['guest_name']}) must be present with valid Passport.",
        "3. No-Show Policy: Failure to check in may result in a fee equivalent to full stay cost.",
        "4. Incidentals: Mini-bar, laundry, etc., must be settled directly.",
        f"5. Occupancy: Confirmed for {data['adults']} Adults. Changes may incur extra charges.",
        "6. Hotel Rights: Hotel may refuse admission for inappropriate conduct.",
        "7. Liability: Hotel not responsible for lost valuables unless in safety deposit box.",
        "8. Non-Transferable: Booking cannot be resold or transferred.",
        "9. City Tax: City tax (if any) has to be paid and settled directly at the hotel."
    ]
    c.setFillColor(text_color); c.setFont("Helvetica", 7)
    for line in tnc_lines: c.drawString(left_margin, current_y, line); current_y -= 9

    # --- Footer ---
    c.setStrokeColor(odaduu_orange); c.setLineWidth(3); c.line(0, 45, width, 45)
    c.setFillColor(odaduu_blue); c.setFont("Helvetica-Bold", 9); c.drawString(left_margin, 32, "Issued by: Odaduu Travel DMC")
    c.setFillColor(text_color); c.setFont("Helvetica", 9); c.drawString(left_margin, 20, "Email: aashwin@odaduu.jp")

def generate_multipage_pdf(data, hotel_info, img_exterior_url, img_room_url, conf_numbers_list):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    img_ext_reader = ImageReader(download_image(img_exterior_url)) if img_exterior_url and download_image(img_exterior_url) else None
    img_room_reader = ImageReader(download_image(img_room_url)) if img_room_url and download_image(img_room_url) else None

    for i, conf_no in enumerate(conf_numbers_list):
        draw_voucher_page(c, width, height, data, hotel_info, img_ext_reader, img_room_reader, conf_no, i + 1, len(conf_numbers_list))
        c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

# --- 4. UI LOGIC ---

if 'form_data' not in st.session_state:
    st.session_state.form_data = {}

st.title("🏯 Odaduu Voucher Generator (Auto-Fill)")

# --- UPLOAD SECTION ---
st.markdown("### 📤 Step 1: Upload Supplier Voucher (Optional)")
uploaded_file = st.file_uploader("Drop a PDF here to auto-fill details", type="pdf")

if uploaded_file:
    if st.session_state.get('last_uploaded') != uploaded_file.name:
        with st.spinner("🔍 Reading PDF..."):
            extracted = extract_details_from_pdf(uploaded_file)
            if extracted:
                st.session_state.form_data = extracted
                st.session_state.last_uploaded = uploaded_file.name
                st.success("Data Extracted! Please verify below.")
            else:
                st.error("Could not extract data from PDF.")

def get_val(key, default):
    return st.session_state.form_data.get(key, default)

# --- FORM SECTION ---
st.markdown("### 📝 Step 2: Verify & Edit Details")
col1, col2 = st.columns(2)

with col1:
    hotel_name = st.text_input("Hotel Name", value=get_val("hotel_name", ""))
    city = st.text_input("City", value=get_val("city", "Osaka"))
    guest_name = st.text_input("Lead Guest Name", value=get_val("guest_name", ""))
    
    st.subheader("Room Details")
    num_rooms = st.number_input("Number of Rooms", min_value=1, value=1)
    
    conf_numbers = []
    default_conf = get_val("confirmation_no", "")
    
    if num_rooms > 1:
        same_conf = st.checkbox("Use same Confirmation No. for all rooms?", value=True)
        if same_conf:
            main_conf = st.text_input("Confirmation No (All Rooms)", value=default_conf)
            conf_numbers = [main_conf] * num_rooms
        else:
            for i in range(num_rooms):
                conf = st.text_input(f"Room {i+1} Confirmation No", key=f"conf_{i}", value=default_conf)
                conf_numbers.append(conf)
    else:
        conf = st.text_input("Confirmation No", value=default_conf)
        conf_numbers.append(conf)

    st.subheader("Cancellation Policy")
    default_policy = "Refundable" if get_val("is_refundable", False) else "Non-Refundable"
    policy_type = st.radio("Is this booking refundable?", ["Non-Refundable", "Refundable"], index=0 if default_policy=="Non-Refundable" else 1, horizontal=True)

with col2:
    try:
        def_checkin = datetime.strptime(get_val("checkin_date", ""), "%Y-%m-%d").date()
        def_checkout = datetime.strptime(get_val("checkout_date", ""), "%Y-%m-%d").date()
    except:
        def_checkin = datetime.now().date()
        def_checkout = datetime.now().date() + timedelta(days=1)

    checkin = st.date_input("Check-In Date", value=def_checkin)
    checkout = st.date_input("Check-Out Date", value=def_checkout)
    
    room_type = st.text_input("Room Type", value=get_val("room_type", ""))
    adults = st.number_input("No. of Adults (Per Room)", min_value=1, value=get_val("adults", 2))
    
    meal_plan_val = get_val("meal_plan", "Breakfast Only")
    options = ["Breakfast Only", "Room Only", "Half Board", "Full Board"]
    idx = 0
    if meal_plan_val in options: idx = options.index(meal_plan_val)
    meal_plan = st.selectbox("Meal Plan", options, index=idx)
    
    if policy_type == "Refundable":
        extracted_deadline = get_val("cancel_deadline", None)
        default_days = 3
        if extracted_deadline:
            try:
                d_date = datetime.strptime(extracted_deadline, "%Y-%m-%d").date()
                diff = (checkin - d_date).days
                if diff > 0: default_days = diff
            except: pass
            
        cancel_days = st.number_input("Free cancellation until how many days before?", min_value=1, value=default_days)
        deadline_date = checkin - timedelta(days=cancel_days)
        policy_text = f"Free Cancellation until {deadline_date.strftime('%d %b %Y')}"
        st.info(f"Voucher will say: **{policy_text}**")
    else:
        policy_text = "Non-Refundable & Non-Amendable"

if st.button("✨ Generate Voucher", type="primary"):
    if not hotel_name or not guest_name:
        st.warning("Please fill in Hotel Name and Guest Name.")
    else:
        with st.status("🤖 AI Agent Working...") as status:
            status.write(f"Fetching details for {hotel_name}...")
            hotel_info = fetch_hotel_details(hotel_name, city, room_type)
            
            status.write("Searching for hotel images...")
            img_ext = fetch_image_url(f"{hotel_name} {city} hotel exterior building")
            img_room = fetch_image_url(f"{hotel_name} {city} {room_type} interior")
            
            status.write("Generating PDF...")
            if hotel_info:
                user_data = {
                    "hotel_name": hotel_name, "guest_name": guest_name,
                    "checkin": checkin, "checkout": checkout,
                    "room_type": room_type, "adults": adults,
                    "meal_plan": meal_plan, "policy_text": policy_text
                }
                pdf_file = generate_multipage_pdf(user_data, hotel_info, img_ext, img_room, conf_numbers)
                
                status.update(label="✅ Voucher Ready!", state="complete")
                st.download_button(label="⬇️ Download PDF Voucher", data=pdf_file, file_name=f"Voucher_{guest_name.replace(' ', '_')}.pdf", mime="application/pdf")
            else:
                status.update(label="❌ Failed to fetch details", state="error")
