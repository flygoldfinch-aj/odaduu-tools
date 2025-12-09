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

# --- 1. SETUP ---
st.set_page_config(page_title="Odaduu Voucher Generator", page_icon="🏨", layout="wide")

try:
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
    SEARCH_KEY = st.secrets["SEARCH_API_KEY"]
    SEARCH_CX = st.secrets["SEARCH_ENGINE_ID"]
    genai.configure(api_key=GEMINI_KEY)
except:
    st.error("⚠️ Secrets not found.")
    st.stop()

# --- 2. SESSION STATE ---
def init_state():
    defaults = {
        'hotel_name': '', 'city': '', 'lead_guest': '', 
        'checkin': datetime.now().date(), 'checkout': datetime.now().date() + timedelta(days=1),
        'num_rooms': 1, 'room_type': '', 'adults': 2, 'meal_plan': 'Breakfast Only',
        'policy_type': 'Non-Refundable', 'cancel_days': 3, 'room_size': '',
        'room_0_conf': '', 'room_0_guest': '',
        'suggestions': [], 'last_search': '', 'room_options': []
    }
    for k, v in defaults.items():
        if k not in st.session_state: st.session_state[k] = v

init_state()

def reset_form():
    """Clears all form data cleanly."""
    st.session_state.hotel_name = ""
    st.session_state.city = ""
    st.session_state.lead_guest = ""
    st.session_state.num_rooms = 1
    st.session_state.room_type = ""
    st.session_state.room_size = ""
    st.session_state.room_options = []
    for i in range(10):
        st.session_state[f'room_{i}_conf'] = ""
        st.session_state[f'room_{i}_guest'] = ""

# --- 3. AI FUNCTIONS ---
def get_hotel_suggestions(query):
    model = genai.GenerativeModel('gemini-2.0-flash')
    try:
        res = model.generate_content(f'Return JSON list of 3 official hotel names for: "{query}". JSON ONLY: ["Name 1", "Name 2"]').text
        return json.loads(res.replace("```json", "").replace("```", "").strip())
    except: return []

def detect_city(hotel_name):
    model = genai.GenerativeModel('gemini-2.0-flash')
    try: return model.generate_content(f'City of "{hotel_name}"? Return ONLY city string.').text.strip()
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
        Extract booking details. Detect MULTIPLE rooms (Room 1, Room 2).
        Text: {text[:20000]}
        Return JSON:
        {{
            "hotel_name": "Name", "city": "City", 
            "checkin": "YYYY-MM-DD", "checkout": "YYYY-MM-DD", 
            "meal": "Plan", "refundable": true, "cancel_date": "YYYY-MM-DD",
            "room_size": "Size string",
            "rooms": [
                {{"guest": "Guest Room 1", "conf": "Conf Room 1", "type": "Type Room 1", "adults": 2}},
                {{"guest": "Guest Room 2", "conf": "Conf Room 2", "type": "Type Room 2", "adults": 2}}
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

# --- 4. PDF DRAWING ---
def draw_vector_seal(c, x, y, size):
    c.saveState()
    color = Color(0.1, 0.2, 0.4)
    c.setStrokeColor(color); c.setFillColor(color); c.setFillAlpha(0.8); c.setStrokeAlpha(0.8); c.setLineWidth(1.5)
    cx, cy = x + size/2, y + size/2
    c.circle(cx, cy, size/2, stroke=1, fill=0)
    c.setLineWidth(0.5); c.circle(cx, cy, size/2 - 4, stroke=1, fill=0)
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
        try: c.saveState(); c.setFillAlpha(0.04); c.drawImage("logo.png", w/2-200, h/2-75, 400, 150, mask='auto', preserveAspectRatio=True); c.restoreState()
        except: pass
        try: c.drawImage("logo.png", w/2-80, h-60, 160, 55, mask='auto', preserveAspectRatio=True)
        except: c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 18); c.drawCentredString(w/2, h-50, "ODADUU TRAVEL DMC")

        c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 16)
        title = "HOTEL CONFIRMATION VOUCHER" + (f" (Room {i+1}/{len(rooms_list)})" if len(rooms_list)>1 else "")
        c.drawCentredString(w/2, h-90, title)

        y = h - 120; left = 40
        def draw_sect(title, content_list):
            nonlocal y
            c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, title)
            y -= 5; c.setStrokeColor(lightgrey); c.line(left, y, w-40, y); y -= 12
            for label, val, bold in content_list:
                c.setFillColor(Color(0.1, 0.1, 0.1)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, label)
                c.setFillColor(Color(0.2, 0.2, 0.2)); c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
                c.drawString(left + 120, y, str(val)); y -= 12
            y -= 5

        draw_sect("Guest Information", [("Guest Name:", room['guest'], True), ("Confirmation No.:", room['conf'], True), ("Booking Date:", datetime.now().strftime("%d %b %Y"), False)])

        c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 12); c.drawString(left, y, "Hotel Details"); y-=5; c.line(left, y, w-40, y); y-=12
        c.setFillColor(Color(0.1, 0.1, 0.1)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "Hotel:")
        c.setFillColor(Color(0.2, 0.2, 0.2)); c.setFont("Helvetica-Bold", 10); c.drawString(left+120, y, data['hotel']); y-=12
        c.setFillColor(Color(0.1, 0.1, 0.1)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "Address:")
        c.setFillColor(Color(0.2, 0.2, 0.2)); c.setFont("Helvetica", 10); c.drawString(left+120, y, info.get('addr1','')); y-=10
        if info.get('addr2'): c.drawString(left+120, y, info.get('addr2','')); y-=12
        else: y-=2
        
        nights = (data['out'] - data['in']).days
        for l, v in [("Phone:", info.get('phone','')), ("Check-In:", data['in'].strftime("%d %b %Y")), ("Check-Out:", data['out'].strftime("%d %b %Y")), ("Nights:", str(nights))]:
            c.setFillColor(Color(0.1, 0.1, 0.1)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, l)
            c.setFillColor(Color(0.2, 0.2, 0.2)); c.setFont("Helvetica", 10); c.drawString(left+120, y, v); y-=12
        y -= 5

        r_list = [("Room Type:", data['room_type'], False), ("No. of Pax:", f"{data['adults']} Adults", False), ("Meal Plan:", data['meal'], False)]
        if data['room_size']: r_list.append(("Room Size:", data['room_size'], False))
        r_list.append(("Cancellation:", data['policy'], "Refundable" in data['policy']))
        draw_sect("Room Information", r_list)

        if i_ext or i_lobby or i_room:
            ix = left; ih=95; iw=160; gap=10
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

        c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 11); c.drawString(left, y, "HOTEL CHECK-IN & CHECK-OUT POLICY"); y -= 15
        pt = [["Policy", "Time / Detail"], ["Standard Check-in:", info.get('in', '3:00 PM')], ["Standard Check-out:", info.get('out', '12:00 PM')], ["Early/Late:", "Subject to availability. Request on arrival."], ["Required:", "Passport & Credit Card/Cash Deposit."]]
        t = Table(pt, colWidths=[130, 380]); t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),Color(0.05, 0.15, 0.35)), ('TEXTCOLOR',(0,0),(-1,0),Color(1,1,1)), ('FONTNAME',(0,0),(-1,-1),'Helvetica'), ('FONTSIZE',(0,0),(-1,-1),8), ('PADDING',(0,0),(-1,-1),3), ('GRID', (0,0), (-1,-1), 0.5, Color(0.2, 0.2, 0.2))]))
        t.wrapOn(c, w, h); t.drawOn(c, left, y-60); y -= (60 + 30)

        c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 10); c.drawString(left, y, "STANDARD HOTEL BOOKING TERMS & CONDITIONS"); y -= 10
        tnc = ["1. Voucher Validity: Valid for dates/services specified. Present at front desk.", f"2. Identification: Lead guest ({room['guest']}) must present valid Passport.", "3. No-Show Policy: Full fee applies for no-shows without prior cancellation.", "4. Incidentals: Mini-bar, laundry, etc., must be settled directly at check-out.", f"5. Occupancy: Confirmed for {data['adults']} Adults. Changes may incur charges.", "6. Hotel Rights: Hotel may refuse admission for inappropriate conduct.", "7. Liability: Hotel not responsible for lost valuables not in safety box.", "8. Non-Transferable: Booking cannot be resold.", "9. City Tax: Not included. Must be paid directly at hotel.", "10. Bed Type: Subject to availability and cannot be guaranteed."]
        styles = getSampleStyleSheet(); styleN = styles["Normal"]; styleN.fontSize = 7; styleN.leading = 8
        t_data = [[Paragraph(x, styleN)] for x in tnc]
        t2 = Table(t_data, colWidths=[510]); t2.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.5,Color(0.2,0.2,0.2)), ('PADDING',(0,0),(-1,-1),2), ('VALIGN',(0,0),(-1,-1),'TOP')]))
        tw, th = t2.wrapOn(c, w, h); t2.drawOn(c, left, y-th)

        draw_vector_seal(c, w-130, 45, 80)
        c.setStrokeColor(Color(1, 0.4, 0)); c.setLineWidth(3); c.line(0, 45, w, 45)
        c.setFillColor(Color(0.05, 0.15, 0.35)); c.setFont("Helvetica-Bold", 9); c.drawString(left, 32, "Issued by: Odaduu Travel DMC"); c.setFillColor(Color(0.2, 0.2, 0.2)); c.setFont("Helvetica", 9); c.drawString(left, 20, "Email: aashwin@odaduu.jp")
        c.showPage()
    c.save(); buffer.seek(0); return buffer

# --- 5. UI LOGIC ---
st.title("🏯 Odaduu Voucher Generator")

# UPLOAD
with st.expander("📤 Upload Supplier Voucher (PDF)", expanded=True):
    up_file = st.file_uploader("Drop PDF here", type="pdf")
    if up_file:
        if st.session_state.last_uploaded_file != up_file.name:
            with st.spinner("Analyzing PDF..."):
                reset_form()
                data = extract_pdf_data(up_file)
                if data:
                    st.session_state.hotel_name = data.get('hotel_name', '')
                    st.session_state.city = data.get('city', '')
                    
                    try: st.session_state.checkin = datetime.strptime(data.get('checkin_date'), "%Y-%m-%d").date()
                    except: pass
                    try: st.session_state.checkout = datetime.strptime(data.get('checkout_date'), "%Y-%m-%d").date()
                    except: pass
                    
                    st.session_state.meal_plan = data.get('meal_plan', 'Breakfast Only')
                    st.session_state.room_type = data.get('room_type', '')
                    st.session_state.room_size = data.get('room_size', '')
                    st.session_state.adults = data.get('rooms', [{}])[0].get('adults', 2)
                    
                    # Room Logic
                    rooms = data.get('rooms', [])
                    if rooms:
                        st.session_state.num_rooms = len(rooms)
                        for i, r in enumerate(rooms):
                            st.session_state[f'room_{i}_conf'] = r.get('confirmation_no', '')
                            st.session_state[f'room_{i}_guest'] = r.get('guest_name', '')
                    
                    st.session_state.room_options = get_room_types(st.session_state.hotel_name)
                    st.session_state.last_uploaded_file = up_file.name
                    st.success("PDF Data Loaded!")
                    st.rerun()

# MANUAL SEARCH
st.markdown("### 🏨 Hotel Details")
col_s, col_res = st.columns([2,1])
with col_s: 
    search = st.text_input("Search Hotel Name", key="search_input")
    if search and search != st.session_state.last_search:
        st.session_state.last_search = search
        st.session_state.suggestions = get_hotel_suggestions(search)

with col_res:
    if st.session_state.suggestions:
        # Index=None PREVENTS auto-selection jumping
        sel = st.radio("Select:", st.session_state.suggestions, index=None, key="hotel_radio")
        if sel and sel != st.session_state.hotel_name:
            st.session_state.hotel_name = sel
            with st.spinner("Fetching details..."):
                st.session_state.city = detect_city(sel)
                st.session_state.room_options = get_room_types(sel)
            st.rerun()

# FORM
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
            val = st.session_state.get(f'room_{0}_conf', '') if same_conf and i > 0 else st.session_state.get(f'room_{i}_conf', '')
            st.text_input(f"Conf {i+1}", value=val, key=f"room_{i}_conf")

    st.subheader("Policy")
    ptype = st.radio("Type", ["Non-Refundable", "Refundable"], horizontal=True, key="policy_type")

with c2:
    st.date_input("Check-In", key="checkin")
    st.date_input("Check-Out", key="checkout", min_value=st.session_state.checkin + timedelta(days=1))
    
    opts = st.session_state.room_options.copy()
    current = st.session_state.room_type
    if current and current not in opts: opts.insert(0, current)
    opts.append("Manual...")
    
    def on_room_change():
        if st.session_state.room_sel != "Manual...": st.session_state.room_type = st.session_state.room_sel
    st.selectbox("Room Type", opts, key="room_sel", on_change=on_room_change)
    if st.session_state.get("room_sel") == "Manual...": st.text_input("Type Name", key="room_type")
    
    st.number_input("Adults", 1, key="adults")
    st.text_input("Size (Optional)", key="room_size")
    st.selectbox("Meal", ["Breakfast Only", "Room Only", "Half Board", "Full Board"], key="meal_plan")
    
    policy_txt = "Non-Refundable & Non-Amendable"
    if ptype == "Refundable":
        d = st.number_input("Free Cancel Days", 1, key="cancel_days")
        policy_txt = f"Free Cancellation until {(st.session_state.checkin - timedelta(days=d)).strftime('%d %b %Y')}"
        st.info(policy_txt)

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
