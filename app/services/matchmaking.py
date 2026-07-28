from app.models.employee import Employee
from app.services.notification import NotificationService
from app.models.notification import NotificationType

async def match_and_notify_employees(job_id: str, job_category: str, job_city: str, job_title: str, is_pan_india: bool, company_name: str):
    """
    Runs in the background to find matching employees and send them a notification.
    """
    # 1. Start with the core requirement: They must be in the same job category
    query = {"category": job_category}
    
    # 2. If the job is NOT remote/Pan-India, they must also be in the same city
    if not is_pan_india and job_city:
        query["location_name"] = job_city
        
    # 3. Fetch the matches (Limiting to 100 so we don't accidentally spam thousands at once)
    matching_employees = await Employee.find(query).limit(100).to_list()
    
    # 4. Loop through the matches and fire the WebSockets/Database alerts
    for emp in matching_employees:
        await NotificationService.notify_user(
            user_id=str(emp.id),
            title="New Job Match! 🎯",
            message=f"{company_name} is looking for a {job_title} in your area.",
            notif_type=NotificationType.NEW_JOB_MATCH,
            related_entity_id=job_id
        )