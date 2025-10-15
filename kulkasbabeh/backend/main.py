from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, time
import sqlite3
import pika
import json
from queue import Queue

app = FastAPI()
DB_PATH = "drivers.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

# Queue global untuk komunikasi ESP → Streamlit
enroll_status_queue = Queue()
delete_status_queue = Queue()

# Tambahkan kolom phone_number jika belum ada
cursor.execute('''
CREATE TABLE IF NOT EXISTS drivers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    fingerprint_id INTEGER,
    status TEXT CHECK(status IN ('STAY', 'JALAN', 'OFF')) NOT NULL DEFAULT 'OFF',
    delivery_start TEXT,
    phone_number TEXT
)
''')
# Periksa dan tambahkan kolom phone_number jika tidak ada (untuk update database lama)
try:
    cursor.execute("ALTER TABLE drivers ADD COLUMN phone_number TEXT")
except sqlite3.OperationalError:
    pass  # kolom sudah ada
conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone_number TEXT NOT NULL
)
''')
conn.commit()

# Configure CORS
origins = [
    "http://localhost",
    "http://localhost:8080",
    "http://localhost:8501",
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

class Driver(BaseModel):
    id: Optional[int] = None
    name: str
    fingerprint_id: int
    phone_number: Optional[str] = None  
    status: Optional[str] = None
    delivery_start: Optional[str] = None
    
    
class Admin(BaseModel):
    id: Optional[int] = None
    name: str
    phone_number: Optional[str] = None
    
    
class EnrollmentStatus(BaseModel):
    id: int
    status: str  # "success" or "failed"
    reason: str | None = None
    
class DeletionStatus(BaseModel):
    id: int
    status: str  # "success" or "failed"
    reason: str | None = None


@app.get("/drivers", response_model=List[Driver])
def read_drivers():
    conn = get_db()
    cursor = conn.cursor()
    drivers = cursor.execute("SELECT * FROM drivers").fetchall()
    conn.close()
    return [dict(d) for d in drivers]

@app.get("/admins", response_model=List[Admin])
def read_admins():
    conn = get_db()
    cursor = conn.cursor()
    admins = cursor.execute("SELECT * FROM admins").fetchall()
    conn.close()
    return [dict(a) for a in admins]

@app.post("/drivers", response_model=Driver)
def create_driver(driver: Driver):
    status = "OFF"
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO drivers (name, fingerprint_id, status, delivery_start, phone_number)
        VALUES (?, ?, ?, ?, ?)
    """, (driver.name, driver.fingerprint_id, status, None, driver.phone_number))
    conn.commit()
    driver.id = cursor.lastrowid
    driver.status = status
    conn.close()
    return driver

@app.post("/admins", response_model=Admin)
def create_admin(admin: Admin):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO admins (name, phone_number)
        VALUES (?, ?)
    """, (admin.name, admin.phone_number))
    conn.commit()
    admin.id = cursor.lastrowid
    conn.close()
    return admin

@app.put("/drivers/{driver_id}")
def update_driver(driver_id: int, driver: Driver):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE drivers
        SET name=?, fingerprint_id=?, status=?, delivery_start=?, phone_number=?
        WHERE id=?
    """, (
        driver.name,
        driver.fingerprint_id,
        driver.status,
        driver.delivery_start,
        driver.phone_number,
        driver_id
    ))
    conn.commit()
    conn.close()
    return {"message": "Driver updated"}

@app.put("/admins/{admin_id}")
def update_admin(admin_id: int, admin: Admin):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE admins
        SET name=?, phone_number=?
        WHERE id=?
    """, (
        admin.name,
        admin.phone_number,
        admin_id
    ))
    conn.commit()
    conn.close()
    return {"message": "Admin updated"}


@app.put("/drivers/{driver_id}/status")
def update_driver_status(driver_id: int, status: str):
    conn = get_db()
    cursor = conn.cursor()
    delivery_start = datetime.now().isoformat() if status == "JALAN" else None
    cursor.execute("""
        UPDATE drivers SET status=?, delivery_start=? WHERE id=?
    """, (status, delivery_start, driver_id))
    conn.commit()
    conn.close()
    return {"message": "Status updated"}

@app.delete("/drivers/{driver_id}")
def delete_driver(driver_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM drivers WHERE id=?", (driver_id,))
    conn.commit()
    conn.close()
    return {"message": "Driver deleted"}

@app.delete("/admins/{admin_id}")
def delete_admin(admin_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM admins WHERE id=?", (admin_id,))
    conn.commit()
    conn.close()
    return {"message": "Admin deleted"}

@app.post("/drivers/{fingerprint_id}")
def toggle_driver_status_by_fingerprint(fingerprint_id: int):
    conn = get_db()
    cursor = conn.cursor()
    driver = cursor.execute("SELECT * FROM drivers WHERE fingerprint_id=?", (fingerprint_id,)).fetchone()
    
    if not driver:
        conn.close()
        raise HTTPException(status_code=404, detail="Driver not found")
    
    old_status = driver["status"]
    new_status = "JALAN" if old_status == "STAY" else "STAY"
    delivery_start = datetime.now().isoformat() if new_status == "JALAN" else None

    cursor.execute(
        "UPDATE drivers SET status=?, delivery_start=? WHERE id=?",
        (new_status, delivery_start, driver["id"])
    )
    conn.commit()
    conn.close()

    return {
        "message": f"Driver {driver['id']} status updated from {old_status} to {new_status}",
        "driver_id": driver["id"],
        "driver_name": driver["name"],
        "phone_number": driver["phone_number"],
        "old_status": old_status,
        "new_status": new_status
    }
    
    
@app.post("/drivers/{fingerprint_id}/OFF")
def toggle_driver_status_to_off(fingerprint_id: int):
    conn = get_db()
    cursor = conn.cursor()
    driver = cursor.execute("SELECT * FROM drivers WHERE fingerprint_id=?", (fingerprint_id,)).fetchone()
    
    if not driver:
        conn.close()
        raise HTTPException(status_code=404, detail="Driver not found")
    
    old_status = driver["status"]
    new_status = "OFF"
    delivery_start = None

    cursor.execute(
        "UPDATE drivers SET status=?, delivery_start=? WHERE id=?",
        (new_status, delivery_start, driver["id"])
    )
    conn.commit()
    conn.close()

    return {
        "message": f"Driver {driver['id']} status updated from {old_status} to {new_status}",
        "driver_id": driver["id"],
        "driver_name": driver["name"],
        "phone_number": driver["phone_number"],
        "old_status": old_status,
        "new_status": new_status
    }
    
    
RABBITMQ_HOST = "localhost"
RABBITMQ_PORT = 5679
RABBITMQ_USER = "guest"
RABBITMQ_PASS = "guest"
QUEUE_NAME = "whatsapp_message_queue"  # Sesuaikan jika teman Anda memberi nama lain

def publish_late_driver(payload: dict):
    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    parameters = pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        credentials=credentials
    )
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    message = json.dumps(payload)
    channel.basic_publish(
        exchange='',
        routing_key=QUEUE_NAME,
        body=message,
        properties=pika.BasicProperties(delivery_mode=2)  # persistent
    )
    connection.close()

BOT_NUMBER = "6282387455975"  # Nomor bot WhatsApp

def log_message(payload: dict):
    print(f"[{datetime.now()}] Sent to {payload['number_recipient']}: {payload['message']}")


@app.post("/sync")
def sync_status(treshold_minutes: int = 45):
    now = datetime.now()
    conn = get_db()
    cursor = conn.cursor()

    # Ambil semua admin
    admins = cursor.execute("SELECT phone_number FROM admins").fetchall()
    admin_numbers = [a["phone_number"] for a in admins]

    # Ambil semua driver
    drivers = cursor.execute("SELECT * FROM drivers").fetchall()

    for d in drivers:

        # Cek keterlambatan
        if d["delivery_start"]:
            start_time = datetime.fromisoformat(d["delivery_start"])
            elapsed = int((now - start_time).total_seconds() / 60)
            print(f"Elapsed: {elapsed} min")
            print(f"Treshold: {treshold_minutes} min")

            if elapsed == treshold_minutes:
                print("⚠️  TERLAMBAT!")

                # Format waktu mulai pengantaran
                start_time_str = start_time.strftime("%H:%M")

                # Pesan untuk driver
                msg_driver = (
                    f"[KAMU MENGANTAR LEBIH LAMA DARI ESTIMASI]\n\n"
                    f"Halo {d['name']}! Kamu mengantar lebih dari estimasi.\n"
                    f"Segera konfirmasi ke SPV jika ada kendala di jalan.\n\n"
                    f"Start pengantaran: {start_time_str}"
                )

                payload_driver = {
                    "command": "send_message",
                    "number": BOT_NUMBER,
                    "number_recipient": d["phone_number"],
                    "message": msg_driver
                }
                publish_late_driver(payload_driver)
                log_message(payload_driver)

                # Pesan untuk admin
                msg_admin = (
                    f"[ADA DRIVER BELUM KEMBALI]\n\n"
                    f"Driver {d['name']} belum kembali ke gudang dan sudah lebih dari estimasi waktu yang diberikan.\n\n"
                    f"Segera follow up ke Driver: {d['phone_number']}\n\n"
                    f"Start pengantaran: {start_time_str}"
                )

                for admin_number in admin_numbers:
                    payload_admin = {
                        "command": "send_message",
                        "number": BOT_NUMBER,
                        "number_recipient": admin_number,
                        "message": msg_admin
                    }
                    publish_late_driver(payload_admin)
                    log_message(payload_admin)

    conn.commit()
    conn.close()
    return {"message": "Status synchronized with WA payloads"}

@app.get("/drivers/next_id")
def get_next_driver_id():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(id) FROM drivers")
    last_id = cursor.fetchone()[0]
    next_id = (last_id or 0) + 1
    conn.close()
    return {"next_id": next_id}

@app.post("/enroll/status")
def receive_enroll_status(payload: EnrollmentStatus):
    # Kirim data ke queue agar Streamlit bisa ambil
    enroll_status_queue.put(payload.dict())
    return {"received": True}

@app.get("/enroll/status/poll")
def poll_enroll_status():
    if not enroll_status_queue.empty():
        return enroll_status_queue.get()
    return {}  # kosong jika belum ada

@app.post("/delete/status")
def receive_delete_status(payload: DeletionStatus):
    # Kirim data ke queue agar Streamlit bisa ambil
    delete_status_queue.put(payload.dict())
    return {"received": True}

@app.get("/delete/status/poll")
def poll_delete_status():
    if not delete_status_queue.empty():
        return delete_status_queue.get()
    return {}  # kosong jika belum ada

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
