# Dockerfile

# 1. Base image
FROM python:3.11-slim

# 2. Environment settings
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. Workdir inside the container
WORKDIR /app

# 4. Install system dependencies (optional but common)
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 5. Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy project code
COPY app ./app

# 7. Expose port
EXPOSE 8000

# 8. Run the FastAPI app with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
