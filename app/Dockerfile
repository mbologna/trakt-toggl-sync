# Use Python as the base image
FROM python:3.10-slim

# Set work directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the script
COPY sync.py .

# Set the entrypoint
ENTRYPOINT ["python", "-u", "sync.py"]
