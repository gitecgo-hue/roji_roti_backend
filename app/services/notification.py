from datetime import datetime, timezone
from app.models.notification import Notification, NotificationType
from app.services.websocket import notifier

class NotificationService:
    @staticmethod
    async def notify_user( 
        user_id: str,      
        title: str, 
        message: str, 
        notif_type: NotificationType,
        related_entity_id: str = None
    ):
        """Creates or updates an in-app notification and pushes via WebSocket."""
        saved_notif = None 
        
        if related_entity_id:
            existing_notif = await Notification.find_one({
                "user_id": user_id,
                "type": notif_type,
                "related_entity_id": related_entity_id
            })

            if existing_notif:
                # You can add Employee-specific grouping logic here too later!
                if notif_type == NotificationType.NEW_APPLICANT:
                    existing_notif.message = "You have multiple new applicants for this job!"
                else:
                    existing_notif.message = message
                
                existing_notif.created_at = datetime.now(timezone.utc)
                await existing_notif.save()
                saved_notif = existing_notif

        if not saved_notif:
            new_notification = Notification(
                user_id=user_id,
                title=title,
                message=message,
                type=notif_type,
                related_entity_id=related_entity_id
            )
            await new_notification.insert()
            saved_notif = new_notification

        await notifier.send_personal_message(
            message={
                "id": str(saved_notif.id),
                "title": saved_notif.title,
                "message": saved_notif.message,
                "type": saved_notif.type,
                "related_entity_id": saved_notif.related_entity_id,
                "created_at": saved_notif.created_at.isoformat()
            },
            user_id=user_id
        )
        
        return saved_notif