from google import genai

PROJECT_ID = "project-c3cf90ac-efd1-4b3e-871"
LOCATION = "global"
MODEL = "gemini-3.8-flash"

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)

question = "How many registrations are there for each sport?"

schema = """
Table: individual_affiliation

Relevant columns:
- registration_date TIMESTAMP
- registration_year INTEGER
- registration_quarter VARCHAR
- individual_type VARCHAR
- gender VARCHAR
- age INTEGER
- status VARCHAR
- nationality VARCHAR
- federation VARCHAR
- sports_type_en VARCHAR
- AgeGroup_en VARCHAR
- isparalympicsport_en VARCHAR
- city VARCHAR
- region VARCHAR
"""

prompt = f"""
You are a PostgreSQL expert.

Convert the user's natural-language question into a PostgreSQL SELECT query.

Database schema:
{schema}

Rules:
1. Generate PostgreSQL syntax only.
2. Only SELECT queries are allowed.
3. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE or TRUNCATE.
4. Use only the table and columns provided in the schema.
5. Do not invent columns.
6. Return only SQL without markdown or explanation.

User question:
{question}
"""

response = client.models.generate_content(
    model=MODEL,
    contents=prompt,
)

sql = response.text.strip()

print("\nQUESTION:")
print(question)

print("\nGENERATED SQL:")
print(sql)