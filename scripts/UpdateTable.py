from sqlalchemy import create_engine
engine = create_engine("sqlite:///./app.db")
with engine.begin() as c:
    try:
        c.exec_driver_sql("ALTER TABLE workflow_state ADD COLUMN process_definition_id VARCHAR(36)")
        print("Added workflow_state.process_definition_id")
    except Exception as e:
        print("No change / already exists:", e)
