# 1. Use an official Python runtime as a parent image
FROM python:3.11-slim

# 2. Set the working directory in the container
WORKDIR /app

# 3. Install system dependencies required for PostgreSQL (psycopg2/asyncpg)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 4. Copy the requirements file into the container
COPY requirements.txt .

# 5. Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy the rest of your application code
COPY . .

# 7. Hugging Face Spaces uses port 7860 by default
EXPOSE 7860

# 8. Command to run the FastAPI server
# We use 'server:app' because your main file is server.py and the FastAPI instance is 'app'
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]