from typing import Any, Dict, List, Optional, TypedDict


class Doctor(TypedDict, total=False):
    id: str
    name: str
    specialty: str
    city: str
    credentials: str
    rating: str
    oladocUrl: str


class Appointment(TypedDict, total=False):
    id: str
    userId: str
    specialistId: str
    date: str
    time: str
    reason: str
    status: str
    bookingReference: str


class Disease(TypedDict, total=False):
    id: str
    label: str
    specialty: str
    precautions: List[str]
    sources: List[str]


class Checklist(TypedDict, total=False):
    id: str
    appointmentId: str
    items: List[Dict[str, Any]]
    completed: bool

SPECIALISTS = [
    {
        "id": "sp-primary",
        "name": "Dr. Muhammad Nouman Anjum",
        "specialty": "Primary Care",
        "source": "Marham",
        "profileUrl": "https://www.marham.pk/online-consultation/general-physician/lahore/dr-muhammad-nouman-anjum-4262",
        "city": "Lahore",
        "credentials": "MBBS, MACP (USA), Certified Diabetologist (UK)",
        "experience": "13 years",
        "rating": "4.9/5",
        "reviews": 2596,
        "oladocUrl": "https://oladoc.com/pakistan/lahore/general-physician",
    },
    {
        "id": "sp-gastro",
        "name": "Dr. Usman Javaid",
        "specialty": "Gastroenterology",
        "source": "Marham",
        "profileUrl": "https://www.marham.pk/doctors/lahore/gastroenterologist/dr-usman-javaid",
        "city": "Lahore",
        "credentials": "MBBS, Diploma in Gastroenterology (UK), Diploma in Diabetes (UK)",
        "experience": "10 years",
        "rating": "5/5",
        "reviews": 1860,
        "oladocUrl": "https://oladoc.com/pakistan/lahore/gastroenterologist",
    },
    {
        "id": "sp-cardio",
        "name": "Dr. Syed Nouman Kazmi",
        "specialty": "Cardiology & Emergency",
        "source": "Marham",
        "profileUrl": "https://www.marham.pk/doctors/lahore/cardiologist/dr-syed-nouman-kazmi",
        "city": "Lahore",
        "credentials": "MBBS, FCPS (Cardiology)",
        "experience": "11 years",
        "rating": "5/5",
        "reviews": 200,
        "oladocUrl": "https://oladoc.com/pakistan/lahore/cardiologist",
    },
    {
        "id": "sp-neuro",
        "name": "Dr. Hashir Amin Malik",
        "specialty": "Neurology",
        "source": "Marham",
        "profileUrl": "https://www.marham.pk/doctors/lahore/neuro-physician/dr-hashir-amin-malik",
        "city": "Lahore",
        "credentials": "MBBS, MD Neurology",
        "experience": "10 years",
        "rating": "4.9/5",
        "reviews": 85,
        "oladocUrl": "https://oladoc.com/pakistan/lahore/neurologist",
        "oladocProfileUrl": "https://oladoc.com/pakistan/lahore/dr/neurologist/hashir-amin-malik/3637096",
    },
    {
        "id": "sp-derma",
        "name": "Dr. Sana Tariq",
        "specialty": "Dermatology",
        "source": "Oladoc",
        "profileUrl": "https://oladoc.com/pakistan/lahore/dermatologist",
        "city": "Lahore",
        "credentials": "MBBS, FCPS Dermatology",
        "experience": "12 years",
        "rating": "4.8/5",
        "reviews": 1350,
        "oladocUrl": "https://oladoc.com/pakistan/lahore/dermatologist",
    },
    {
        "id": "sp-child",
        "name": "Dr. Ayesha Ali",
        "specialty": "Pediatrics",
        "source": "Oladoc",
        "profileUrl": "https://oladoc.com/pakistan/lahore/pediatrician",
        "city": "Lahore",
        "credentials": "MBBS, FCPS Pediatrics",
        "experience": "9 years",
        "rating": "4.9/5",
        "reviews": 980,
        "oladocUrl": "https://oladoc.com/pakistan/lahore/pediatrician",
    },
    {
        "id": "sp-gyn",
        "name": "Dr. Hina Akhtar",
        "specialty": "Gynecology",
        "source": "Oladoc",
        "profileUrl": "https://oladoc.com/pakistan/lahore/gynecologist",
        "city": "Lahore",
        "credentials": "MBBS, FCPS Gynecology",
        "experience": "11 years",
        "rating": "4.9/5",
        "reviews": 1180,
        "oladocUrl": "https://oladoc.com/pakistan/lahore/gynecologist",
    },
    {
        "id": "sp-ent",
        "name": "Dr. Talha Fareed",
        "specialty": "ENT",
        "source": "Oladoc",
        "profileUrl": "https://oladoc.com/pakistan/lahore/ent-specialist",
        "city": "Lahore",
        "credentials": "MBBS, FCPS ENT",
        "experience": "10 years",
        "rating": "4.7/5",
        "reviews": 890,
        "oladocUrl": "https://oladoc.com/pakistan/lahore/ent-specialist",
    },
    {
        "id": "sp-ortho",
        "name": "Dr. Waqas Ahmed",
        "specialty": "Orthopedics",
        "source": "Oladoc",
        "profileUrl": "https://oladoc.com/pakistan/lahore/orthopedic-surgeon",
        "city": "Lahore",
        "credentials": "MBBS, MS Orthopedics",
        "experience": "14 years",
        "rating": "4.8/5",
        "reviews": 1700,
        "oladocUrl": "https://oladoc.com/pakistan/lahore/orthopedic-surgeon",
    },
    {
        "id": "sp-eye",
        "name": "Dr. Maryam Iqbal",
        "specialty": "Ophthalmology",
        "source": "Oladoc",
        "profileUrl": "https://oladoc.com/pakistan/lahore/ophthalmologist",
        "city": "Lahore",
        "credentials": "MBBS, FCPS Ophthalmology",
        "experience": "8 years",
        "rating": "4.9/5",
        "reviews": 715,
        "oladocUrl": "https://oladoc.com/pakistan/lahore/ophthalmologist",
    },
]


def specialist_public(specialist: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(specialist)
    result["bookingType"] = "physical"
    result["bookingLabel"] = "Physical appointment"
    result["bookingUrl"] = specialist.get("profileUrl")
    result["oladocBookingUrl"] = specialist.get("oladocUrl")
    result["oladocProfileUrl"] = specialist.get("oladocProfileUrl")
    result["dataSource"] = "Verified provider profile; live availability is confirmed on the provider site."
    return result


def default_reason(specialist: Dict[str, Any]) -> str:
    return f"Consultation with {specialist.get('name', 'specialist')} for {specialist.get('specialty', 'care')}."
