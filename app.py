def get_img_reader(url):
    if not url:
        return None
    try:
        # better reliability (some sites block default python UA)
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, timeout=6, headers=headers)
        if r.status_code == 200 and r.content:
            return ImageReader(io.BytesIO(r.content))
    except:
        return None
    return None


def draw_vector_seal(c, x, y, size=80):
    """Odaduu Seal (Double Circle with Text)."""
    c.saveState()
    color = Color(0.05, 0.15, 0.35)
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setFillAlpha(0.85)
    c.setStrokeAlpha(0.85)
    c.setLineWidth(1.4)

    cx, cy = x + size/2, y + size/2
    r_outer = size/2
    r_inner = r_outer - 4

    c.circle(cx, cy, r_outer, stroke=1, fill=0)
    c.setLineWidth(0.6)
    c.circle(cx, cy, r_inner, stroke=1, fill=0)

    c.setFillAlpha(1)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(cx, cy + 4, "ODADUU")
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(cx, cy - 7, "TRAVEL DMC")

    c.setFont("Helvetica-Bold", 6)

    # Top arc
    text_top = "CERTIFIED VOUCHER"
    angle_start = 140
    for i, ch in enumerate(text_top):
        ang = angle_start - (i * 10)
        rad = radians(ang)
        tx = cx + (r_inner - 4) * cos(rad)
        ty = cy + (r_inner - 4) * sin(rad)
        c.saveState()
        c.translate(tx, ty)
        c.rotate(ang - 90)
        c.drawCentredString(0, 0, ch)
        c.restoreState()

    # Bottom arc
    text_bot = "OFFICIAL"
    angle_start = 235
    for i, ch in enumerate(text_bot):
        ang = angle_start + (i * 12)
        rad = radians(ang)
        tx = cx + (r_inner - 4) * cos(rad)
        ty = cy + (r_inner - 4) * sin(rad)
        c.saveState()
        c.translate(tx, ty)
        c.rotate(ang + 90)
        c.drawCentredString(0, 0, ch)
        c.restoreState()

    c.restoreState()


def generate_pdf(data, info, imgs, rooms_list):
    """
    Matches your app.py intended layout:
    - Centered Odaduu logo
    - Big bold centered title
    - 3 images row
    - Guest/Hotel/Room compact bold sections
    - Policy table + T&C table boxed with orange border
    - One-page safe footer
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4

    # === Brand colors ===
    BRAND_BLUE = Color(0.05, 0.15, 0.35)
    BRAND_ORANGE = Color(0.95, 0.42, 0.13)  # odaduu orange approx

    left = 40
    right = w - 40
    content_w = right - left

    footer_line_y = 40
    footer_safe_y = 140  # keep all content above this

    # preload images
    i_ext = get_img_reader(imgs[0]) if imgs and len(imgs) > 0 else None
    i_lobby = get_img_reader(imgs[1]) if imgs and len(imgs) > 1 else None
    i_room = get_img_reader(imgs[2]) if imgs and len(imgs) > 2 else None

    styles = getSampleStyleSheet()
    addr_style = ParagraphStyle(
        "addr",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=11.0,
        textColor=Color(0.15, 0.15, 0.15),
    )

    for idx, room in enumerate(rooms_list):
        if idx > 0:
            c.showPage()

        # =========================
        # HEADER (center logo + big title)
        # =========================
        y = h - 40

        # watermark (fixed — your file had truncated "l...0")
        try:
            c.saveState()
            c.setFillAlpha(0.04)
            c.drawImage(LOGO_FILE, w/2 - 200, h/2 - 75, 400, 150, mask='auto', preserveAspectRatio=True)
            c.restoreState()
        except:
            pass

        try:
            c.drawImage(LOGO_FILE, w/2 - 95, y - 55, 190, 55, mask='auto', preserveAspectRatio=True)
        except:
            c.setFillColor(BRAND_BLUE)
            c.setFont("Helvetica-Bold", 20)
            c.drawCentredString(w/2, y - 35, COMPANY_NAME)

        y -= 85

        c.setFillColor(BRAND_BLUE)
        c.setFont("Helvetica-Bold", 22)
        c.drawCentredString(w/2, y, "HOTEL CONFIRMATION VOUCHER")
        y -= 25

        # =========================
        # IMAGES ROW (3 columns)
        # =========================
        img_h = 70
        gap = 8
        img_w = (content_w - 2 * gap) / 3

        img_y_top = y
        for i_img, im in enumerate([i_ext, i_lobby, i_room]):
            if im:
                try:
                    c.drawImage(im, left + i_img*(img_w+gap), img_y_top - img_h, img_w, img_h,
                                preserveAspectRatio=True, anchor="c")
                except:
                    pass
        y = img_y_top - img_h - 12

        # =========================
        # BOXED COMPACT SECTIONS
        # =========================
        def boxed_section(title, rows):
            nonlocal y
            tdata = [[title, ""]] + rows
            t = Table(tdata, colWidths=[155, content_w - 155])

            t.setStyle(TableStyle([
                ("SPAN", (0, 0), (-1, 0)),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
                ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_BLUE),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, lightgrey),

                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 1), (1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 1), (-1, -1), 9.5),

                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),

                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 1.0, BRAND_ORANGE),
            ]))

            tw, th = t.wrapOn(c, content_w, 9999)
            t.drawOn(c, left, y - th)
            y = y - th - 8

        # Guest
        boxed_section("Guest Information", [
            ["Guest Name:", room.get("guest", "")],
            ["Confirmation No.:", room.get("conf", "")],
            ["Booking Date:", datetime.now().strftime("%d %b %Y")],
        ])

        # Hotel
        addr_html = (info.get("addr1") or "")
        if info.get("addr2"):
            addr_html += "<br/>" + info.get("addr2")
        addr_para = Paragraph(addr_html, addr_style) if addr_html.strip() else ""

        nights = max((data["out"] - data["in"]).days, 1)

        boxed_section("Hotel Details", [
            ["Hotel:", data["hotel"]],
            ["Address:", addr_para],
            ["Phone:", info.get("phone", "")],
            ["Check-In:", data["in"].strftime("%d %b %Y")],
            ["Check-Out:", data["out"].strftime("%d %b %Y")],
            ["Nights:", str(nights)],
        ])

        # Room
        boxed_section("Room Information", [
            ["Room Type:", data["room_type"]],
            ["No. of Pax:", f"{data['adults']} Adults"],
            ["Meal Plan:", data["meal"]],
            ["Cancellation:", data["policy"]],
        ])

        # REQUIRED SPACE between cancellation row and policy title
        y -= 14

        # =========================
        # POLICY TABLE (compact boxed)
        # =========================
        c.setFillColor(BRAND_BLUE)
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(left, y, "HOTEL CHECK-IN & CHECK-OUT POLICY")
        y -= 10

        pol_data = [
            ["Policy", "Time / Detail"],
            ["Standard Check-in Time:", "3:00 PM"],
            ["Standard Check-out Time:", "12:00 PM"],
            ["Early Check-in/Late Out:", "Subject to availability. Request upon arrival."],
            ["Required at Check-in:", "Passport & Credit Card/Cash Deposit."],
        ]
        pol = Table(pol_data, colWidths=[170, content_w - 170])
        pol.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.4),

            ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 7.2),

            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),

            ("GRID", (0, 0), (-1, -1), 0.5, BRAND_ORANGE),
            ("BOX", (0, 0), (-1, -1), 1.0, BRAND_ORANGE),
        ]))
        pw, ph = pol.wrapOn(c, content_w, 9999)
        pol.drawOn(c, left, y - ph)
        y = y - ph - 10

        # =========================
        # T&C TABLE (compact boxed, auto-shrink to fit above footer)
        # =========================
        c.setFillColor(BRAND_BLUE)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(left, y, "STANDARD HOTEL BOOKING TERMS & CONDITIONS")
        y -= 8

        lead_guest = (room.get("guest", "") or "Guest").split(",")[0].strip()

        tnc_lines = [
            "1. Voucher Validity: Must be presented at hotel front desk.",
            f"2. Identification: Guest(s) {lead_guest} must present valid ID.",
            "3. No-Show: Full charge applies for no-shows.",
            "4. Incidentals: Paid by guest directly.",
            "5. Occupancy: Standard occupancy rules apply.",
            "6. Rights: Hotel reserves right of admission.",
            "7. Liability: Use safety deposit box for valuables.",
            "8. Resale: Booking is non-transferable.",
            "9. Tax: City/Tourism tax payable at hotel if applicable.",
            "10. Bedding: Subject to availability.",
        ]

        def make_tnc_table(font_size, leading):
            tnc_style = ParagraphStyle(
                "tnc",
                parent=styles["Normal"],
                fontName="Helvetica",
                fontSize=font_size,
                leading=leading,
                textColor=black,
                spaceAfter=0,
            )
            rows = [[Paragraph(x, tnc_style)] for x in tnc_lines]
            t = Table(rows, colWidths=[content_w])
            t.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 1.0, BRAND_ORANGE),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, Color(0.82, 0.82, 0.82)),
            ]))
            return t

        available = y - footer_safe_y
        chosen = None
        fs, ld = 7.0, 8.2
        while fs >= 5.2:
            tnc = make_tnc_table(fs, ld)
            tw, th = tnc.wrapOn(c, content_w, 9999)
            if th <= available:
                chosen = (tnc, th)
                break
            fs -= 0.2
            ld = max(6.4, ld - 0.2)

        if chosen is None:
            tnc = make_tnc_table(5.2, 6.4)
            tw, th = tnc.wrapOn(c, content_w, 9999)
            chosen = (tnc, th)

        tnc, th = chosen
        tnc.drawOn(c, left, y - th)

        # =========================
        # FOOTER
        # =========================
        draw_vector_seal(c, w - 130, 45, 80)

        c.setStrokeColor(BRAND_ORANGE)
        c.setLineWidth(2)
        c.line(0, footer_line_y, w, footer_line_y)

        c.setFillColor(BRAND_BLUE)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(left, 25, f"Issued by: {COMPANY_NAME}")
        c.drawString(left, 13, f"Email: {COMPANY_EMAIL}")

    c.save()
    buffer.seek(0)
    return buffer
