from io import BytesIO
from reportlab.pdfgen import canvas
from app.models.transaction import Transaction

class ReceiptService:
    @staticmethod
    async def generate_receipt_pdf(transaction: Transaction, user_name: str) -> BytesIO:
        buffer = BytesIO()
        p = canvas.Canvas(buffer)
        
        p.setFont("Helvetica-Bold", 16)
        p.drawString(100, 800, "ROJI ROTI - PAYMENT RECEIPT")
        
        p.setFont("Helvetica", 12)
        p.drawString(100, 770, f"Receipt ID: {transaction.razorpay_payment_id}")
        p.drawString(100, 750, f"Date: {transaction.created_at.strftime('%Y-%m-%d %H:%M')}")
        p.drawString(100, 730, f"Customer: {user_name}")
        
        p.line(100, 710, 500, 710)
        
        p.drawString(100, 680, f"Description: {transaction.package_name}")
        p.drawString(100, 660, f"Amount Paid: {transaction.currency} {transaction.amount}")
        p.drawString(100, 640, f"Status: {transaction.status.upper()}")
        
        p.setFont("Helvetica-Oblique", 10)
        p.drawString(100, 600, "Thank you for using Roji Roti!")
        
        p.showPage()
        p.save()
        buffer.seek(0)
        return buffer