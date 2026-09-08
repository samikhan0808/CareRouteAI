class SymptomSession(db.Model):
    __tablename__ = "symptom_sessions"
    session_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.patient_id"))
    # MVP convenience fields (no login/patient-profile system yet)
    patient_name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    symptoms = db.Column(db.Text)