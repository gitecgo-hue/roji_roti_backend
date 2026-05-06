from app.models.audit import AuditLog
from app.models.employer import Employer
from bson import ObjectId

class AuditService:
    @staticmethod
    async def log_action(
        admin: Employer,
        action: str,
        target_id: str,
        target_type: str,
        details: str = None
    ):
        """
        Creates a background entry in the audit_logs collection.
        """
        new_log = AuditLog(
            admin_id=admin.id,
            admin_name=admin.name,
            action=action,
            target_id=ObjectId(target_id),
            target_type=target_type,
            details=details
        )
        await new_log.insert()