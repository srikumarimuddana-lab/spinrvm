from fastapi import APIRouter, WebSocket, WebSocketDisconnect
try:
    from ..socket_manager import manager
    from ..db import db
    from ..dependencies import verify_jwt_token
except ImportError:
    from socket_manager import manager
    from db import db
    from dependencies import verify_jwt_token
from firebase_admin import auth as firebase_auth
from datetime import datetime
import uuid
import logging

logger = logging.getLogger(__name__)

# Note: WebSocket routes are usually attached directly to the app, but APIRouter supports them too.
# However, the original server.py had it on @app.websocket.
# We will define a function here that can be registered in server.py, or use a router.
# Let's use a router.

router = APIRouter()

@router.websocket("/ws/{client_type}/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_type: str, client_id: str):
    """Require clients to authenticate via a first 'auth' message that contains a Firebase ID token or legacy JWT.

    After successful verification we register the connection as '{client_type}_{user_id}' and proceed to handle messages.
    """
    await websocket.accept()
    authenticated = False
    user = None
    connection_key = None

    try:
        # Require the first message to be an auth message containing a token
        auth_msg = await websocket.receive_json()
        if not auth_msg or auth_msg.get('type') != 'auth' or not auth_msg.get('token'):
            await websocket.send_json({'type': 'error', 'message': 'authentication_required'})
            await websocket.close()
            return

        token = auth_msg.get('token')
        # Try Firebase token first
        try:
            payload = firebase_auth.verify_id_token(token)
            uid = payload.get('uid') or payload.get('user_id')
            user = await db.users.find_one({'id': uid})
            if not user:
                phone = payload.get('phone_number')
                if phone:
                    user = await db.users.find_one({'phone': phone})
                if not user:
                    new_user = {
                        'id': uid,
                        'phone': phone or '',
                        'role': 'rider',
                        'created_at': datetime.utcnow(),
                        'profile_complete': False
                    }
                    await db.users.insert_one(new_user)
                    user = new_user
        except Exception:
            # Fallback to legacy JWT
            try:
                payload = verify_jwt_token(token)
                user = await db.users.find_one({'id': payload['user_id']})
            except Exception:
                user = None

        if not user:
            await websocket.send_json({'type': 'error', 'message': 'invalid_token_or_user_not_found'})
            await websocket.close()
            return

        # If connecting as driver, ensure user has a driver profile
        if client_type == 'driver':
             driver_profile = await db.drivers.find_one({'user_id': user['id']})
             if not driver_profile:
                 await websocket.send_json({'type': 'error', 'message': 'user_is_not_a_driver'})
                 await websocket.close()
                 return

        # Register the connection with a server-controlled key to prevent impersonation
        connection_key = f"{client_type}_{user['id']}"
        await manager.connect(websocket, connection_key)
        authenticated = True

        # Main message loop
        while True:
            data = await websocket.receive_json()

            if data.get('type') in ('driver_location', 'location_update'):
                # Accept both message types for backwards compat
                driver_id = data.get('driver_id')
                lat = data.get('lat')
                lng = data.get('lng')

                # If driver_id not sent, look it up from the authenticated user
                if not driver_id and client_type == 'driver':
                    dp = await db.drivers.find_one({'user_id': user['id']})
                    if dp:
                        driver_id = dp['id']

                # Verify driver ownership
                is_valid_driver = False
                if client_type == 'driver' and driver_id:
                    owned_driver = await db.drivers.find_one({'id': driver_id, 'user_id': user['id']})
                    if owned_driver:
                        is_valid_driver = True

                if driver_id and lat and lng and is_valid_driver:
                    manager.update_driver_location(driver_id, lat, lng)
                    await db.drivers.update_one({'id': driver_id}, {'$set': {'lat': lat, 'lng': lng}})

                    # ── Persist GPS breadcrumb ──────────────────────
                    active_ride = await db.rides.find_one({
                        'driver_id': driver_id,
                        'status': {'$in': ['driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress']}
                    })
                    ride_id = active_ride['id'] if active_ride else None

                    # Determine tracking phase
                    tracking_phase = 'online_idle'
                    if active_ride:
                        status_map = {
                            'driver_assigned': 'navigating_to_pickup',
                            'driver_accepted': 'navigating_to_pickup',
                            'driver_arrived': 'arrived_at_pickup',
                            'in_progress': 'trip_in_progress',
                        }
                        tracking_phase = status_map.get(active_ride.get('status', ''), 'online_idle')

                    breadcrumb = {
                        'id': str(uuid.uuid4()),
                        'driver_id': driver_id,
                        'ride_id': ride_id,
                        'lat': lat,
                        'lng': lng,
                        'speed': data.get('speed'),
                        'heading': data.get('heading'),
                        'tracking_phase': tracking_phase,
                        'timestamp': datetime.utcnow(),
                    }
                    # 'accuracy' and 'altitude' columns seem missing in Supabase schema, so omitted for now.
                    
                    await db.driver_location_history.insert_one(breadcrumb)

                    # Forward to rider in real-time
                    rides = await db.rides.find({
                        'driver_id': driver_id,
                        'status': {'$in': ['driver_assigned', 'driver_arrived', 'in_progress']}
                    }).to_list(10)
                    for ride in rides:
                        await manager.send_personal_message(
                            {
                                'type': 'driver_location_update',
                                'driver_id': driver_id,
                                'lat': lat,
                                'lng': lng,
                                'speed': data.get('speed'),
                                'heading': data.get('heading'),
                            },
                            f"rider_{ride['rider_id']}"
                        )

            elif data.get('type') == 'location_batch':
                # Batch upload of buffered GPS points (offline recovery)
                points = data.get('points', [])
                driver_id = data.get('driver_id')
                if not driver_id and client_type == 'driver':
                    dp = await db.drivers.find_one({'user_id': user['id']})
                    if dp:
                        driver_id = dp['id']
                if driver_id and points and client_type == 'driver':
                    owned = await db.drivers.find_one({'id': driver_id, 'user_id': user['id']})
                    if owned:
                        docs = []
                        for pt in points[:500]:  # cap at 500 points per batch
                            docs.append({
                                'id': str(uuid.uuid4()),
                                'driver_id': driver_id,
                                'ride_id': pt.get('ride_id'),
                                'lat': pt.get('lat'),
                                'lng': pt.get('lng'),
                                'speed': pt.get('speed'),
                                'heading': pt.get('heading'),
                                'accuracy': pt.get('accuracy'),
                                'altitude': pt.get('altitude'),
                                'tracking_phase': pt.get('tracking_phase', 'online_idle'),
                                'timestamp': datetime.fromisoformat(pt['timestamp']) if pt.get('timestamp') else datetime.utcnow(),
                            })
                        if docs:
                            await db.driver_location_history.insert_many(docs)
                        await websocket.send_json({'type': 'location_batch_ack', 'count': len(docs)})

            elif data.get('type') == 'ride_status_update':
                ride_id = data.get('ride_id')
                status = data.get('status')
                if ride_id and status:
                    ride = await db.rides.find_one({'id': ride_id})
                    if ride:
                        await manager.send_personal_message(
                            {
                                'type': 'ride_status_changed',
                                'ride_id': ride_id,
                                'status': status
                            },
                            f"rider_{ride['rider_id']}"
                        )

            elif data.get('type') == 'get_nearby_drivers':
                lat = data.get('lat')
                lng = data.get('lng')
                radius = data.get('radius', 5)  # km
                if lat and lng:
                    drivers = await db.drivers.find({
                        'is_online': True,
                        'is_available': True
                    }).to_list(100)

                    nearby = []
                    for driver in drivers:
                        # Calculate distance
                        from ..utils import calculate_distance
                        dist = calculate_distance(lat, lng, driver['lat'], driver['lng'])
                        if dist <= radius:
                            nearby.append({
                                'id': driver['id'],
                                'lat': driver['lat'],
                                'lng': driver['lng'],
                                'vehicle_type_id': driver['vehicle_type_id']
                            })

                    await websocket.send_json({'type': 'nearby_drivers', 'drivers': nearby})

            elif data.get('type') == 'chat_message':
                ride_id = data.get('ride_id')
                message = data.get('text')
                sender = data.get('sender')
                if ride_id and message:
                    ride = await db.rides.find_one({'id': ride_id})
                    if ride:
                        target = None
                        if sender == 'driver':
                            target = f"rider_{ride['rider_id']}"
                        elif sender == 'rider' and ride.get('driver_id'):
                            driver = await db.drivers.find_one({'id': ride['driver_id']})
                            if driver and driver.get('user_id'):
                                target = f"driver_{driver['user_id']}"
                        
                        if target:
                            await manager.send_personal_message(
                                {
                                    'type': 'chat_message',
                                    'id': str(uuid.uuid4()),
                                    'ride_id': ride_id,
                                    'text': message,
                                    'sender': sender,
                                    'timestamp': datetime.utcnow().isoformat()
                                },
                                target
                            )

    except WebSocketDisconnect:
        if connection_key:
            manager.disconnect(connection_key)
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        if connection_key:
            manager.disconnect(connection_key)
        try:
            await websocket.close()
        except Exception:
            pass
