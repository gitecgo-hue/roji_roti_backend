from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        # Track regular users (employers & employees)
        self.active_connections: dict[str, WebSocket] = {}
        
        # Track admins separately so we can broadcast to them!
        self.admin_connections: dict[str, WebSocket] = {}

    # --- Standard User Methods ---
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]

    # --- Admin Methods ---
    async def connect_admin(self, websocket: WebSocket, admin_id: str):
        await websocket.accept()
        self.admin_connections[admin_id] = websocket

    def disconnect_admin(self, admin_id: str):
        if admin_id in self.admin_connections:
            del self.admin_connections[admin_id]

    # --- The Delivery System ---
    async def send_personal_message(self, message: dict, user_id: str):
        """Pushes a JSON message to a specific user, OR broadcasts to all admins."""
        
        # Intercept the Broadcast Command
        if user_id == "ADMIN_BROADCAST":
            for admin_ws in self.admin_connections.values():
                await admin_ws.send_json(message)
            return # We are done here!

        # Standard 1-to-1 Delivery (Look in standard users first)
        websocket = self.active_connections.get(user_id)
        
        # Fallback: Check if it's a direct 1-to-1 message to a specific admin
        if not websocket:
            websocket = self.admin_connections.get(user_id)
            
        # Send the message if they are online
        if websocket:
            await websocket.send_json(message)

# Create the global instance
notifier = ConnectionManager()