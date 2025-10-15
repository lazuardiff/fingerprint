import streamlit as st
import requests
import datetime
import time
from streamlit_autorefresh import st_autorefresh
import paho.mqtt.client as mqtt
import json
import threading
from queue import Queue

# --- App Setup ---
st.set_page_config(page_title="Driver Dashboard", layout="wide")
if "redirect_to" in st.session_state:
    st.session_state["menu_selection"] = st.session_state["redirect_to"]
    del st.session_state["redirect_to"]
if "menu_selection" not in st.session_state:
    st.session_state["menu_selection"] = "Driver Status"
menu = st.sidebar.radio("Navigate", ["Driver Status", "Add Driver", "Modify Driver", "Admin Page"], index=["Driver Status", "Add Driver", "Modify Driver", "Admin Page"].index(st.session_state.menu_selection), key="menu_selection")

API_URL = "http://localhost:8080"

# Konfigurasi RabbitMQ
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_USER = "guest"
MQTT_PASS = "guest"

ENROLL_TOPIC = "fingerprint/enroll"
ENROLL_RESPONSE = "fingerprint/enroll/response"
DELETE_TOPIC = "fingerprint/delete"
DELETE_RESPONSE = "fingerprint/delete/response"

enroll_queue = Queue()
delete_queue = Queue()

def on_message(client, userdata, msg):
    if msg.topic == ENROLL_RESPONSE:
        enroll_queue.put(msg.payload.decode())
    elif msg.topic == DELETE_RESPONSE:
        delete_queue.put(msg.payload.decode())

@st.cache_resource
def init_mqtt_client():
    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.subscribe([(ENROLL_RESPONSE, 0), (DELETE_RESPONSE, 0)])
    client.loop_start()
    return client

mqtt_client = init_mqtt_client()

def publish_mqtt(topic, payload):
    mqtt_client.publish(topic, payload)


def wait_response_enroll_http(timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with requests.get(f"{API_URL}/enroll/status/poll") as response:
                if response.status_code == 200:
                    data = response.json()
                    if data:
                        return data
        except Exception:
            pass
        time.sleep(1)
    return None

def wait_response_delete_http(timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with requests.get(f"{API_URL}/delete/status/poll") as response:
                if response.status_code == 200:
                    data = response.json()
                    if data:
                        return data
        except Exception:
            pass
        time.sleep(1)
    return None

# --- Driver Status Page ---
if menu == "Driver Status":
    
    # Tampilkan judul di tengah
    st.markdown("<h1 style='text-align: center;'>DASHBOARD STATUS DRIVER</h1>", unsafe_allow_html=True)
    
    treshold_minutes = st.number_input(
        "Threshold keterlambatan (menit)",
        min_value=1,
        value=45,
        step=1,
        help="Masukkan waktu maksimal pengiriman dalam menit sebelum dianggap terlambat."
    )

    st_autorefresh(interval=40 * 1000, key="sync_every_minute")
    requests.post(f"{API_URL}/sync", params={"treshold_minutes": treshold_minutes})
    
    # Ambil semua driver dari API
    with requests.get(f"{API_URL}/drivers") as response:
        drivers = response.json()

    # Hitung jumlah driver untuk setiap status
    stay_count = sum(1 for d in drivers if d["status"] == "STAY")
    jalan_count = sum(1 for d in drivers if d["status"] == "JALAN")
    off_count = sum(1 for d in drivers if d["status"] == "OFF")

    # Kolom untuk status
    col_STAY, col_JALAN, col_OFF = st.columns(3)

    # Tambahkan judul masing-masing kolom dengan jumlah
    with col_STAY:
        st.subheader(f"STAY | {stay_count}")
        st.markdown("<hr style='border: 2px solid #ccc; margin-top: 20px; margin-bottom: 20px;'>", unsafe_allow_html=True)
    with col_JALAN:
        st.subheader(f"JALAN | {jalan_count}")
        st.markdown("<hr style='border: 2px solid #ccc; margin-top: 20px; margin-bottom: 20px;'>", unsafe_allow_html=True)
    with col_OFF:
        st.subheader(f"OFF | {off_count}")
        st.markdown("<hr style='border: 2px solid #ccc; margin-top: 20px; margin-bottom: 20px;'>", unsafe_allow_html=True)


    

    for driver in drivers:
        id = driver["id"]
        name = driver["name"]
        fingerprint_id = driver["fingerprint_id"]
        phone_number = driver.get("phone_number", "-")
        status = driver["status"]
        delivery_start = driver["delivery_start"]

        with eval(f"col_{status}"):
            st.subheader(f"{name}")
            st.caption(f"Phone Number: {phone_number}")
            if status == "JALAN" and delivery_start:
                start_time = datetime.datetime.fromisoformat(delivery_start)
                elapsed = (datetime.datetime.now() - start_time).total_seconds() / 60
                progress = min(elapsed / treshold_minutes, 1.0)
                st.progress(progress)
                if elapsed > treshold_minutes:
                    st.error(f"Telat! ({int(elapsed)} min)")
                else:
                    st.info(f"Jalan: {int(elapsed)} min")


                
                
elif menu == "Add Driver":
    st.title("Add New Driver")

    with st.form("driver_form"):
        name = st.text_input("Driver Name")
        phone_number = st.text_input("Phone Number (628xxxxxxxxxx)")
        submitted = st.form_submit_button("Add Driver")

    if submitted:
        # 1. Ambil next available ID
        with requests.get(f"{API_URL}/drivers/next_id") as response:
            next_id = response.json()["next_id"]


        # 2. Kirim perintah enroll ke ESP32
        enroll_payload = json.dumps({"command": "enroll", "id": next_id})
        publish_mqtt(ENROLL_TOPIC, enroll_payload)

        # 3. Tunggu fingerprint dari ESP32
        with st.spinner("Waiting for fingerprint to be enrolled..."):
            result = wait_response_enroll_http(timeout=90)

        if result:
            try:
                if result and result.get("status") == "success":
                    st.success("Fingerprint enrolled successfully.")

                    # 4. Kirim data driver ke backend
                    response = requests.post(f"{API_URL}/drivers", json={
                        "name": name,
                        "phone_number": phone_number,
                        "fingerprint_id": next_id,  # fingerprint_id = id driver
                    })

                    if response.status_code == 200:
                        st.success("Driver added. Redirecting to dashboard...")
                        st.session_state["redirect_to"] = "Driver Status"
                        st.rerun()
                    else:
                        st.error("Failed to save driver to backend.")
                else:
                    st.error(f"Enrollment failed")
            except Exception as e:
                st.error(f"Invalid response format from device: {e}")
        else:
            st.error("Fingerprint enrollment failed or timed out.")




# --- Modify Driver Page ---
elif menu == "Modify Driver":
    st.title("Modify Driver Info and Status")
    with requests.get(f"{API_URL}/drivers") as response:
        drivers = response.json()


    if not drivers:
        st.warning("No drivers available. Please add a driver first.")
        st.stop()

    driver_options = {f"{d['name']} (ID: {d['id']})": d["id"] for d in drivers}
    selected = st.selectbox("Select Driver", list(driver_options.keys()))

    if selected:
        selected_id = driver_options[selected]
        driver_data = next(d for d in drivers if d["id"] == selected_id)

        current_name = driver_data["name"]
        current_fingerprint_id = driver_data["fingerprint_id"]
        current_phone_number = driver_data.get("phone_number", "")
        current_status = driver_data["status"]

        new_name = st.text_input("New Name", value=current_name)
        new_phone_number = st.text_input("New Phone Number", value=current_phone_number)
        new_status = st.selectbox("New Status", ["STAY", "JALAN", "OFF"], index=["STAY", "JALAN", "OFF"].index(current_status))

        col1, col2 = st.columns(2)
        with col1:
            update_info = st.button("Update Driver Info")
        with col2:
            delete_info = st.button("Delete Driver")

        if update_info:
            requests.put(f"{API_URL}/drivers/{selected_id}", json={
            "id": selected_id,
            "name": new_name,
            "phone_number": new_phone_number,
            "fingerprint_id": current_fingerprint_id,
            "status": new_status,
            "delivery_start": driver_data["delivery_start"]
        })
            requests.put(f"{API_URL}/drivers/{selected_id}/status", params={"status": new_status})
            st.success("Driver info and status updated. Redirecting to dashboard...")
            st.session_state["redirect_to"] = "Driver Status"
            st.rerun()
        
        if delete_info:
            payload = json.dumps({"command": "delete", "id": current_fingerprint_id})
            publish_mqtt(DELETE_TOPIC, payload)

            with st.spinner("Waiting for ESP32 to delete fingerprint..."):
                result = wait_response_delete_http(timeout=90)

            if result:
                try:
                    if result and result.get("status") == "success":
                        requests.delete(f"{API_URL}/drivers/{selected_id}")
                        st.success("Driver deleted. Redirecting to dashboard...")
                        st.session_state["redirect_to"] = "Driver Status"
                        st.rerun()
                    else:
                        st.error(f"Failed to delete fingerprint")
                except Exception as e:
                    st.error(f"Invalid response format from device: {e}")
            else:
                st.error("No response from device. Deletion failed or timed out.")

# --- Admin Page ---
elif menu == "Admin Page":
    st.title("Admin Management")

    # Fetch admin data
    with requests.get(f"{API_URL}/admins") as response:
        admins = response.json()


    st.subheader("Existing Admins")

    if not admins:
        st.info("No admin registered yet.")
    else:
        col1, col2, col3 = st.columns(3)
        cols = [col1, col2, col3]

        for idx, admin in enumerate(admins):
            with cols[idx % 3]:
                st.markdown(f"**{admin['name']} (ID: {admin['id']})**")
                st.caption(f"Phone: {admin['phone_number']}")

    st.subheader("Add New Admin")
    with st.form("add_admin_form"):
        admin_name = st.text_input("Admin Name")
        admin_phone = st.text_input("Admin Phone Number")
        submitted = st.form_submit_button("Add Admin")

        if submitted:
            response = requests.post(f"{API_URL}/admins", json={
                "name": admin_name,
                "phone_number": admin_phone
            })
            if response.status_code == 200:
                st.success("Admin added successfully.")
                st.rerun()
            else:
                st.error("Failed to add admin.")

    st.subheader("Modify Existing Admin")
    if admins:
        admin_options = {f"{a['name']} (ID: {a['id']})": a for a in admins}
        selected_admin_label = st.selectbox("Select Admin to Modify", list(admin_options.keys()))
        selected_admin = admin_options[selected_admin_label]

        new_admin_name = st.text_input("New Admin Name", value=selected_admin["name"])
        new_admin_phone = st.text_input("New Admin Phone Number (628xxxxxxxxxx)", value=selected_admin["phone_number"])
        update_button = st.button("Update Admin Info")

        if update_button:
            response = requests.put(f"{API_URL}/admins/{selected_admin['id']}", json={
                "name": new_admin_name,
                "phone_number": new_admin_phone
            })
            if response.status_code == 200:
                st.success("Admin info updated successfully.")
                st.rerun()
            else:
                st.error("Failed to update admin.")

    st.subheader("Delete Admin")
    if admins:
        admin_options = {f"{a['name']} (ID: {a['id']})": a["id"] for a in admins}
        selected_admin_del = st.selectbox("Select Admin to Delete", list(admin_options.keys()), key="delete_admin_select")
        if st.button("Delete Selected Admin"):
            selected_id = admin_options[selected_admin_del]
            requests.delete(f"{API_URL}/admins/{selected_id}")
            st.success("Admin deleted successfully.")
            st.rerun()
