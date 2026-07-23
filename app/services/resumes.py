import io
from app.models.employee import Employee
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

class ResumeService:
    @staticmethod
    def generate_pdf(employee: Employee) -> io.BytesIO:
        """
        Generates a standard Roji Roti resume in PDF format.
        Includes all mandatory employee details required by the platform.
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
        p.drawCentredString(width / 2.0, y_pos, "ROJI ROTI - Professional Employee Resume")
        y_pos -= 40
        
        # --- 1. PERSONAL INFORMATION ---
        y_pos = draw_section_header("Personal Information", y_pos)
        p.setFont("Helvetica", 11)
        p.setFillColor(HexColor("#000000"))
        p.drawString(50, y_pos, f"Name: {getattr(employee, 'name', 'N/A')}")
        y_pos -= 20
        p.drawString(50, y_pos, f"Gender: {getattr(employee, 'gender', 'Not Specified')}")
        y_pos -= 20
        p.drawString(50, y_pos, f"Phone: {getattr(employee, 'phone', 'N/A')}")
        y_pos -= 30

        # --- 2. WORK EXPERIENCE & SKILLS ---
        y_pos = draw_section_header("Work Experience & Skills", y_pos)
        p.setFont("Helvetica", 11)
        p.drawString(50, y_pos, f"Category: {getattr(employee, 'category', 'N/A')}")
        y_pos -= 20
        
        # Using getattr to safely handle variations in your model names
        exp = getattr(employee, "experience", getattr(employee, "experience_years", 0))
        p.drawString(50, y_pos, f"Total Experience: {exp} Years")
        y_pos -= 20
        
        langs = getattr(employee, "languages", [])
        p.drawString(50, y_pos, f"Languages Known: {', '.join(langs) if langs else 'Not Specified'}")
        y_pos -= 20
        
        salary = getattr(employee, "expected_salary", getattr(employee, "daily_rate", "Negotiable"))
        p.drawString(50, y_pos, f"Expected Salary: {salary}")
        y_pos -= 30

        # --- 3. LOCATION & PLATFORM TRUST ---
        y_pos = draw_section_header("Location & Platform Trust", y_pos)
        p.setFont("Helvetica", 11)
        
        loc = getattr(employee, "location_name", getattr(employee, "location", "Not Specified"))
        p.drawString(50, y_pos, f"Current Location: {loc}")
        y_pos -= 20
        
        pref_locs = getattr(employee, "preferred_locations", [])
        p.drawString(50, y_pos, f"Preferred Job Locations: {', '.join(pref_locs) if pref_locs else 'Open to any location'}")
        y_pos -= 20
        
        p.setFont("Helvetica-Bold", 11)
        p.drawString(50, y_pos, f"Verified Employee Rating: {getattr(employee, 'rating', 0.0)} / 5.0 Stars")
        y_pos -= 30

        # --- 4. ABOUT THE EMPLOYEE ---
        y_pos = draw_section_header("About the Employee", y_pos)
        p.setFont("Helvetica", 11)
        bio_line_1 = f"{getattr(employee, 'name', 'This employee')} is a verified {getattr(employee, 'category', 'professional')} seeking job opportunities."
        bio_line_2 = f"Known for quality work with a {getattr(employee, 'rating', 0.0)} star performance record."
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