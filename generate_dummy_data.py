import os
import random
import uuid
from datetime import datetime, timedelta

import psycopg
from faker import Faker

fake = Faker()
random.seed(42)
Faker.seed(42)

# --------------------------------------------------
# Database configuration
# --------------------------------------------------

DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "national_sports_poc"
DB_USER = "postgres"
DB_PASSWORD = os.environ["POSTGRES_PASSWORD"]

TOTAL_RECORDS = 1000

# --------------------------------------------------
# Synthetic reference data
# These are fictional POC values only.
# --------------------------------------------------

INDIVIDUAL_TYPES = [
    ("ATH", "Athlete", "رياضي"),
    ("COA", "Coach", "مدرب"),
    ("ADM", "Administrator", "إداري"),
]

GENDERS = [
    ("Male", "ذكر"),
    ("Female", "أنثى"),
]

NATIONALITIES = [
    ("Country A", "الدولة أ"),
    ("Country B", "الدولة ب"),
    ("Country C", "الدولة ج"),
    ("Country D", "الدولة د"),
]

MINISTRY_OFFICES = [
    ("Central Sports Office", "المكتب الرياضي المركزي"),
    ("Northern Sports Office", "المكتب الرياضي الشمالي"),
    ("Southern Sports Office", "المكتب الرياضي الجنوبي"),
    ("Eastern Sports Office", "المكتب الرياضي الشرقي"),
]

SPORTS = [
    ("Football", "كرة القدم"),
    ("Basketball", "كرة السلة"),
    ("Swimming", "السباحة"),
    ("Athletics", "ألعاب القوى"),
    ("Volleyball", "الكرة الطائرة"),
    ("Cycling", "ركوب الدراجات"),
    ("Table Tennis", "تنس الطاولة"),
    ("Archery", "الرماية"),
]

FEDERATIONS = [
    ("Federation 01", "الاتحاد 01"),
    ("Federation 02", "الاتحاد 02"),
    ("Federation 03", "الاتحاد 03"),
    ("Federation 04", "الاتحاد 04"),
]

CLUBS = [
    ("Sports Club 01", "النادي الرياضي 01"),
    ("Sports Club 02", "النادي الرياضي 02"),
    ("Sports Academy 01", "الأكاديمية الرياضية 01"),
    ("Sports Academy 02", "الأكاديمية الرياضية 02"),
]

REGIONS = {
    "Region 01": ["City 01", "City 02", "City 03"],
    "Region 02": ["City 04", "City 05"],
    "Region 03": ["City 06", "City 07"],
    "Region 04": ["City 08", "City 09"],
}

STATUSES = [
    ("1", "Active", "نشط"),
    ("2", "Inactive", "غير نشط"),
    ("3", "Pending", "قيد الانتظار"),
]

COACH_STATUSES = [
    ("1", "Active", "نشط"),
    ("2", "Inactive", "غير نشط"),
    ("3", "Not Applicable", "غير مطبق"),
]

CLUB_MATURITY = [
    ("1", "Developing", "قيد التطوير"),
    ("2", "Established", "راسخ"),
    ("3", "Advanced", "متقدم"),
]

# --------------------------------------------------
# Helper functions
# --------------------------------------------------

def get_age_group(age):
    if age < 18:
        return "JUN", "Junior", "ناشئ"
    return "SEN", "Senior", "كبار"


def get_quarter(date_value):
    return f"Q{((date_value.month - 1) // 3) + 1}"


def generate_record(index):
    registration_date = fake.date_time_between(
        start_date=datetime(2022, 1, 1),
        end_date=datetime(2026, 9, 30),
    )

    modified_date = registration_date + timedelta(
        days=random.randint(0, 120)
    )

    individual_type_code, individual_type, individual_type_ar = (
        random.choice(INDIVIDUAL_TYPES)
    )

    gender, gender_ar = random.choice(GENDERS)

    age = random.randint(12, 55)

    age_group_code, age_group_en, age_group_ar = get_age_group(age)

    athlete_status_code, status, status_ar = random.choice(STATUSES)

    if individual_type == "Coach":
        coach_status_code, coach_status, coach_status_ar = (
            random.choice(COACH_STATUSES[:2])
        )
    else:
        coach_status_code, coach_status, coach_status_ar = (
            COACH_STATUSES[2]
        )

    nationality, nationality_ar = random.choice(NATIONALITIES)

    ministry_en, ministry_ar = random.choice(MINISTRY_OFFICES)

    federation, federation_ar = random.choice(FEDERATIONS)

    sport_en, sport_ar = random.choice(SPORTS)

    club_en, club_ar = random.choice(CLUBS)

    region = random.choice(list(REGIONS.keys()))
    city = random.choice(REGIONS[region])

    is_para = random.random() < 0.12

    if is_para:
        para_en = "Yes"
        para_ar = "نعم"
        para_discipline = "1"
    else:
        para_en = "No"
        para_ar = "لا"
        para_discipline = "0"

    maturity_code, maturity_en, maturity_ar = random.choice(
        CLUB_MATURITY
    )

    athlete_type_en = random.choice(
        ["Amateur", "Professional", "Development"]
    )

    athlete_type_ar_map = {
        "Amateur": "هاوي",
        "Professional": "محترف",
        "Development": "تطوير",
    }

    athlete_type_ar = athlete_type_ar_map[athlete_type_en]

    institutional_en = random.choice(
        ["Public", "Private", "Ministry"]
    )

    institutional_ar_map = {
        "Public": "عام",
        "Private": "خاص",
        "Ministry": "وزارة",
    }

    institutional_ar = institutional_ar_map[institutional_en]

    amateur_professional = (
        "Professional"
        if athlete_type_en == "Professional"
        else "Amateur"
    )

    return (
        f"AFF-{index:06d}",
        registration_date,
        modified_date,
        registration_date.year,
        get_quarter(registration_date),

        f"CNT-{index:06d}",

        individual_type_code,
        individual_type,
        individual_type_ar,

        gender,
        gender_ar,
        age,

        athlete_status_code,
        status,
        status_ar,

        coach_status_code,
        coach_status,
        coach_status_ar,

        nationality,
        nationality_ar,

        ministry_en,
        ministry_ar,

        federation,
        federation_ar,

        sport_en,
        sport_ar,

        f"IND-{index:06d}",

        random.choice(
            ["Level 1", "Level 2", "Level 3", "Not Applicable"]
        ),

        club_en,
        club_ar,

        random.choice(["CLUB", "ACADEMY"]),

        athlete_type_en,
        athlete_type_ar,

        institutional_en,
        institutional_ar,

        para_en,
        para_ar,

        age_group_en,
        age_group_ar,

        maturity_code,
        maturity_en,
        maturity_ar,

        f"SEASON-{random.randint(2022, 2026)}",

        f"MEM-{index:06d}",

        random.choice(["AMATEUR", "PROFESSIONAL", "OTHER"]),

        para_discipline,

        amateur_professional,

        age_group_code,

        random.choice(["TYPE-A", "TYPE-B", "TYPE-C"]),

        city,
        region,
    )


# --------------------------------------------------
# SQL
# --------------------------------------------------

INSERT_SQL = """
INSERT INTO individual_affiliation (
    ldv_contactaffiliationrecordid,
    registration_date,
    modified_date,
    registration_year,
    registration_quarter,
    contactid,
    individual_type_code,
    individual_type,
    individual_type_ar,
    gender,
    gender_ar,
    age,
    athlet_status_code,
    status,
    status_ar,
    coach_status_code,
    coach_status,
    coach_status_ar,
    nationality,
    nationality_ar,
    ministry_office_en,
    ministry_office_ar,
    federation,
    federation_ar,
    sports_type_en,
    sports_type,
    individual_id,
    degree,
    clubacadmeyname_en,
    clubacadmeyname_ar,
    ldv_clubtypecode,
    athletetype_en,
    athletetype_ar,
    institutionalclub_en,
    institutionalclub_ar,
    isparalympicsport_en,
    isparalympicsport_ar,
    "AgeGroup_en",
    "AgeGroup_ar",
    club_maturitycode,
    club_maturity_en,
    club_maturity_ar,
    sport_season_id,
    federation_membership_id,
    ldv_sportprofessionalsubtypecode,
    ldv_isparalympicsportdiscipline,
    amature_to_professional,
    "AgeGroup_code",
    typecode,
    city,
    region
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s
)
"""


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    print("Connecting to PostgreSQL...")

    connection_string = (
        f"host={DB_HOST} "
        f"port={DB_PORT} "
        f"dbname={DB_NAME} "
        f"user={DB_USER} "
        f"password={DB_PASSWORD}"
    )

    records = [
        generate_record(i)
        for i in range(1, TOTAL_RECORDS + 1)
    ]

    with psycopg.connect(connection_string) as conn:
        with conn.cursor() as cur:

            # Prevent accidental duplicate dummy data
            cur.execute(
                "SELECT COUNT(*) FROM individual_affiliation"
            )

            existing_count = cur.fetchone()[0]

            if existing_count > 0:
                print(
                    f"Table already contains {existing_count} records."
                )
                print("No new records inserted.")
                return

            cur.executemany(INSERT_SQL, records)

        conn.commit()

    print(
        f"SUCCESS: {TOTAL_RECORDS} synthetic records inserted."
    )


if __name__ == "__main__":
    main()