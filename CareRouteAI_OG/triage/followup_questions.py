"""
Followup Questions — CareRouteAI
Specialty-specific question banks for deeper triage.
"""
FOLLOWUP_BANK: dict[str, list[str]] = {
    "cardiology": [
        "Does the chest pain radiate to your arm, jaw, or back?",
        "Are you experiencing shortness of breath or sweating?",
        "Do you have a history of heart disease or high blood pressure?",
        "Is the pain constant or does it come and go?",
        "Does exertion make it worse?",
    ],
    "pulmonology": [
        "Are you coughing up any mucus or blood?",
        "Do you smoke or have you ever smoked?",
        "Have you had any recent respiratory infections?",
        "Does the breathlessness worsen when lying flat?",
        "Do you have any known allergies or asthma?",
    ],
    "neurology": [
        "Where exactly is the headache located?",
        "Is it the worst headache of your life?",
        "Are you experiencing any vision changes or sensitivity to light?",
        "Do you have numbness, tingling, or weakness in any limb?",
        "Have you had seizures or blackouts before?",
    ],
    "gastroenterology": [
        "Is the pain before or after eating?",
        "Any blood in your stool or vomit?",
        "How long have you had these symptoms?",
        "Any recent travel or change in diet?",
        "Do you have nausea or loss of appetite?",
    ],
    "orthopedics": [
        "Was there any injury or accident that caused this?",
        "Where exactly is the pain — joint, muscle, or bone?",
        "Does it hurt more when moving or at rest?",
        "Is there any swelling, bruising, or deformity?",
        "Can you bear weight on the affected area?",
    ],
    "generalPractice": [
        "How long have you had these symptoms?",
        "Do you have a fever?",
        "Any other symptoms like cough, runny nose, or body aches?",
        "Have you been in contact with anyone who is sick?",
        "Are you on any medications currently?",
    ],
    "psychiatry": [
        "How long have you been feeling this way?",
        "Is this affecting your sleep or appetite?",
        "Have you experienced this before?",
        "Do you have support from family or friends?",
        "Are you having any thoughts of harming yourself?",
    ],
}

def get_followups(specialty: str, count: int = 3) -> list[str]:
    """Return up to `count` questions for a given specialty."""
    return FOLLOWUP_BANK.get(specialty, FOLLOWUP_BANK["generalPractice"])[:count]
