from gemini_service import generate_sql


question = "How many registrations are there for each sport?"

sql = generate_sql(question)

print("\nQUESTION:")
print(question)

print("\nGENERATED BIGQUERY SQL:")
print(sql)