import pytest
import os
import json
from unittest.mock import AsyncMock, patch, MagicMock

# Import necessary modules
# Since agent.py initializes lots of things, we might want to mock its dependencies or just test the tools
import db_helper

@pytest.mark.asyncio
async def test_patient_registration_tool_trigger():
    """TC-12: LLM executes record_patient_data correctly."""
    # Assuming record_patient_data is an LLM tool in agent.py or db_helper.py
    # For now, we mock the tool or call the actual db_helper
    # The actual implementation of db_helper has Mock SixtyDB in mock_sixtydb.py
    import mock_sixtydb
    
    # Reset mock db for clean test
    mock_sixtydb.clear_db()
    
    # The tool signature usually takes name, age, gestational_weeks
    # We simulate the LLM calling the function
    from mock_sixtydb import MockSixtyDB
    db = MockSixtyDB()
    patient_id = db.record_patient_data(name="ಸುಮಾ", age=24, gestational_weeks=28)
    
    assert patient_id is not None
    
    patient = db.get_patient(patient_id)
    assert patient["name"] == "ಸುಮಾ"
    assert patient["age"] == 24
    assert patient["gestational_weeks"] == 28

@pytest.mark.asyncio
async def test_normal_bp_tool_trigger():
    """TC-13: Normal BP reading."""
    from mock_sixtydb import MockSixtyDB
    db = MockSixtyDB()
    patient_id = db.record_patient_data(name="test", age=25, gestational_weeks=20)
    
    result = db.record_bp(patient_id, 120, 80)
    assert result.get("pre_eclampsia_risk") == "NORMAL"

@pytest.mark.asyncio
async def test_high_risk_preeclampsia_bp_trigger():
    """TC-14: High BP reading triggers alert."""
    from mock_sixtydb import MockSixtyDB
    db = MockSixtyDB()
    patient_id = db.record_patient_data(name="test", age=25, gestational_weeks=20)
    
    result = db.record_bp(patient_id, 150, 100)
    assert result.get("pre_eclampsia_risk") == "HIGH"
    assert result.get("alert_flag") == True

@pytest.mark.asyncio
async def test_symptom_extraction_tool():
    """TC-15: Record symptoms tool."""
    from mock_sixtydb import MockSixtyDB
    db = MockSixtyDB()
    patient_id = db.record_patient_data(name="test", age=25, gestational_weeks=20)
    
    result = db.record_symptoms(patient_id, headache=True, blurry_vision=True, swelling=False)
    assert result.get("status") == "success"
    
    patient = db.get_patient(patient_id)
    assert patient["symptoms"]["headache"] == True
    assert patient["symptoms"]["blurry_vision"] == True
    assert patient["symptoms"]["swelling"] == False
