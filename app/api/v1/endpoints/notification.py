from fastapi import APIRouter, Depends, HTTPException
from app.models.notification import Notification

# Import your new universal dependency
from app.api.dependencies import get_any_current_user

router = APIRouter()

@router.get("/")
async def get_my_notifications(user_id: str = Depends(get_any_current_user)):
    """Fetches notifications for whoever is logged in (Employer or Employee)"""
    
    notifs = await Notification.find(
        {"user_id": user_id}
    ).sort("-created_at").to_list()
    
    return {
        "count": len(notifs), 
        "notifications": notifs
    }

@router.delete("/{notif_id}")
async def read_and_delete_notification(
    notif_id: str, 
    user_id: str = Depends(get_any_current_user)
):
    """Deletes a notification, ensuring the logged-in user actually owns it."""
    
    notif = await Notification.get(notif_id)
    
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
        
    if notif.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this notification")

    await notif.delete()
    
    return {"status": "success", "message": "Notification read and deleted."}