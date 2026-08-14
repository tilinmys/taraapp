import json
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger("sakhi_agent")

MOCK_DB_FILE = "mock_sixtydb.json"

def init_mock_db():
    """Initializes SixtyDB with pre-populated test mothers from the Bidar region."""
    initial_data = {
        "patients": [
            {
                "patient_id": "M-201",
                "name": "Suma Gowda",
                "age": 24,
                "village": "Kamalnagar",
                "phone": "9-8-7-6-5-4-3-2-1-0",
                "risk_status": "NORMAL",
                "history": [],
                "symptoms": {}
            },
            {
                "patient_id": "M-202",
                "name": "Priya Patil",
                "age": 29,
                "village": "Aurad",
                "phone": "8-7-6-5-4-3-2-1-0-9",
                "risk_status": "HIGH_RISK",
                "history": [
                    {"date": "2026-08-01", "bp": "145/95", "notes": "Mild pre-eclampsia indicators."}
                ],
                "symptoms": {}
            }
        ],
        "teleconsult_logs": [],
        "bp_records": []
    }
    with open(MOCK_DB_FILE, "w") as f:
        json.dump(initial_data, f, indent=4)
    logger.info("[Mock SixtyDB] Initialized fresh test database.")

def read_db() -> Dict[str, Any]:
    if not os.path.exists(MOCK_DB_FILE):
        init_mock_db()
    with open(MOCK_DB_FILE, "r") as f:
        return json.load(f)

def write_db(data: Dict[str, Any]):
    with open(MOCK_DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

def clear_db():
    init_mock_db()

class MockSixtyDB:
    def __init__(self):
        self.db = read_db()
        
    def _save(self):
        write_db(self.db)
        
    def record_patient_data(self, name: str, age: int, gestational_weeks: int) -> str:
        patient_id = f"M-{len(self.db['patients']) + 201}"
        patient = {
            "patient_id": patient_id,
            "name": name,
            "age": age,
            "gestational_weeks": gestational_weeks,
            "risk_status": "NORMAL",
            "history": [],
            "symptoms": {}
        }
        self.db["patients"].append(patient)
        self._save()
        return patient_id

    def get_patient(self, patient_id: str) -> Optional[Dict[str, Any]]:
        for p in self.db["patients"]:
            if p["patient_id"] == patient_id:
                return p
        return None

    def record_bp(self, patient_id: str, systolic: int, diastolic: int) -> Dict[str, Any]:
        risk = "NORMAL"
        alert = False
        if systolic >= 140 or diastolic >= 90:
            risk = "HIGH"
            alert = True
            
        record = {
            "patient_id": patient_id,
            "date": datetime.now().isoformat(),
            "systolic": systolic,
            "diastolic": diastolic,
            "pre_eclampsia_risk": risk
        }
        self.db["bp_records"].append(record)
        
        patient = self.get_patient(patient_id)
        if patient:
            patient["history"].append({"date": record["date"], "bp": f"{systolic}/{diastolic}"})
            if risk == "HIGH":
                patient["risk_status"] = "HIGH_RISK"
        
        self._save()
        return {"status": "success", "pre_eclampsia_risk": risk, "alert_flag": alert}

    def record_symptoms(self, patient_id: str, headache: bool, blurry_vision: bool, swelling: bool) -> Dict[str, Any]:
        patient = self.get_patient(patient_id)
        if not patient:
            return {"status": "error", "message": "Patient not found"}
            
        patient["symptoms"] = {
            "headache": headache,
            "blurry_vision": blurry_vision,
            "swelling": swelling
        }
        self._save()
        return {"status": "success"}
