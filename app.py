from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from dotenv import load_dotenv
from bson import ObjectId
from bson.errors import InvalidId
from datetime import date
import hashlib
import certifi
import os

load_dotenv()

app = Flask(__name__)
CORS(app)

client = MongoClient(os.getenv("MONGO_URI"), tlsCAFile=certifi.where())
db = client["event_management"]

users = db["users"]
events = db["events"]
registrations = db["registrations"]

# Helper: hash password
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Helper: validate ObjectId safely
def valid_object_id(id_str):
    try:
        return ObjectId(id_str)
    except (InvalidId, Exception):
        return None

# ─────────────────────────────────────────
# HOME
# ─────────────────────────────────────────
@app.route('/')
def home():
    return "MongoDB Connected Successfully"

# ─────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────

# Register
@app.route('/register', methods=['POST'])
def register():
    data = request.json

    # Validate required fields
    if not data.get('name') or not data.get('email') or not data.get('password'):
        return jsonify({"message": "Name, email and password are required!"}), 400

    # Check duplicate email
    existing = users.find_one({"email": data['email']})
    if existing:
        return jsonify({"message": "Email already registered!"}), 400

    users.insert_one({
        "name": data['name'],
        "email": data['email'],
        "password": hash_password(data['password']),  # store hashed
        "role": "user"
    })
    return jsonify({"message": "User Registered Successfully"})

# Login
@app.route('/login', methods=['POST'])
def login():
    data = request.json

    if not data.get('email') or not data.get('password'):
        return jsonify({"message": "Email and password are required!"}), 400

    user = users.find_one({
        "email": data['email'],
        "password": hash_password(data['password'])  # compare hashed
    })
    if user:
        user['_id'] = str(user['_id'])
        return jsonify(user)
    return jsonify({"message": "Invalid credentials"}), 401

# ─────────────────────────────────────────
# EVENTS
# ─────────────────────────────────────────

# Create Event
@app.route('/event/create', methods=['POST'])
def create_event():
    data = request.json

    # Validate required fields
    if not data.get('name') or not data.get('date') or not data.get('location') or not data.get('seats'):
        return jsonify({"message": "All fields (name, date, location, seats) are required!"}), 400

    seats = int(data['seats'])
    if seats <= 0:
        return jsonify({"message": "Seats must be greater than 0!"}), 400

    events.insert_one({
        "name": data['name'],
        "date": data['date'],
        "location": data['location'],
        "seats": seats,
        "registered_users": []
    })
    return jsonify({"message": "Event Created Successfully"})

# Get All Events
@app.route('/events', methods=['GET'])
def get_events():
    all_events = list(events.find())
    for e in all_events:
        e['_id'] = str(e['_id'])
    return jsonify(all_events)

# Update/Edit Event
@app.route('/event/update/<event_id>', methods=['PUT'])
def update_event(event_id):
    oid = valid_object_id(event_id)
    if not oid:
        return jsonify({"message": "Invalid event ID!"}), 400

    data = request.json
    update_fields = {}

    if 'name' in data and data['name']:
        update_fields['name'] = data['name']
    if 'date' in data and data['date']:
        update_fields['date'] = data['date']
    if 'location' in data and data['location']:
        update_fields['location'] = data['location']
    if 'seats' in data:
        new_seats = int(data['seats'])

        # Check how many people are already registered
        already_registered = registrations.count_documents({"event_id": event_id})
        if new_seats < already_registered:
            return jsonify({
                "message": f"Cannot reduce seats to {new_seats}. Already {already_registered} people registered!"
            }), 400

        update_fields['seats'] = new_seats

    if not update_fields:
        return jsonify({"message": "No fields to update!"}), 400

    events.update_one({"_id": oid}, {"$set": update_fields})
    return jsonify({"message": "Event Updated Successfully"})

# Delete Event
@app.route('/event/delete/<event_id>', methods=['DELETE'])
def delete_event(event_id):
    oid = valid_object_id(event_id)
    if not oid:
        return jsonify({"message": "Invalid event ID!"}), 400

    events.delete_one({"_id": oid})
    # Clean up all registrations for this event
    registrations.delete_many({"event_id": event_id})
    return jsonify({"message": "Event Deleted"})

# ─────────────────────────────────────────
# REGISTRATIONS
# ─────────────────────────────────────────

# Register for Event
@app.route('/event/register', methods=['POST'])
def register_event():
    data = request.json

    if not data.get('user_id') or not data.get('event_id'):
        return jsonify({"message": "user_id and event_id are required!"}), 400

    oid = valid_object_id(data['event_id'])
    if not oid:
        return jsonify({"message": "Invalid event ID!"}), 400

    # Prevent duplicate registration
    existing = registrations.find_one({
        "user_id": data['user_id'],
        "event_id": data['event_id']
    })
    if existing:
        return jsonify({"message": "Already Registered!"}), 400

    # Check event exists and has seats
    event = events.find_one({"_id": oid})
    if not event:
        return jsonify({"message": "Event not found!"}), 404
    if event['seats'] <= 0:
        return jsonify({"message": "No seats available!"}), 400

    # Save registration with date
    registrations.insert_one({
        "user_id": data['user_id'],
        "event_id": data['event_id'],
        "date": str(date.today())   # ✅ now saves registration date
    })

    # Decrement seat count
    events.update_one({"_id": oid}, {"$inc": {"seats": -1}})

    return jsonify({"message": "Registered Successfully!"})

# Get User Registrations
@app.route('/registrations/<user_id>', methods=['GET'])
def get_registrations(user_id):
    regs = list(registrations.find({"user_id": user_id}))
    for r in regs:
        r['_id'] = str(r['_id'])
    return jsonify(regs)

# Get Participants for an Event (Admin use)
@app.route('/participants/<event_id>', methods=['GET'])
def get_participants(event_id):
    regs = list(registrations.find({"event_id": event_id}))
    result = []
    for r in regs:
        uid = valid_object_id(r['user_id'])
        if not uid:
            continue
        user = users.find_one({"_id": uid})
        if user:
            result.append({
                "name": user['name'],
                "email": user['email'],
                "registered_on": r.get('date', 'N/A')
            })
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True)
