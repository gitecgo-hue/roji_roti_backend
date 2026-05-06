import io
from app.models.employee import Employee
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

class ResumeService:
    @staticmethod
    def generate_pdf(worker: Employee) -> io.BytesIO:
        """
        Generates a standard Roji Roti resume in PDF format.
        Includes all mandatory worker details required by the platform.
        """
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        
        # Start Y position from the top of the page
        y_pos = height - 50

        # Helper function to draw section headers and horizontal lines
        def draw_section_header(title, y):
            p.setFillColor(HexColor("#2C3E50")) # Dark Blue
            p.setFont("Helvetica-Bold", 14)
            p.drawString(50, y, title)
            
            p.setStrokeColor(HexColor("#BDC3C7")) # Light Gray line
            p.setLineWidth(1)
            p.line(50, y - 8, width - 50, y - 8)
            return y - 30 # Return the new Y position for the text below
            
        # --- HEADER ---
        p.setFillColor(HexColor("#2C3E50"))
        p.setFont("Helvetica-Bold", 16)
        p.drawCentredString(width / 2.0, y_pos, "ROJI ROTI - Professional Worker Resume")
        y_pos -= 40
        
        # --- 1. PERSONAL INFORMATION ---
        y_pos = draw_section_header("Personal Information", y_pos)
        p.setFont("Helvetica", 11)
        p.setFillColor(HexColor("#000000"))
        p.drawString(50, y_pos, f"Name: {getattr(worker, 'name', 'N/A')}")
        y_pos -= 20
        p.drawString(50, y_pos, f"Gender: {getattr(worker, 'gender', 'Not Specified')}")
        y_pos -= 20
        p.drawString(50, y_pos, f"Phone: {getattr(worker, 'phone', 'N/A')}")
        y_pos -= 30

        # --- 2. WORK EXPERIENCE & SKILLS ---
        y_pos = draw_section_header("Work Experience & Skills", y_pos)
        p.setFont("Helvetica", 11)
        p.drawString(50, y_pos, f"Category: {getattr(worker, 'category', 'N/A')}")
        y_pos -= 20
        
        # Using getattr to safely handle variations in your model names
        exp = getattr(worker, "experience", getattr(worker, "experience_years", 0))
        p.drawString(50, y_pos, f"Total Experience: {exp} Years")
        y_pos -= 20
        
        langs = getattr(worker, "languages", [])
        p.drawString(50, y_pos, f"Languages Known: {', '.join(langs) if langs else 'Not Specified'}")
        y_pos -= 20
        
        salary = getattr(worker, "expected_salary", getattr(worker, "daily_rate", "Negotiable"))
        p.drawString(50, y_pos, f"Expected Salary: {salary}")
        y_pos -= 30

        # --- 3. LOCATION & PLATFORM TRUST ---
        y_pos = draw_section_header("Location & Platform Trust", y_pos)
        p.setFont("Helvetica", 11)
        
        loc = getattr(worker, "location_name", getattr(worker, "location", "Not Specified"))
        p.drawString(50, y_pos, f"Current Location: {loc}")
        y_pos -= 20
        
        pref_locs = getattr(worker, "preferred_locations", [])
        p.drawString(50, y_pos, f"Preferred Job Locations: {', '.join(pref_locs) if pref_locs else 'Open to any location'}")
        y_pos -= 20
        
        p.setFont("Helvetica-Bold", 11)
        p.drawString(50, y_pos, f"Verified Worker Rating: {getattr(worker, 'rating', 0.0)} / 5.0 Stars")
        y_pos -= 30

        # --- 4. ABOUT THE WORKER ---
        y_pos = draw_section_header("About the Worker", y_pos)
        p.setFont("Helvetica", 11)
        bio_line_1 = f"{getattr(worker, 'name', 'This worker')} is a verified {getattr(worker, 'category', 'professional')} seeking job opportunities."
        bio_line_2 = f"Known for quality work with a {getattr(worker, 'rating', 0.0)} star performance record."
        p.drawString(50, y_pos, bio_line_1)
        y_pos -= 15
        p.drawString(50, y_pos, bio_line_2)

        # --- FOOTER ---
        p.setFont("Helvetica-Oblique", 10)
        p.setFillColor(HexColor("#95A5A6"))
        p.drawString(50, 30, "Generated securely by Roji Roti ATS")

        # --- Finalize and Return ---
        p.showPage()
        p.save()
        
        buffer.seek(0)
        return buffer