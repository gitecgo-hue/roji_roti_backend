from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# --- Service Imports ---
from app.services.websocket import notifier

router = APIRouter()

@router.websocket("/notifications/{user_id}")
async def websocket_notifications(websocket: WebSocket, user_id: str):
    # Connect the user when they open the app
    await notifier.connect(websocket, user_id)
    
    try:
        while True:
            # Keep the connection open. 
            # We don't expect the client to send messages, but we must listen 
            # to detect when they close the browser tab.
            data = await websocket.receive_text()
            
    except WebSocketDisconnect:
        # Clean up the dictionary when they leave
        notifier.disconnect(user_id)